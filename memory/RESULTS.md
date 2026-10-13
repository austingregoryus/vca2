<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# smem — measured, 2026-09-22

> **Snapshot.** These are the 2026-09-22 measurements: one card (three nodes)
> and the Qwen3-Embedding-0.6B embedder. Production has since moved to two
> cards and, on 2026-10-07, to EmbeddingGemma 2 (768-d); the current embedder
> numbers are in [EMBED_BAKEOFF.md](EMBED_BAKEOFF.md). Figures here are not
> re-measured.

All numbers from an Intel VCA 2 (VCA1585LMV): three Xeon E3-1585L v5 nodes,
15.4 GB RAM each, in the Proxmox host. Nothing ran on the host except SQLite, numpy
and the HTTP glue. No cloud model, no API key.

**Corpus:** this repository's own `docs/` — 22 markdown files, 296,040
characters, **105,729 tokens**.

---

## 1. Ingest

| | |
|---|---|
| documents | 22 |
| chunks | 448 |
| memories extracted | 748 |
| wall time | **4,315 s (71.9 min)** |
| throughput | 0.10 chunks/s, ~69 chars/s |
| node dispatch | 145 / 145 / 144 — even to within one request |

Ingest is **prefill-bound, not decode-bound.** An extraction call is a
~760-token prompt for ~130 tokens of output; at the E3-1585L's ~23 tok/s
prefill the prompt costs ~33 s of the ~43 s call. Quantising further would not
help — prefill tracks *parameter count*, not file size
([GOTCHAS.md](../docs/GOTCHAS.md)).

This is a batch operation. 72 minutes for 300 KB is the honest number.

### Scheduling: the ingest was a third idle

Every gemma-4 request leaves an end timestamp and a duration in its node's
journal, so `timeline.py` can rebuild exactly which nodes were working at every
moment of a past run. For the ingest above:

```
nodes working at once    share of wall time
      0                 13.4%    9.7 min
      1                 19.3%   13.9 min
      2                 20.4%   14.7 min
      3                 47.0%   33.9 min
```

The work was 145 node-minutes, which is 48 minutes on three nodes, against 72
actual. Three causes, none of them the model:

1. **Per-document batching.** A document's extraction calls finished before the
   next document started, and most documents produce only 1-2 calls, so they
   could not fill three nodes.
2. **Round-robin is not a work queue.** A request went to the next node in
   rotation even when that node was still busy, and waited there behind a
   ~58 s call while the node that had just finished sat idle.
3. **Embedding one document at a time**, each as a single batch on one node.

The fix (`Engine.ingest_many`, and least-in-flight dispatch in `Pool`) puts
every extraction call from every document into one queue, longest first, with
one worker per node; embeds every chunk in one fanned-out call; and reconciles
afterwards in document order, so supersede behaviour does not change.
Reconcile is deliberately *not* overlapped with extraction: each server runs
one request at a time, so a reconcile call would wait behind a ~58 s
extraction, and 200 serial reconcile calls each waiting ~30 s would cost far
more than the ~10 minutes they take alone.

| | per document | shared queue |
|---|---|---|
| wall time | 72.2 min | **59.8 min** (−17%) |
| node-work | 145.2 node-min | 143.7 node-min |
| all 3 nodes working | 47.0% | **72.5%** |
| all 3 working during extraction | — | **94.2%** (46.0 min vs 45.1 ideal) |

The same work, packed better. Extraction is now effectively saturated; the
remaining gap is reconcile (9.7 min, mostly one node at a time) and embedding
(4.1 min).

The queued run was also the first full build using the current extraction
code (grammar, de-exampled prompt, corrected reconcile rule). Its retrieval,
against the store above:

| configuration | store above | current pipeline |
|---|---|---|
| memories + chunks, rerank 40 | 15/15, 1,232 tok | 15/15, 1,480 tok |
| memories + chunks, no rerank | 13/15, 417 tok | 12/15, 783 tok |
| memories only, k=15 | 13/15, 661 tok | 12/15, 720 tok |
| superseded during ingest | 72 (48 later restored) | **4** |

Best-configuration recall is unchanged. The memory-only rows are one question
worse, which a 15-question set cannot distinguish from run-to-run variation.
The superseded count is the corrected reconcile rule working at ingest time
rather than via a repair pass afterwards. Production stays on the store above
for now; the queued build is kept on the host as `smem-queue.db`.

---

## 2. Retrieval

15 questions about the corpus, each with hand-picked expected terms verified
to occur in it. A hit counts as relevant if it contains one of those terms.

