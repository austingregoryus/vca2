<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Thermal and power envelope (measured 2026-09-21)

First real power numbers for this card. Everything before this was short
GEMM bursts; these are steady-state loads held for two minutes with RAPL
energy counters sampled every 5 s.

> ## Superseded by the 2026-09-22 cooling upgrade
>
> The measurements below are the **stock-airflow baseline** for card 0. After
> fitting this repo's printed 120 mm shroud (v1, `shrouds/vca2_fan_shroud.scad`,
> 2×120 mm fans) the same synchronized all-three-node AVX2 load peaks at
> **75-79 °C instead of 92-94 °C**. The final finding on card 0 is that
> **neither temperature nor the 45 W cap binds**: real CPU+GPU work draws
> 42.6 W per node and never reaches the cap; what costs it is the iGPU
> evicting the CPU's working set from the shared eDRAM/LLC
> ([HETERO re-test](#hetero-re-test-2026-09-22)). An intermediate version of
> this banner said the 45 W cap was the binding constraint; that was wrong.
> On card 1, cooled by 2×80 mm fans, **heat is binding** — one node was very
> likely lost to it — so its nodes are capped at 35 W
> ([below](#software-mitigation-worth-considering)). The baseline is kept
> because it is what the shroud work was designed against and what the
> improvement is measured from.

**Headline (stock airflow): the binding thermal case is an AVX2 CPU load, not
the GPU.** With all three nodes running AVX2, cores reached **92-94 °C** —
Intel's limit is 96 °C and throttle is 100 °C. A GPU load on all three tops
out at 76 °C.

**Ongoing history (since 2026-10-09):** every node's temperature, power and
throttle events are logged every 30 s by [`monitor/`](../monitor/README.md);
`vcamon report --daily` on the host.

Raw CSVs: `docs/artifacts/thermal/`. Harness: `compute/thermal_run.sh`,
`compute/gpuburn.c`, `compute/burn.c`.

## What was run

Each node, locally, with a hard safety trip that kills all load if any core
reaches the limit (enforced on the node, so a dropped SSH cannot leave it
running):

| Phase | Load | Duration |
|---|---|---|
| idle | none | 40 s |
| gpu | `gpuburn` — tuned GEMM re-enqueued continuously | 120 s |
| gpu+cpu | `gpuburn` + `burn` (8 threads of AVX2 FMA) | 120 s |
| cpu | `burn` only | 120 s |
| cool | none | 90 s |

`burn` is a pure register FMA chain with no memory traffic — the realistic
worst case for CPU power density.

## Steady-state results

Per node. "solo" = only node 0 loaded; "all three" = every node loaded.

| Phase | | pkg W | core W | iGPU W | T core | T pkg | | pkg W | core W | iGPU W | T core | T pkg | ΔT |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| idle | solo | 3.7 | 0.1 | 0.0 | 44 | 44 | all3 | 3.9 | 0.4 | 0.0 | 40 | 40 | −4 |
| gpu | solo | 44.8 | 3.3 | 35.1 | 64 | 72 | all3 | 44.8 | 3.2 | 35.2 | **68** | **76** | +4 |
| gpu+cpu | solo | 41.4 | 9.5 | 25.6 | 69 | 71 | all3 | 41.1 | 9.5 | 25.1 | **75** | **78** | +6 |
| cpu | solo | 38.8 | 34.7 | 0.0 | 82 | 82 | all3 | 43.4 | 39.1 | 0.0 | **88** | **88** | +6 |

Peak core temperature during the all-three run:

| Node | idle | gpu | gpu+cpu | cpu |
|---|---:|---:|---:|---:|
| 0 | 40 | 73 | 75 | **88** |
| 1 | — | 70 | 92 | **92** |
| 2 | — | — | — | **94 (tripped)** |

Nodes 1 and 2 run consistently hotter than node 0. That gradient is stable
across phases and is worth knowing when aiming a shroud.

## Five things this changes

### 1. The CPU is the thermal problem, not the GPU

39 W in four small cores runs **~20 °C hotter** than 35 W spread across the
GT4e die, at essentially the same package power. Every previous thermal note
in these docs came from GPU or LLM work and topped out at 49-54 °C, which is
why this never showed up. Size cooling for the AVX2 case.

### 2. The 45 W package limit makes CPU and GPU compete

> **Corrected 2026-09-22:** true only for a pure-ALU load like `burn`. Real
> GPU+CPU work never reaches the cap and competes for cache instead — see
> [HETERO re-test, Result 3](#result-3--they-compete-for-cache-not-watts).

RAPL caps each node's package at 45 W and the card enforces it:

| Load | core W | iGPU W | total |
|---|---:|---:|---:|
| GPU only | 3.2 | **35.2** | 44.8 |
| GPU + CPU | 9.5 | **25.1** | 41.1 |

Adding CPU work does not add power, it **reallocates** it — the iGPU loses
10 W and slows down. This is the physical explanation for the `HETERO`
result in [SIX_DEVICES.md](archive/SIX_DEVICES.md), where giving the CPU rows made
the whole thing slower. That was read as "the CPU is ~1/5 the GPU, so don't
steal its rows." The deeper reason is that CPU work literally takes watts
away from the iGPU. `HADD` only worked because the extra CPU slice was small
enough to fit in the headroom.

**Consequence:** there is no configuration where all six devices run flat
out. Per node it is ~45 W total, split however you choose. Plan the split as
a power budget, not as two independent devices.

### 3. Card-level power

Steady state, summing the three node packages:

| Load | 3x package | Note |
|---|---:|---|
| idle | ~12 W | |
| all GPU | ~134 W | |
| all CPU | ~130 W | |

Plus the PEX 8717, DRAM, VRM losses and the iGPU's share of DRAM — consistent
with the 235 W card TDP in the Intel guide. Cooling has to move roughly
**135-180 W**, not 235 W, for compute loads.

### 4. The ramp is fast

Node 0 went from 66 °C to 86 °C in **six seconds** when the AVX2 phase
started — roughly 3 °C/s. The 5 s sampling interval was enough for nodes 1
and 2 to overshoot an 88 °C trip point and be caught at 92 and 94 °C.

There is very little thermal mass between these dies and the sensor. Any
protective throttling has to react in ~1 s, not 10 s.

### 5. Going from one loaded node to three only costs 4-6 °C

That is much less than 3x the heat would suggest, and it says the dominant
thermal resistance is **die-to-heatsink on each node**, not heatsink-to-air
across the card. Useful for the shroud: raw CFM matters less than getting
air actually through the fin stack over each node. It also means node 2 being
hottest is a local airflow or mounting effect, not neighbours cooking it.

## What this means for the shrouds

The card currently reaches **92-94 °C on an all-CPU load** with whatever
airflow the chassis already provides — 2-4 °C from Intel's 96 °C core limit.
So:

- The shrouds are genuinely needed, and they should be designed and tested
  against the **CPU AVX2** case, not a GEMM burst. `compute/burn.c` on all
  three nodes is the benchmark to re-measure against once a fan is fitted.
- Intel's spec is 18 CFM at 0.246 in H2O (35 °C inlet) and 25.5 CFM at
  0.476 in H2O (45 °C). The static pressure number is the one that matters;
  see [`../shrouds/REFERENCE.md`](../shrouds/REFERENCE.md).
- Bias flow toward nodes 1 and 2. They ran 4-6 °C hotter than node 0
  throughout.
- Re-run this profile after fitting a fan. The delta on the `cpu` phase peak
  is the number that says whether the shroud works. (Done for the 120 mm
  shroud on card 0: −15 to −18 °C, [below](#after-the-cooling-upgrade-2026-09-22).)

## Software mitigation worth considering

Each node's package limit is writable:

```
/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw   # 45000000
```

Dropping it to ~35 W would cap the AVX2 case well below 96 °C at a modest
performance cost, and unlike a fan it cannot fail. Untested here at the time
(2026-09-21) — it is a live write on a running node and was left alone.

**Now in use on card 1 (2026-09-29).** The second card has 2×80 mm fans
instead of card 0's 2×120 mm cooling, which is this repo's printed 120 mm
shroud (v1). Card 1's node logs, read back from the disk
images, show that the render farm held it at the thermal limit:

| Node | Cooling | Kernel throttle events since 2026-09-21 |
|---|---|---:|
| card 0, nodes 0–2 | 2×120 mm | 0 |
| node 4 (card 1 cpu 1, 172.31.5.1) | 2×80 mm | 3 |
| node 5 (card 1 cpu 2, 172.31.6.1) | 2×80 mm | 6,531 |
| node 3 (card 1 cpu 0, 172.31.4.1) | 2×80 mm | ~1,890, then dead |

Node 3 (card 1 cpu 0) logged package and core throttling on every CPU at
13:46:48 on 2026-09-28. The host logged a DMA hang 34 s later. Since then the node never
reaches BIOS: its PCIe link on switch port `0a:08` stays at x0. That survived a
node power cycle, a card reset and a host power-off (see GOTCHAS). A thermal
trip that only clears with standby power removed would have fitted; **tested
2026-09-29 22:42 and ruled out** — unplugging the host from the wall for 60 s
did not bring the node back (LOG, 2026-09-26 → 10-09 entry). With the RAM
reseat and repaste below also failing, the likeliest explanation is heat
damage to the node. The one idea left is swapping its SO-DIMMs with a working
sibling's.

`scripts/card1_power_cap.sh` sets PL1 to 35 W and PL2 to 40 W on the live card-1
nodes. It uses a `rapl-cap.service` unit on each node's own disk, so the cap
survives reboots. A 180 s all-core load on node 5 (cpu 2) with the cap in place:
package max **81 °C**, 37 W average, **0** throttle events. Remove the cap
(`systemctl disable --now rapl-cap` on the node, then reboot it or write
45000000 / 56250000 back) only once card 1 has the 120 mm shroud.

**After repasting card 1 and reseating its RAM (2026-09-30).** Both live nodes
were loaded together for 180 s, as a render does:

| Card 1 | node 4 (cpu 1, 172.31.5.1) | node 5 (cpu 2, 172.31.6.1) |
|---|---:|---:|
| PL1 35 W / PL2 40 W | 71 °C | 80 °C |
| PL1 45 W / PL2 56 W (stock) | 83 °C | **94 °C** |

There were no throttle events in either run. The test aborts at 95 °C and always
restores the cap. At stock limits node 5 still sits 2 °C under Intel's 96 °C
spec, so its position on the card simply gets the least air from the 80 mm fans.
The cap stays until the 120 mm shroud. Node 3 (cpu 0) stayed dead after the reseat.

## Sustained GPU performance does not throttle

Separately worth recording: the tuned GEMM held **455.1 / 455.2 / 455.4 /
455.5 GFLOP/s** across four consecutive 10 s windows at 1150 MHz, with the
package pinned at its 45 W limit the whole time. The P580 does not throttle
under sustained load — burst and sustained numbers are the same. See
[GEMM_WORKGROUP.md](GEMM_WORKGROUP.md).

## Safety notes for re-running

- `thermal_run.sh` enforces the trip **on the node**. Do not move that check
  to the host; an SSH drop would leave the load running.
- Every load is additionally wrapped in `timeout`.
- Trip at 85 °C, not 88 — the observed overshoot between samples was 4-6 °C.
- `pgrep -f gpuburn` from a shell whose own command line contains "gpuburn"
  matches itself. Match on `ps -eo comm=` instead (same trap as the
  `pkill -f` warning in [GOTCHAS.md](GOTCHAS.md)).


## After the cooling upgrade (2026-09-22)

Active cooling fitted to card 0: this repo's printed 120 mm shroud, v1 from
[`shrouds/vca2_fan_shroud.scad`](../shrouds/vca2_fan_shroud.scad), with
2×120 mm fans (the LOG called it "super-cooler-5000"; the owner has since
confirmed it is the repo's shroud). So the deltas below are a measurement of
that shroud in service. Same harness, same loads, but
this time with a **synchronized start** so all three nodes are in the same
phase simultaneously — the first attempt launched sequentially over SSH and
node 0 had finished before node 2 began, which does not reproduce the
all-three-loaded condition the baseline was measured under.

| phase | baseline peak | **after** | delta |
|---|---:|---:|---:|
| idle | ~40 °C | **35-37 °C** | −4 |
| gpu (all 3) | 76 °C | **59-60 °C** | **−16** |
| gpu+cpu | 92 °C | **79-80 °C** | **−12** |
| **cpu, AVX2 (all 3)** | **92-94 °C** | **75-79 °C** | **−15 to −18** |

Headroom against Intel's 96 °C limit went from **2-4 °C to 17-21 °C**.

Package power for the same work fell from ~130 W to **121.4 W** across the
three nodes. That is not a measurement artifact — cooler silicon leaks less,
so roughly 7% of the old draw was static power burned as heat because it was
hot. The cooling paid for a little of itself.

### The constraint moved

| | value |
|---|---|
| RAPL limits, every node | long_term **45 W** / 28 s, short_term 56 W |
| `constraint_0_max_power_uw` | **45 W** — the RAPL driver echoing TDP, not a ceiling (corrected below) |
| cpu phase draw | 39.2 / 40.3 / 41.9 W — **under** the cap |
| gpu+cpu draw | 43.5 / 44.0 / 44.6 W — **at** the cap |
| GT clock, gpu phase | 1150 MHz, min = avg = max — **never throttled** |

~~**Temperature is no longer what limits this card; the 45 W package cap is.**~~
**Corrected 2026-09-22:** temperature no longer limits card 0, but neither
does the cap. The CPU-only case does not reach it, and the combined GPU+CPU
case sits on it only with `burn`, a pure-ALU load. Real GEMM work draws
42.6 W per node and competes for the shared eDRAM/LLC, not watts — see the
[HETERO re-test](#hetero-re-test-2026-09-22).

### Is raising the cap the next move? No -- but not for the reason first given.

**Correction.** An earlier version of this section claimed
`constraint_0_max_power_uw` reporting 45 W meant "the interface advertises no
headroom", and treated the cap as a hard hardware limit. That was wrong on
both counts, and the MSRs say so directly:

```
MSR 0x610 (PKG_POWER_LIMIT)  PL1 = 45.00 W   PL2 = 56.25 W   LOCK bit 63 = 0
MSR 0x614 (PKG_POWER_INFO)   TDP = 45.00 W   MIN = 0 W   MAX = 0 W
sysfs constraint_0_power_limit_uw: writable (no-op write accepted)
```

- **The limit is not locked.** Bit 63 of `MSR_PKG_POWER_LIMIT` is 0, so PL1
  is changeable at runtime and reverts on reset.
- **`max_power_uw = 45 W` is the RAPL driver echoing TDP**, not a silicon
  ceiling. `PKG_POWER_INFO` reports MAX = 0, i.e. the package declares no
  maximum at all.
- **The card has budget.** Intel's guide gives a **235 W card TDP**; three
  node packages account for 135 W of that, and measured compute loads put the
  whole card at roughly 135-180 W including the PLX switches, DRAM and VRM
  losses.

So raising it is *possible*, *reversible*, and within the card's rating.

**It is still not worth doing, for a better reason: nothing is asking for the
watts.** Measured on one node under sustained AVX2:

| | |
|---|---|
| core clock | **3400 MHz** (base 3.0, single-core turbo 3.7) |
| package power | **39.0 W** of a 45 W cap -- 6 W unused |
| hottest core | **62 °C** against a 96 °C limit -- 34 °C unused |

The CPU is neither power- nor thermally limited. It is sitting at its
**all-core turbo multiplier**, which lives in the CPU's firmware turbo table
and is not a RAPL setting. Raising PL1 cannot move it.

The one configuration that *is* cap-bound is **GPU+CPU together**, at
43.5-44.6 W against the 45 W limit. That is the only place a higher PL1 could
buy anything -- and it is exactly the `HETERO` case
[SIX_DEVICES.md](archive/SIX_DEVICES.md) found unproductive, though that was measured
when the CPU was 1.9x slower and 15 °C hotter.

**If it is ever tried**, the defensible envelope is PL1 up to ~56 W -- the
board already supports that as PL2, so it is within an envelope the VRMs were
designed for, rather than an invented one. Pair it with a `HETERO` re-run so
there is a reason for the watts, and revert afterwards.

### What the extra headroom is actually good for

- **Sustained load is now safe.** The old 2-4 °C margin made any long run a
  judgement call. A 17-21 °C margin does not.
- **Re-test `HETERO`.** The CPU is 1.9x faster since the 22.04 image
  ([NODE_IMAGE.md](NODE_IMAGE.md)) and the thermal ceiling is gone, so the
  "giving the CPU work makes things slower" conclusion is worth re-running.
  ~~It will still be power-bound~~ (it was not — see the
  [HETERO re-test](#hetero-re-test-2026-09-22), which ran both experiments
  together).
- **Node 2 is still the hottest** (79 °C vs 75-76 °C), consistent with the
  baseline's 4-6 °C spread, so the airflow bias noted below still applies —
  just with far less at stake.

## HETERO re-test (2026-09-22)

Ran after the cooling upgrade, to settle whether raising PL1 buys anything.
Sustained 40 s loads on all three nodes, `gemm_fast` (iGPU) and `cpu_gemm`
(AVX2, blocked SGEMM), aggregated across the card.

### Result 1 — heterogeneous is now a win

| config | GPU | CPU | combined | power | peak |
|---|---:|---:|---:|---:|---:|
| GPU only | 1250.6 | — | 1250.6 | 57.7 W | 53 °C |
| CPU only | — | 205.1 | 205.1 | 135.1 W | 69 °C |
| **both** | 1240.5 | 102.6 | **1343.1** | 127.8 W | 70 °C |

**GPU+CPU beats GPU-alone by +92 GFLOP/s (+7.4%)**, and the GPU gives up only
0.8% to get it. That reverses [SIX_DEVICES.md](archive/SIX_DEVICES.md)'s `HETERO`
conclusion, which was measured when the CPU ran at ~36 GFLOP/s on 16.04.
At ~70 GFLOP/s per node the CPU is finally worth including.

### Result 2 — raising the power cap does nothing

| config | PL1 = 45 W | PL1 = 56 W | delta |
|---|---:|---:|---:|
| GPU only | 1250.6 | 1249.9 | −0.1% |
| CPU only | 205.1 | 205.4 | +0.1% |
| both | 1343.1 | 1344.8 | +0.1% |
| both, power | 127.8 W | 128.7 W | +0.7% |

All inside noise. The reason is in the power column: `both` draws **42.6 W
per node against a 45 W cap — it never reaches it.** Raising a limit that
nothing is hitting changes nothing.

**Do not raise PL1.** It is unlocked and writable, the card has budget, and
it still buys nothing.

### Result 3 — they compete for cache, not watts

If the combined case is not power-limited, what costs the CPU half its
throughput? A working-set test answers it — if the cause were DRAM bandwidth,
a cache-resident problem would be largely immune:

| CPU working set | alone | with iGPU busy | loss |
|---|---:|---:|---:|
| n=512 → 3 MiB (**fits** the 8 MiB LLC) | 48.86 | 11.20 | **−77%** |
| n=1024 → 12 MiB (**exceeds** it) | 72.19 | 33.62 | −53% |

The **cache-resident case degrades worse**, which is the opposite of a
bandwidth story. The iGPU is evicting the CPU's working set from the shared
eDRAM/LLC. At n=512 the CPU depends entirely on cache residency, so eviction
is catastrophic; at n=1024 it is already going to memory anyway and has less
to lose.

**This corrects a claim carried elsewhere in these docs.** "CPU and GPU
compete for the same 45 W package cap" is true only for a *pure-ALU*
synthetic load: `burn` has no memory traffic and does pin the package at
43.5-44.6 W. Any workload that touches memory hits the cache wall first and
never reaches the cap. Plan the split as a **cache budget**, not a power
budget.

### Practical consequence

Run GPU and CPU together — it is worth +7.4%. But size the CPU side so its
working set does not fight the iGPU for the LLC, and expect the CPU to deliver
roughly half its standalone throughput regardless of how many watts you allow
it.
