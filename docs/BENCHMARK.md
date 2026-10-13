<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Is this card worth the slot?

The question is not "is a 2016 accelerator faster than a modern GPU" — it
isn't, and the host in this machine has an **RTX 3070**. The useful question
is **additive**: with the host already busy, what does the card add, and what
does that addition cost in energy?

Harness: [`bench/`](../bench/). Every workload runs three ways — `host`,
`card`, `both` — for an **identical fixed wall-clock window**, and the verdict
is computed from the margin.

> **Host GPU, noted 2026-10-13.** The RTX 3070 described here was the host's
> GPU when this benchmark was run (2026-09-22). It was later replaced by an
> AMD GPU: by 2026-10-09 `monitor/vcamon.py` reads the host GPU through
> `amdgpu`. The NVENC caveats below describe the host as it was then.

## Result: transcode

1080p30 H.264, identical raw NV12 source both sides, 32 s window, 2 reps.
Host runs 4× `libx264 veryfast`; card runs 3× `h264_vaapi`, one per node.

| config | aggregate fps | energy (J) | frames/J |
|---|---:|---:|---:|
| host alone | 687.6 | 5239 | 4.21 |
| card alone | 438.5 | 2258 | 6.19 |
| **both** | **1132.1** | 6392 | 5.67 |

**The card adds +444 fps (+65%) for +36 W.**

- Marginal efficiency **12.35 frames/J**, against the host's own **4.21** —
  **2.9× better** per joule for the work it adds.
- **No contention.** The host's share under `both` is 1132 − 438 ≈ 694 fps,
  against 688 running alone. The card is not stealing host throughput; it is
  genuinely parallel capacity.
- The card idles at ~12 W and peaks at ~48 W for all three encoders.

### Verdict

**Keep it, for transcode.** A 65% throughput increase for 36 W, with the host
unaffected, is a good trade in a machine that already has the slot and the
PSU headroom.

### The honest asterisk

The host is encoding in **software** because its RTX 3070 is on `nouveau`,
which exposes no NVENC. A 3070's NVENC would likely beat three Gen9 Quick Sync
engines on both throughput and energy, so this result may well invert once the
proprietary driver is available.

