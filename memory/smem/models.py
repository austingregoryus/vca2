# SPDX-License-Identifier: MIT
"""The three model stages: embed, rerank, generate.

Each is a thin wrapper over a llama-server endpoint plus the prompt-format
details that the specific model actually wants.
"""

import concurrent.futures as cf
import json
import math
import re

import numpy as np

from .pool import PORT_EMB, PORT_LLM, PORT_RRK, Pool

DIM = 768                 # EmbeddingGemma 2 (Qwen3-Embedding-0.6B was 1024)
EMB_BATCH = 16            # texts per request
EMB_CHARS = 5000          # truncate before embedding; 2048-token slots on the server
RRK_GROUP = 4             # doc-query pairs per rerank request
RRK_CHARS = 1200

# EmbeddingGemma 2 (since 2026-10-07; EMBED_BAKEOFF.md) is asymmetric and
# prompted on BOTH sides, as its model card specifies: a query is
# "task: search result | query: ...", a document "title: ... | text: ...".
# These exact strings are what the bake-off measured; skipping them costs
# real retrieval quality -- it is not cosmetic.
QUERY_INSTRUCT = "task: search result | query: "


def doc_text(text, title=None):
    """A document or memory as the embedder expects it."""
    return f"title: {title or 'none'} | text: {text}"


