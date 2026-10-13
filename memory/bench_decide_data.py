# SPDX-License-Identifier: MIT
"""Dump unlabelled samples for the decision bake-off (see DECIDE.md).

    python3 bench_decide_data.py /root/supermem/smem.db > decide_raw.json

Types: 80 random live memories, printed WITHOUT their current mtype so the
hand labels are not anchored on the model's answer.
Pairs: 50 (newer, older) candidates with cosine >= 0.80 -- the same filter the
extends judge sees -- half already linked, half not, so both kinds of mistake
are represented.
"""
import json
import random
import sqlite3
import sys

import numpy as np

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
random.seed(20260923)

live = db.execute("SELECT id, content, created_at, vec FROM memories WHERE"
                  " container='vca2-docs' AND is_latest=1 AND forgotten_at IS NULL"
                  " ORDER BY id").fetchall()
types = [{"id": r["id"], "text": r["content"]} for r in random.sample(live, 80)]

ids = [r["id"] for r in live]
mat = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in live])
mat /= np.linalg.norm(mat, axis=1, keepdims=True)
S = mat @ mat.T
np.fill_diagonal(S, -1)
linked = {(r[0], r[1]) for r in db.execute(
    "SELECT src, dst FROM memory_edges WHERE kind='extends'")}
cands = []
for i in range(len(live)):
    for j in np.where(S[i] >= 0.80)[0]:
        a, b = live[i], live[int(j)]
        if (a["created_at"], a["id"]) > (b["created_at"], b["id"]):   # a newer
            cands.append((a, b, float(S[i, j])))
pos = [c for c in cands if (c[0]["id"], c[1]["id"]) in linked]
neg = [c for c in cands if (c[0]["id"], c[1]["id"]) not in linked]
pairs = [{"new": a["id"], "old": b["id"], "cos": round(s, 3),
          "A": b["content"], "B": a["content"]}
         for a, b, s in random.sample(pos, 25) + random.sample(neg, 25)]
random.shuffle(pairs)
print(json.dumps({"types": types, "pairs": pairs,
                  "pool": {"candidates": len(cands), "linked": len(pos)}}, indent=1))
