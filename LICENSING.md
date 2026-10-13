<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Licensing

Three licences, because one part of the repository had no choice about it.
Source files and documents carry an `SPDX-License-Identifier` so you never
have to guess which applies. The exceptions are data files (JSON, CSV, logs,
STL), which take the licence of the directory they sit in per the table
below, and `compute/plx_mbox/`, a patch against Intel's GPL-2.0 driver that
stays under Intel's terms.

| Part | Licence | Why |
|---|---|---|
| Linux kernel modules | **GPL-2.0** | Not a choice — see below |
| Userspace code, scripts, build glue | **MIT** | Lowest barrier to reuse |
| Documentation, photos, shroud designs, measurement data | **CC BY 4.0** | Attribution keeps the measurements traceable |

## The kernel modules had to be GPL-2.0

Everything in `compute/dma/`, `compute/alut/` and `compute/jumbo/` that builds
as a `.ko` is GPL-2.0. That is not a preference:

- They call `iommu_map()`, `iommu_unmap()`, `iommu_get_domain_for_dev()`,
  `register_kprobe()` and `kallsyms_lookup_name()`, all of which the kernel
  exports as `EXPORT_SYMBOL_GPL`. A module that does not declare a GPL-
  compatible licence cannot link them — the kernel refuses to resolve
  GPL-only symbols for it. All of these files already declare
  `MODULE_LICENSE("GPL")` for exactly that reason.
- `compute/alut/plx_mbox.c` includes Intel's `plx_device.h` and `plx_hw.h` and
  is compiled *into* `plx87xx.ko`. It is a derivative work of Intel's
  GPL-2.0 VCASS driver.

Full text: [`LICENSES/GPL-2.0.txt`](LICENSES/GPL-2.0.txt).

## Why MIT and not Apache-2.0 for the rest

Apache-2.0 is **incompatible with GPLv2**. Since this repository already
contains GPL-2.0 kernel modules, choosing Apache would mean two halves of the
same project could not be combined. MIT is GPL-compatible, so that problem
never arises.

Apache-2.0's real advantage is its express patent grant, which protects users
against patents held by *contributors*. That is worth having in a corporate
project; here it buys very little against a concrete compatibility cost.

MIT is also the shortest possible barrier for the intended audience — someone
who just bought one of these cards as e-waste and wants it to do something
useful.

Full text: [`LICENSE`](LICENSE).

## Why CC BY 4.0 for docs and designs

Software licences fit 3D models and prose badly. CC BY 4.0 is the normal
vehicle for both.

Attribution matters here for a specific reason. The value of `docs/` is that
the numbers are **real measurements from one real card in one real host**. If
they get quoted elsewhere, attribution keeps them traceable back to that
single setup — which is exactly the caveat `README.md` and `SAFETY.md` work to
keep visible. A measurement separated from its conditions is worse than no
measurement.

Covers `docs/` (including `docs/archive/` and the photographs of the card
in `docs/img/`, which are the author's own), `shrouds/` (including
`vca2_fan_shroud.scad` and the generated `.stl` files), every `README.md`,
`SAFETY.md`, this file, and the measurement data and write-ups in `memory/`
and `bench/`.

Full text: [`LICENSES/CC-BY-4.0.txt`](LICENSES/CC-BY-4.0.txt).

## Not covered by any of the above

These are excluded from the repository via `.gitignore` and remain the
property of their owners:

- **Intel's VCASS distribution** (`vcawic/`) — BIOS, EEPROM, host and card
  images, Windows drivers.
- **The VCASS 2.3.26 kernel sources** (`src/`) — Intel's to distribute.
  `docs/SOURCE_MAP.md` explains what is in them and which files matter.
- **Intel's documentation PDFs and diagrams** — freely downloadable from
  Intel. Intel's block diagram and exploded view of the card were in early
  commits of this repository and have been removed from its history.
- **Donor STL/STEP models** used only as fit references for the shroud work;
  they belong to their authors.

## Attribution

Copyright (c) 2026 Austin Gregory.

Portions of this work were developed with AI assistance (Claude).

## A caveat

This file describes open-source convention as applied honestly to what is
actually in the repository. It is not legal advice. The GPL-2.0 conclusion is
about as settled as these things get; the MIT-versus-Apache reasoning is a
judgement call about compatibility.
