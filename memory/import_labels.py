# SPDX-License-Identifier: MIT
"""Seed calibration with the bake-off's hand labels (decide_labels.json).

    python3 import_labels.py /root/supermem/smem.db decide_labels.json

Items are matched to live memories by exact text. Only unambiguous items are
imported (one acceptable answer): an ambiguous label would teach calibration
that a defensible answer is wrong. Labels are stored with source='import', so
they can be told apart from corrections made in the console, and a later
console correction of the same item replaces them.
"""
import json
import sys

from smem.store import Store


def main(db, labels):
    s = Store(db)
    lab = json.load(open(labels, encoding="utf-8"))
    by_text = {}
    for r in s.db.execute("SELECT id, content, created_at FROM memories"
                          " WHERE is_latest=1 AND forgotten_at IS NULL"):
        by_text.setdefault(r["content"], r)
    n_t = n_p = miss = skip = 0
    for it in lab["types"]:
        if len(it["ok"]) != 1:
            skip += 1
            continue
        m = by_text.get(it["text"])
        if not m:
            miss += 1
            continue
        s.add_label("type", m["id"], "", it["gold"].upper(), "import")
        n_t += 1
    for it in lab["pairs"]:
        if len(it["ok"]) != 1:
            skip += 1
            continue
        a, b = by_text.get(it["A"]), by_text.get(it["B"])    # A older, B newer
        if not a or not b:
            miss += 1
            continue
        if (a["created_at"], a["id"]) > (b["created_at"], b["id"]):
            a, b = b, a
        s.add_label("extends", b["id"], a["id"], it["gold"], "import")
        n_p += 1
    print(f"imported {n_t} type labels, {n_p} pair labels; "
          f"{skip} ambiguous skipped, {miss} no longer in the store")


if __name__ == "__main__":
    main(*sys.argv[1:3])
