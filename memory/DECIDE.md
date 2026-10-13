<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Decision bake-off: typing memories and judging extends links

2026-09-23. Question: could smem make its background decisions (type each
memory; decide whether a newer memory *extends* an older one) faster or more
reliably with the technique from [Reflex](https://github.com/kshetrajna12/reflex)
— offer the answers as letters, generate one token, read each letter's
probability — or with a smaller model?

Harness: `bench_decide.py` (one node, sequential calls, llama-server's own
timings), `bench_decide_small.sh` (small models on a temporary port),
`bench_decide_score.py`. Raw results and the full score table are in `decide/`.

## Data

`decide_labels.json`, labelled by hand (by Claude, blind to the model's
labels for the random part):

- **112 memories** — 80 random live memories from `vca2-docs`, plus the 22 the
  live model currently calls PREFERENCE, the `claude` space's personal
  memories and the original 12-item set (random sampling alone yields almost
  no preferences). 79 fact / 19 preference / 14 episode. 26 are ambiguous and
  carry two acceptable labels; "strict" scores only the other 86.
- **50 pairs** — candidate pairs the extends judge actually sees (cosine ≥
  0.80, newer vs older), half currently linked, half not. The decision that
  matters is binary, link or not; 34 pairs are clear-cut (11 should link,
  23 should not), 16 could go either way and are not scored.

Four of the personal memories described the author's home network and
plans, so their text has been replaced with a `[redacted: …]` placeholder in
`decide_labels.json` and in the raw results under `decide/`. Their labels,
model outputs and probabilities are untouched, so every score here still
reconciles with `decide/SCORES.txt`. Re-running the harness on the redacted
text would type those four items differently.

Small sets: one or two items move a score by a few points. Read differences
of 1–2 as noise.

## Methods

| method | what |
|---|---|
| grammar | production: the model writes the class name, a GBNF grammar allows nothing else |
| letters1 | same definitions, options as letters, ONE generated token, probabilities from logprobs |
| letters | letters1 twice, options reversed the second time, averaged (Reflex's position-bias fix) |
| rerank | the Qwen3-Reranker-0.6B already on every node, instruction in the query, score = P(yes) |

Models: gemma-4-E4B Q4_K_M (production, node 2), Qwen3.5-0.8B Q8_0,
Qwen3.5-2B Q4_K_M, Llama-3.2-1B (node 1, port 8083, 3 GB cap).

## Results

Typing (112 memories; "always fact" scores 87 lenient / 64 strict):

| model / method | lenient | strict | PREFERENCE precision | PREF recall | s / decision |
|---|---|---|---|---|---|
| gemma-4 grammar (prod) | 99 | 74 | 20/29 | 18/19 | 1.66 |
| **gemma-4 letters1** | **101** | **76** | **16/17** | 16/19 | 2.41 |
| gemma-4 letters | 101 | 76 | 17/18 | 16/19 | 4.81 |
| gemma-4 rerank | 57 | 37 | 7/13 | 7/19 | 2.71 |
| Qwen3.5-2B letters1 | 93 | 70 | 3/3 | 7/19 | 1.01 |
| Qwen3.5-0.8B letters1 | 91 | 68 | 2/2 | 6/19 | 0.42 |
| Llama-3.2-1B letters | 77 | 58 | 0/1 | 3/19 | 0.66 |

Confidence, gemma-4 letters1: calibration error 0.072; at confidence ≥ 0.9 it
covers 98/112 memories and is right on 91. Qwen3.5-2B is the best calibrated
(0.026; 33/33 right at ≥ 0.9) but only because it is rarely sure.

Extends (34 clear pairs; "never link" gets 23 right):

| model / method | links right / wrong | recall | correct | s / decision |
|---|---|---|---|---|
| gemma-4 grammar (prod) | 11 / 7 | 11/11 | 27 | 3.44 |
| **gemma-4 letters1** | 11 / 5 | 11/11 | 29 | 4.21 |
| gemma-4 letters1, p ≥ 0.9 | **11 / 3** | 11/11 | 31 | 4.21 |
| gemma-4 letters, p ≥ 0.9 | 11 / 2 | 11/11 | 32 | 8.42 |
| gemma-4 rerank | 11 / 23 | 11/11 | 11 | 1.03 |
| Qwen3.5-2B letters1 | 7 / 3 | 7/11 | 27 | 1.78 |
| Qwen3.5-0.8B grammar | 5 / 1 | 5/11 | 27 | 0.67 |

gemma-4 letters1 ranks pairs well (AUC 0.964). Its three confident false links
are two "sibling measurements" (one-node vs three-node AVX2 temperature; the
same MTU in two directions) and one near-duplicate. The grammar version links
duplicates the letter version correctly calls DUPLICATE (P ≥ 0.69).

## Findings

1. **Keep gemma-4.** No small model is close. Qwen3.5-2B, the best of them,
   is 2.4x faster on typing but finds 7 of 19 preferences and misses 4 of 11
   true links; the 0.8B and Llama-1B barely beat "always fact".
2. **Switch to letters1.** Same model, one extra ~20-token option list and
   logprobs instead of a grammar: typing precision on PREFERENCE goes from
   20/29 to 16/17 — the mislabels that were landing in the profile's "Always
   known" — and false links drop from 7 to 5, or 3 with a 0.9 threshold,
   with no lost true links. Cost: +0.75 s per memory, +0.8 s per pair.
3. **Drop the option shuffle.** Two passes cost 2x and bought nothing on
   typing and one false link on extends.
4. **The reranker is not a judge.** Every candidate is already same-topic
   (cosine ≥ 0.80), and it scores almost all of them ≥ 0.9 — relevance, not
   extends. It also leaks: under this load it was OOM-killed at its 3 GB cap
   twice on node 2 (systemd restarted it; production search saw two brief
   gaps).
5. **Speed is prompt reading, and the prompt cache is the lever.** gemma
   reads about 32 tokens/s on a node and generates only one token here. The
   first harness put the shuffled options in the system prompt; the two
   orders evicted each other from llama-server's single-slot cache and every
   call re-read ~250 tokens: **14–28 s per decision instead of 2–4**. Keep the
   instructions byte-identical and everything that varies at the end, and
   each call pays only for the memory and the options. Production already
   does this.

## Not measured

- Asking type and extends against one shared read of a memory (would save
  the memory's own tokens, ~30–70, on each extends call).
- Fine-tuning or calibrating the small models on labels (Reflex's LoRA step);
  the 2B's calibration suggests it might be worth it, but it needs a GPU.
- More than one node or concurrent load.

## Deployed (2026-09-23), with calibration

Production now judges with letters1 (`Models.choose`, `engine.classify_batch`,
`engine.judge_extends`). Every decision is logged with its probability vector
(`decisions` table); setting a type or pressing ✓ / ✗ on a link in the
console saves a label (`labels`). `smem/calibrate.py` fits, per decision
kind, a temperature and a threshold from labelled decisions (targets: 85%
link precision, 90% confident-type accuracy), then re-applies them to every
logged decision without calling the model, so the whole store follows one
standard. The worker refits after every 5 new labelled decisions; labels
always win over the judge.

Seeded with the unambiguous items of this bake-off (82 type, 34 pair labels;
30 pairs had a logged decision). First fit, then a full re-run of all 854
memories:

| | temperature | threshold | calibration error | on the labels |
|---|---|---|---|---|
| typing | 2.16 | 0.73 | 0.100 → 0.028 | 91% right above threshold, covers 93% |
| extends | 2.29 | 0.81 | 0.155 → 0.021 | links 90% right, 82% of true links found |

Both judges were over-confident (T > 2). The store went from 138 extends
links (old grammar judge) to 56, plus 4 duplicate edges; 62 memories in
`vca2-docs` have an uncertain type and stay out of the static profile.

**Caveat:** these numbers are in-sample — the fit is scored on the labels it
was fitted to, and 30 pairs is small. They will become honest as console
corrections (which the fit has not seen) accumulate.
