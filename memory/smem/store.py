# SPDX-License-Identifier: MIT
"""SQLite-backed store for documents, chunks and memories.

Three ideas from Supermemory are load-bearing here and shape the schema:

  container      Every row is scoped to a container tag. Retrieval never
                 crosses one. This is the multi-tenancy / per-user boundary,
                 and making it a column that every query filters on (rather
                 than a convention) is what keeps it actually airtight.

  memory vs      A *document* is source material, split into *chunks* for
  document       retrieval. A *memory* is an atomic fact extracted from it.
                 Memories are what make answers short: you retrieve a handful
                 of one-line facts instead of pages of prose.

  temporal       Updating a memory does NOT overwrite it. The old row keeps
  truth          its content and gets is_latest=0 plus a pointer from the new
                 row, so the history of what was believed and when stays
                 intact and auditable. Forgetting is a soft delete for the
                 same reason -- see forget(), and purge() for when you really
                 mean it.

Vectors live inline as float32 BLOBs and are brute-force scanned with numpy.
At this scale that is the honest choice: an ANN index would add a dependency
and an approximation in order to speed up a sub-millisecond scan, while the
actual latency is dominated by the model calls out over PCIe.
"""

import json
import sqlite3
import time
import uuid

import numpy as np

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    container   TEXT NOT NULL,
    custom_id   TEXT,
    title       TEXT,
    source      TEXT,
    content     TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}',
    doc_date    REAL,
    created_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'done'
);
CREATE INDEX IF NOT EXISTS ix_doc_container ON documents(container);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_custom
    ON documents(container, custom_id) WHERE custom_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS chunks (
    id        TEXT PRIMARY KEY,
    doc_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    container TEXT NOT NULL,
    pos       INTEGER NOT NULL,
    heading   TEXT,
    text      TEXT NOT NULL,
    vec       BLOB
);
CREATE INDEX IF NOT EXISTS ix_chunk_container ON chunks(container);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    container    TEXT NOT NULL,
    content      TEXT NOT NULL,
    kind         TEXT DEFAULT 'fact',
    doc_id       TEXT REFERENCES documents(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL DEFAULT 1,
    is_latest    INTEGER NOT NULL DEFAULT 1,
    supersedes   TEXT,
    forgotten_at REAL,
    created_at   REAL NOT NULL,
    event_date   REAL,
    vec          BLOB
);
CREATE INDEX IF NOT EXISTS ix_mem_container ON memories(container, is_latest);
CREATE INDEX IF NOT EXISTS ix_mem_doc ON memories(doc_id);

