<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Extraction model bake-off, 2026-09-22

Ingest is prefill-bound. Prefill FLOPs = tokens × **active** parameters × 2,
and the measured budget is ~184 GFLOP/s per node, so a smaller active
parameter count is close to a linear win on the slowest stage of the pipeline.
This is the measurement of whether the fast models still do the job.

Workload: 12 chunk groups from 10 documents of this repo's `docs/`, 21,525
characters — the same grouping the real ingest uses. Raw output in
[`bakeoff-results.json`](bakeoff-results.json), transcript in
[`bakeoff-results.log`](bakeoff-results.log).

---

## Result

| model | active params | prefill | s/call | vs base | facts | form-bad | **usable** | clean |
|---|---|---|---|---|---|---|---|---|
| gemma-4-E4B Q4 | ~4B | 34.6 t/s | 57.3 s | 1.00× | 72 | 0 | **72** | **100.0%** |
| MiniCPM5-2B Q4 | 1.98B | 67.5 t/s | 18.1 s | 3.17× | 32 | 3 | 29 | 90.6% |
| OLMoE-1B-7B Q4 | 1.3B | 99.4 t/s | 17.7 s | 3.24× | 60 | 7 | 53 | 88.3% |
| Llama-3.2-1B Q4 | 1.24B | 150.5 t/s | 17.3 s | 3.31× | 49 | 11 | 38 | 77.6% |

Quality is **exactly monotonic with model size**, and the per-call speedup is
**flat at ~3.2× for everything below 2B**.

### Prefill rate stops mattering below ~2B

Llama-3.2-1B has **2.2× MiniCPM5's prefill throughput** (150.5 vs 67.5 t/s)
and is *slower* per call (17.3 s vs 18.1 s). There is a ~6 s floor of fixed
per-call cost — HTTP, server queueing, grammar-constrained sampling — that
swamps the compute saving once the model is small enough. Going below ~2B buys
nothing but worse facts.

### Usable throughput is not the same ranking

| model | projected ingest | usable facts/s |
|---|---|---|
| gemma-4-E4B | 71.9 min | 0.105 |
| MiniCPM5-2B | 30.1 min | 0.114 |
| OLMoE-1B-7B | 29.6 min | **0.231** |
| Llama-3.2-1B | 29.3 min | 0.190 |

MiniCPM5 is 3.17× faster per call but extracts **2.7 facts/call against
gemma-4's 6.0**, so its throughput advantage nearly evaporates: +9% usable
facts per second. OLMoE leads on this metric by extracting far more per
call — but 7 of its 60 were headings and markdown.

---

## The three checks, and why each one was needed

Every one of these was found by **reading the output**, not by predicting it.
Each caught a failure mode the previous ones structurally could not see.

### 1. Numeric provenance — `verify.check()`

Does every number in a fact appear in the source? In a memory store the
dangerous failure is not a malformed reply, it is a well-formed fact carrying
a transposed digit: it gets embedded, retrieved, ranked first and cited with
full authority, and nothing downstream will ever catch it.

Caught OLMoE inventing `2100 rpm` and `71.3 tok/s`.
**Blind to:** any hallucination without a number.

### 2. Prompt echo — `verify.echo_check()`

Is the "fact" a sentence copied out of the prompt? The first prompt ended with
a format example:

```
["The fan runs at 2100 rpm.", "The heatsink is copper."]
```

**Llama-3.2-1B returned both of those, verbatim, as facts about a PCIe
document.** OLMoE returned a mutated `"The link runs at 2100 rpm"`. Both
scored well on facts-per-call for producing nothing at all, and the numeric
check only caught the half that had a number in it — *"The heatsink is
copper"* has none.

The example existed to pin the output format. **The GBNF grammar does that job
properly, so the example was not merely harmful, it was redundant.** Removing
it took every model to 0 echoes and dropped Llama from 59 facts to 39 — about
20 of its original "facts" were contamination the example enabled.

**Blind to:** text copied from the *document* rather than the prompt.

### 3. Well-formedness — `verify.form_check()`

Is it a standalone claim at all, or a heading, a question, or a fragment?

```
OLMoE:        "Is this card worth the slot?"        <- a section heading
              "What the card is *not* for"          <- a section heading
Llama-3.2-1B: "Must allocate a whole segment"       <- no subject
              "Host BAR2"                           <- not a sentence
```

