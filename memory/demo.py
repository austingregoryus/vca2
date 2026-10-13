# SPDX-License-Identifier: MIT
"""End-to-end walkthrough of every Supermemory behaviour this implements.

    python3 demo.py [--db PATH] [--keep]

Runs against a throwaway container with a small hand-written corpus, because
the behaviours worth showing -- a fact changing over time, a belief being
forgotten, an old fact decaying -- need a story, and repo documentation does
not have one. Takes a few minutes: every step is real inference on the card.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.engine import Engine  # noqa: E402
from smem.models import Models  # noqa: E402
from smem.pool import Pool  # noqa: E402

DAY = 86400.0

NOTES = [
    ("standup-jan.md", """# Standup notes, January

Priya is leading the payments migration. She wants everything off the legacy
Stripe integration by the end of Q1.

We are running Postgres 14 in production. The read replica lags by about
4 seconds under load, which is fine for the dashboards but not for the
checkout path.

Deploys go out on Tuesdays and Thursdays at 10am. Marco owns the deploy
rotation.
"""),
    ("arch-review.md", """# Architecture review

The checkout path reads directly from the primary because the replica lag
makes it unsafe. This is the main reason the primary is at 70% CPU.

We agreed to keep the monolith and not split out a payments service. The team
is too small to run five services well.

Priya raised that the legacy Stripe integration still handles all refunds.
"""),
    ("incident-mar.md", """# Incident 2026-03-11

The primary hit 100% CPU during a marketing push and checkout timed out for
about 20 minutes.

Root cause: an unindexed query on orders.customer_id added in the previous
Thursday deploy.

