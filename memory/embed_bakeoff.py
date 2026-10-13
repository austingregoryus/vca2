# SPDX-License-Identifier: MIT
"""EmbeddingGemma 2 vs Qwen3-Embedding-0.6B on smem's own corpus. Runs on the
host: copy this file and eval.py to /root/supermem/eval-eg2/, run from
/root/supermem. Results: EMBED_BAKEOFF.md. Never touches the live DB: works on two snapshots.

Both models are served by TEST llama-servers on node 5, 172.31.6.1 (Qwen :8091, Gemma
:8090), each started with -p MemoryLimit=4G. Never point a bulk run at the
live sm-emb/sm-rrk: llama-server's embedding and rerank modes leak under
sustained load and hit their caps (see services.sh). Qwen is scored on the
vectors production already stored; only a 200-text sample is re-embedded,
for timing. The production search pipeline (rerank) is deliberately left out:
the reranker is the same for both, and the embedder's job is the top 40."""
import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, "/root/supermem")
from smem.engine import Engine           # noqa: E402
from smem.models import EMB_CHARS, Models   # noqa: E402

# Qwen3-Embedding's query instruction as production used it until 2026-10-07
# (smem.models.QUERY_INSTRUCT now holds EmbeddingGemma 2's prompt).
QWEN_QUERY = ("Instruct: Given a question, retrieve passages and remembered facts "
              "that answer it\nQuery: ")
from smem.pool import Pool               # noqa: E402

NODE = "172.31.6.1"
OUT = "/root/supermem/eval-eg2"
C = "vca2-docs"
sys.path.insert(0, OUT)                  # eval.py is copied here

# eval.py's 15, plus extra questions from the repo docs. Any whose terms do
# not occur in the corpus are dropped below (it shows how many).
from eval import QUESTIONS as BASE     # noqa: E402
EXTRA = [
    ("Which port do the ggml RPC servers listen on?", ["50052"]),
    ("What flag stops llama-cli from sitting in its chat TUI?", ["--single-turn", "single-turn"]),
    ("What decode speed did OLMoE reach on the host alone?", ["33.4"]),
    ("Why were the llama.cpp RPC workers cross-compiled with Zig?", ["2.23"]),
    ("How much faster were independent workers than one node?", ["2.96"]),
    ("What GEMM throughput did the optimised kernels reach?", ["1.36"]),
    ("What generation speed did TinyLlama reach over RPC?", ["14.5"]),
    ("What is the default root password on the nodes?", ["vista1"]),
    ("What is the per-node prefill compute budget?", ["184"]),
    ("How fast is Quick Sync H.264 encoding on a node?", ["188"]),
    ("What is the fixed per-call overhead that makes small extraction models no faster?", ["6 s", "~6"]),
    ("Which extraction model gave the cleanest facts in the bake-off?", ["gemma-4-E4B", "E4B"]),
    ("Which kernel module sets the jumbo MTU on the virtual NICs?", ["jumbo_mtu"]),
    ("What prefill rate did Llama-3.2-1B reach in the bake-off?", ["150.5"]),
    ("What GPU device nodes exist on each VCA node?", ["renderD128"]),
    ("What threads flag did the RPC servers use?", ["-t 4"]),
]