| configuration | recall@k | median rank | latency | context |
|---|---|---|---|---|
| memories + chunks, rerank 40 | **15/15 (100%)** | 1 | 16.3 s | 1,232 tok |
| memories + chunks, rerank 20 | 14/15 (93%) | 1 | 7.4 s | 781 tok |
| memories + chunks, **no rerank** | 13/15 (87%) | 1 | **0.18 s** | 417 tok |
| chunks only, rerank 40 | 15/15 (100%) | 1 | 28.0 s | 2,845 tok |
| memories only, rerank 40 | 13/15 (87%) | 1 | 10.9 s | 364 tok |
| memories only, k=15 | 13/15 (87%) | 1 | 10.9 s | 661 tok |

### What this says

**The extracted memories earn their cost.** Chunks alone reach 100% recall,
so retrieval does not *need* them — but it costs 2,845 context tokens and
28 s. Adding memories reaches the same 100% at **1,232 tokens (2.3× fewer)
in 16.3 s (1.7× faster)**. Memories are short, so the cross-encoder has far
less text to score; the compression shows up twice, once in the prompt and
once in the rerank.

**Reranking buys 13 points of recall for 90× the latency.** Without it,
retrieval is 0.18 s end to end and still puts the right result at rank 1 in
the median case. That is the configuration to use interactively; the rerank is
for when a wrong answer costs more than 16 seconds.

**Median rank is 1 in every configuration.** The bi-encoder alone is already
finding the right thing first most of the time. The rerank is fixing the tail.

### Against Supermemory's published figure

Supermemory reports **95% Recall@15 on LongMemEval at ~720 tokens**. The
closest row here is *memories only, k=15*: **87% Recall@15 at 661 tokens**.

Same order of magnitude, on a different and much smaller benchmark, with a
keyword grader rather than a human one. It is a sanity check that the
architecture lands in the right region — not a like-for-like score.

### Compression

```
corpus                 105,729 tokens
context for an answer    1,232 tokens     98.83% reduction, 100% recall
                           661 tokens     99.37% reduction,  87% recall
```

---

## 3. End-to-end answers

Retrieve → rerank → gemma-4 answers with `[n]` citations.

**12/15 (80%)** of answers contained the expected fact.
Median latency **55.7 s**, median context **1,232 tokens**.

All three misses were audited individually:

| question | what happened |
|---|---|
| fastest node-to-node DMA | Answered *4581/4487 MiB/s on 7.0.14-17-pve*; the label expected *4423*. **The label was wrong** — [KERNEL_PORT.md](../docs/KERNEL_PORT.md) records 4581/4487 as "marginally faster than the pinned kernel". The system was more correct than the test. |
| what GPU does each node have | Answered *Intel Gen9 HD Graphics NEO* — the OpenCL device string for the P580, taken from an early log entry, and scoped to node 0. Not wrong, but not the useful answer. **A genuine quality miss.** |
| verify a rebuilt module | Answered *"checking the source hash"*, which is what `srcversion` is, without naming the field. Borderline. |

So: 12/15 by strict grading, ~13/15 substantively, with one test-label error
found *by* the system. The grader is reported as-is rather than adjusted after
the fact.

Answer latency is dominated by prefill of the context: 2,380 tokens of context
took 117.7 s, 357 tokens took 37.7 s.

---

## 4. Temporal truth

The classifier decides DUPLICATE / UPDATE / DISTINCT for each new fact against
its nearest existing neighbour.

### Accuracy

| set | first prompt | after fixes |
|---|---|---|
| 12 synthetic pairs | 12/12 | 11/12 |
| 10 pairs taken from real ingest decisions | **1/10** | **8/10** |
| overall | — | **19/22** |

The synthetic set scored 12/12 while the real corpus was quietly losing facts.
A test set containing only the cases you already thought of will tell you
everything is fine.

### What was going wrong

The prompt treated "the same measurement under different conditions" as an
update, and demoted the earlier reading. Of 73 supersede decisions made during
the ingest, **50 were wrong** — including:

```
MTU 1500 gives 2810 Mbit/s        superseded by   MTU 65521 gives 15934 Mbit/s
host→node2 14.6 Gbit/s            superseded by   node2→host 25.6 Gbit/s
hottest core 62 °C on one node    superseded by   92-94 °C on all three
GEMM N=1408 → 1359.5 GFLOP/s      superseded by   GEMM N=1536 → 1335.3 GFLOP/s
unloading the driver hangs boot   superseded by   recovering needs a host bounce
```

Every one of those pairs is two true facts, not a correction.

### The fix

One rule, applied before anything else:

> **Can A and B both be true at the same time? Then it is never UPDATE.**

