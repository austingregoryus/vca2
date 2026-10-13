# SPDX-License-Identifier: MIT
"""Re-judge past supersede decisions with the current classifier.

    python3 reconcile_repair.py [--db PATH] [-c CONTAINER] [--apply]

Temporal truth means the store keeps its history, which also means a bad
classifier leaves a durable trail of facts wrongly demoted to is_latest=0.
Improving the prompt does not retroactively fix them, and re-ingesting the
corpus costs over an hour of inference to regenerate facts that are already
correct.

So: walk every supersede link, ask the current classifier again, and restore
the ones that now come back DISTINCT. Roughly 70 short LLM calls instead of a
full re-ingest. Dry run by default.

Only DISTINCT triggers a restore. A link that is now judged DUPLICATE stays
demoted: the pair really does say the same thing and the newer row is the one
that survived, which is the outcome either way.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.engine import Engine  # noqa: E402
from smem.models import Models  # noqa: E402
from smem.pool import Pool  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/root/supermem/smem.db")
    ap.add_argument("-c", "--container", default="vca2-docs")
    ap.add_argument("--apply", action="store_true",
                    help="actually restore; otherwise dry run")
    a = ap.parse_args()

    e = Engine(a.db, models=Models(pool=Pool()), container=a.container)
    db = e.store.db

    links = db.execute(
        "SELECT m.id new_id, m.content new_c, m.version new_v,"
        "       o.id old_id, o.content old_c"
        "  FROM memories m JOIN memories o ON m.supersedes = o.id"
        " WHERE m.container = ? ORDER BY m.created_at", (a.container,)).fetchall()
    print(f"{len(links)} supersede links in {a.container}\n")

    counts = {"DISTINCT": 0, "DUPLICATE": 0, "UPDATE": 0}
    restore = []
    for r in links:
        v = e._reconcile(r["old_c"], r["new_c"])
        counts[v] = counts.get(v, 0) + 1
        if v == "DISTINCT":
            restore.append(r)
            print(f"  RESTORE  {r['old_c'][:92]}")
            print(f"    (was superseded by) {r['new_c'][:78]}")

    print(f"\n  re-judged: " +
          "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"  {len(restore)} facts to restore")

    if not a.apply:
        print("\n  dry run -- pass --apply to write")
        e.close()
        return 0

    n = 0
    for r in restore:
        # Do not resurrect a row that something ELSE still supersedes; that
        # would put two versions of the same claim in the live set.
        others = db.execute(
            "SELECT COUNT(*) c FROM memories WHERE supersedes = ? AND id != ?",
            (r["old_id"], r["new_id"])).fetchone()["c"]
        if others:
            print(f"  skip (still superseded by another row): {r['old_c'][:60]}")
            continue
        db.execute("UPDATE memories SET is_latest = 1 WHERE id = ?"
                   "   AND forgotten_at IS NULL", (r["old_id"],))
        # The new row was only version 2 because it claimed to supersede.
        db.execute("UPDATE memories SET supersedes = NULL, version = 1"
                   " WHERE id = ?", (r["new_id"],))
        n += 1
    db.commit()

    st = e.store.stats(a.container)
    print(f"\n  restored {n}")
    print(f"  live memories now {st['memories_live']}, "
          f"still superseded {st['memories_superseded']}")
    e.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
