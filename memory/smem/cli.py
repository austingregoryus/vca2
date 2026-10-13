# SPDX-License-Identifier: MIT
"""smem command line.

    python3 -m smem health
    python3 -m smem ingest  <path|dir> [-c container] [--no-extract]
    python3 -m smem search  "query"    [-c container] [-k 8]
    python3 -m smem ask     "question" [-c container]
    python3 -m smem remember "a fact"  [-c container]
    python3 -m smem list    [-c container] [--history]
    python3 -m smem forget  "query"    [-c container] [--yes]
    python3 -m smem stats   [-c container]
    python3 -m smem compress "query"   [-c container]
    python3 -m smem reembed            (after an embedder change; smem-api stopped)
"""

import argparse
import os
import sys
import time
from pathlib import Path

from .engine import Engine
from .models import Models
from .pool import Pool

DEFAULT_DB = os.environ.get("SMEM_DB", "/root/supermem/smem.db")
TEXT_EXT = {".md", ".txt", ".rst", ".py", ".sh", ".c", ".h", ".conf", ".json"}


def _engine(a):
    return Engine(a.db, models=Models(pool=Pool()), container=a.container)


def _files(path):
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*")
                  if f.is_file() and f.suffix.lower() in TEXT_EXT
                  and ".git" not in f.parts)


def cmd_health(a):
    for node, st in Pool().health().items():
        print(f"  {node}  {'  '.join(st)}")


def cmd_ingest(a):
    e = _engine(a)
    files = _files(a.path)
    if not files:
        print("no ingestable files found", file=sys.stderr)
        return 1
    root = Path(a.path)
    root = root if root.is_dir() else root.parent
    t0 = time.time()

    docs = []
    for f in files:
        rel = str(f.relative_to(root))
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as ex:
            print(f"  {rel}: unreadable ({ex})")
            continue
        if text.strip():
            docs.append({"content": text, "title": rel, "source": str(f),
                         "custom_id": rel})

    if a.sequential:
        # The original document-at-a-time path, kept for A/B comparison.
        tot_c = tot_m = 0
        for i, d in enumerate(docs, 1):
            print(f"[{i}/{len(docs)}] {d['title']}  ({len(d['content'])} chars)")
            r = e.ingest(d["content"], title=d["title"], source=d["source"],
                         custom_id=d["custom_id"], extract=not a.no_extract,
                         progress=lambda s: print(s))
            tot_c += r["chunks"]
            tot_m += r["memories"]
        el = time.time() - t0
        print(f"\n{len(docs)} docs, {tot_c} chunks, {tot_m} memories in {el:.1f}s")
    else:
        r = e.ingest_many(docs, extract=not a.no_extract,
                          progress=lambda s: print(s, flush=True))
        el = time.time() - t0
        print(f"\n{r['documents']} docs, {r['chunks']} chunks, "
              f"{r['memories']} memories in {el:.1f}s")
        print(f"  embed {r['embed_s']}s  extract {r['extract_s']}s "
              f"({r['calls']} calls)  reconcile {r['reconcile_s']}s")
    print("  node dispatch:", e.models.pool.calls)
    e.close()
    return 0


def cmd_search(a):
    e = _engine(a)
    hits = e.search(a.query, k=a.k, half_life_days=a.half_life)
    if not hits:
        print("no hits")
        return 0
    print(f"  vector {hits[0]['vector_ms']}ms   rerank {hits[0]['rerank_ms']}ms\n")
    for i, h in enumerate(hits, 1):
        d = e.store.get_document(h["doc_id"]) if h["doc_id"] else None
        src = (d["title"] or "") if d else ""
        print(f"[{i}] {h['kind']:7s} score={h['score']:.4f} "
              f"(vec={h['vector_score']:.3f} rrk={h['rerank_score']:.4f} "
              f"decay={h['decay']})  {src}")
        body = h["text"].replace("\n", " ")
        print(f"    {body[:220]}{'...' if len(body) > 220 else ''}\n")
    e.close()
    return 0


def cmd_ask(a):
    e = _engine(a)
    r = e.ask(a.query, k=a.k, half_life_days=a.half_life)
    print(r["answer"])
    print(f"\n--- {len(r['hits'])} sources, {r['context_tokens']} context tokens"
          f"  (retrieve {r.get('retrieve_s', 0)}s, generate {r.get('generate_s', 0)}s)")
    for i, h in enumerate(r["hits"], 1):
        d = e.store.get_document(h["doc_id"]) if h["doc_id"] else None
        print(f"  [{i}] {h['kind']:7s} {(d['title'] if d else '')}"
              f"   {h['text'][:90].replace(chr(10), ' ')}")
    e.close()
    return 0


def cmd_remember(a):
    e = _engine(a)
    n = e.remember(a.fact)
    print(f"stored {n} memory" if n else "duplicate of an existing memory")
    e.close()
    return 0