Plus worked examples drawn from the failures above. `reconcile_repair.py` then
re-judged all 73 links and restored 48 (2 more were correctly skipped because
another row still superseded them) — about 70 short LLM calls instead of a
72-minute re-ingest.

Final store: **724 live memories, 24 superseded, 0 forgotten.**

---

## 5. Three ways this failed silently

Each returned HTTP 200 and reported success.

### gemma-4-E4B is a reasoning model

It writes its chain of thought to `message.reasoning_content` and only then
starts `message.content`. Every prompt here was long enough that it spent the
whole token budget thinking, hit `finish_reason: "length"`, and returned
`content: ""`.

**A complete 22-document ingest ran to completion reporting success with zero
facts extracted.**

`chat_template_kwargs: {"enable_thinking": false}` is the switch that works.
`reasoning_budget` and `thinking_budget` are ignored by this build;
`reasoning_format: "none"` only stops the server splitting the channels.

*Corollary:* the 21.8 tok/s aggregate previously measured for gemma-4 in
[BENCHMARK.md](../docs/BENCHMARK.md) was largely reasoning tokens.

### A free-text one-word classifier returns an empty string

Asked to reply `DUPLICATE` / `UPDATE` / `DISTINCT`, the model returned `""`
for *precisely the DUPLICATE cases* — which fell through to the default and
stored the duplicate. A GBNF grammar (`root ::= "DUPLICATE" | "UPDATE" |
"DISTINCT"`) makes the wrong answer unrepresentable.

### `MemoryMax=` is silently ignored on cgroup v1

These nodes run kernel 4.14, so cgroups are v1, where the property is
`MemoryLimit=`. systemd 249 **accepts `MemoryMax=`, reports it back when
queried, and writes `unlimited`** into `memory.limit_in_bytes`. The cap looked
applied; the reranker sailed past its "3 GB limit" to 7.4 GB.

---

## 6. Memory: the sizing cannot be reasoned from model size

The three GGUFs total 6.26 GB on a 15.4 GB node, which looks comfortable.

| | first config | after |
|---|---|---|
| embedder (0.64 GB model) | ~5.0 GB | **0.90 GB** |
| reranker (0.64 GB model) | ~5.0 GB | **0.90 GB** |
| llm (4.98 GB model) | 7.0 GB | 7.0 GB |
| total | **17.5 GB on a 15.4 GB node** | **8.8 GB** |

Resident set is dominated by the compute buffer, which scales with the
**micro-batch** (`-ub`), not the model. Dropping `-ub` from 2048 to 768 cut
the two small servers by 5.5× each.

It survived the 72-minute ingest and died during the benchmark, because the
buffers had not yet grown into their worst case — which is why this needs
measuring (`measure_rss.sh`) rather than estimating.

### The reranker leaks

Under sustained reranking, `--reranking` grows without bound: **0.29 GB
anonymous for the embedder against 7.4 GB for the reranker**, same model size,
same flags. Mitigated rather than fixed:

- `MemoryLimit=3G` on the leaker, so systemd restarts a 0.64 GB model in ~10 s
  instead of the kernel choosing the largest process (the 5 GB LLM).
- `Restart=always` on all three units.
- `Pool.post()` retries with backoff across all nodes. All three nodes do
  identical work, so they hit the cap within seconds of each other — three
  attempts against three restarting servers failed an entire run.

During the final evaluation the reranker restarted **twice on every node** and
the run completed with identical numbers. That is the system working.

---

## 7. Verification

```
test_smem.py     24/24   chunker + store, offline, no models needed
recon_eval.py    19/22   temporal-truth classifier, labelled
eval.py                  retrieval + answers, table above
demo.py                  all seven behaviours end to end
```

---

## What would move the needle next

1. ~~**A smaller extraction model.**~~ **Answered: no.** The
   [extraction bake-off](BAKEOFF.md) ran `OLMoE-1B-7B` and two other small
   models. With a grammar they hold the JSON contract and run ~3.2× faster per
   call, but OLMoE's facts cover 26% of the source's numbers against
   gemma-4-E4B's 44%. Extraction stays on gemma-4-E4B.
2. **Rerank fewer candidates, or not at all.** 20 candidates recall 93% at
   7.4 s; zero candidates recall 87% at 0.18 s. The right default depends on
   whether the caller is a human or a batch job.
3. **Fix or replace the reranking server.** The leak is worked around, not
   understood.
4. **Profile buckets and the knowledge graph.** Both are real Supermemory
   features. v0.3 (2026-09-23) added part of each: one static/dynamic profile
   per space (`GET /v4/profile`) and a memory graph with updates and extends
   relations. Buckets, derived facts and entity extraction are still not
   implemented ([README.md](README.md#limits-honestly)).
