<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Compute mailbox (3-CPU PoC)

> **Archived 2026-10-13.** One of the first six-device demos: a BlockIO-file mailbox that let the host drive the three nodes over PCIe DMA. It was superseded by the DMA engine ([../DMA.md](../DMA.md)) and the tuned GEMM ([../GEMM_WORKGROUP.md](../GEMM_WORKGROUP.md)). Kept for provenance.

First working “host drives all three Xeons over PCIe DMA, not IP”.

## Idea

BlockIO already DMAs between a host file and a guest block device. Attach a
second disk (`vcablk1`) per node, put a 512-byte command header at LBA 0,
payload at 4 KiB. Host writes the file; guest worker polls `/dev/vcablk1`,
computes, writes the result back.

This is **not** the final fabric (extra `kernel_read`/`kernel_write`, poll +
`BLKFLSBUF`). It is a control-plane and a proof that extra BlockIO devices
can be hot-added while Linux is up.

## Layout

```
offset 0      512-byte header (magic VCAM)
offset 4096   payload
disk size     16 MiB  /tank-red/vmdata/vca/mbox{0,1,2}.bin
```

Header (`compute/mbox_proto.py`), little-endian:

```
4s magic
I  op        1=SUM_U64  2=FILL_U64  3=PING
I  status    0 idle  1 request  2 done  3 error
Q  seq       must be unique; worker skips repeats
Q  nbytes
Q  in_off
Q  out_off
Q  result
```

## Attach (node already `net_device_ready`)

```bash
dd if=/dev/zero of=/tank-red/vmdata/vca/mbox0.bin bs=1M count=16
vcactl blockio open 0 0 vcablk1 RW /tank-red/vmdata/vca/mbox0.bin
# guest: /dev/vcablk1 appears (major 253, minor 16 observed)
```

Same for CPUs 1 and 2 → `mbox1.bin`, `mbox2.bin`.

## Code

| File | Role |
|---|---|
| `compute/mbox_proto.py` | Header pack/unpack |
| `compute/mbox_worker.py` | Guest loop on `/dev/vcablk1` |
| `compute/mbox_host.py` | Host jobs against one mailbox file |
| `compute/mbox_host3.py` | Parallel ping + sum on all three |
| `compute/mbox_bench.py` | Size sweep |

Workers on the guests (started via SSH from the host). Host coordinator
runs on `vca-host` and only touches the files.

Guest uses `ioctl(BLKFLSBUF)` before reads so host `pwrite`s are visible.
Do not reuse `seq`. Do not `pkill -f mbox_worker` from a command line that
contains that string.

## Results (2026-09-20)

Ping (node 0): **~8–11 ms** (poll + flush, not wire delay). Parallel ping
of all three: ~63 ms wall.

CPU job: sum of 1,048,576 uint64s (8 MiB) per node.

| | Result | Time |
|---|---|---|
| Node 0 | correct | 0.33 s |
| Node 1 | correct | 0.37 s |
| Node 2 | correct | 0.37 s |
| All three in parallel | combined sum correct | **0.37 s wall** |

They ran concurrently. ~65 MiB/s effective for that 24 MiB of payload.

Size sweep on node 0 (later, unique seqs):

| Payload | RTT | Effective |
|---:|---:|---:|
| ping | 7.9 ms | — |
| 4 KiB | 22 ms | 0.2 MiB/s |
| 64 KiB | 44 ms | 1.4 |
| 256 KiB | 53 ms | 4.7 |
| 1 MiB | 69 ms | 14.6 |
| 4 MiB | 966 ms | 4.1 |
| 8 MiB | 1.05 s | 7.6 |

Fixed cost dominates small messages. Throughput is far below virtio-net
TCP (~400–600 MiB/s). Use this path for **commands**, not bulk model weights,
until we have an ALUT window + doorbell.

A host-side timeout happened when `seq` was reused (`last_seq` skip) and
when a worker died; not a DMA hang.

## How to rerun

On the host, after nodes are `net_device_ready` and `vcablk1` is attached:

```bash
# guests: python3 /tmp/vca_compute/mbox_worker.py  (one per node)
python3 /tmp/vca_compute/mbox_host3.py
```

Copy of sources: `C:\Projects\VCA2\compute\`.