def cmd_list(a):
    e = _engine(a)
    rows = e.store.memories(a.container, include_old=a.history,
                            include_forgotten=a.history)
    for r in rows:
        flag = ("OLD " if not r["is_latest"] else "    ")
        if r["forgotten_at"]:
            flag = "GONE"
        ts = time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))
        print(f"{flag} v{r['version']} {ts} {r['id']}  {r['content']}")
    print(f"\n{len(rows)} memories")
    e.close()
    return 0


def cmd_forget(a):
    e = _engine(a)
    hits = e.forget_matching(a.query, dry_run=True)
    if not hits:
        print("nothing matches")
        return 0
    print(f"{len(hits)} memories match:")
    for m in hits:
        print(f"  {m['score']:.3f}  {m['content']}")
    if not a.yes:
        print("\n(dry run -- pass --yes to actually forget these)")
        return 0
    e.forget_matching(a.query, dry_run=False)
    print(f"\nforgot {len(hits)}")
    e.close()
    return 0


def cmd_stats(a):
    e = _engine(a)
    cs = [a.container] if a.container != "*" else e.store.containers()
    for c in cs:
        s = e.store.stats(c)
        print(f"{c}:")
        for k, v in s.items():
            print(f"    {k:22s} {v}")
    e.close()
    return 0


def cmd_compress(a):
    e = _engine(a)
    r = e.measure_compression(a.query, k=a.k)
    print(f"  corpus  {r['corpus_tokens']:>8,} tokens")
    print(f"  context {r['context_tokens']:>8,} tokens  ({r['hits']} hits)")
    print(f"  reduction {r['reduction_pct']}%")
    e.close()
    return 0


def cmd_reembed(a):
    """Re-embed every stored vector with the current embedder, after it changes.

    Every container, every chunk and every memory version -- forgotten and
    superseded ones too, since restoring one needs a vector of the right size.
    Run with smem-api stopped: one stale-size row makes a search skip its
    whole table (Engine.search checks the dimension), so all rows are written
    in one transaction and then checked."""
    import numpy as np
    from .models import DIM
    from .pool import BG_NODES, NODES
    from .store import Store

    live = [n for n, st in Pool(nodes=NODES + BG_NODES).health().items() if "emb" in st]
    if not live:
        print("no embedder answering", file=sys.stderr)
        return 1
    m = Models(pool=Pool(nodes=live), workers=2 * len(live))
    st = Store(a.db)
    ch = st.db.execute("SELECT id, heading, text FROM chunks WHERE vec IS NOT NULL").fetchall()
    me = st.db.execute("SELECT id, content FROM memories WHERE vec IS NOT NULL").fetchall()
    print(f"re-embedding {len(ch)} chunks + {len(me)} memories on {len(live)} nodes: {live}")
    t0 = time.time()
    vc = m.embed([r["text"] for r in ch], titles=[r["heading"] for r in ch])
    vm = m.embed([r["content"] for r in me])
    t = time.time() - t0
    if vc.shape[1:] != (DIM,) or (len(me) and vm.shape[1:] != (DIM,)):
        print(f"embedder returned {vc.shape}/{vm.shape}, expected dim {DIM}; nothing written",
              file=sys.stderr)
        return 1
    with st.db:                                    # one transaction: all or nothing
        st.db.executemany("UPDATE chunks SET vec=? WHERE id=?",
                          [(v.astype(np.float32).tobytes(), r["id"]) for r, v in zip(ch, vc)])
        st.db.executemany("UPDATE memories SET vec=? WHERE id=?",
                          [(v.astype(np.float32).tobytes(), r["id"]) for r, v in zip(me, vm)])
    bad = {tb: st.db.execute(f"SELECT COUNT(*) FROM {tb} WHERE vec IS NOT NULL AND length(vec) != ?",
                             (DIM * 4,)).fetchone()[0] for tb in ("chunks", "memories")}
    st.close()
    n = len(ch) + len(me)
    print(f"done in {t:.1f}s ({n / max(t, 1e-9):.1f} texts/s); rows with the wrong size: {bad}")
    return 1 if any(bad.values()) else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="smem")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("-c", "--container", default="default")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health").set_defaults(fn=cmd_health)

    s = sub.add_parser("ingest")
    s.add_argument("path")
    s.add_argument("--no-extract", action="store_true")
    s.add_argument("--sequential", action="store_true",
                   help="old document-at-a-time path, for comparison")
    s.set_defaults(fn=cmd_ingest)

    for name, fn in (("search", cmd_search), ("ask", cmd_ask),
                     ("compress", cmd_compress)):
        s = sub.add_parser(name)
        s.add_argument("query")
        s.add_argument("-k", type=int, default=8)
        s.add_argument("--half-life", type=float, default=None,
                       help="recency half-life in days (memories only)")
        s.set_defaults(fn=fn)

    s = sub.add_parser("remember")
    s.add_argument("fact")
    s.set_defaults(fn=cmd_remember)

    s = sub.add_parser("list")
    s.add_argument("--history", action="store_true",
                   help="include superseded and forgotten rows")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("forget")
    s.add_argument("query")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_forget)

    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)

    sub.add_parser("reembed", help="re-embed every vector after an embedder change "
                                   "(stop smem-api first)").set_defaults(fn=cmd_reembed)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