It is **not** one `apt install` away — that was assumed here and turned out to
be wrong. Debian trixie's NVIDIA 550 cannot build against this host's 7.x
kernel; see [NVENC on the host](#nvenc-on-the-host-attempted-and-blocked).

The card's case is strongest when the 3070 is *busy with something else*, or
absent. That is still a real case — it is exactly the "additive capacity"
framing — but it is narrower than "this card is good at transcode".

Also note: this measures **throughput, not quality-per-bit**. At the same
nominal 5 Mbit/s the two encoders produced quite different output sizes
(VAAPI 675 kB vs libx264 1794 kB on an earlier single-stream run), so their
rate control is not comparable. A VMAF comparison is a separate exercise and
has not been done.

## What the card is *not* for

Already measured, in [LLAMA.md](LLAMA.md):

| OLMoE-1B-7B Q4, single stream | gen t/s |
|---|---:|
| Host only (E5-2699 v3) | **33.4** |
| RPC → 1 node | 30.2 |
| RPC → all 3 nodes | **12.7** |

**Three nodes are 2.4× slower than one** (12.7 vs 30.2 t/s; 2.6× slower than
the host alone). llama.cpp's RPC splits *layers*, so
at batch size 1 every token traverses the whole pipeline serially — spreading
layers across three nodes means three network hops per token. More nodes is
structurally worse.

**This is not a fabric limitation.** The DMA ring does 4462 MiB/s CPU-free
([DMA.md](DMA.md)) and would not change that number at all, because the
problem is serialization, not bandwidth. Tensor parallelism *would* use the
fabric well — activations are only ~8 KB per token per layer, so it is
latency-bound, and the ~35 µs doorbell implies a ceiling around 450 tok/s —
but **llama.cpp has no tensor-parallel RPC backend**. Building one means
writing a distributed inference engine, not configuring one.

So: **one model at low latency, no. Many concurrent requests, yes** — and
that second half is no longer an assertion. Measured below: three independent
workers scale **2.96x**, and the card out-serves the host outright.

## How the harness avoids fooling you

Three versions of the transcode benchmark were wrong before this one, and each
failure is instructive:

1. **Variable-length runs.** Average watts is energy ÷ duration, so comparing
   configurations that ran for different wall times is meaningless — the
   longer run dilutes its own average with idle time. This produced a
   confident "the card adds 50% throughput for 1 W, 57× the host's
   efficiency", which was pure artifact.
2. **`ffmpeg -t`.** That caps *media* duration, not wall clock. Each side
   still ran as long as its own speed dictated (7.0 s vs 8.2 s).
3. **Fixed by `timeout -s INT`** — a real wall-clock limit, SIGINT so ffmpeg
   flushes its final `frame=` line.

`verdict.sh` now **refuses to trust** a margin when the windows differ by more
than 15%, and prints a warning instead. It caught failure (2) on its own.

Energy (joules) is reported alongside watts because energy is additive and
duration-independent; watts are only meaningful here because the windows match.

### Limits of the power numbers

Both sides are RAPL **package domains only**. The host excludes DRAM and
platform; the card excludes the two PLX switches, the DDR4 and VRM losses.
[THERMAL.md](THERMAL.md) measured ~134 W at the card under all-GPU load
against a node-package sum well below that. **Neither figure is wall power**
— treat the ratio as indicative, and do not quote the absolute watts as
system power.

## Running it

```bash
# on the host, as root, with all three nodes net_device_ready
cd bench
./vca-bench.sh --list
./vca-bench.sh --workload transcode --config all --reps 2
./verdict.sh results/bench-<stamp>.json
```

`TC_RUN_SECS`, `TC_HOST_JOBS` and `NODE_PW` are the knobs worth touching.

## Not yet measured

- **Quality-per-bit** (VMAF) — the throughput win could be partly a
  quality loss, and this does not rule that out.
- **NVENC on the host** — **attempted 2026-09-22 and BLOCKED.** See below.
- ~~Concurrent LLM serving~~ **MEASURED 2026-09-22 — the card wins outright.**
  See below.
- **GPU compute** as an additive workload; the host's 3070 is on nouveau so
  there is no usable OpenCL/CUDA comparison without the proprietary driver.


## NVENC on the host: attempted and blocked

The biggest asterisk on the transcode result is that the host encodes in
software, because its RTX 3070 runs on `nouveau` (which has never implemented
NVENC). Installing the NVIDIA driver would settle whether the card's transcode
win survives. It was attempted on 2026-09-22 and **does not currently work.**

### What blocks it

The host is Proxmox 9.2.2 / Debian 13 trixie on kernel **7.0.14-17-pve**.
Debian trixie ships NVIDIA **550.163.01**, and it will not build:

```
nvidia-uvm/uvm_ats_sva.c:313: error: 'IOMMU_DEV_FEAT_SVA' undeclared
nvidia-uvm/uvm_ats_sva.c:328: error: implicit declaration of
                              'iommu_dev_disable_feature'
```

Linux reworked the IOMMU SVA API and removed those symbols. Driver 550
predates that, so its UVM layer cannot compile against a 7.x kernel. This is
a hard version incompatibility, not a configuration mistake.

### What was checked first, and why it was safe to try

- **No VM passthrough.** Neither VM had `hostpci`/`vga` entries, and
  `vfio-pci` was not bound to `02:00.0` — so taking the GPU on the host could
  not break a guest.
- **Nothing was using nouveau** (refcount 0) and the host is headless with no
  display manager.
- `nvidia-open-kernel-dkms` is in `trixie/contrib`, already enabled; only the
  userspace `nvidia-alternative-*` needed `non-free`, which was added
  temporarily and reverted from a backup.

### Reverted cleanly

All `nvidia-*` packages purged, the DKMS entry removed, `debian.sources`
restored from backup (`non-free` gone again), package count back to its
starting 1225, `nouveau` still bound, and the VCA stack untouched — both
`vca2-vcass` DKMS entries intact and all three nodes `net_device_ready`.

### What would be needed

A driver new enough to know about the reworked IOMMU API — i.e. a 5xx/6xx-era
release, which is not in Debian trixie. Options, in increasing order of
footprint:

1. Wait for trixie-backports (or a Proxmox-packaged driver) to carry one.
2. NVIDIA's `.run` installer with a current driver. Bigger footprint on the
   host, blacklists nouveau, and needs re-running on every kernel update —
   worth it only if NVENC is wanted for real work, not for one benchmark row.
3. Pin the host back to an older kernel that 550 supports. **Not worth it** —
   it would trade a working, validated 7.0.14 setup for a benchmark data point.

### Does this change the verdict?

**No, and it is worth being precise about why.** The measured result stands:
the card adds **+65% transcode throughput for +36 W**, at 2.9x the host's
energy efficiency per added frame, with no contention.

What NVENC would change is the *alternative*: if the 3070 could absorb that
work more efficiently, the card's strongest demonstrated use case would be
much less compelling. That remains genuinely unknown, and the honest position
is that the transcode verdict is measured against a **software-encoding
host** — which is the host as it actually is today, but not as it could be.


## Result: concurrent LLM serving — the card's best case

TinyLlama-1.1B Q4_K_M, 60 s window, 128 tokens per request with
`--ignore-eos` so token accounting is exact. Host runs 4 workers x 8 threads
(32 of its 36); the card runs one whole model per node, 8 threads each. Both
sides use `llama-cli` from the **same source commit** (`ce8caa6`) — the node
binaries are a native gcc 11 build because the host's is linked against glibc
2.41 and the nodes are 2.35.

| config | aggregate tok/s | energy (J) | tok/J |
|---|---:|---:|---:|
| host alone (E5-2699 v3, 18c/36t) | 82.9 | 10068 | 0.53 |
| **card alone** (3x E3-1585L v5) | **88.4** | 6964 | **0.81** |
| **both** | **172.5** | 14804 | 0.74 |

**Two things stand out.**

**1. The card beats the host outright.** Three 2016 mobile-class Xeons
out-serve an 18-core/36-thread E5-2699 v3 — 88.4 against 82.9 tok/s — while
drawing *less* energy to do it (1.5x the tokens per joule). That is not an
additive-capacity argument; it is a straight win.

**2. Together they more than double host-only throughput**: +89.6 tok/s
(**+108%**) for +47% energy. Marginal efficiency **1.21 tok/J against the
host's own 0.53 — 2.28x**.

### Independent workers scale almost perfectly

The specific claim was "roughly 3x aggregate". Measured, one whole model per
node, 45 s window:

| workers | tok/s | scaling |
|---|---:|---:|
| 1 node | 29.7 | 1.00x |
| 2 nodes | 58.6 | 1.97x |
| **3 nodes** | **88.0** | **2.96x** |

### The contrast that matters

Same hardware, same fabric, opposite outcomes — the difference is entirely
*how the work is split*:

| approach | result |
|---|---|
| **RPC layer-split** — one model across 3 nodes | **2.4x SLOWER** than one node (12.7 vs 30.2 t/s) |
| **Independent workers** — one model per node | **2.96x FASTER** than one node |

Layer-splitting makes every token traverse the pipeline serially, so more
nodes means more hops per token. Independent workers share nothing, so they
scale with node count. **This card is a throughput device, not a latency
device** — and the way to lose is to try to make three computers act like one.

### Caveats

- **TinyLlama-1.1B is small.** It fits comfortably in each node's 15 GB, so
  this measures scheduling and compute, not memory pressure. A 7B+ model per
  node would be a different test; OLMoE-1B-7B Q4 (4.2 GB) would still fit and
  is the obvious next one.
- **No batching.** Each worker is a separate `llama-cli` process with batch
  size 1. A real server (`llama-server` with parallel slots, continuous
  batching) would likely do better on *both* sides, and the ratio could move.
- The host is a 2014 Haswell-EP part. Against a modern CPU the card would
  not win outright — but "beats the host you actually have" is the question
  that decides whether it stays in the slot.

## Can it host a modern MoE? Yes — and MoE is the right architecture for it

MoE suits this card unusually well. Token rate on a CPU is set by **memory
bandwidth ÷ bytes read per token**, and an MoE only reads its *active*
parameters. That turns a weak 4-core Skylake with decent bandwidth into a
reasonable inference node, as long as the whole model fits in RAM.

### The two numbers that decide everything

| | per node |
|---|---|
| usable RAM | **13.8 GiB** (15810 MiB total, 14181 available) |
| raw read bandwidth | **27.5 GB/s** (measured, 8 threads) |
| effective bandwidth in llama.cpp | **~19 GB/s** (72% of raw — normal) |

So: **model must fit in ~12.5 GB** (leaving room for KV cache and runtime),
and `tok/s ≈ 19 / (active_params × bytes_per_param)`.

### Validated against a real MoE

OLMoE-1B-7B Q4_K_M (7B total, 1B active, 4.2 GB), three independent workers:

| | per node | aggregate | scaling |
|---|---:|---:|---:|
| **measured** | 30.4 | **91.3 tok/s** | **2.83x** |
| predicted by the formula | 31.7 | 95.0 | — |

4% error, so the formula is trustworthy for sizing other models.

**Same model, same hardware, 7.1x faster than RPC layer-splitting** (91.3 vs
12.7 t/s). The architecture of the *serving*, not the model, is what decided
that.

### Candidates that fit

| model | active | file | fits? | tok/s/node | x3 nodes |
|---|---:|---:|:---:|---:|---:|
| OLMoE-1B-7B Q4_K_M | 1.0B | 4.2 GB | yes | 31.7 | **95** (measured 91) |
| DeepSeek-V2-Lite Q4_K_M | 2.4B | 10.4 GB | yes | 12.2 | 37 |
| **Qwen3-30B-A3B IQ3_XXS** | 3.0B | 12.2 GB | yes | 15.6 | **47** |
| gpt-oss-20b MXFP4 | 3.6B | 12.8 GB | **tight** | 8.7 | 26 |
| Qwen3-30B-A3B Q4_K_M | 3.0B | 18.6 GB | **no** | — | — |

**The 16 GB per node is the binding constraint, not the compute.** Every one
of these would run acceptably; most do not fit. A 2026-era MoE at a decent
quant wants 20-40 GB, and this card has 16 per node and no way to add more.

Realistic picks:

- **Qwen3-30B-A3B at IQ3_XXS** — a genuinely modern 30B-class MoE at ~47
  tok/s aggregate. The catch is IQ3 on a 30B MoE costs real quality; Q4 would
  be better and does not fit.
- **DeepSeek-V2-Lite Q4** — comfortable fit with headroom, ~37 tok/s
  aggregate, no quantisation compromise. The safe choice.
- **gpt-oss-20b MXFP4** — 12.8 GB against 13.8 available leaves ~1 GB for KV
  and runtime. Plausible at short context, likely OOM otherwise. Worth trying
  only with `--ctx-size` kept small.

### Untested, and stated as such

These are predictions from a validated formula, not measurements. Nothing
above except OLMoE has been downloaded or run. The formula assumes the model
is fully resident and that active-parameter reads dominate — true for decode,
less so for long-prompt prefill, which is compute-bound instead.

### Harness lesson worth keeping

The first attempt at this measured **17.9 tok/s/node** and suggested poor
scaling. That was an artifact: the benchmark loop launches a fresh
`llama-cli` per request, so every request re-loaded a 4.2 GB model. TinyLlama
(668 MB) barely noticed; OLMoE was dominated by it.

`llama-cli`'s reported *Generation* rate excludes load time, so measuring one
long generation per node (`-n 512`) gives the steady-state figure a real
server would see. **Process-per-request benchmarking silently penalises large
models** — if a bigger model appears to scale worse, suspect the harness
before the hardware.

### Measured: modern models that fit (2026-09-22)

Two models measured end to end on the card, three independent workers each.
Everything else in the table above is a prediction from the formula.

| model | file | free/node | tok/s/node | **x3 nodes** | scaling |
|---|---:|---:|---:|---:|---:|
| OLMoE-1B-7B Q4_K_M | 4.20 GB | 10.7 GB | 30.4 | **91.3** | 2.83x |
| **gemma-4-E4B-it Q4_K_M** | 4.98 GB | 9.9 GB | 7.3 | **21.8** | **2.98x** |

> **Correction, 2026-09-22 — what those 21.8 tok/s were spent on.**
> gemma-4-E4B is a **reasoning model**. llama-server routes its chain of
> thought to `message.reasoning_content` and the reply to `message.content`.
> The benchmark above counted generated tokens, and on any prompt that needs
> real thought most of them are reasoning tokens, not answer tokens. The
> *throughput* figure stands — the hardware really does emit 21.8 tok/s — but
> tokens-you-can-use is lower unless thinking is switched off with
> `chat_template_kwargs: {"enable_thinking": false}`.
>
> This is not cosmetic. Left on, it spends the whole `max_tokens` budget
> thinking, returns `finish_reason: "length"`, and hands back `content: ""` —
> an empty string, HTTP 200, no warning. See [GOTCHAS.md](GOTCHAS.md) and
> [../memory/RESULTS.md](../memory/RESULTS.md).

`google/gemma-4-E4B-it` is **official, instruct-tuned, multimodal** (ships an
`mmproj`), and heavily used (4.4M downloads on the base repo, 493k on the
unsloth GGUF). It is the best *quality that fits* found so far.

**"E4B" is effective parameters, not MoE** — MatFormer plus per-layer
embeddings. Measured, it reads **2.61 GB/token = 53% of the 4.98 GB file**.
A truly dense model that size would read all of it and manage only 3.8
tok/s; it gets 7.3. So the effective-parameter design is real and roughly
halves memory traffic, but it does not approach an MoE's ratio — OLMoE reads
0.6 GB/token, which is why it is 4x faster despite being a 7B model.

### Why gemma-4-E4B is the better practical pick over gpt-oss-20b

| | gemma-4-E4B Q4_K_M | gpt-oss-20b MXFP4 |
|---|---|---|
| file | **4.98 GB** | 12.11 GB |
| free per node | **9.9 GB** | 1.3 GB |
| aggregate | **21.8 tok/s (measured)** | ~25 tok/s (predicted) |
| context headroom | large | very tight |
| multimodal | **yes** | no |
| can go up a quant | **yes — Q8_0 is 8.19 GB and still fits** | no |

Similar throughput, a quarter of the memory. The headroom is the real
argument: gpt-oss-20b at 12.11 GB leaves ~1.3 GB for KV cache, which caps
context hard. gemma-4-E4B leaves 9.9 GB, and **Q8_0 (8.19 GB) also fits** —
near-lossless quality at an estimated ~13 tok/s aggregate, which no other
candidate here can offer.

### The general lesson for this card

Ranked by what actually decides throughput:

1. **Bytes read per token**, which is active params x bytes per param — not
   total parameter count.
2. **Fit**, at 14.87 GB usable per node.
3. Everything else.

An MoE with ~1B active (OLMoE) hits 91 tok/s aggregate. A 4B-effective dense-ish
model hits 22. A 3B-active MoE would land near 25-30. **Total model size
barely matters except for whether it fits** — a 7B MoE is 4x faster than a
4B effective-param model here.

## Result: embeddings and retrieval

The retrieval stack is arguably this card's best-fitting workload. Embedding a
corpus is pure throughput with no latency sensitivity, and a reranker scoring
100 candidate documents is 100 completely independent jobs.

### What a retrieval stack is (two models, different jobs)

- **Embedder (bi-encoder)** turns text into a vector. Embed the corpus once,
  embed the query, take nearest neighbours. Fast, scales to millions of docs,
  but approximate — a whole document is compressed into one fixed vector.
- **Reranker (cross-encoder)** scores a (query, document) **pair together**,
  reading both with full attention. Much more accurate, far too slow to run
  over a whole corpus — so it only rescores the embedder's top ~100.

Standard pipeline: embedder retrieves top-100 → reranker rescores → top-5 to
the LLM. A reranker is optional but is usually the single biggest quality win
in a RAG system.

### Measured: Qwen3-Embedding-0.6B Q8_0 (0.64 GB)

256 documents, batches of 32, via `llama-server` with the model resident:

| | docs/s | scaling |
|---|---:|---:|
| 1 node | 6.90 | — |
| **3 nodes** | **20.18** | **2.92x** |

Consistent with every other independent-worker measurement here (2.83, 2.92,
2.96, 2.98) — the card scales linearly whenever the work does not need to be
coordinated.

### The 8B embedder fits — and is not worth using

It was tempting to conclude that because most deployments are VRAM-limited to
300M-600M embedders and this card has 14.87 GB per node, the obvious move was
to run `Qwen3-Embedding-8B` at Q8_0 (8.05 GB). **Measured, that is wrong.**

| model | file | docs/s/node | x3 nodes |
|---|---:|---:|---:|
| Qwen3-Embedding-0.6B Q8_0 | 0.64 GB | **6.90** | **20.18** |
| Qwen3-Embedding-8B Q8_0 | 8.05 GB | 0.54 | 1.59 |

**12.8x slower for a 13.3x parameter increase** — almost exactly
proportional. That ratio is the finding:

> **Embedding is prefill, so it is COMPUTE-bound, not memory-bandwidth-bound.**
> Decode reads each weight once per token, so its speed tracks file size and
> quantisation helps enormously. Prefill does full matmuls against every
> weight for every token, so its speed tracks *parameter count*, and
> quantisation barely helps at all.

This is the opposite of the rule established for text generation elsewhere in
this file, and applying the decode rule to embeddings gives the wrong answer.

What it means in practice — time to embed 100k documents across all three
nodes:

| | rate | 100k docs |
|---|---:|---:|
| 0.6B | 20.18 docs/s | **1.4 hours** |
| 8B | 1.59 docs/s | 17.5 hours |

**Use the 0.6B, or at most the 4B.** The RAM headroom is real but it buys
context and batch size, not the ability to run a big embedder usefully. The
reranker (0.64 GB) fits alongside comfortably either way, so a full retrieval
stack per node still works — just not with the 8B.

Sizes, all verified:

| model | Q8_0 |
|---|---:|
| embeddinggemma-300M | 0.33 GB |
| Qwen3-Embedding-0.6B | 0.64 GB |
| Qwen3-Embedding-4B | 4.28 GB |
| **Qwen3-Embedding-8B** | **8.05 GB** |
| Qwen3-Reranker-0.6B | 0.64 GB |
| bge-reranker-v2-m3 | 0.64 GB |

### Two failures worth recording

**1. Process-per-request destroys large-model benchmarks.** Launching
`llama-embedding` once per 32-doc chunk reloads the model every time, and
those reloads hit the *shared* BlockIO path back to the host. Measured
against a persistent `llama-server`:

| | per-invocation | persistent server |
|---|---:|---:|
| 1 node | 5.67 docs/s | **6.90** |
| 3 nodes | 14.02 docs/s | **20.18** |
| scaling | 2.47x | **2.92x** |

The same artifact made OLMoE look like it scaled 1.67x, and made the 8B
embedder look like **0.45 docs/s** with a reported scaling of **3.20x** —
above the theoretical maximum of 3.0, which is the tell that a measurement is
load-dominated rather than compute-dominated.

**2. A 256-line batch OOM'd a node and killed sshd.** Embedding models are
non-causal, so an entire batch must fit in one ubatch. 256 documents against
the 8.05 GB model exhausted the 15.8 GB node; the OOM killer took sshd, and
the node pinged but refused connections.

Recovery is worth knowing: `vcactl reset` **correctly refuses** while a
BlockIO device is mounted RW ("may lead to data corruption... reset in a
graceful way, or invoke with --force"). The right move is **not** `--force`
— it is `vcactl pwrbtn-short`, which sends ACPI and lets systemd shut down
cleanly even with sshd dead. Recovered with no filesystem damage.

Keep embedding batches to 32 documents at 2048 context on a 16 GB node.