def post(port, texts):
    req = urllib.request.Request(f"http://{NODE}:{port}/v1/embeddings",
                                 data=json.dumps({"input": texts}).encode(),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                rows = sorted(json.load(r)["data"], key=lambda d: d["index"])
            break
        except OSError as e:          # the server recycles itself at its memory cap
            if attempt == 7:
                raise
            print(f"  retry {attempt + 1} on :{port}: {e}", flush=True)
            time.sleep(10)
    v = np.asarray([d["embedding"] for d in rows], dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


MODELS = {
    # Qwen: score the vectors production already stored (the real baseline);
    # only a 200-text sample is re-embedded, to time it on the same node.
    "qwen3-0.6B": dict(port=8091, reembed=False,
                       query=lambda q: QWEN_QUERY + q,
                       chunk=lambda h, t: (h + "\n\n" + t).strip() if h else t,
                       memory=lambda m: m),
    "embgemma-2": dict(port=8090, reembed=True,
                       query=lambda q: f"task: search result | query: {q}",
                       chunk=lambda h, t: f"title: {h or 'none'} | text: {t}",
                       memory=lambda m: f"title: none | text: {m}"),
}


def embed_all(port, texts, batch=16):
    out = []
    for i in range(0, len(texts), batch):
        out.append(post(port, [t[:EMB_CHARS] for t in texts[i:i + batch]]))
    return np.vstack(out)


def snapshot(dst):
    if os.path.exists(dst):
        os.remove(dst)
    src = sqlite3.connect("file:/root/supermem/smem.db?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    src.backup(d)
    src.close()
    d.close()


def reembed(db, spec):
    c = sqlite3.connect(db)
    ch = c.execute("SELECT id, heading, text FROM chunks WHERE container=?", (C,)).fetchall()
    me = c.execute("SELECT id, content FROM memories WHERE container=?", (C,)).fetchall()
    texts = [spec["chunk"](h, t) for _, h, t in ch] + [spec["memory"](m) for _, m in me]
    sample = texts[:100] + texts[len(ch):len(ch) + 100]          # 100 chunks + 100 memories
    t0 = time.time()
    v = embed_all(spec["port"], sample)
    dt = time.time() - t0
    if not spec["reembed"]:
        c.close()
        return len(sample), dt, v.shape[1]
    v = embed_all(spec["port"], texts)
    for (i, *_), vec in zip(ch, v[:len(ch)]):
        c.execute("UPDATE chunks SET vec=? WHERE id=?", (vec.tobytes(), i))
    for (i, _), vec in zip(me, v[len(ch):]):
        c.execute("UPDATE memories SET vec=? WHERE id=?", (vec.tobytes(), i))
    c.commit()
    c.close()
    return len(sample), dt, v.shape[1]


class SpecModels(Models):
    """smem's Models, but queries embedded the way this spec says, on node 5."""
    def __init__(self, spec):
        super().__init__(pool=Pool(nodes=[NODE]))
        self.spec = spec

    def embed(self, texts, is_query=False):
        if is_query:
            texts = [self.spec["query"](t) for t in texts]
        return embed_all(self.spec["port"], texts)


def relevant(text, terms):
    t = text.lower()
    return any(x.lower() in t for x in terms)


def evaluate(db, spec, questions):
    e = Engine(db, models=SpecModels(spec), container=C)
    rows_m, mat_m = e.store.matrix(C, "memories")
    rows_c, mat_c = e.store.matrix(C, "chunks")
    items = [r["content"] for r in rows_m] + [r["text"] for r in rows_c]
    kinds = ["m"] * len(rows_m) + ["c"] * len(rows_c)
    mat = np.vstack([mat_m, mat_c])
    ranks, mranks, qlat = [], [], []
    for q, terms in questions:
        t0 = time.time()
        qv = e.models.embed([q], is_query=True)[0]
        qlat.append(time.time() - t0)
        order = np.argsort(-(mat @ qv))
        rel = [k for k, j in enumerate(order, 1) if relevant(items[j], terms)]
        ranks.append(rel[0] if rel else 10 ** 6)
        mo = [j for j in order if kinds[j] == "m"]
        mrel = [k for k, j in enumerate(mo, 1) if relevant(items[j], terms)]
        mranks.append(mrel[0] if mrel else 10 ** 6)
    e.close()
    n = len(questions)
    at = lambda rs, k: sum(r <= k for r in rs) / n
    return {"recall@1": at(ranks, 1), "recall@5": at(ranks, 5), "recall@15": at(ranks, 15),
            "recall@40": at(ranks, 40), "MRR": sum(1 / r for r in ranks) / n,
            "memories_recall@15": at(mranks, 15),
            "query_embed_ms": 1000 * statistics.median(qlat), "ranks": ranks}


def main():
    os.makedirs(OUT, exist_ok=True)
    corpus = sqlite3.connect("file:/root/supermem/smem.db?mode=ro", uri=True)
    text = "\n".join(r[0] for r in corpus.execute(
        "SELECT text FROM chunks WHERE container=? UNION ALL SELECT content FROM memories WHERE container=?",
        (C, C))).lower()
    corpus.close()
    qs = list(BASE) + [q for q in EXTRA if any(t.lower() in text for t in q[1])]
    print(f"questions: {len(BASE)} from eval.py + {len(qs) - len(BASE)} of {len(EXTRA)} extra "
          f"(the rest name terms not in the corpus)", flush=True)
    res = {}
    for name, spec in MODELS.items():
        db = f"{OUT}/{name}.db"
        snapshot(db)
        n, dt, dim = reembed(db, spec)
        print(f"{name}: timed sample {n} texts in {dt:.1f}s ({n / dt:.2f} texts/s), dim {dim}"
              + (", full container re-embedded" if spec["reembed"] else ", scoring production vectors"),
              flush=True)
        r = evaluate(db, spec, qs)
        r.update(embed_s=round(dt, 1), texts_per_s=round(n / dt, 1), dim=dim)
        res[name] = r
        print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "ranks"},
              flush=True)
    print("\nper-question first-relevant rank (embedder only):")
    for (q, _), a, b in zip(qs, res["qwen3-0.6B"]["ranks"], res["embgemma-2"]["ranks"]):
        f = lambda r: "miss" if r >= 10 ** 6 else r
        print(f"  qwen {f(a)!s:>5}  gemma {f(b)!s:>5}  {q}")
    json.dump(res, open(f"{OUT}/results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
