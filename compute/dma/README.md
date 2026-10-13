<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# compute/dma/ — the PLX DMA engine across the NTB

Kernel modules and drivers that measure each node's on-card DMA engine, move
data node-to-node through a host-staged buffer (up to 4423 MiB/s, verified),
and prove that true node-to-node P2P is not possible on this host. Results
and method: [docs/DMA.md](../../docs/DMA.md).

| Module | Side | Role |
|---|---|---|
| `hostbuf.ko` | host | One coherent DMA buffer, bus address published. Touches no VCA driver. |
| `alut_hmap.ko` | host | Host A-LUT window onto a guest buffer. Identical copy of `../alut/alut_hmap.c`. |
| `dmabench.ko`, `dmabench2.ko` | either | DMA throughput; `dmabench2` adds pipelined submission and direction choice. |
| `n2n.ko`, `ring.ko` | node | Node-to-node via a shared host buffer; `ring` is the pipelined version. |
| `ctrlpoke.ko` | node | Read or write one word in an A-LUT window. |
| `p2pmap.ko` | host | **Not a tool.** Documents the negative P2P result; see its header. |
| `gbufpeek.c`, `hostpeek.c` | node / host | Userspace dumps of `/dev/vca_gbuf` and `/dev/vca_hostbuf`. |

The `run_*.sh` drivers run on the host, source `preflight.sh`, load modules
in order and always tear down in reverse. Node arguments are the
`172.31.N.1` octet (1–3 = nodes 0–2 on card 0).

Build: copy the tree to `/tmp/vca_compute/dma` on the host and on each node,
then `cp Makefile.host Makefile && make` (host) or
`cp Makefile.guest Makefile && make` (node). `Makefile.guest` also builds
`alut_gmap.ko`, so copy `../alut/alut_gmap.c` in first. On a node built
with a modern toolchain, run `plt32-to-pc32 *.ko` after `make` or Intel's
4.14 kernel rejects the modules (see
[IMAGE_BUILDER.md](../../docs/IMAGE_BUILDER.md)).

**Hazards.** These are kernel modules on a live card. Do not `rmmod` or
reload `plx87xx`, do not touch the `plx87xx_dma` debugfs tests, and keep the
P2P path write-only. Read [SAFETY.md](../../SAFETY.md) and
[GOTCHAS.md](../../docs/GOTCHAS.md) before running anything here.
