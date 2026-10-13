# SPDX-License-Identifier: MIT
"""Offline tests -- chunker and store only, no model servers needed.

    python3 test_smem.py

The model-dependent behaviour (extraction, reconciliation, retrieval quality)
is measured by eval.py instead, because it is a measurement rather than an
assertion: it has a score, not a pass/fail.
"""

import os
import sqlite3
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.chunk import MAX_CHARS, chunk  # noqa: E402
from smem import calibrate  # noqa: E402
from smem.store import Store  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILED.append(name)


def rand_vec(seed):
    r = np.random.default_rng(seed)
    v = r.standard_normal(8).astype(np.float32)
    return v / np.linalg.norm(v)


# ------------------------------------------------------------------ chunker

DOC = """# Top

Intro paragraph about the card.

## Fabric

The PEX 8749 is a 1:3 mux with an x16 uplink.

It cascades into a PEX 8717.

### Node 2

Node 2 sits behind the second switch, which is why its reads are slower.

## Thermal

Package power tops out around 45 W per node.
"""


def test_chunker():
    print("chunker")
    cs = chunk(DOC, title="ARCH.md")
    check("produces chunks", len(cs) > 0, f"got {len(cs)}")
    heads = [c["heading"] for c in cs]
    check("title is prefixed onto headings",
          all(h and h.startswith("ARCH.md") for h in heads), str(heads))
    check("heading path nests",
          any("Fabric > Node 2" in (h or "") for h in heads), str(heads))
    check("embed_text carries the heading, stored text does not",
          all((c["heading"] or "") not in c["text"] for c in cs)
          and all((c["heading"] or "") in c["embed_text"] for c in cs))
    check("no chunk exceeds the cap",
          all(len(c["text"]) <= MAX_CHARS * 2 for c in cs),
          str([len(c["text"]) for c in cs]))

    # A single paragraph far over the cap must still be split.
    big = "# H\n\n" + ("Sentence number one is here. " * 400)
    cs2 = chunk(big, title="B.md")
    check("oversized paragraph is split, and nothing exceeds the cap",
          len(cs2) > 1 and all(len(c["text"]) <= MAX_CHARS for c in cs2),
          f"{len(cs2)} chunks, max {max(len(c['text']) for c in cs2)}")

    check("empty input does not explode", chunk("", title="E.md") == [])
    check("headingless input still chunks", len(chunk("Just prose.")) == 1)


# -------------------------------------------------------------------- store

def test_store():
    print("store")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    s = Store(path)

    d1 = s.add_document("a", "hello world", title="t1", custom_id="f1")
    s.add_chunks(d1, "a", [{"heading": "h", "text": "hello world"}],
                 [rand_vec(1)])
    check("document stored", s.get_document(d1) is not None)
    check("chunk stored", len(s.matrix("a", "chunks")[0]) == 1)

    # Re-ingesting the same custom_id must replace, not duplicate.
    d2 = s.add_document("a", "hello again", title="t1", custom_id="f1")
    s.add_chunks(d2, "a", [{"heading": "h", "text": "hello again"}],
                 [rand_vec(2)])
    check("re-ingest replaces by custom_id", len(s.documents("a")) == 1)
    check("old chunks cascade away", len(s.matrix("a", "chunks")[0]) == 1)

    # Container isolation.
    s.add_document("b", "other tenant", custom_id="f1")
    check("same custom_id allowed in another container",
          len(s.documents("b")) == 1 and len(s.documents("a")) == 1)

    # Temporal truth.
    m1 = s.add_memory("a", "PL1 is 45 W", rand_vec(3))
    m2 = s.add_memory("a", "PL1 is 56 W", rand_vec(4), supersedes=m1)
    live = [r["id"] for r in s.memories("a")]
    check("superseded memory leaves the live set", m1 not in live and m2 in live)
    check("version increments", s.memory(m2)["version"] == 2)
    check("old row is retained, not deleted", s.memory(m1) is not None)
    check("history walks the chain", [r["id"] for r in s.history(m2)] == [m2, m1])

    # Forgetting is soft and reversible.
    m3 = s.add_memory("a", "the fan is loud", rand_vec(5))
    s.forget(m3)
    check("forgotten memory leaves the live set",
          m3 not in [r["id"] for r in s.memories("a")])
    check("forgotten memory leaves the search matrix",
          m3 not in [r["id"] for r in s.matrix("a", "memories")[0]])
    check("forgotten row survives", s.memory(m3) is not None)
    s.unforget(m3)
    check("unforget restores it", m3 in [r["id"] for r in s.memories("a")])

    # Vectors round-trip exactly.
    rows, mat = s.matrix("a", "memories")
    ix = [r["id"] for r in rows].index(m2)
    check("float32 vector round-trips bit-exact",
          np.array_equal(mat[ix], rand_vec(4)))

    st = s.stats("a")
    check("stats count the live set",
          st["memories_live"] == 2 and st["memories_superseded"] == 1,
          str(st))

    n = s.purge("a")
    check("purge removes the container", n == 1 and s.documents("a") == [])
    check("purge does not touch other containers", len(s.documents("b")) == 1)

    s.close()
    os.unlink(path)


# -------------------------------------------------------------- calibration

