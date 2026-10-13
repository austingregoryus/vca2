<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# scripts/sessions

75 one-off scripts from the exploration sessions (2026-09-20 onward). They are
kept for provenance, because [`docs/LOG.md`](../../docs/LOG.md) refers to the
experiments they ran, not because they are meant to be reused.

- They are hardcoded to the author's host: paths under `/tank-red/vmdata/vca/`
  and `/tmp/vca_compute/`, node IPs `172.31.1-3.1`, and `sshpass -p vista1` (or
  `SSHPASS=vista1`), Intel's documented default password.
- They are not maintained and were written for card 0 only.
- **Several touch the PCIe fabric in ways [`SAFETY.md`](../../SAFETY.md) warns
  about** (A-LUT windows, DMA, doorbells, MaxReadReq, BAR access, module
  reloads), and a wrong step can hang the host. Read `SAFETY.md` and
  [`docs/GOTCHAS.md`](../../docs/GOTCHAS.md) before running any of them. The
  read-only ones are marked as such in their own header comments.

Scripts are grouped here by filename prefix; each file's first comment says
what it does.

| Prefix | What the group did |
|---|---|
| `_alut_*` | A-LUT window experiments: guest DMA buffers mapped into host BAR2, host pushes, doorbell round-trips, installing and rebooting into the experimental `plx87xx` driver. Risky; see [`docs/ALUT_MBOX.md`](../../docs/ALUT_MBOX.md). |
| `_ntb_*` | NTB window mapping and timing: guest A-LUT maps, read-only BAR2 reads, 1 MiB / 8 MiB copy timing, ping-pong, and restoring the window header. Risky. |
| `_mrr_*` | Whether PCIe MaxReadReq can be raised (it does not stick), measured with iperf3 and an ALUT pull, node 0 only. See [`docs/FABRIC.md`](../../docs/FABRIC.md). |
| `_iperf*`, `_jumbo_*`, `_live_ifaces` | TCP throughput over the virtio NICs at MTU 1500 versus jumbo 65521, and loading `jumbo_mtu` on the host. |
| `_gemm*`, `_kern_gemm`, `_gridpad`, `_pad_hadd`, `_lda`, `_gpu_*`, `_cl_all3` | OpenCL work on the P580 iGPUs: SAXPY and GEMM scaling, workgroup and padding experiments, Quick Sync encode, GPU sensors. See [`docs/GEMM_WORKGROUP.md`](../../docs/GEMM_WORKGROUP.md) and [`docs/GPU.md`](../../docs/GPU.md). |
| `_hetero`, `_warm_and_burn`, `_cont_prep`, `_continue_six`, `_bench_only`, `_temps` | Using CPUs and GPUs together, sustained-load runs with temperature readings, and the six-device demo. |
| `_llama_*`, `_olmoe_*` | llama.cpp builds and runs on the host and nodes (TinyLlama, OLMoE). See [`docs/LLAMA.md`](../../docs/LLAMA.md). |
| `_mbox_*`, `_attach_mbox`, `_run_mbox_poc` | The BlockIO mailbox proof of concept (archived in [`docs/archive/COMPUTE_MAILBOX.md`](../../docs/archive/COMPUTE_MAILBOX.md)). |
| `_explore*`, `_node_survey`, `_dump_fabric`, `_debugfs_dump`, `_host_followup`, `_guest_pread`, `_guest_kbuild` | Inventory and probing of sysfs, debugfs, `/dev` and PCI state, mostly read-only; `_guest_pread` reads a node BAR through sysfs, so check it against GOTCHAS first. |
| `_boot_*`, `_check_state`, `_recover_node0`, `_verify_nodes` | Booting, checking and recovering the nodes during the first sessions. The maintained path is [`../vca2-boot.sh`](../vca2-boot.sh). |
