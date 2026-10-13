# SPDX-License-Identifier: MIT
"""Decision bake-off: how should smem type memories and judge extends links?

Compares, on hand-labelled data (decide_labels.json, see DECIDE.md):

  grammar  production: the model writes one word, a GBNF grammar allows only
           the class names. No confidence.
  letters1 the same, one pass only (forward order): what the shuffle costs.
  letters  Reflex-style: the same definitions, options offered as letters,
           ONE token generated, and the probability of each letter read from
           the logprobs. Asked twice with the options in opposite orders and
           averaged, which cancels the model's pull toward the first option.
           Gives a confidence for every decision.
  rerank   the Qwen3-Reranker-0.6B already loaded on every node, used as a
           yes/no judge: the instruction rides in the query, the score is its
           P(yes).

Every call is sequential against ONE node, so latency is per decision with no
queueing. Times are wall clock and llama-server's own prompt timings.

    python3 bench_decide.py --node 172.31.3.1 --llm-port 8082 --name gemma4 \
        --tasks types,pairs --methods grammar,letters,rerank > out.json
"""
import argparse
import http.client
import json
import os
import math
import sys
import time
import urllib.request

sys.path.insert(0, "/root/supermem")
from smem.engine import (EXTENDS_GRAMMAR, EXTENDS_SYSTEM,  # noqa: E402
                         TYPE_GRAMMAR, TYPE_SYSTEM)

TYPES = ["FACT", "PREFERENCE", "EPISODE"]
RELS = ["EXTENDS", "DUPLICATE", "UPDATE", "UNRELATED"]


RESTARTS = []   # (url, time) of every server that dropped a request


def post(url, body, timeout=600):
    """POST with recovery. The reranker's leak gets it OOM-killed at its
    cgroup cap (see services.sh) and systemd restarts it; wait for /health and
    retry, and record the event so the run's timings can be read honestly."""
    base = "/".join(url.split("/")[:3])          # http://host:port
    for _ in range(4):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
            return j, time.time() - t
        except (OSError, http.client.HTTPException) as e:
            RESTARTS.append((url, time.time(), str(e)[:80]))
            print(f"  !! {url}: {e}; waiting for the server", file=sys.stderr)
            for _ in range(90):
                time.sleep(2)
                try:
                    with urllib.request.urlopen(base + "/health", timeout=3) as r:
                        if b"ok" in r.read():
                            break
                except OSError:
                    pass
    raise RuntimeError(f"{url} failed 4 times")


class Chat:
    def __init__(self, node, port):
        self.url = f"http://{node}:{port}/v1/chat/completions"

    def __call__(self, system, user, max_tokens=6, grammar=None, logprobs=False):
        body = {"messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": max_tokens, "temperature": 0.0,
                "chat_template_kwargs": {"enable_thinking": False}}
        if grammar:
            body["grammar"] = grammar
        if logprobs:
            body.update(logprobs=True, top_logprobs=20)
        j, wall = post(self.url, body)
        c = j["choices"][0]
        tops = {}
        if logprobs and c.get("logprobs"):
            for t in c["logprobs"]["content"][0]["top_logprobs"]:
                k = t["token"].strip()
                tops[k] = max(tops.get(k, -99.0), t["logprob"])
        tm = j.get("timings", {})
        return {"text": (c["message"]["content"] or "").strip(), "tops": tops,
                "wall": wall, "prompt_n": tm.get("prompt_n"),
                "cache_n": tm.get("cache_n"), "prompt_ms": tm.get("prompt_ms")}


def lettered(system, user, names, order):
    """-> (system, user). The system prompt is the production definitions with
    the closing instruction removed, and it is IDENTICAL for every call and
    every option order: llama-server keeps it in the KV cache, so each call
    only pays for the statement and the options (see DECIDE.md, first run)."""
    head = system.rsplit("Answer with exactly one word.", 1)[0].rstrip()
    opts = "\n".join(f"{chr(65 + i)}) {names[k]}" for i, k in enumerate(order))
    return head, f"{user}\n\nOptions:\n{opts}\n\nAnswer with the letter only."


def letter_probs(chat, system, user, names, passes=2):
    """Options forward, then (passes=2) reversed; -> (probs, calls)."""
    n = len(names)
    orders = [list(range(n)), list(range(n))[::-1]][:passes]
    acc, calls = [0.0] * n, []
    for order in orders:
        sy, us = lettered(system, user, names, order)
        r = chat(sy, us, max_tokens=1, logprobs=True)
        p = [math.exp(r["tops"].get(chr(65 + i), -30.0)) for i in range(n)]
        z = sum(p) or 1.0
        for i, k in enumerate(order):
            acc[k] += p[i] / z / len(orders)
        calls.append(r)
    return acc, calls


class Rerank:
    def __init__(self, node, port=8081):
        self.url = f"http://{node}:{port}/rerank"

    def __call__(self, query, docs):
        j, wall = post(self.url, {"query": query, "documents": docs})
        s = [0.0] * len(docs)
        for r in j["results"]:
            s[r["index"]] = r["relevance_score"]
        return s, wall, j.get("usage", {}).get("prompt_tokens")


TYPE_DESCS = [
    "A durable fact: what something is, has, does, costs or measured.",
    "A preference, rule, recommendation or decision: what someone wants, or how things should be done.",
    "An event: something that happened at a particular time, or a temporary state.",
]
EXT_Q = ("Does statement B add a new detail about the SAME specific subject as "
         "statement A, without merely restating it? A: {a}")


