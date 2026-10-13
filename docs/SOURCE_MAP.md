<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Source map (VCASS 2.3.26)

## Trees

| Path | What |
|---|---|
| `vcawic/intelvisualcomputeaccelerator_sources_2.3.26.zip` | Original Intel zip |
| `src/vca_modules/` | Extracted kernel modules (this session) |
| `src/vca_apps/` | Extracted `vcactl` / `vcassd` userspace |
| `vcawic/Intel_SOC_VCA_kernel-master.zip` | Not yet unpacked |
| `vcawic/intelvisualcomputeaccelerator_kernels_development_package_2.3.26.zip` | Not yet unpacked |
| `vcawic/` persistent/volatile Ubuntu & CentOS images, Windows drivers | Reference images |

Host DKMS package name observed: `vca2-vcass/2.3.26+ubuntu24.04.10` built
2026-09-19, installed for both `7.0.2-6-pve` and `7.0.14-17-pve`.

## Kernel modules (`src/vca_modules/vca/`)

| Dir | Role |
|---|---|
| `plx87xx/` | NTB, LBP boot, ALUT, doorbells, `plx_ioremap` |
| `plx87xx_dma/` | PLX DMA engine (`dmaengine` client) |
| `vop/` | Virtio Over PCIe; ioctl; debugfs DMA tests |
| `vca_virtio/` | virtio-net + vring helpers |
| `blockio/` | Guest `vcablk` + host `vcablk_bcknd` |
| `vca_csa/` | Card-side agent (guest) |
| `vca_csm/` | Card state manager (host) |
| `vca_mgr/`, `vca_mgr_extd/` | `vcactl` ioctl targets |
| `bus/` | `vop_bus`, `vca_*_bus` |
| `common/vca_dev_common.h` | Device page, bootparam, `VCA_DP_SIZE` |

PCI IDs (`plx_hw.h`): host node functions 0x2954/2955/2956, guest upstream
0x2958/2959/295A, DMA 0x2952. **Node 2 (`0x2956`) is `num_ntbs = 1`;**
nodes 0/1 are `num_ntbs = 2`. That is why ALUT geometry differs.

## Userspace (`src/vca_apps/src/`)

- `vcactrl.cpp` — `vcactl`
- `vca_blockio_ctl.cpp` — blockio open/close
- `vcassd_virtio_backend.c` — userspace virtio-net add-device (legacy vs
  in-kernel `vca_virtio_net`; COPY_DESC is stub so this path cannot copy)

`VCA_VIRTIO_COPY_DESC` in `vop_vringh.c` is `return -EFAULT;` — no
implementation in this tree.

## Files worth reading next

- `vop/vop_kvec_buff.c` — actual DMA descriptor path for virtio
- `plx87xx/plx_hw_ops_vop.c` — ioremap hooks VOP uses
- `plx87xx/plx_intr.c` — doorbell assignment
- `vca_csa/vca_csa_sysfs.c` — guest sysfs `csa_mem` / `net_config`
- Guest `blockio/vcablk/vcablk.c` — how `vcablk1` is probed hot
