<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# compute/image/ — node image builder

Builds a bootable VCA 2 node image with a modern userspace on Intel's 4.14
node kernel. Intel's kernel, dracut initramfs and ESP are kept verbatim; only
the root filesystem is replaced. All six node images run Ubuntu 22.04
(`ubuntu-jammy`) built here; Intel's stock 16.04 images are kept, unmodified,
for rollback. Full documentation: [docs/IMAGE_BUILDER.md](../../docs/IMAGE_BUILDER.md);
what the new image buys, measured: [docs/NODE_IMAGE.md](../../docs/NODE_IMAGE.md).

| File | Role |
|---|---|
| `build_node_image.sh` | The builder. `--list`, `--profile <name>`, `--out <file>`. Runs on the host as root. |
| `deploy_node_image.sh` | Copies a golden master to one node's own image and boots it. `--card C --node N` (cpu N on card C). |
| `verify_node_image.sh` | Runs on a node (`/opt/vca-tools/`); checks the image is what the builder promised. |
| `plt32_to_pc32.py` | Installed on nodes as `plt32-to-pc32`; makes modern-toolchain `.ko` files loadable on 4.14. |
| `profiles/*.conf` | One per distro. Only `ubuntu-jammy` is `proven`; `ubuntu-focal` and `debian-bookworm` are `untested` (never built); `ubuntu-noble` is `expected-broken`. |
| `overlay/common/` | Files dropped into every rootfs: network config unit, sysctl, extra modules. |

```bash
./build_node_image.sh --profile ubuntu-jammy
./deploy_node_image.sh --card 0 --node 1 --master /path/to/node-ubuntu-jammy.vcad --verify
```

**Hazards.** Never boot the golden master itself: BlockIO opens images
read-write, so two nodes on one file corrupt it (the deploy script refuses).
`deploy_node_image.sh` power-cycles the target node. The builder only reads
Intel's reference image. See [GOTCHAS.md](../../docs/GOTCHAS.md) and
[SAFETY.md](../../SAFETY.md).