class Models:
    def __init__(self, pool=None, workers=6):
        self.pool = pool or Pool()
        self.workers = workers

    # ---------------------------------------------------------------- embed

    def _embed_batch(self, texts):
        r = self.pool.post(
            PORT_EMB, "/v1/embeddings",
            {"input": [t[:EMB_CHARS] for t in texts]},
        )
        # llama-server does not guarantee data[] order matches input order;
        # it carries an explicit index, so sort by it rather than trusting it.
        rows = sorted(r["data"], key=lambda d: d.get("index", 0))
        # A node still running an old embedder (e.g. one back from repair with
        # its old unit) must fail loudly, not store vectors of another size:
        # Engine.search skips a whole table whose size does not match.
        bad = {len(d["embedding"]) for d in rows} - {DIM}
        if bad:
            raise RuntimeError(f"embedder returned dim {sorted(bad)}, expected {DIM}: "
                               "run `services.sh install` on that node")
        return [d["embedding"] for d in rows]

    def embed(self, texts, is_query=False, titles=None):
        """-> (n, DIM) float32, L2-normalised. Batched and fanned across nodes.

        Documents and memories get the document prompt; `titles` (a chunk's
        heading path, one per text) fills its title, otherwise "none"."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        if is_query:
            texts = [QUERY_INSTRUCT + t for t in texts]
        else:
            titles = titles or [None] * len(texts)
            texts = [doc_text(t, h) for t, h in zip(texts, titles)]
        batches = [texts[i:i + EMB_BATCH] for i in range(0, len(texts), EMB_BATCH)]
        out: list = [None] * len(batches)
        with cf.ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._embed_batch, b): i for i, b in enumerate(batches)}
            for f in cf.as_completed(futs):
                out[futs[f]] = f.result()
        vecs = np.asarray([v for b in out for v in b], dtype=np.float32)
        # The server already returns unit vectors, but a stray non-unit row
        # would silently skew every cosine it takes part in. Cheap insurance.
        n = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(n, 1e-9)

    # --------------------------------------------------------------- rerank

    def _rerank_group(self, query, docs):
        r = self.pool.post(
            PORT_RRK, "/rerank",
            {"query": query, "documents": [d[:RRK_CHARS] for d in docs]},
        )
        scores = [0.0] * len(docs)
        for item in r.get("results", []):
            scores[item["index"]] = float(item.get("relevance_score", 0.0))
        return scores

    def rerank(self, query, docs):
        """Cross-encoder scores, one per doc, in input order.

        Sent in small groups: the reranker runs one forward pass per pair and
        a 50-doc request would blow past the server's 2048-token batch. Scores
        are absolute per-pair values, so they stay comparable across groups.
        """
        if not docs:
            return []
        groups = [docs[i:i + RRK_GROUP] for i in range(0, len(docs), RRK_GROUP)]
        out: list = [None] * len(groups)
        with cf.ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._rerank_group, query, g): i
                    for i, g in enumerate(groups)}
            for f in cf.as_completed(futs):
                out[futs[f]] = f.result()
        return [s for g in out for s in g]

    # ------------------------------------------------------------- generate

    def chat(self, messages, max_tokens=512, temperature=0.2, grammar=None,
             think=False):
        """Chat completion. Returns message.content -- which can be empty.

        WHY think=False BY DEFAULT: gemma-4-E4B is a reasoning model. Left to
        itself it writes its chain of thought into `reasoning_content` and only
        then starts `content`. Every prompt here is long enough that it burned
        the whole token budget thinking, returned finish_reason=length, and
        handed back content='' -- an empty string, not an error. A full 22-file
        ingest "succeeded" with 0 facts extracted before this was found.

        `chat_template_kwargs.enable_thinking` is the switch that actually
        works. `reasoning_budget`/`thinking_budget` are ignored by this build,
        and `reasoning_format:"none"` only stops llama-server splitting the
        channels -- the model still thinks, it just leaves raw `<|channel>`
        markers in the content.
        """
        p = {"messages": messages, "max_tokens": max_tokens,
             "temperature": temperature}
        if not think:
            p["chat_template_kwargs"] = {"enable_thinking": False}
        if grammar:
            p["grammar"] = grammar
        r = self.pool.post(PORT_LLM, "/v1/chat/completions", p)
        msg = r["choices"][0]["message"]
        return msg.get("content") or ""

    def choose(self, system, user, n):
        """Multiple choice by probability, not by generation.

        The options must be offered in `user` as letters A.. . One token is
        generated and the probability of each of the first `n` letters is read
        from the logprobs, renormalised over those letters. -> [p_A, p_B, ...]
        or None if the server returned no logprobs.

        Measured against the grammar-constrained one-word answer on the same
        model (DECIDE.md): more accurate on both decisions, and it yields a
        confidence to threshold on. Keep `system` byte-identical across calls
        -- llama-server's single slot caches it, and a changing system prompt
        made each decision 5-7x slower.
        """
        p = {"messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
             "max_tokens": 1, "temperature": 0.0, "logprobs": True,
             "top_logprobs": 20, "chat_template_kwargs": {"enable_thinking": False}}
        r = self.pool.post(PORT_LLM, "/v1/chat/completions", p)
        lp = (r["choices"][0].get("logprobs") or {}).get("content") or []
        if not lp:
            return None
        best = {}
        for t in lp[0].get("top_logprobs", []):
            k = t["token"].strip()
            best[k] = max(best.get(k, -99.0), t["logprob"])
        ps = [math.exp(best.get(chr(65 + i), -30.0)) for i in range(n)]
        z = sum(ps)
        return [x / z for x in ps] if z > 1e-9 else None

    def choose_many(self, jobs, n):
        """jobs: [(system, user)]. Fanned across nodes, order preserved."""
        out: list = [None] * len(jobs)
        with cf.ThreadPoolExecutor(max_workers=min(self.workers, 3)) as ex:
            futs = {ex.submit(self.choose, s, u, n): i for i, (s, u) in enumerate(jobs)}
            for f in cf.as_completed(futs):
                try:
                    out[futs[f]] = f.result()
                except Exception:
                    out[futs[f]] = None
        return out

    def count_tokens(self, text):
        """Exact token count from the answering model, not a chars/4 guess.

        The headline claim of a memory layer is a token number, so the token
        number should not itself be an estimate. Falls back to chars/4 only if
        the endpoint is unavailable.
        """
        if not text:
            return 0
        try:
            r = self.pool.post(PORT_LLM, "/tokenize", {"content": text},
                               attempts=2)
            return len(r.get("tokens", []))
        except Exception:
            return len(text) // 4

    def chat_many(self, jobs, **kw):
        """jobs: list of message-lists. Fanned across nodes, order preserved."""
        out: list = [None] * len(jobs)
        with cf.ThreadPoolExecutor(max_workers=min(self.workers, 3)) as ex:
            futs = {ex.submit(self.chat, m, **kw): i for i, m in enumerate(jobs)}
            for f in cf.as_completed(futs):
                try:
                    out[futs[f]] = f.result()
                except Exception:
                    out[futs[f]] = ""
        return out


def parse_json_loose(text):
    """Pull a JSON array out of an LLM reply.

    Small instruct models wrap JSON in prose or ```json fences no matter how
    the prompt is worded, so parsing has to tolerate that rather than treat it
    as a failure. Returns [] if nothing parses -- a bad extraction should skip
    a document, not abort an ingest.
    """
    if not text:
        return []
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else [v]
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, list) else [v]
        except json.JSONDecodeError:
            pass
    # last resort: one object per line
    out = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out
