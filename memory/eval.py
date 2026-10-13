# SPDX-License-Identifier: MIT
"""Measure whether any of this actually works.

    python3 eval.py [--db PATH] [-c CONTAINER] [--k 8]

Four questions, in order of how much they matter:

  1. Does retrieval find the answer at all?           Recall@k
  2. Do the extracted memories earn their cost, or    memories+chunks
     would plain chunk retrieval have been enough?    vs chunks-only
  3. Does the cross-encoder rerank stage help?        rerank on/off
  4. How much context does an answer actually cost?   compression

GRADING: a hit counts as relevant if it contains any of the question's
expected terms, case-insensitively. That is a keyword proxy for relevance, not
a human judgement -- it can call a passage relevant because it happens to
mention "4423" in an unrelated sentence. It is used because it is
reproducible and because grading retrieval with the same model family that
did the retrieving would be circular. Treat the absolute numbers as
directional and the A/B comparisons between configurations -- which share the
grader and therefore share its bias -- as the real result.

The expected terms are all verified to occur in the corpus.
"""

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.engine import Engine  # noqa: E402
from smem.models import Models  # noqa: E402
from smem.pool import Pool  # noqa: E402

QUESTIONS = [
    ("What is the fastest node-to-node DMA throughput measured?", ["4423"]),
    ("Why are node 2's PCIe reads slower than its writes?",
     ["non-posted", "posted"]),
    ("What causes the error 'Unknown rela relocation: 4' when loading a module?",
     ["PLT32", "binutils"]),
    ("Which package has to be installed to build kernel modules with ORC unwinding?",
     ["libelf"]),
    ("What MTU do the jumbo frames use on the virtio links?", ["65521"]),
    ("How do you recover a node whose sshd has died?", ["pwrbtn"]),
    ("Which PCIe switch provides the x16 uplink to the host?", ["8749"]),
    ("Which switch is node 2 connected behind?", ["8717"]),
    ("What GPU does each node have?", ["P580"]),
    ("Which host kernel versions are known to work with the card?",
     ["7.0.14-17-pve", "7.0.2-6-pve"]),
    ("What is the right way to start a long-running server over ssh?",
     ["systemd-run"]),
    ("Which ZFS feature makes the per-node image copies cheap?",
     ["block_cloning", "reflink"]),
    ("How big is the gemma-4 Q4_K_M model file?", ["4.98"]),
    ("What embedding throughput did the three nodes reach together?", ["20.18"]),
    ("How do you verify a rebuilt kernel module matches the original binary?",
     ["srcversion"]),
]


def relevant(text, terms):
    t = text.lower()
    return any(x.lower() in t for x in terms)


def run_config(e, container, k, label, **kw):
    hit, lat, ctx_toks, ranks = 0, [], [], []
    for q, terms in QUESTIONS:
        t0 = time.time()
        hits = e.search(q, container=container, k=k, **kw)
        lat.append(time.time() - t0)
        found = [i for i, h in enumerate(hits, 1) if relevant(h["text"], terms)]
        if found:
            hit += 1
            ranks.append(found[0])
        ctx_toks.append(e.models.count_tokens(e.context(hits)))
    n = len(QUESTIONS)
    print(f"  {label:26s} recall@{k}={hit}/{n} ({100*hit/n:.0f}%)  "
          f"median rank {statistics.median(ranks) if ranks else float('nan'):.0f}  "
          f"latency {statistics.median(lat):.2f}s  "
          f"ctx {statistics.median(ctx_toks):.0f} tok")
    return {"label": label, "recall": hit / n, "ranks": ranks,
            "latency": statistics.median(lat),
            "ctx": statistics.median(ctx_toks)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/supermem/smem.db")
    ap.add_argument("-c", "--container", default="vca2-docs")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--skip-answers", action="store_true")
    a = ap.parse_args()

    e = Engine(a.db, models=Models(pool=Pool()), container=a.container)
    st = e.store.stats(a.container)
    print("corpus")
    for kk, v in st.items():
        print(f"  {kk:22s} {v}")
    corpus_tok = e.models.count_tokens(
        "\n\n".join(d["content"] for d in e.store.documents(a.container)))
    print(f"  {'corpus_tokens':22s} {corpus_tok:,}")

    print("\nretrieval")
    res = [
        run_config(e, a.container, a.k, "mem+chunks, rerank 40"),
        # The rerank stage is the dominant query cost -- one forward pass per
        # candidate. If 20 candidates recall as well as 40, that halves it.
        run_config(e, a.container, a.k, "mem+chunks, rerank 20", candidates=20),
        run_config(e, a.container, a.k, "mem+chunks, no rerank", rerank=False),
        run_config(e, a.container, a.k, "chunks only, rerank",
                   include_memories=False),
        run_config(e, a.container, a.k, "memories only, rerank",
                   include_chunks=False),
        # Supermemory's published operating point is Recall@15 at ~720 tokens.
        # Memories are ~30 tokens each, so 15 of them land in the same range
        # and this row is the closest like-for-like comparison available.
        run_config(e, a.container, 15, "memories only, k=15",
                   include_chunks=False),
    ]

    best = max(res, key=lambda r: (r["recall"], -r["ctx"]))
    print(f"\n  best: {best['label']}")
    print(f"  compression: {corpus_tok:,} corpus tokens -> "
          f"{best['ctx']:.0f} context tokens "
          f"({100 * (1 - best['ctx'] / corpus_tok):.2f}% reduction)")

    if a.skip_answers:
        return 0

    print("\nend-to-end answers")
    ok, tot_t, tot_ctx = 0, [], []
    for q, terms in QUESTIONS:
        r = e.ask(q, container=a.container, k=a.k)
        good = relevant(r["answer"], terms)
        ok += good
        tot_t.append(r["total_s"])
        tot_ctx.append(r["context_tokens"])
        mark = "ok  " if good else "MISS"
        print(f"  {mark} {r['total_s']:5.1f}s {r['context_tokens']:5d}tok  {q}")
        print(f"       {r['answer'][:160].replace(chr(10), ' ')}")
    n = len(QUESTIONS)
    print(f"\n  answers containing the expected fact: {ok}/{n} ({100*ok/n:.0f}%)")
    print(f"  median latency {statistics.median(tot_t):.1f}s, "
          f"median context {statistics.median(tot_ctx):.0f} tokens")
    print(f"  node dispatch: {e.models.pool.calls}")
    e.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