Action items: add the index, and move the checkout read off the primary once
replica lag is solved.
"""),
]


def h(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def show(hits, limit=4):
    for i, x in enumerate(hits[:limit], 1):
        body = x["text"].replace("\n", " ")
        print(f"  [{i}] {x['kind']:7s} {x['score']:.4f} (decay {x['decay']})  "
              f"{body[:110]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/supermem/demo.db")
    ap.add_argument("--keep", action="store_true",
                    help="do not purge the demo containers at the end")
    a = ap.parse_args()

    e = Engine(a.db, models=Models(pool=Pool()), container="demo-acme")
    e.store.purge("demo-acme")
    e.store.purge("demo-other")

    # ---------------------------------------------------------------- ingest
    h("1. INGEST  documents -> chunks -> extracted facts")
    t0 = time.time()
    for name, body in NOTES:
        r = e.ingest(body, title=name, custom_id=name,
                     progress=lambda s: print(s))
        print(f"  {name:18s} {r['chunks']} chunks, {r['memories']} memories,"
              f" {r['total_s']}s")
    print(f"  total {time.time() - t0:.1f}s")
    print("  facts now held:")
    for m in e.store.memories("demo-acme"):
        print(f"    - {m['content']}")

    # ------------------------------------------------------------ containers
    h("2. CONTAINER TAGS  isolation is a filter on every query, not a convention")
    e.ingest("Our database is MySQL 8 and we deploy continuously.",
             title="other.md", custom_id="other.md", container="demo-other",
             extract=False)
    for c in ("demo-acme", "demo-other"):
        hits = e.search("what database do we run", container=c, k=2)
        print(f"  {c}:")
        show(hits, 2)

    # --------------------------------------------------------- temporal truth
    h("3. TEMPORAL TRUTH  a changed fact supersedes, it does not overwrite")
    print("  before:")
    for m in e.store.memories("demo-acme"):
        if "postgres" in m["content"].lower() or "replica" in m["content"].lower():
            print(f"    - {m['content']}")
    print('\n  remember: "We upgraded to Postgres 16 and replica lag is now '
          'under 200ms."')
    e.remember("We upgraded to Postgres 16 and the read replica lag is now "
               "under 200 milliseconds.")
    print("\n  after (live set):")
    for m in e.store.memories("demo-acme"):
        if "postgres" in m["content"].lower() or "replica" in m["content"].lower():
            print(f"    v{m['version']}  {m['content']}")
    print("\n  after (with history -- the old belief is still on record):")
    for m in e.store.memories("demo-acme", include_old=True):
        if not m["is_latest"]:
            print(f"    SUPERSEDED  {m['content']}")

    # ------------------------------------------------------------------ decay
    h("4. RECENCY DECAY  an old fact fades without being deleted")
    # These two must be DISTINCT, not contradictory. A stale fact and its
    # replacement would be caught by step 3 and superseded, leaving nothing to
    # decay -- decay is for facts that are all still true but not equally
    # current.
    old = e.remember_many(
        ["On-call rotation is weekly and hands over on Monday mornings."],
        event_date=time.time() - 400 * DAY)
    print(f"  planted a fact dated 400 days ago ({old} stored)")
    e.remember("On-call escalation goes to Priya if a page is unacknowledged "
               "for 15 minutes.")
    q = "how does on-call work"
    for hl in (None, 120):
        hits = e.search(q, k=3, half_life_days=hl)
        print(f"\n  half_life={hl if hl else 'off'}:")
        show(hits, 3)

    # -------------------------------------------------------------- retrieval
    h("5. ASK  retrieve, rerank, answer with citations")
    for q in ("why does checkout read from the primary database",
              "who owns the payments migration",
              "what caused the March incident"):
        r = e.ask(q, k=6)
        print(f"\n  Q: {q}")
        print(f"  A: {r['answer'][:400]}")
        print(f"     ({r['context_tokens']} context tokens, "
              f"{r['total_s']}s, {len(r['hits'])} sources)")

    # ------------------------------------------------------------- forgetting
    h("6. FORGETTING  soft delete by natural-language query")
    print('  forget "anything about the legacy Stripe integration" (dry run):')
    gone = e.forget_matching("the legacy Stripe integration", dry_run=True)
    for m in gone:
        print(f"    {m['score']:.3f}  {m['content']}")
    if gone:
        e.forget_matching("the legacy Stripe integration", dry_run=False)
        print(f"\n  forgot {len(gone)}. Same query again:")
        show(e.search("legacy Stripe integration", k=3), 3)
        print("\n  the rows still exist, flagged, and can be restored:")
        for m in e.store.memories("demo-acme", include_old=True,
                                  include_forgotten=True):
            if m["forgotten_at"]:
                print(f"    FORGOTTEN  {m['content'][:90]}")

    # ------------------------------------------------------------ compression
    h("7. COMPRESSION  what an answer actually costs")
    r = e.measure_compression("why does checkout read from the primary", k=6)
    print(f"  whole corpus     {r['corpus_tokens']:>7,} tokens")
    print(f"  context for one  {r['context_tokens']:>7,} tokens "
          f"({r['hits']} hits)")
    print(f"  reduction        {r['reduction_pct']:>7}%")
    if r["reduction_pct"] <= 0:
        # Not a bug, and worth seeing: retrieving six passages out of three
        # short notes costs more than the three notes. A memory layer is
        # pointless on a corpus that already fits in the context window --
        # its entire value is that it does not grow with the corpus. The same
        # measurement over this repo's docs/ (105,729 tokens) is 98.8%.
        print("\n  Negative, and correctly so: this demo corpus is three short")
        print("  notes. Six retrieved passages cost more than simply pasting")
        print("  all of it. Compression only pays once the corpus stops")
        print("  fitting in the window -- over docs/ (105,729 tokens) the")
        print("  same measurement reads 98.8%. See RESULTS.md.")

    print(f"\n  node dispatch: {e.models.pool.calls}")
    if not a.keep:
        e.store.purge("demo-acme")
        e.store.purge("demo-other")
        print("  demo containers purged (--keep to retain)")
    e.close()


if __name__ == "__main__":
    main()
