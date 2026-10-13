<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Live map (as of 2026-09-20, after recovery reboot)

> **Archived 2026-10-13.** A snapshot of addresses and devices as observed on 2026-09-20, before the second card. PCI addresses (BDFs) were not re-verified afterwards. For the current layout read [../STATUS.md](../STATUS.md) and [../FABRIC.md](../FABRIC.md).

## Host

| | |
|---|---|
| Name | `vca-host` |
| IP | `vca-host` (SSH as root) |
| Kernel | **`7.0.14-17-pve`** (pinned 2026-09-21). `7.0.2-6-pve` stays installed as a fallback. The old "7.0.2 required" claim was wrong — see [KERNEL_PORT.md](../KERNEL_PORT.md). |
| Other VMs | 100 `ubuntu-1`, 101 `signal-web-dad` |
| VCA images | `/tank-red/vmdata/vca/images/node{0,1,2}-ubuntu-jammy.vcad` (live), `node{0,1,2}.vcad` (stock 16.04, rollback) |
| Mailboxes | `/tank-red/vmdata/vca/mbox{0,1,2}.bin` (16 MB each) |

Host VCA driver stack: **`chainsx/intel-vca2-dkms` @ `edb3e51`**, packages
`2.3.26+ubuntu24.04.10`, built locally by DKMS for both installed kernels.
**Not Intel's stock modules** — and the running `plx87xx.ko` is a local
hand-built variant that differs from the DKMS build (different `srcversion`,
adds a misc device). See [HOST_STACK.md](../HOST_STACK.md).

Host VCA modules observed loaded: `plx87xx`, `plx87xx_dma`, `vop`, `vop_bus`,
`vca_virtio_net`, `vca_virtio`, `vca_virtio_ring`, `vca_vringh`, `vcablk_bckend`,
`vcablkfe`, `vca_csm`, `vca_mgr`, `vca_mgr_extd`, plus the `*_bus` modules.

BIOS on all three nodes: `0ACIE203` (2018). Transfer mode:
`DMA with feature fix alignment`. GPU aperture BIOS setting: 256 MB (this is
iGPU stolen memory, **not** the PCIe BAR).

## Nodes

Guest OS: **Ubuntu 22.04** + Intel kernel `4.14.20-1.2.3.26.vca`, on all
three nodes since 2026-09-21, from one reproducible image
(`VCA_IMAGE_BUILDER_GIT=cf796ef`; see [IMAGE_BUILDER.md](../IMAGE_BUILDER.md)).
Each node runs its own reflink copy of a golden master:
`node{0,1,2}-ubuntu-jammy.vcad`. The stock 16.04 images `node{0,1,2}.vcad`
are untouched and remain the rollback.
Default SSH user `root`, Intel password `vista1`. Reach them **from the
host**, not from the LAN.

| Node | IP | Host gateway | Host NIC MAC | Guest hostname | CPU | iGPU |
|---|---|---|---|---|---|---|
| 0 | 172.31.1.1 | 172.31.1.254 | fe:00:00:00:01:01 | vca_node_00 | E3-1585L v5 8 threads | 8086:193A P580 |
| 1 | 172.31.2.1 | 172.31.2.254 | fe:00:00:00:01:02 | vca_node_01 | same | same |
| 2 | 172.31.3.1 | 172.31.3.254 | fe:00:00:00:01:03 | vca_node_02 | same | same |

Each node: ~16 GB RAM, `/dev/kvm`, `/dev/dri/card0` + `renderD128`,
`/dev/vca`, `/dev/vcablk0` (root). After the mailbox attach: `/dev/vcablk1`.

Node 0 also has libvirt + a shut-off `alpine-test` VM (was running before the
crash reboot). Nodes 1–2 do not have qemu/libvirt packages.

All three nodes: `ocl-icd-libopencl1` via Xenial apt, so `libOpenCL.so.1`
loads `/usr/local/lib64/libigdrcl.so`. SAXPY OK on every P580.

Warm OpenCL daemons (this session, still running unless killed): TCP 9200,
pid file `/tmp/vca_compute/daemon.pid`, INPUT accept for 9200.

llama.cpp RPC: `ggml-rpc-server -H 0.0.0.0 -p 50052 -t 4` on each node
(`/tmp/ggml-rpc-server`, `/tmp/rpc.pid`). INPUT 50052 open. Model on host
`/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf`.

Inter-node ping works with **TTL 63** — traffic hairpins through the host
router, not a switch on the card.

## PCI (host)

NTB endpoints (one per node), 4 GB prefetchable BAR2:

| Node | Host BDF | Device | BAR0 | BAR2 |
|---|---|---|---|---|
| 0 | `0000:05:00.0` | 8086:2954 | 256 KB | 4 GB @ `383e00000000` |
| 1 | `0000:07:00.0` | 8086:2955 | 256 KB | 4 GB @ `383d00000000` |
| 2 | `0000:0a:00.0` | 8086:2956 | 256 KB | 4 GB @ `383c00000000` |

DMA engines: `8086:2952` (e.g. `0000:03:00.2` for node 0).

Switch: PLX/Broadcom PEX 8717.

Guest-side NTB (node 0 measured): `01:00.0` 8086:2958, BAR0 256 KB @
`dfe00000`, BAR2 4 GB @ `3e00000000`. Link **8 GT/s ×8** (PCIe Gen3 x8).
Host virtio `eth0` jumbo via `compute/jumbo/jumbo_mtu.c` (not left loaded).
MaxPayload 256 B, MaxReadReq **128 B** (that last number is a possible
bandwidth limiter).

Sysfs mmap nodes exist (`resource2`, `resource2_wc`) on **both** sides.
Do not write them. See GOTCHAS.

## Host userspace devices

```
/dev/vca00 /dev/vca01 /dev/vca02
/dev/vca_mgr0 /dev/vca_mgr_extd0
/dev/vca_blk_bcknd00..02
/dev/vop_virtio00 /dev/vop_virtio01 /dev/vop_virtio02
```

Group `vcausers`. `/dev/vop_virtioN` is the `VCA_VIRTIO_ADD_DEVICE` ioctl
path (virtio-net backend). `VCA_VIRTIO_COPY_DESC` is a stub (`-EFAULT`).

## Guest sysfs `/sys/class/vca/vca/`

- `state` — `net_device_ready`
- `net_config` — a **shell script** the agent runs (`ip link set eth0 mtu 65521`,
  address, route). Not a binary blob.
- `sys_config` — `sysctl -w vm.min_free_kbytes=524288`
- `csa_mem` — CSA shared-memory interface for the card-side agent, not a
  general compute mailbox.