def run_types(items, method, chat=None, rr=None):
    out = []
    for it in items:
        rec = {"text": it["text"][:80], "gold": it["gold"], "ok": it["ok"], "src": it["src"]}
        if method == "grammar":
            r = chat(TYPE_SYSTEM, it["text"], max_tokens=6, grammar=TYPE_GRAMMAR)
            rec.update(pred=r["text"].lower(), conf=None, wall=r["wall"],
                       prompt_n=r["prompt_n"], cache_n=r["cache_n"])
        elif method in ("letters", "letters1"):
            p, calls = letter_probs(chat, TYPE_SYSTEM, f"Statement: {it['text']}", TYPES,
                                    passes=1 if method == "letters1" else 2)
            k = max(range(3), key=p.__getitem__)
            rec.update(pred=TYPES[k].lower(), conf=p[k],
                       probs={TYPES[i].lower(): round(p[i], 4) for i in range(3)},
                       wall=sum(c["wall"] for c in calls),
                       prompt_n=sum(c["prompt_n"] or 0 for c in calls),
                       cache_n=sum(c["cache_n"] or 0 for c in calls))
        elif method == "rerank":
            s, wall, ntok = rr(f"Which description fits this statement? {it['text']}", TYPE_DESCS)
            z = sum(s) or 1.0
            k = max(range(3), key=s.__getitem__)
            rec.update(pred=TYPES[k].lower(), conf=s[k] / z,
                       probs={TYPES[i].lower(): round(s[i], 4) for i in range(3)},
                       wall=wall, prompt_n=ntok, cache_n=None)
        out.append(rec)
        print(f"  {method:8s} {rec['pred']:10s} gold={it['gold']:10s} {rec['wall']:.2f}s",
              file=sys.stderr)
    return out


def run_pairs(items, method, chat=None, rr=None):
    out = []
    for it in items:
        rec = {"A": it["A"][:70], "B": it["B"][:70], "gold": it["gold"], "ok": it["ok"],
               "link": it["link"], "cos": it["cos"]}
        user = f"A: {it['A']}\nB: {it['B']}"
        if method == "grammar":
            r = chat(EXTENDS_SYSTEM, user, max_tokens=6, grammar=EXTENDS_GRAMMAR)
            v = r["text"].upper()
            rec.update(pred=v, p_ext=None, wall=r["wall"],
                       prompt_n=r["prompt_n"], cache_n=r["cache_n"])
        elif method in ("letters", "letters1"):
            p, calls = letter_probs(chat, EXTENDS_SYSTEM, user, RELS,
                                    passes=1 if method == "letters1" else 2)
            k = max(range(4), key=p.__getitem__)
            rec.update(pred=RELS[k], p_ext=p[0],
                       probs={RELS[i]: round(p[i], 4) for i in range(4)},
                       wall=sum(c["wall"] for c in calls),
                       prompt_n=sum(c["prompt_n"] or 0 for c in calls),
                       cache_n=sum(c["cache_n"] or 0 for c in calls))
        elif method == "rerank":
            s, wall, ntok = rr(EXT_Q.format(a=it["A"]), [f"B: {it['B']}"])
            rec.update(pred="EXTENDS" if s[0] >= 0.5 else "UNRELATED", p_ext=s[0],
                       wall=wall, prompt_n=ntok, cache_n=None)
        out.append(rec)
        print(f"  {method:8s} {rec['pred']:10s} gold={it['gold']:10s} {rec['wall']:.2f}s",
              file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="/root/supermem/decide_labels.json")
    ap.add_argument("--node", default="172.31.3.1")
    ap.add_argument("--llm-port", type=int, default=8082)
    ap.add_argument("--name", required=True)
    ap.add_argument("--tasks", default="types,pairs")
    ap.add_argument("--methods", default="grammar,letters")
    ap.add_argument("--limit", type=int, default=0, help="first N items (smoke test)")
    ap.add_argument("--out", required=True,
                    help="results file; written after every run, and runs already "
                         "in it are skipped, so a crashed run resumes")
    a = ap.parse_args()
    lab = json.load(open(a.labels, encoding="utf-8"))
    if a.limit:
        lab = {k: v[:a.limit] for k, v in lab.items()}
    chat, rr = Chat(a.node, a.llm_port), Rerank(a.node)
    res = {"name": a.name, "node": a.node, "port": a.llm_port, "runs": {}}
    if os.path.exists(a.out):
        res = json.load(open(a.out, encoding="utf-8"))
    for task in a.tasks.split(","):
        for m in a.methods.split(","):
            key = f"{task}/{m}"
            if key in res["runs"]:
                print(f"  {key}: already done, skipping", file=sys.stderr)
                continue
            t0, n0 = time.time(), len(RESTARTS)
            fn = run_types if task == "types" else run_pairs
            rows = fn(lab[task], m, chat=chat, rr=rr)
            res["runs"][key] = {"rows": rows, "total_s": round(time.time() - t0, 1),
                                "restarts": RESTARTS[n0:]}
            with open(a.out + ".tmp", "w", encoding="utf-8") as f:
                json.dump(res, f, indent=1, ensure_ascii=False)
            os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    main()
