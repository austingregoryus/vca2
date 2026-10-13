<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Thermal profile raw data (2026-09-21)

Columns: `t_s,phase,pkg_w,core_w,uncore_w,dram_w,tmax_c,tpkg_c,gt_mhz`

`uncore_w` is the iGPU RAPL domain. `tmax_c` is the hottest `Core N` line
from `sensors`; `tpkg_c` is `Package id 0`.

| File | Run |
|---|---|
| `solo_n0.csv` | node 0 loaded alone, full profile, completed |
| `all3_n0.csv` | all three loaded, node 0, tripped at 88 C in the cpu phase |
| `all3_n1.csv` | all three loaded, node 1, tripped at 92 C |
| `all3_n2.csv` | all three loaded, node 2, tripped at 94 C (file truncated by a restart; the trip line is the useful record) |

See [../../THERMAL.md](../../THERMAL.md).
