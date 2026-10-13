<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# GPUs (Iris Pro P580 / GT4e)

Each node has one Skylake GT4e, PCI ID **8086:193A**, 128 MB eDRAM.
`i915` 1.6.0 (20170818). Firmware `i915/skl_dmc_ver1_27.bin`. RC6 on.
No CRTC (headless). Usable graphics memory reported 4096 MB (BIOS aperture
setting is 256 MB stolen; the rest is stolen-from-RAM / GTT).

`/dev/dri/card0` and `/dev/dri/renderD128` exist on all three nodes.

Idle: **350 MHz** (graphics base). Max non-overclocked **1150 MHz** (BIOS
caps the datasheet 1.0 GHz turbo a bit above that in practice).

## VA-API / Media SDK — proven hardware encode

Default `vainfo` fails: it looks for `/usr/lib64/dri/i965_drv_video.so`.
Mesa’s `i965_dri.so` is under `/usr/lib/x86_64-linux-gnu/dri/` and is **not**
the VA driver.

Working environment on the guest:

```bash
export LIBVA_DRIVER_NAME=iHD
export LIBVA_DRIVERS_PATH=/opt/intel/mediasdk/lib64
export LD_LIBRARY_PATH=/opt/intel/mediasdk/lib64:/opt/intel/mediasdk/lib/lin_x64:$LD_LIBRARY_PATH
```

Then `vainfo` reports Intel iHD driver **16.9.00189**, VA-API 1.1, including:

- H.264 Main/High/Constrained Baseline: VLD, EncSlice, EncSliceLP, FEI
- MPEG-2 Simple/Main: VLD, EncSlice
- JPEG Baseline: VLD, EncPicture
- VC-1 Simple/Main/Advanced: VLD
- VPP / stats (`VAProfileNone`)

Media SDK 2018-era is installed at `/opt/intel/mediasdk` (samples version
8.3.26.189, SDK 1.27). Binaries: `sample_encode`, `sample_decode`,
`sample_fei`, `sample_hevc_fei`, `sample_multi_transcode`, `sample_vpp`,
`ocl_rotate.cl`, `libsample_plugin_opencl.so`.

### Encode test (node 0, 2026-09-20)

```
sample_encode h264 -nv12 -w 320 -h 240 -n 30 -u veryfast \
  -i /tmp/in.yuv -o /tmp/out.h264
```

- `Media SDK impl` **hw**
- 30 frames random NV12: 3,456,000 bytes in → **88,606** bytes out
- GPU clock after: **Current freq 1150 MHz**, CAGF 1000 MHz

This is the original product workload, and it still runs.

## OpenCL — SAXPY ran on node 0 (2026-09-20)

ICD file: `/etc/OpenCL/vendors/intel.icd` contains
`/usr/local/lib64/libigdrcl.so` (NEO runtime, 2018-11-28). That `.so` is a
**vendor driver**, not the ICD loader. It exports `clGetPlatformInfo` and
`clGetExtensionFunctionAddress` but **not** `clGetPlatformIDs`. Direct
`dlopen` of `libigdrcl.so` cannot run OpenCL apps.

Need `ocl-icd-libopencl1` (Xenial apt). Node 0 also has `ocl-icd-opencl-dev`
and `opencl-headers`. Then `libOpenCL.so.1` loads the ICD.

Probe: `compute/cl_probe.c` (dlopen, no headers required). Result:

```
loaded libOpenCL.so.1
platform Intel(R) OpenCL HD Graphics
dev type=GPU (0x4)  Intel(R) Gen9 HD Graphics NEO
OpenCL 2.1 NEO   CUs=8   global mem=13262938112 (~12.4 GiB)
SAXPY 1024 floats: y[i]=2*x[i]+1  → y[0]=1 y[10]=21  OK
```

**Correction 2026-09-21:** `CUs=8` above is a bug, not a NEO quirk.
`cl_probe.c` defined `CL_DEVICE_MAX_COMPUTE_UNITS` as `0x1021`, which is
`CL_DEVICE_MAX_CONSTANT_ARGS` (= 8). The correct enum is `0x1002` and the
P580 reports **72 compute units**, matching the datasheet's 72 EUs. Fixed in
`cl_probe.c` and `cl_daemon.c`. See [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md).

Global mem is most of the node’s 16 GB DRAM, which is how GT4e works (no big
dedicated VRAM).

All three nodes now have `ocl-icd-libopencl1`. SAXPY probe succeeded on
every P580.

### GPU vs CPU SAXPY (8,388,608 floats × 20 kernel iterations)

Naive single-thread CPU loop vs NEO OpenCL kernel (no host copy in the
timed GPU loop). `compute/cl_bench.c`.

| Node | CPU GFLOP/s | GPU kernel GFLOP/s | Speedup | GPU clock after |
|---|---:|---:|---:|---|
| 0 | 0.69 | **9.61** | 13.9× | 1150 MHz |
| 1 | 0.68 | **9.50** | 14.0× | (same class) |
| 2 | 0.67 | **9.33** | 14.0× | (same class) |

CPU side is a scalar `for` loop, not AVX, so this is “the iGPU is real
compute,” not a fair BLAS comparison. Combined, three P580s are ~28 GFLOP/s
on this kernel if a host job can keep them fed. Peak Gen9 GT4e is much
higher on paper; SAXPY is memory-bound.

`cl_probe.c` / `cl_bench.c` / `cl_daemon.c` live in `compute/`.

OpenCL device (INFO), after the enum fix: **72 CUs**, max workgroup 256,
local mem **64 KB**, preferred float width **1**. FP32 peak is therefore
~1325 GFLOP/s per P580 (72 x 8 x 2 x 1150 MHz), ~4.0 TFLOP/s per card.

No discrete iGPU temperature. On each guest: ACPI TZ `acpitz` ~28 °C,
`x86_pkg_temp` (package), `coretemp` (matches `vcactl temp`), i915
`gt_cur_freq_mhz` (idle 350 / busy 1150), RAPL `energy_uj`. Watch **package
+ cores + GT freq**, not a GPU diode.

These iGPUs are 2015–2016 Quick Sync / OpenCL 2.1 devices, not CUDA.
Sensible AI split is **CPU `llama.cpp` / OpenVINO-era** plus OpenCL on the
P580, not a 2024 GPU LLM.

## KVM / GVT

`/dev/kvm` exists on all three; `kvm-ok` passed on node 0. libvirt + Alpine
VM was proven earlier on node 0. KVMGT (GPU slices into VMs) is in the
Intel matrix for Ubuntu 16.04 on this card (`vca_kvmgtctl.sh` on the host;
it ships with Intel's VCASS userspace source, `vca_apps/kvm_scripts/`, not
with this repo — see [SOURCE_MAP.md](SOURCE_MAP.md)) and has **not** been re-tested this session.
