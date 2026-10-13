<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Embedder bake-off: EmbeddingGemma 2 vs Qwen3-Embedding-0.6B (2026-10-07)

Candidate: [`google/embeddinggemma-2`](https://huggingface.co/google/embeddinggemma-2),
run as [`ggml-org/embeddinggemma-2-GGUF`](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF)
`Q8_0` (310 MB). Architecture `gemma-embedding2` needs llama.cpp **b11476+**
(support merged 2026-10-06, PR #30054); the nodes' 0.4.1-dev cannot load it. The
official `llama-b11476-bin-ubuntu-x64` build needs glibc 2.34 and runs as-is on
the nodes' Ubuntu 22.04 (2.35), from `/root/eg2` with `LD_LIBRARY_PATH`.

Script: [`embed_bakeoff.py`](embed_bakeoff.py). Corpus: `vca2-docs`, 517 chunks +
852 memories. 30 questions: `eval.py`'s 15 plus 15 more from the docs (one more
was dropped because its term is not in the corpus). Same keyword grader as
`eval.py`. Ranking is the embedder alone, memories and chunks together.

Formats: Qwen as production (query instruction, documents raw). Gemma per its
model card: `task: search result | query: …`, `title: <heading or none> | text: …`.

## Result

| | Qwen3-Embedding-0.6B (production vectors) | EmbeddingGemma 2 |
|---|---|---|
| recall@1 | 43% | 43% |
| recall@5 | 80% | 83% |
| recall@15 | 87% | 90% |
| recall@40 (the reranker's pool) | **100%** | 97% |
| MRR | 0.594 | 0.601 |
| memories only, recall@15 | 77% | 77% |
| bulk embed, node 5 (172.31.6.1, 35 W cap) | 1.16 texts/s | **3.08 texts/s (2.6×)** |
| one query embed | 73 ms | **36 ms (2×)** |
| vector size | 1024 | 768 |

**Quality is a tie.** Every difference is one question out of 30, and the
per-question ranks move both ways (Gemma: kernel versions 18 -> 1, GPU 39 -> 16;
Qwen: dead-sshd recovery 4 vs 16, `systemd-run` 10 vs 42, the one Gemma
pushed outside the top 40).

**Speed is the model, not the newer llama.cpp:** Qwen on the same b11476 build
measured 1.11 texts/s.

## Lessons from running it

- Pointing a bulk re-embed at the live `sm-emb` on node 5 (172.31.6.1) OOM-killed it twice
  at its 3 GB cgroup cap: a freshly restarted embedder went 0.3 -> 3 GB in about
  two minutes of sustained work (normal use: ~1.8 days). The rerank stage did
  the same to `sm-rrk`. systemd brought both back in 5 s and smem was not using
  node 5 at the time, but large real ingests can hit this too.
- An uncapped test Qwen embedder reached 6.6 GB on a node whose LLM holds
  7.3 GB; it was stopped before the kernel had to choose a victim. Test
  servers now run with `-p MemoryLimit=4G` (cgroup v1: not `MemoryMax`).
- EmbeddingGemma 2 on b11476 leaks the same way (0.17 -> 2.4 GB over the run),
  so the daily `sm-emb` recycle would still be needed.

## Switched, 2026-10-07

Production now embeds with EmbeddingGemma 2 on nodes 0-2, 4 and 5 (node 3 is
dead; `services.sh install` copies the build and model to a node that lacks
them). What changed:

- `models.py`: `DIM 768`; queries `task: search result | query: …`; documents
  and memories `title: <heading or none> | text: …` (exactly as measured).
- `services.sh`: the embedder runs `/root/llama-b11476/llama-server`
  (`LD_LIBRARY_PATH` in the unit), no `--pooling`, `-c 4096 -b 2048 -ub 2048
  -np 2`; 3 GB cap and daily recycle kept. Reranker and LLM unchanged.
- `python3 -m smem reembed`: all 517 chunks + 950 memories (every container,
  superseded and forgotten too) in 78.7 s on 5 nodes, 18.6 texts/s; 0 rows left
  at the old size. Backup: `smem.db.bak-before-embgemma2-20261007`.
- Cosine thresholds re-derived by matching the pairs Qwen's values selected on
  the same snapshot (Gemma packs scores high: unrelated memories ~0.69 against
  Qwen's ~0.35): `NEAR 0.82 -> 0.89`, `EXT_LOW 0.80 -> 0.875`, graph
  `doc_sim 0.78 -> 0.90`, forget floor `0.25 -> 0.55`. `SAME 0.985 -> 0.99`
  from altered copies: a full stop or spacing scores >= 0.99, one changed word
  0.95-0.98 and must reach the judge. Same-pair overlap 62-75%: the models
  disagree on marginal pairs, and these feed the LLM judge, not decisions.
- **Balanced candidate pool** (`Engine.search`). Gemma scores short memories
  above chunks (median top-40 cosine 0.738 vs 0.709), so the cosine merge gave
  memories ~30 of the reranker's 40 slots and dropped `eval.py`'s `systemd-run`
  answer (chunk rank 14 among chunks). Memories and chunks now get 20 each,
  the rest by score.

`eval.py --skip-answers` after the switch, 15 questions:

| configuration | before fix | after balanced pool | Sept baseline (Qwen) |
|---|---|---|---|
| mem+chunks, rerank 40 | 14/15 | **15/15**, 21.3 s | 15/15, 16.3 s |
| mem+chunks, rerank 20 | 14/15 | 14/15 | 14/15 |
| mem+chunks, no rerank | 12/15 | 12/15, 0.11 s | 13/15, 0.18 s |
| chunks only, rerank | 15/15 | (unchanged) | 15/15 |
| memories only, k=15 | 12/15 | (unchanged) | 13/15 |

The September baseline is an older corpus; on today's corpus Qwen's own
ranks (bake-off) give the same 12/15 without rerank. The reranked search is
~5 s slower because chunks are longer than memories for the cross-encoder.

Functional checks: a new memory stored in 0.11 s; an exact repeat and a
trailing-period copy both dropped as duplicates; search and a `hermes` search
(33 ms) answer; the dreaming worker refit cleanly.