-- Relations between memories other than `updates` (which lives in
-- memories.supersedes, because an update also demotes the old row).
--   extends:   src (newer) adds detail to dst (older); both stay true.
--   duplicate: src restates dst (recorded for a later clean-up pass).
-- score is the cosine between the two; conf is the judge's probability.
CREATE TABLE IF NOT EXISTS memory_edges (
    src        TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    dst        TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    score      REAL,
    created_at REAL NOT NULL,
    PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS ix_edge_dst ON memory_edges(dst);

-- Calibration data. Every background decision is logged with the model's
-- full probability vector; labels are ground truth (a person correcting a
-- type or a link in the console, or an imported hand-labelled set). Pairing
-- the two lets calibrate.py fit how far to trust the probabilities and where
-- to put the thresholds, and re-fit as labels accumulate.
--   kind     'type' (subject = memory, other = '') or
--            'extends' (subject = newer memory, other = older memory)
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    subject    TEXT NOT NULL,
    other      TEXT NOT NULL DEFAULT '',
    options    TEXT NOT NULL,          -- JSON list, in letter order
    probs      TEXT NOT NULL,          -- JSON list, raw model probabilities
    choice     TEXT NOT NULL,
    model      TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dec_subject ON decisions(kind, subject, other);
CREATE TABLE IF NOT EXISTS labels (
    kind       TEXT NOT NULL,
    subject    TEXT NOT NULL,
    other      TEXT NOT NULL DEFAULT '',
    label      TEXT NOT NULL,
    source     TEXT NOT NULL,          -- 'user' | 'import'
    created_at REAL NOT NULL,
    PRIMARY KEY (kind, subject, other)
);
CREATE TABLE IF NOT EXISTS calibration (
    kind        TEXT PRIMARY KEY,
    temperature REAL NOT NULL,
    threshold   REAL NOT NULL,
    n           INTEGER NOT NULL,
    stats       TEXT NOT NULL,         -- JSON: before/after log loss, ECE, accuracy
    fitted_at   REAL NOT NULL
);
"""

# Columns added after the first release. SQLite has no ADD COLUMN IF NOT
# EXISTS, so they are applied by _migrate() against PRAGMA table_info.
#   mtype       fact | preference | episode -- NULL means "not classified
#               yet". The older `kind` column defaults to 'fact' on every
#               row, so it cannot tell classified from unclassified.
#   dreamed_at  when the background worker last looked for extends
#               relations for this memory; NULL means never.
#   mtype_conf  the (calibrated) probability of mtype; NULL = unknown,
#               1.0 = set by a person.
#   conf        on memory_edges: the judge's probability for the edge kind.
_MIGRATIONS = [
    ("memories", "mtype", "TEXT"),
    ("memories", "dreamed_at", "REAL"),
    ("memories", "mtype_conf", "REAL"),
    ("memory_edges", "conf", "REAL"),
]


def _vec_out(v):
    return np.asarray(v, dtype=np.float32).tobytes()


def _vec_in(b):
    return np.frombuffer(b, dtype=np.float32) if b else None


def nid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, path, readonly=False):
        self.path = str(path)
        self.readonly = readonly
        if readonly:
            # A read-only connection per request lets the console and the
            # read endpoints run WITHOUT the server's write lock. WAL mode
            # gives readers a consistent snapshot while a writer is active,
            # so a dashboard refresh never waits behind a 60 s ask() or a
            # document ingest.
            self.db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True,
                                      check_same_thread=False, timeout=15)
            self.db.row_factory = sqlite3.Row
            return
        # check_same_thread=False because ThreadingHTTPServer hands each
        # request to a fresh thread. It is safe ONLY because every WRITER
        # serialises: the CLI is single-threaded, the server holds one lock
        # around every write, and the engine's own thread pools touch HTTP
        # endpoints, never this connection. Remove the lock and this becomes a
        # corruption bug rather than an exception.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self):
        for table, col, decl in _MIGRATIONS:
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def close(self):
        self.db.close()

    # ------------------------------------------------------------ documents

    def add_document(self, container, content, title=None, source=None,
                     custom_id=None, metadata=None, doc_date=None):
        """Insert, or replace an existing doc with the same custom_id.

        Re-ingesting the same file should not silently double every chunk, so
        a repeated custom_id deletes the old document and cascades to its
        chunks and memories. Without the ON DELETE CASCADE in the schema this
        is the classic way a store fills up with duplicates nobody notices.
        """
        now = time.time()
        if custom_id:
            old = self.db.execute(
                "SELECT id FROM documents WHERE container=? AND custom_id=?",
                (container, custom_id)).fetchone()
            if old:
                self.db.execute("DELETE FROM documents WHERE id=?", (old["id"],))
        did = nid("doc")
        self.db.execute(
            "INSERT INTO documents"
            " (id,container,custom_id,title,source,content,metadata,doc_date,"
            "  created_at,status) VALUES (?,?,?,?,?,?,?,?,?,'done')",
            (did, container, custom_id, title, source, content,
             json.dumps(metadata or {}), doc_date or now, now))
        self.db.commit()
        return did

    def get_document(self, doc_id):
        return self.db.execute("SELECT * FROM documents WHERE id=?",
                               (doc_id,)).fetchone()

    def documents(self, container):
        return self.db.execute(
            "SELECT * FROM documents WHERE container=? ORDER BY created_at",
            (container,)).fetchall()

    # --------------------------------------------------------------- chunks

    def add_chunks(self, doc_id, container, chunks, vecs):
        rows = [(nid("chk"), doc_id, container, i, c.get("heading"), c["text"],
                 _vec_out(vecs[i]))
                for i, c in enumerate(chunks)]
        self.db.executemany(
            "INSERT INTO chunks (id,doc_id,container,pos,heading,text,vec)"
            " VALUES (?,?,?,?,?,?,?)", rows)
        self.db.commit()
        return [r[0] for r in rows]

    # ------------------------------------------------------------- memories

    def add_memory(self, container, content, vec, kind="fact", doc_id=None,
                   event_date=None, supersedes=None):
        """Insert a memory; if it supersedes one, demote the old row."""
        now = time.time()
        version = 1
        if supersedes:
            old = self.db.execute("SELECT version FROM memories WHERE id=?",
                                  (supersedes,)).fetchone()
            if old:
                version = old["version"] + 1
                self.db.execute(
                    "UPDATE memories SET is_latest=0 WHERE id=?", (supersedes,))
        mid = nid("mem")
        self.db.execute(
            "INSERT INTO memories"
            " (id,container,content,kind,doc_id,version,is_latest,supersedes,"
            "  created_at,event_date,vec) VALUES (?,?,?,?,?,?,1,?,?,?,?)",
            (mid, container, content, kind, doc_id, version, supersedes,
             now, event_date or now, _vec_out(vec)))
        self.db.commit()
        return mid

    def forget(self, mem_id):
        """Soft delete. The row stays; retrieval stops seeing it."""
        self.db.execute(
            "UPDATE memories SET forgotten_at=?, is_latest=0 WHERE id=?",
            (time.time(), mem_id))
        self.db.commit()

    def unforget(self, mem_id):
        self.db.execute(
            "UPDATE memories SET forgotten_at=NULL, is_latest=1 WHERE id=?",
            (mem_id,))
        self.db.commit()

    def purge(self, container):
        """Hard delete a whole container. The one operation that is not undoable."""
        n = self.db.execute("SELECT COUNT(*) c FROM documents WHERE container=?",
                            (container,)).fetchone()["c"]
        self.db.execute("DELETE FROM documents WHERE container=?", (container,))
        self.db.execute("DELETE FROM chunks WHERE container=?", (container,))
        self.db.execute("DELETE FROM memories WHERE container=?", (container,))
        self.db.commit()
        return n

    def memory(self, mem_id):
        return self.db.execute("SELECT * FROM memories WHERE id=?",
                               (mem_id,)).fetchone()

    def memories(self, container, include_old=False, include_forgotten=False):
        q = "SELECT * FROM memories WHERE container=?"
        if not include_old:
            q += " AND is_latest=1"
        if not include_forgotten:
            q += " AND forgotten_at IS NULL"
        return self.db.execute(q + " ORDER BY created_at", (container,)).fetchall()

    def history(self, mem_id):
        """Walk the supersedes chain back to the original claim."""
        out, seen = [], set()
        cur = self.memory(mem_id)
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            out.append(cur)
            cur = self.memory(cur["supersedes"]) if cur["supersedes"] else None
        return out

    # -------------------------------------------------------------- vectors

    def matrix(self, container, table):
        """-> (rows, (n,dim) float32) for the live rows in a container."""
        if table == "memories":
            q = ("SELECT * FROM memories WHERE container=? AND is_latest=1"
                 " AND forgotten_at IS NULL AND vec IS NOT NULL")
        else:
            q = "SELECT * FROM chunks WHERE container=? AND vec IS NOT NULL"
        rows = [r for r in self.db.execute(q, (container,)).fetchall() if r["vec"]]
        if not rows:
            return [], np.zeros((0, 1), dtype=np.float32)
        m = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        return rows, m.astype(np.float32)

    def containers(self):
        # A container can hold memories with no documents (anything written
        # through remember()), so both tables count.
        return [r[0] for r in self.db.execute(
            "SELECT container FROM documents UNION "
            "SELECT container FROM memories ORDER BY 1")]

    # ------------------------------------------------------- console queries

    def memory_page(self, container, status="live", q=None, doc_id=None,
                    limit=50, offset=0, mtype=None):
        """Paged memories for the console. status: live|superseded|forgotten|all."""
        where = ["m.container=?"]
        args = [container]
        if status == "live":
            where.append("m.is_latest=1 AND m.forgotten_at IS NULL")
        elif status == "superseded":
            where.append("m.is_latest=0 AND m.forgotten_at IS NULL")
        elif status == "forgotten":
            where.append("m.forgotten_at IS NOT NULL")
        if q:
            where.append("m.content LIKE ?")
            args.append(f"%{q}%")
        if doc_id:
            where.append("m.doc_id=?")
            args.append(doc_id)
        if mtype == "unclassified":
            where.append("m.mtype IS NULL")
        elif mtype:
            where.append("m.mtype=?")
            args.append(mtype)
        w = " AND ".join(where)
        total = self.db.execute(
            f"SELECT COUNT(*) FROM memories m WHERE {w}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT m.id, m.content, m.version, m.is_latest, m.supersedes,"
            f"       m.forgotten_at, m.created_at, m.event_date, m.doc_id, m.mtype, m.mtype_conf,"
            f"       d.title AS doc_title,"
            f"       (SELECT n.id FROM memories n WHERE n.supersedes=m.id"
            f"        LIMIT 1) AS superseded_by"
            f"  FROM memories m LEFT JOIN documents d ON d.id=m.doc_id"
            f" WHERE {w} ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
            args + [int(limit), int(offset)]).fetchall()
        return total, rows

    def document_summary(self, container):
        """Per document: size, chunk count, live/superseded fact counts."""
        return self.db.execute(
            "SELECT d.id, d.title, d.custom_id, d.created_at,"
            "       LENGTH(d.content) AS chars,"
            "       (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks,"
            "       (SELECT COUNT(*) FROM memories m WHERE m.doc_id=d.id"
            "          AND m.is_latest=1 AND m.forgotten_at IS NULL) AS facts,"
            "       (SELECT COUNT(*) FROM memories m WHERE m.doc_id=d.id"
            "          AND m.is_latest=0) AS superseded"
            "  FROM documents d WHERE d.container=? ORDER BY d.title",
            (container,)).fetchall()

    def created_series(self, container, bucket_s=3600):
        """Memories created per time bucket -> [(bucket_start, n)]."""
        return self.db.execute(
            "SELECT CAST(created_at / ? AS INTEGER) * ? AS b, COUNT(*)"
            "  FROM memories WHERE container=? GROUP BY b ORDER BY b",
            (bucket_s, bucket_s, container)).fetchall()

    def stats(self, container):
        def g(q):
            return self.db.execute(q, (container,)).fetchone()[0]
        return {
            "documents": g("SELECT COUNT(*) FROM documents WHERE container=?"),
            "chunks": g("SELECT COUNT(*) FROM chunks WHERE container=?"),
            "memories_live": g(
                "SELECT COUNT(*) FROM memories WHERE container=?"
                " AND is_latest=1 AND forgotten_at IS NULL"),
            "memories_superseded": g(
                "SELECT COUNT(*) FROM memories WHERE container=?"
                " AND is_latest=0 AND forgotten_at IS NULL"),
            "memories_forgotten": g(
                "SELECT COUNT(*) FROM memories WHERE container=?"
                " AND forgotten_at IS NOT NULL"),
            "corpus_chars": g("SELECT COALESCE(SUM(LENGTH(content)),0)"
                              " FROM documents WHERE container=?"),
        }

    # ------------------------------------------------------ enrichment state

    def set_mtype(self, mem_id, mtype, conf=None):
        self.db.execute("UPDATE memories SET mtype=?, mtype_conf=? WHERE id=?",
                        (mtype, conf, mem_id))
        self.db.commit()

    # ------------------------------------------------------- calibration data

    def log_decisions(self, rows, model=None):
        """rows: [(kind, subject, other, options, probs, choice)]"""
        now = time.time()
        self.db.executemany(
            "INSERT INTO decisions (kind,subject,other,options,probs,choice,model,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(k, s, o or "", json.dumps(opts), json.dumps([round(p, 5) for p in ps]),
              ch, model, now) for k, s, o, opts, ps, ch in rows])
        self.db.commit()

    def add_label(self, kind, subject, other, label, source="user"):
        self.db.execute(
            "INSERT OR REPLACE INTO labels (kind,subject,other,label,source,created_at)"
            " VALUES (?,?,?,?,?,?)", (kind, subject, other or "", label, source, time.time()))
        self.db.commit()

    def labelled_decisions(self, kind):
        """Each label joined with the model's most recent decision on the same
        item. -> [(options, probs, label)]. Labels with no decision yet (the
        model never judged that pair) are skipped."""
        out = []
        for r in self.db.execute(
                "SELECT l.label, d.options, d.probs FROM labels l"
                "  JOIN decisions d ON d.id = (SELECT MAX(id) FROM decisions x"
                "       WHERE x.kind=l.kind AND x.subject=l.subject AND x.other=l.other)"
                " WHERE l.kind=?", (kind,)):
            out.append((json.loads(r["options"]), json.loads(r["probs"]), r["label"]))
        return out

    def label_counts(self):
        return {f"{r[0]}/{r[1]}": r[2] for r in self.db.execute(
            "SELECT kind, source, COUNT(*) FROM labels GROUP BY kind, source")}

    def calibration(self, kind):
        r = self.db.execute("SELECT * FROM calibration WHERE kind=?", (kind,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["stats"] = json.loads(d["stats"])
        return d

    def set_calibration(self, kind, temperature, threshold, n, stats):
        self.db.execute(
            "INSERT OR REPLACE INTO calibration (kind,temperature,threshold,n,stats,fitted_at)"
            " VALUES (?,?,?,?,?,?)",
            (kind, temperature, threshold, n, json.dumps(stats), time.time()))
        self.db.commit()

    def unclassified(self, limit=12):
        """Live memories the background worker has not typed yet."""
        return self.db.execute(
            "SELECT id, container, content FROM memories"
            " WHERE mtype IS NULL AND forgotten_at IS NULL AND is_latest=1"
            " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def undreamed(self, limit=12):
        """Live memories not yet checked for extends relations."""
        return self.db.execute(
            "SELECT * FROM memories WHERE dreamed_at IS NULL"
            " AND forgotten_at IS NULL AND is_latest=1 AND vec IS NOT NULL"
            " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def mark_dreamed(self, mem_ids):
        now = time.time()
        self.db.executemany("UPDATE memories SET dreamed_at=? WHERE id=?",
                            [(now, m) for m in mem_ids])
        self.db.commit()

    def add_edge(self, src, dst, kind, score=None, conf=None):
        self.db.execute(
            "INSERT OR REPLACE INTO memory_edges (src,dst,kind,score,created_at,conf)"
            " VALUES (?,?,?,?,?,?)", (src, dst, kind, score, time.time(), conf))
        self.db.commit()

    def delete_edges(self, a, b):
        """Remove every judged edge between two memories, either direction."""
        n = self.db.execute(
            "DELETE FROM memory_edges WHERE (src=? AND dst=?) OR (src=? AND dst=?)",
            (a, b, b, a)).rowcount
        self.db.commit()
        return n

    def edges(self, container, kind=None):
        q = ("SELECT e.* FROM memory_edges e JOIN memories m ON m.id=e.src"
             " WHERE m.container=?")
        args = [container]
        if kind:
            q += " AND e.kind=?"
            args.append(kind)
        return self.db.execute(q, args).fetchall()

    def relations(self, mem_id):
        """Every relation touching one memory, both directions, with text."""
        rows = self.db.execute(
            "SELECT e.kind, e.score, e.conf, e.src, e.dst,"
            "       CASE WHEN e.src=? THEN 'out' ELSE 'in' END AS dir,"
            "       o.id AS other_id, o.content AS other_content,"
            "       o.is_latest AS other_latest"
            "  FROM memory_edges e"
            "  JOIN memories o ON o.id = CASE WHEN e.src=? THEN e.dst ELSE e.src END"
            " WHERE e.src=? OR e.dst=?",
            (mem_id, mem_id, mem_id, mem_id)).fetchall()
        return rows

    def enrichment_counts(self, type_threshold=0.8):
        g = lambda q: self.db.execute(q).fetchone()[0]
        live = "forgotten_at IS NULL AND is_latest=1"
        return {
            "live": g(f"SELECT COUNT(*) FROM memories WHERE {live}"),
            "unclassified": g(f"SELECT COUNT(*) FROM memories WHERE {live} AND mtype IS NULL"),
            "undreamed": g(f"SELECT COUNT(*) FROM memories WHERE {live} AND dreamed_at IS NULL"),
            "extends": g("SELECT COUNT(*) FROM memory_edges WHERE kind='extends'"),
            "duplicates": g("SELECT COUNT(*) FROM memory_edges WHERE kind='duplicate'"),
            "uncertain": g(f"SELECT COUNT(*) FROM memories WHERE {live}"
                           f" AND mtype_conf < {float(type_threshold)}"),
        }

    def type_counts(self, container):
        return {r[0] or "unclassified": r[1] for r in self.db.execute(
            "SELECT mtype, COUNT(*) FROM memories WHERE container=?"
            " AND is_latest=1 AND forgotten_at IS NULL GROUP BY mtype",
            (container,))}

    def doc_vectors(self, container):
        """-> (doc_rows, (n, dim) float32) mean chunk vector per document."""
        docs = self.db.execute(
            "SELECT id, title FROM documents WHERE container=?", (container,)).fetchall()
        out_rows, vecs = [], []
        for d in docs:
            ch = [np.frombuffer(r["vec"], dtype=np.float32) for r in self.db.execute(
                "SELECT vec FROM chunks WHERE doc_id=? AND vec IS NOT NULL", (d["id"],))]
            if not ch:
                continue
            v = np.mean(np.vstack(ch), axis=0)
            n = np.linalg.norm(v)
            if n > 0:
                out_rows.append(d)
                vecs.append(v / n)
        if not vecs:
            return [], np.zeros((0, 1), dtype=np.float32)
        return out_rows, np.vstack(vecs).astype(np.float32)

