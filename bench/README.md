<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# bench/ — additive-capacity benchmark

Answers one question: with the host already busy, does adding the card get
more work done per watt? Every workload runs three ways — `host` alone,
`card` alone, and `both` at once — and the verdict is the margin
(`both - host` in throughput, `both_W - host_W` in watts). Method, results
and caveats: [docs/BENCHMARK.md](../docs/BENCHMARK.md).

| File | Role |
|---|---|
| `vca-bench.sh` | Driver. `--workload transcode\|llm\|all`, `--config host\|card\|both\|all`, `--reps N`, `--list`. Writes JSON to `results/`. |
| `verdict.sh` | Turns a results JSON into the marginal-throughput / marginal-watt verdict. |
| `lib/power.sh` | RAPL energy sampling, host package plus the node packages. Read-only; writes no power cap. |
| `workloads/transcode.sh` | Concurrent 1080p30 encodes: libx264 on the host, Quick Sync (VAAPI) on each node. |
| `workloads/llm.sh` | Concurrent llama.cpp generation, one independent worker per node, fixed wall-clock window. |

```bash
# on the host, as root, with card 0 booted
./vca-bench.sh --workload all --reps 3
./verdict.sh results/bench-*.json
```

Notes:

- It drives **card 0 only** (nodes 0–2, `172.31.1-3.1`); the node list is
  hard-coded. Needs `sshpass`; `NODE_PW` defaults to Intel's documented node
  password.
- Watt figures are RAPL package counters on both sides, not wall power. The
  limits are spelt out at the top of `lib/power.sh`; read them before quoting
  a number.
- The host-side comments describe the host as it was when the benchmark was
  written (RTX 3070 on `nouveau`, so no NVENC).
