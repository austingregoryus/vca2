<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# VCA2 fan shrouds — parameter reference

> **These are unfinished.** Only the 120 mm v1 shroud has been measured: with
> 2×120 mm fans on card 0 it took the all-node AVX2 peak from 92-94 C to
> 75-79 C (−15 to −18 C; [docs/THERMAL.md](../docs/THERMAL.md#after-the-cooling-upgrade-2026-09-22)).
> The 80 mm and 92 mm designs, and 92 mm v2, are unmeasured; an 80 mm
> face-exit part was printed and test-fitted only. The thermal target also
> turned out to be wrong after they were designed. Read [`README.md`](README.md) in this folder
> before printing anything. This file is the parameter reference only.


Friction-fit fan shrouds for the Intel VCA 2 (VCA1585LMV). One parametric
source file, `vca2_fan_shroud.scad`, produces every STL here.

The socket copies the fit of `Fan-mount-2x40mm-v1.4.stl`, whose card end
measures 96.0 x 40.0 mm inside, 22 mm deep. The card shell is 96.3 x 38.69 mm,
so height is a 0.3 mm interference fit and width is taken up by crush ribs.

## Which file

Both ends of the card take the same socket, so the choice is only about how
the power cables get out and whether the support plate is in the way.

| File | Use |
|---|---|
| `vca2_<size>mm_shroud.stl` | Cables exit through the card's top and bottom edges |
| `vca2_<size>mm_shroud_faceexit.stl` | Both cables exit through one wide face |
| `vca2_<size>mm_shroud_bracket.stl` | Edge exit, plus one edge wall fully open to clear the L-shaped support plate |

Sizes are 80, 92 and 120.

| Fan | Flange | Hole pitch | Aperture | Loft | Total length |
|---|---|---|---|---|---|
| 80 | 85 mm | 71.5 mm | 80 mm | 45 mm | 73.5 mm |
| 92 | 94 mm | 82.5 mm | 88 mm | 55 mm | 83.5 mm |
| 120 | 122 mm | 105 mm | 116 mm | 70 mm | 98.5 mm |
| 92 v2 | 94 mm | 82.5 mm | 88 mm | 110 mm (bell) | 138.5 mm |

The `_v2` files use `loft="bell"`; see the v2 section of [`README.md`](README.md).

## Printing

Flange face down on the bed, no supports. v1: walls are 2.0 mm at the socket
and no less than 2.3 mm through the loft. v2 (bell): 2.0 mm throughout, worst
overhang 17.5 degrees, 138.5 mm tall on the bed.

## Parameters worth touching

Open `vca2_fan_shroud.scad` and re-export with OpenSCAD.

- `fan` — 80, 92 or 120.
- `loft` — `"straight"` (v1, default) or `"bell"` (v2: sliced loft, uniform
  `loft_wall`, eased in at the socket). `loft_len` overrides the table's loft
  length; v2 uses 110 at 92 mm. `loft_ease` (0.15) and `loft_slices` (48) tune
  the bell. A bell render takes ~2.5 min in OpenSCAD 2021.01 (CGAL).
- `rib_h` — crush rib height, default 0.5, giving 39.0 mm across a 38.69 mm
  card. Drop to 0.3 if the socket is too tight, raise to 0.7 if it is loose.
- `sock_depth` — 25 mm of grip. More is more secure, but check clearance.
- `cable_slots` — false gives a plain sleeve with no cable openings.
- `slot_side` — `"edge"` or `"face"`.
- `slot_depth` — 40 mm. Raise it if a plugged-in cable cannot clear the slot.
- `slot_w` — 22 mm, edge mode. The 2x4 Mini-Fit plug is about 18 mm wide.
- `face_slot_len` — 28 mm, face mode.
- `bracket_clear` — true opens one edge wall for the support plate.
- `bracket_w` — 0 removes the whole edge wall and always fits. Set it to the
  measured plate width plus a couple of mm to leave corner tabs and keep a
  tighter grip. `bracket_off` shifts that notch across the card thickness.

Command line, without editing the file:

```
openscad -D fan=92 -D 'slot_side="face"' -o out.stl vca2_fan_shroud.scad
```

## Airflow

Intel specifies 18 CFM at 0.246 in H2O for a 35 C inlet, and 25.5 CFM at
0.476 in H2O at 45 C. Ordinary case fans top out near 0.1 in H2O, so use
high static pressure server fans. Cores must stay at or below 96 C; they
throttle at 100 C. Check with `vcactl temp`.

### Measured heat load (2026-09-21, stock airflow, before any shroud)

Full method and data in [docs/THERMAL.md](../docs/THERMAL.md). Short version:

| Load, all three nodes | Card package power | Hottest core |
|---|---:|---:|
| idle | ~12 W | 40 C |
| all three iGPUs (GEMM) | ~134 W | **76 C** |
| iGPU + CPU | ~123 W | **92 C** |
| **all three CPUs, AVX2** | ~130 W | **92-94 C** |

Plus the PEX switch, DRAM and VRM losses, so budget **135-180 W** of heat for
compute loads, not the 235 W card TDP.

Three things that should drive the design:

1. **Design for the CPU case, not the GPU case.** 39 W concentrated in four
   cores runs ~20 C hotter than 35 W spread across the iGPU at the same
   package power. Every earlier temperature note in this project came from
   GPU or LLM work and peaked at 49-54 C, which is why this was invisible.
   At 92-94 C the card is 2-4 C from Intel's 96 C limit *with the chassis
   airflow it has today*.
2. **Bias flow toward nodes 1 and 2.** They ran 4-6 C hotter than node 0 in
   every phase. If the shroud is asymmetric, favour that end.
3. **Raw CFM matters less than getting air through the stack.** Going from
   one loaded node to all three only added 4-6 C, so the dominant thermal
   resistance is die-to-heatsink per node, not heatsink-to-air across the
   card. Static pressure through the fins beats bulk volume.

**Benchmark to test a fitted shroud:** `compute/burn.c` on all three nodes
(`docs/THERMAL.md` has the harness). The peak core temperature in the `cpu`
phase is the number that says whether the shroud works. Re-run
`compute/thermal_run.sh` after fitting and compare against the table above.
For the 120 mm v1 shroud that number is 75-79 C against 92-94 C.

A software backstop, in case the shroud is marginal: each node's RAPL package
limit at `/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw` is
45 W and writable. Dropping it to ~35 W caps the AVX2 case well below 96 C
and cannot fail the way a fan can. In use since 2026-09-29 on the second card,
which has only 2×80 mm fans and very likely lost a node to heat: PL1 35 W / PL2 40 W,
`scripts/card1_power_cap.sh` ([docs/THERMAL.md](../docs/THERMAL.md#software-mitigation-worth-considering)).

Intel's standard pattern is air in at the plug end and out through the I/O
bracket end. Running a shroud at each end as push and pull suits this card,
since the fin stack is dense and wants pressure more than volume — but the v1
profiles are likely to stall as pullers (README, problem 3), so push-only is
the safer use of them.
