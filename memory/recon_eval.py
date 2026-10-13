# SPDX-License-Identifier: MIT
"""Labelled accuracy for the temporal-truth classifier.

    python3 recon_eval.py

The first 12 pairs are synthetic. The last 10 are REAL pairs taken from
supersede decisions the classifier made during the 22-document ingest,
labelled by hand. They exist because the synthetic set scored 12/12 while the
real corpus was quietly losing facts: the classifier was treating "the same
measurement under different conditions" as an update and demoting the first
reading. A test set that only contains cases you already thought of will tell
you everything is fine.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.engine import Engine  # noqa: E402
from smem.models import Models  # noqa: E402
from smem.pool import Pool  # noqa: E402

SYNTHETIC = [
    ("Node 1 DMA writes run at roughly half the rate of node 0 and node 2.",
     "Node 1 DMA write throughput is about half that of nodes 0 and 2.", "DUPLICATE"),
    ("The cooling upgrade dropped package temperature by 15 to 18 C.",
     "After the new cooler the nodes run 15-18 C cooler.", "DUPLICATE"),
    ("The card draws 250 W at the connector.",
     "Rated board power for the card is 250 watts.", "DUPLICATE"),
    ("Each node has 16 GB of RAM.",
     "The nodes are fitted with 16 GB of memory each.", "DUPLICATE"),
    ("The PEX 8749 uplink trains at x8 Gen3.",
     "The PEX 8749 uplink trains at full x16 Gen3 after the riser was reseated.",
     "UPDATE"),
    ("PL1 is set to 45 W on every node.",
     "PL1 is now set to 56 W on every node.", "UPDATE"),
    ("The best node-to-node DMA rate measured is 2900 MiB/s.",
     "The best node-to-node DMA rate measured is 4423 MiB/s.", "UPDATE"),
    ("Kernel 7.0.14-17-pve does not work with the VCA driver.",
     "Kernel 7.0.14-17-pve works with the VCA driver after all.", "UPDATE"),
    ("The card has three Xeon E3-1585L v5 nodes.",
     "Each node carries an Iris Pro P580 GPU.", "DISTINCT"),
    ("The embedder runs at 20.18 docs per second across three nodes.",
     "The gemma-4 model file is 4.98 GB on disk.", "DISTINCT"),
    ("Node 2 sits behind a second PEX 8717 switch.",
     "libelf-dev is required to build kernel modules with ORC unwinding.",
     "DISTINCT"),
    ("The repo is private at github.com/example/vca2.",
     "The host is a Proxmox machine called vca-host.", "DISTINCT"),
]

# Real decisions from the 2026-09-22 ingest of docs/. Labels are hand-assigned.
FROM_CORPUS = [
    ("MTU 1500 yielded a measured rate of 2810/2739/2767 Mbit/s for node 1 to node 2.",
     "MTU 65521 yielded a measured rate of 15934/7991/7954 Mbit/s for node 1 to node 2.",
     "DISTINCT"),   # different MTU: both readings are true
    ("With an MTU of 65521, the host to node 2 direction achieved 14.6 Gbit/s.",
     "With an MTU of 65521, the node 2 to host direction achieved 25.6 Gbit/s.",
     "DISTINCT"),   # different direction
    ("Unloading plx87xx / plx87xx_dma on a live card leads to dma_hang on next boot.",
     "To recover from a dma_hang after unloading plx87xx / plx87xx_dma, the host "
     "must be bounced, not rmmod'd.",
     "DISTINCT"),   # cause vs remedy
    ("A configuration l2r8 TK64 local={128,2} resulted in 82365 bad elements.",
     "The local={128,2} kernel configuration resulted in 196449 wrong elements.",
     "DISTINCT"),   # different kernel variants
    ("For 1080p30 H.264 transcode, the host alone achieved 687.6 aggregate fps.",
     "When both host and card were used for transcode, the aggregate fps reached 1132.1.",
     "DISTINCT"),   # different configuration
    ("Concurrent LLM serving with independent workers on three nodes achieved 88.0 tok/s.",
     "Concurrent LLM serving using an RPC layer-split across three nodes was 2.6x "
     "slower than a single node.",
     "DISTINCT"),   # opposite strategies, both measured
    ("The running plx87xx.ko is hand-built and is not the version DKMS would install.",
     "The host was rebooted and came back with plx87xx loaded from the DKMS install "
     "at the correct srcversion.",
     "UPDATE"),     # genuinely a later state of the world
    ("The host stack used is chainsx/intel-vca2-dkms, not Intel's.",
     "The host stack is chainsx/intel-vca2-dkms, not Intel's stock plx87xx.",
     "DUPLICATE"),
    ("The correct recovery method when sshd is dead is vcactl pwrbtn-short.",
     "To recover a node with a dead sshd, use vcactl pwrbtn-short instead of "
     "vcactl reset --force.",
     "DUPLICATE"),
    ("The P580 has a theoretical peak of approximately 1325 GFLOP/s FP32 per P580.",
     "The real FP32 peak performance per P580 is approximately 1325 GFLOP/s.",
     "DUPLICATE"),
]


def run(e, cases, label):
    ok = 0
    by = {}
    print(f"\n{label}")
    for a, b, want in cases:
        got = e._reconcile(a, b)
        hit = got == want
        ok += hit
        by.setdefault(want, [0, 0])
        by[want][1] += 1
        by[want][0] += hit
        if not hit:
            print(f"  MISS want={want:9s} got={got:9s}")
            print(f"       A: {a[:78]}")
            print(f"       B: {b[:78]}")
    print(f"  {ok}/{len(cases)} correct   " +
          "  ".join(f"{k} {c}/{n}" for k, (c, n) in sorted(by.items())))
    return ok, len(cases)


if __name__ == "__main__":
    e = Engine("/tmp/recon_eval.db", models=Models(pool=Pool()), container="x")
    a1, n1 = run(e, SYNTHETIC, "synthetic pairs")
    a2, n2 = run(e, FROM_CORPUS, "real decisions from the docs/ ingest")
    print(f"\noverall {a1 + a2}/{n1 + n2}")
    e.close()
    sys.exit(0 if (a1 + a2) == (n1 + n2) else 1)
