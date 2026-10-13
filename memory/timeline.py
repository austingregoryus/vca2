# SPDX-License-Identifier: MIT
"""Reconstruct how busy the three nodes actually were during a past run.

    python3 timeline.py --since 2026-09-22T07:32:55 --until 2026-09-22T08:45:10

Every gemma-4 request leaves a line in its node's journal:

    2026-09-22T07:31:04.127792+0000 ... print_timing: ... total time = 47390.06 ms / 730 tokens

That is an end timestamp and a duration, so each request is an exact busy
interval on a known node. From the three journals this rebuilds, for the whole
run: what fraction of wall time had 0, 1, 2 or 3 nodes working, and how the
work splits between extraction calls (long prompts, many tokens) and
reconcile calls (a short classification of one fact against another).

Node clocks are UTC and agree with each other to well under a second, which
is far finer than the 5-60 s requests being placed.
"""

import argparse
import os
import re
import subprocess
from datetime import datetime

# Intel's documented VCA node default, overridable like every other script
# in this repo (bench/, scripts/, services.sh). Never a host credential.
NODE_PW = os.environ.get("NODE_PW", "vista1")
LINE = re.compile(r"^(\S+)\s.*total time =\s+([\d.]+) ms /\s+(\d+) tokens")


def fetch(node_ip, since, until):
    cmd = (f"journalctl -u sm-llm --since '{since.replace('T', ' ')}' "
           f"--until '{until.replace('T', ' ')}' -o short-iso-precise "
           f"--no-pager | grep 'total time'")
    r = subprocess.run(["sshpass", "-p", NODE_PW, "ssh", "-o",
                        "StrictHostKeyChecking=no", node_ip, cmd],
                       capture_output=True, text=True, timeout=120)
    out = []
    for line in r.stdout.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        end = datetime.fromisoformat(m.group(1).replace("+0000", "+00:00"))
        dur = float(m.group(2)) / 1000.0
        out.append((end.timestamp() - dur, end.timestamp(), int(m.group(3))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--split-tokens", type=int, default=600,
                    help="requests at or above this many tokens are extraction")
    a = ap.parse_args()

    nodes = {f"node{i}": fetch(f"172.31.{i + 1}.1", a.since, a.until)
             for i in range(3)}
    t0 = datetime.fromisoformat(a.since + "+00:00").timestamp()
    t1 = datetime.fromisoformat(a.until + "+00:00").timestamp()
    wall = t1 - t0

    print(f"window {wall / 60:.1f} min\n")
    print("  node    requests  extract  reconcile  busy time   busy %")
    for name, reqs in nodes.items():
        ext = [r for r in reqs if r[2] >= a.split_tokens]
        rec = [r for r in reqs if r[2] < a.split_tokens]
        busy = sum(e - s for s, e, _ in reqs)
        print(f"  {name}  {len(reqs):8d} {len(ext):8d} {len(rec):10d} "
              f"{busy / 60:7.1f} min {100 * busy / wall:7.1f}%")

    # Concurrency over time, sampled every 0.5 s.
    step = 0.5
    hist = [0.0, 0.0, 0.0, 0.0]
    kinds = {"extract-only": 0.0, "reconcile-only": 0.0, "mixed": 0.0}
    t = t0
    while t < t1:
        active = [r for reqs in nodes.values() for r in reqs if r[0] <= t < r[1]]
        hist[min(len(active), 3)] += step
        if active:
            ext = any(r[2] >= a.split_tokens for r in active)
            rec = any(r[2] < a.split_tokens for r in active)
            kinds["mixed" if ext and rec else
                  "extract-only" if ext else "reconcile-only"] += step
        t += step

    print("\n  nodes working at once    share of wall time")
    for k, v in enumerate(hist):
        bar = "#" * int(round(40 * v / wall))
        print(f"        {k}                {100 * v / wall:5.1f}%  {v / 60:5.1f} min  {bar}")

    all_reqs = [r for reqs in nodes.values() for r in reqs]
    ext = [r for r in all_reqs if r[2] >= a.split_tokens]
    rec = [r for r in all_reqs if r[2] < a.split_tokens]
    ext_s = sum(e - s for s, e, _ in ext)
    rec_s = sum(e - s for s, e, _ in rec)
    print(f"\n  extraction: {len(ext)} calls, {ext_s / 60:.1f} node-min, "
          f"median {sorted(e - s for s, e, _ in ext)[len(ext) // 2]:.1f} s")
    if rec:
        print(f"  reconcile:  {len(rec)} calls, {rec_s / 60:.1f} node-min, "
              f"median {sorted(e - s for s, e, _ in rec)[len(rec) // 2]:.1f} s")
    print(f"  wall time with only reconcile running: "
          f"{kinds['reconcile-only'] / 60:.1f} min")

    ideal = (ext_s + rec_s) / 3
    print(f"\n  total node-work {(ext_s + rec_s) / 60:.1f} node-min; "
          f"spread perfectly over 3 nodes that is {ideal / 60:.1f} min "
          f"against {wall / 60:.1f} min actual")


if __name__ == "__main__":
    main()