All of these passed checks 1 and 2 — no numbers to get wrong, and copied from
the document, not the prompt. All are worthless in a memory store: retrieved
out of context they say nothing. This is the check that finally separated the
models (100% / 90.6% / 88.3% / 77.6%).

Validated 12/12 on hand-labelled examples drawn from the actual output,
including the near-miss `"mbox_alloc is 0200 (write-only)"` — four words, but
a complete claim with a subject, a copula and a value. A word-count floor that
rejects it is throwing away exactly the dense facts worth keeping.

---

## Constrained decoding rescues every model

The probe tests {thinking on/off} × {grammar on/off}. Facts extracted from the
same short document:

| model | grammar off | grammar on |
|---|---|---|
| gemma-4-E4B | 0 | **5** |
| MiniCPM5-2B | 0 | **4** |
| OLMoE-1B-7B | 0 | **6** |
| Llama-3.2-1B | 5 | 3 |

Without a grammar, three of the four produce zero parseable facts. This is the
single change that makes a 1–2B model viable for a structured-extraction
contract at all.

**It also independently defeats the reasoning trap.** gemma-4-E4B with
thinking *on* and no grammar returns `content: ""` after 53.8 s of reasoning;
with thinking on and the grammar it returns 5 facts in 24.6 s. Forcing
`content` to open with `[` leaves no room to wander into the reasoning
channel.

---

## Two harness bugs worth recording

**The probe gated on a configuration the benchmark did not use.** It tested
"can this model emit JSON *unaided*" and used that to decide who entered a
benchmark that runs *with* a grammar — so it excluded MiniCPM5 and OLMoE,
precisely the models the grammar exists to rescue. A gate has to run the
configuration it is gating.

**A formatting bug discarded a completed 30-minute run.** A `KeyError` in the
results table threw away four models' worth of finished measurements. The
harness now writes `bakeoff-results.json` — including every extracted fact —
*before* formatting anything, so a new check can be scored against old output
without re-running a single model.

---

## Verdict: stay on gemma-4-E4B

The first version of this file recommended MiniCPM5-2B, on the theory that its
2.7 facts per call (against gemma-4's 6.0) was prompt conservatism rather than
a ceiling. That theory was tested and it was wrong.

[`nudge.py`](nudge.py) ran three prompt variants on MiniCPM5 against the same
workload, with a decision rule fixed before running: at least 60 usable facts,
and number coverage within 10 points of gemma-4's. **Coverage** is the new
measure — of the 153 distinct non-trivial numbers in the source text, the
fraction that appear in at least one extracted fact. It is a cheap proxy for
"did it capture what the document says", because numbers are where this
corpus keeps its facts, and unlike fact count it cannot be gamed by splitting
one claim into three.

| MiniCPM5 variant | facts | usable | coverage | s/call |
|---|---|---|---|---|
| current prompt | 32 | 29 | 9.2% | 18.1 s |
| "a section typically states 4 to 8 facts" | 23 | 23 | 3.9% | 20.2 s |
| "work through it sentence by sentence" | 9 | 9 | 2.6% | 15.2 s |

**Asking for more facts produced fewer.** None qualifies.

Coverage for every model, computed from the facts the bake-off saved (no
model time needed — this is what persisting every fact was for):

| model | usable | coverage |
|---|---|---|
| **gemma-4-E4B** | **72** | **44.4%** |
| OLMoE-1B-7B | 53 | 26.1% |
| Llama-3.2-1B | 38 | 11.8% |
| MiniCPM5-2B | 29 | 9.8% |

This is the measurement that should have come first. The three checks above
tell you whether the facts a model *did* extract are trustworthy; coverage
tells you how much it *missed*, and that is where the small models actually
lose. MiniCPM5's facts were clean because there were so few of them. A 3.2×
faster ingest that captures a fifth of the content is not a trade worth
making, and the hour-long end-to-end retrieval test was skipped because
nothing reached the bar that would have justified it.

Ingest stays at ~72 minutes. The remaining ways to cut it do not touch model
quality:

1. **Skip boilerplate chunks** before extraction — the SPDX headers,
   superseded notices and link lists that currently cost a full call.
2. **Try `-t 4` and `-ub 1024`** on gemma-4. Hyperthreading often hurts AVX2
   GEMM, and a larger micro-batch prefills a ~760-token prompt in one pass
   instead of two. Neither costs anything in quality.
3. **Do not reach for a lower quantisation.** Prefill is compute-bound and
   tracks parameter count, not file size; Q3 would buy almost nothing here.
