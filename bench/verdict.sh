#!/bin/bash
# SPDX-License-Identifier: MIT
# Turn a bench JSON into the only number that matters: the MARGIN.
#
#   marginal throughput = both - host      (what the card actually adds)
#   marginal watts      = both_W - host_W  (what that addition costs)
#
# A card that adds 60% more throughput for 40% more watts earns its slot.
# One that adds 10% for 40% does not.
#
#   ./verdict.sh results/bench-*.json
set -uo pipefail
J="${1:?usage: verdict.sh <bench.json>}"
[ -f "$J" ] || { echo "no such file: $J" >&2; exit 1; }

python3 - "$J" <<'PY'
import json, sys
from collections import defaultdict

runs = json.load(open(sys.argv[1]))["runs"]
by = defaultdict(lambda: defaultdict(list))
unit = {}
for r in runs:
    by[r["workload"]][r["config"]].append(r)
    unit[r["workload"]] = r.get("unit", "units")

def avg(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else 0.0

print()
for w, cfgs in by.items():
    u = unit[w]
    print(f"  {w}  ({u})")
    print(f"    {'config':<6} {'throughput':>12} {'secs':>7} {'joules':>9} {u.split('/')[0]+'/J':>12}")
    for c in ("host", "card", "both"):
        if c not in cfgs:
            continue
        t = avg(cfgs[c], "throughput")
        sec = avg(cfgs[c], "secs")
        j = avg(cfgs[c], "j_total")
        # work done in the window / energy spent in the window
        fpj = (t * sec / j) if j else 0
        print(f"    {c:<6} {t:>12.1f} {sec:>7.1f} {j:>9.0f} {fpj:>10.2f}")

    if "host" in cfgs and "both" in cfgs:
        h, b = cfgs["host"], cfgs["both"]
        ht, hs, hj = avg(h, "throughput"), avg(h, "secs"), avg(h, "j_total")
        bt, bs, bj = avg(b, "throughput"), avg(b, "secs"), avg(b, "j_total")
        # Guard the comparison: identical windows are the whole point.
        if hs and bs and abs(bs - hs) / hs > 0.15:
            print(f"    !! WINDOWS DIFFER ({hs:.0f}s vs {bs:.0f}s) -- margin below is NOT trustworthy.")
            print(f"       Set a fixed run duration in the workload before believing it.")
        dt = bt - ht
        dj = bj - hj
        pct_t = (dt / ht * 100) if ht else 0
        pct_j = (dj / hj * 100) if hj else 0
        print()
        print("    MARGIN the card adds on top of an already-busy host:")
        print(f"      throughput  {dt:+.1f} {u}   ({pct_t:+.0f}%)")
        print(f"      energy      {dj:+.0f} J        ({pct_j:+.0f}%)")
        if dj > 0 and dt > 0:
            marg = dt * bs / dj              # extra frames per extra joule
            hostfpj = (ht * hs / hj) if hj else 0
            unit_j = u.split('/')[0] + '/J'
            print(f"      marginal    {marg:.2f} {unit_j}", end="")
            if hostfpj:
                print(f"   vs the host's own {hostfpj:.2f} {unit_j}  ({marg/hostfpj:.2f}x)")
                print()
                if marg >= hostfpj:
                    print(f"      -> Every extra {u.split('/')[0]} the card adds costs LESS energy")
                    print("         than the host's own. Keep it.")
                elif pct_t >= 25:
                    print("      -> Less efficient per joule than the host, but a real capacity")
                    print("         increase. Worth keeping if you need the throughput.")
                else:
                    print("      -> Small gain at real energy cost. Hard to justify on this alone.")
            else:
                print()
        elif dt <= 0:
            print("      -> The card added no throughput here. Contention, or the wrong workload.")
        # a card that beats the host outright is worth calling out explicitly
        if "card" in cfgs:
            ct = avg(cfgs["card"], "throughput")
            if ct > ht:
                print()
                print(f"      NOTE: the card ALONE ({ct:.1f} {u}) beats the host alone"
                      f" ({ht:.1f} {u}).")
    print()

print("  Caveats that matter when reading the above:")
print("    - Work per JOULE is the honest metric; average watts are only")
print("      meaningful because every config runs an identical fixed window.")
print("    - Energy is RAPL package domains only, on BOTH sides. Not wall power.")
print("      Host excludes DRAM/platform; card excludes the PLX switches, DDR4")
print("      and VRM losses. Both are understated. See bench/lib/power.sh.")
if any(w == "transcode" for w in by):
    print("    - The host's RTX 3070 is on nouveau, so NVENC is unavailable, and")
    print("      the driver will not build on this kernel (see docs/BENCHMARK.md).")
PY