def test_calibration():
    print("calibration")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    a = s.add_memory("c", "PL1 is 45 W", rand_vec(1))
    b = s.add_memory("c", "PL1 changes at runtime", rand_vec(2))

    check("temperature 1 is the identity", calibrate.apply([0.7, 0.2, 0.1], 1.0) == [0.7, 0.2, 0.1])
    soft = calibrate.apply([0.9, 0.05, 0.05], 2.0)
    check("T > 1 softens", 0.5 < soft[0] < 0.9 and abs(sum(soft) - 1) < 1e-9, str(soft))
    check("T never changes the winner", max(range(3), key=soft.__getitem__) == 0)

    s.set_mtype(a, "fact", 0.93)
    check("type confidence is stored",
          s.db.execute("SELECT mtype_conf FROM memories WHERE id=?", (a,)).fetchone()[0] == 0.93)
    s.add_edge(b, a, "extends", 0.84, conf=0.97)
    rel = s.relations(a)
    check("edge confidence is returned with relations", rel and rel[0]["conf"] == 0.97)
    check("delete_edges removes both directions", s.delete_edges(a, b) == 1 and not s.relations(a))

    opts = ["FACT", "PREFERENCE", "EPISODE"]
    s.log_decisions([("type", a, "", opts, [0.6, 0.3, 0.1], "FACT")])
    s.log_decisions([("type", a, "", opts, [0.2, 0.7, 0.1], "PREFERENCE")])   # later re-judge
    s.add_label("type", a, "", "PREFERENCE")
    got = s.labelled_decisions("type")
    check("a label pairs with the LATEST decision", got == [(opts, [0.2, 0.7, 0.1], "PREFERENCE")], str(got))
    check("too few labels -> no fit, defaults stay", calibrate.fit("type", got) is None
          and calibrate.current(s, "type") == (1.0, 0.80, False))

    # An over-confident judge: always 0.99 sure, right 70% of the time.
    ex = [(opts, [0.99, 0.005, 0.005], "FACT" if i % 10 < 7 else "EPISODE") for i in range(40)]
    r = calibrate.fit("type", ex)
    check("over-confidence is fitted as T > 1", r and r["temperature"] > 1.5, str(r))
    check("log loss improves", r["stats"]["logLossAfter"] < r["stats"]["logLossBefore"], str(r["stats"]))

    # extends: probabilities that separate true links cleanly
    eo = ["EXTENDS", "DUPLICATE", "UPDATE", "UNRELATED"]
    ex = ([(eo, [0.97, 0.01, 0.01, 0.01], "EXTENDS")] * 8 +
          [(eo, [0.75, 0.05, 0.05, 0.15], "UNRELATED")] * 6 +
          [(eo, [0.05, 0.05, 0.05, 0.85], "UNRELATED")] * 10)
    r = calibrate.fit("extends", ex)
    check("extends threshold lands above the false links",
          r and r["stats"]["precisionAtThreshold"] >= calibrate.TARGET_PRECISION
          and r["stats"]["recallAtThreshold"] == 1.0, str(r))
    s.set_calibration("extends", r["temperature"], r["threshold"], r["n"], r["stats"])
    t, thr, fitted = calibrate.current(s, "extends")
    check("a stored fit is what current() returns", fitted and thr == r["threshold"])

    # reapply: past decisions follow the current calibration; labels win.
    c2 = s.add_memory("c", "PL1 reverts on reset", rand_vec(3))
    s.log_decisions([("extends", c2, a, eo, [0.96, 0.02, 0.01, 0.01], "EXTENDS")])
    out = calibrate.reapply(s)
    kinds = [x["kind"] for x in s.relations(c2)]
    check("reapply adds the link a past decision now clears", kinds == ["extends"], f"{out} {kinds}")
    s.add_label("extends", c2, a, "UNRELATED")
    calibrate.reapply(s)
    check("a label overrides the judge", s.relations(c2) == [])
    s.add_label("type", a, "", "EPISODE")
    calibrate.reapply(s)
    row = s.db.execute("SELECT mtype, mtype_conf FROM memories WHERE id=?", (a,)).fetchone()
    check("a type label sets the type at full confidence", tuple(row) == ("episode", 1.0), str(tuple(row)))

    # A decision about a memory that was later deleted must not break reapply
    # (FK failure) or leave the write lock held (2026-10-04 "database is locked").
    gone = s.add_memory("c", "PL1 note that gets deleted", rand_vec(4))
    s.log_decisions([("extends", gone, a, eo, [0.96, 0.02, 0.01, 0.01], "EXTENDS"),
                     ("type", gone, "", opts, [0.9, 0.05, 0.05], "FACT")])
    s.db.execute("DELETE FROM memories WHERE id=?", (gone,))
    s.db.commit()
    try:
        calibrate.reapply(s)
        ok = True
    except Exception as e:
        ok = str(e)
    check("reapply skips decisions about deleted memories", ok is True, str(ok))
    check("reapply leaves no transaction open", not s.db.in_transaction)
    other = sqlite3.connect(path, timeout=1)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        free = True
    except sqlite3.OperationalError as e:
        free = str(e)
    other.close()
    check("another connection can write after reapply", free is True, str(free))
    s.close()
    os.unlink(path)


if __name__ == "__main__":
    test_chunker()
    test_store()
    test_calibration()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
        sys.exit(1)
    print("all passed")
