<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The DMA engine (the fabric unlock)

Measured 2026-09-21. This supersedes the "bulk host→guest is a push" advice in
[ALUT_MBOX.md](ALUT_MBOX.md), which was true only for **CPU** copies.

**Headline: guest reads of host RAM went from 28 MiB/s to ~4000 MiB/s, a
144x improvement, and the best node↔node path reaches 4423 MiB/s pipelined,
CPU-free.** Every number below is verified by content, not just timed.

Re-measured 2026-09-21 across all six ordered node pairs. The single most
useful finding is that **direction matters more than distance**: node 2 → node
1 runs at 4423 MiB/s and node 1 → node 2 at 2020, over identical hardware
running an identical OS image.

## Why the CPU numbers were never going to improve

A CPU read across the NTB is a **non-posted** transaction: the core issues a
read and stalls for the completion. MaxReadReq is hard-wired at 128 B
(`setpci` writes are ignored — see [FABRIC.md](FABRIC.md)), so a CPU pull is
128 bytes per round trip, serialized. That is the 28 MiB/s.

CPU *writes* are posted — fire and forget — which is why pushes already ran at
~4.9 GiB/s.

The DMA engine keeps many reads in flight, so MaxReadReq stops mattering. It
is not a tuning knob, it is a different transaction model.

## Results

### Card-side engine (guest initiates)

Node 0, 4 MiB coherent local buffer, A-LUT window onto a host buffer.

| len | PUSH depth=1 | PUSH depth=16 | PULL depth=1 | PULL depth=16 |
|---:|---:|---:|---:|---:|
| 4 KiB | 886 | **3200** | 1425 | **3434** |
| 16 KiB | 2123 | 3731 | 1915 | 3151 |
| 64 KiB | 3377 | 3945 | 3640 | 3930 |
| 256 KiB | 3762 | 3813 | 3845 | 3883 |
| 1 MiB | 3926 | 3820 | 3862 | 3803 |
| 4 MiB | 3955 | 3854 | 3896 | 3845 |

MiB/s. ~4 GB/s is ~50% of the Gen3 x8 raw 7.9 GB/s, a normal TLP efficiency.

### Host-side engine (host initiates) — 1.7x SLOWER

Same window, driven from `0000:03:00.2` instead:

| len | PUSH | PULL |
|---:|---:|---:|
| 64 KiB | 2084 | 2202 |
| 1 MiB | 2244 | 2342 |
| 4 MiB | **2274** | **2350** |

Pipelining barely helps the host engine (2274 → 2290 at depth 4), so it is
path-limited, not latency-limited.

> **Rule: always initiate bulk DMA from the card side.** 3.9-4.8 GB/s versus
> 2.3 GB/s for the identical transfer. The host engine sits behind the root
> port and the first switch; the card engine is one hop from its own NT port.

### Node to node, staged through host RAM

Both nodes map the **same** host buffer through their own A-LUT window and use
their own card-side engine. 4 MiB x 20, pattern content-addressed from a tag
so a receiver cannot pass on stale data.

| Path | send | recv | verify | effective end-to-end |
|---|---:|---:|---|---:|
| node 0 → node 1 | 3970 | **4832** | 0/1048576 words bad | **~2180 MiB/s** |
| node 0 → node 2 | 3968 | **3361** | 0/1048576 words bad | **~1830 MiB/s** |

Those are serial figures (send fully, then receive). The pipelined version is
below and reaches **4423 MiB/s** on the best pair.

> **Correction (2026-09-21, later).** These were originally compared against
> "~325 MiB/s for the virtio hairpin" for a 6.7x–8x claim. That was not a fair
> comparison: the 325 figure was measured at **MTU 1500**, and jumbo was
> already on when the DMA numbers were taken. The honest comparison is in
> [FABRIC.md](FABRIC.md) and repeated below — the DMA win is real but smaller,
> and the bigger part of it is that it costs no CPU.

### The full matrix — direction matters more than the pair

The first round only ever used node 0 as the sender, which hid the most
useful fact about this fabric. Measured across all six ordered pairs
(4 MiB x 20, every run `VERIFIED 0/1048576 words bad`).

**Re-measured with all three nodes on one identical image**, so the only
remaining variable is the hardware:

| | send (write to host RAM) | recv (read from host RAM) |
|---|---:|---:|
| node 0 | 3998, 3995 | 3975, 3923 |
| node 1 | 2060, 2056 | **4973, 4724** |
| node 2 | **5672, 5611** | 3234, 3024 |

#### The distro is provably irrelevant here

Node 0 was the one that moved from Ubuntu 16.04 to 22.04 between these two
rounds. Nodes 1 and 2 did not change OS at all:

| | OS changed? | send, before → after | recv, before → after |
|---|---|---|---|
| node 0 | **yes, 16.04 → 22.04** | 3925 → 3997 (**+1.8%**) | 3911 → 3949 (**+1.0%**) |
| node 1 | no | 2124 → 2058 (−3.1%) | 4855 → 4849 (−0.1%) |
| node 2 | no | 5761 → 5642 (−2.1%) | 3156 → 3129 (−0.9%) |

**The node whose OS changed moved less than the nodes whose OS did not.**
Every delta is run-to-run noise. This is a hardware DMA engine driven by the
same 4.14 kernel in both cases, so that is the expected result — but it is
now measured rather than assumed, on a single-variable test.

**Each node's send and recv rate is a property of that node alone** — it does
not vary with which peer is on the other end. So the fabric has no "slow
pair", it has fast and slow *directions*:

- **node 2 is the best sender** (~5760 MiB/s) and the worst receiver (~3160).
- **node 1 is the best receiver** (~4855) and by far the worst sender (~2124).
- node 0 is unremarkable in both directions (~3900).

Which gives a concrete scheduling rule:

> **Route bulk node-to-node traffic node 2 → node 1.** It is **2.1x** faster
> than node 1 → node 2 over the identical hardware, buffer and code.

### Why node 1 writes at half rate — not yet explained

Ruled out, by direct comparison of node 1 against node 2 (which shares every
one of these settings and still writes 2.7x faster):

| | node 0 | node 1 | node 2 |
|---|---|---|---|
| link | gen3 x8 | gen3 x8 | gen3 x8 |
| MaxPayload | 512 | 256 | 256 |
| MaxReadReq | 512 | 512 | 512 |
| ExtTag / RelaxOrd / NoSnoop | off / on / on | on / on / on | on / on / on |
| distro at measurement | **22.04** | **22.04** | **22.04** |
| **DMA engine PCI function** | **01:00.3** | **01:00.1** | **01:00.3** |

The only difference that tracks the result is the **PCI function the DMA
engine lands on**. Both switch parts are the "with DMA" variants and expose
several channels; the EEPROM decides which one each NT port sees. Node 1 got
the one at function `.1`, nodes 0 and 2 both got `.3`. Node 1 exposes no
second DMA function, so this cannot be tested by simply binding a different
channel.

Note this is not the cascade: node 1 hangs off the **PEX 8749**, the same
switch as node 0.

Software is ruled out as hard as it can be: all three nodes now run **one
identical image**, byte for byte from the same build, and the spread is
unchanged. Whatever this is, it is below the OS.

### Node 2 is slower — on reads only

3137–3175 MiB/s receive versus node 1's ~4855. That is an extra switch hop,
and it is a genuine hardware fact, unlike the GEMM "node 2 is slow" claim
which was an artifact (see [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md)). But it is
a *read* effect only: node 2 is the fastest **writer** on the card.

The topology, verified live against Intel's own block diagram
(`VCA2_arch.jpg`, in Intel's VCA 2 product documentation; not included here),
which the hardware matches exactly. This is card 0, measured before the second
card was fitted:

```
host root port 00:03.2
└─ 03:00.0  PEX 8749  "1:3 mux", 48-lane 18-port, x16-capable edge connector (x8 in this host)
   ├─ 04:08.0 → bus 05 → 05:00.0  NTB node 0   gen3 x8
   ├─ 04:09.0 → bus 06 → (empty)  gen1 x0      <- M.2 riser, Gen3 x2, unpopulated
   ├─ 04:10.0 → bus 07 → 07:00.0  NTB node 1   gen3 x8
   └─ 04:11.0 → bus 08 → 08:00.0  PEX 8717  "1:1 mux", 16-lane 8-port   gen3 x8
                          └─ 09:01.0 → 0a:00.0  NTB node 2   gen3 x8
```

Every NTB link is **gen3 x8**, so link width and speed explain none of the
per-node differences above. What the topology does explain is node 2:

> **Node 2 sits one store-and-forward switch hop further from host RAM.** A
> DMA *read* is non-posted — throughput is bounded by outstanding tags divided
> by round-trip latency — so an extra hop costs it directly. A DMA *write* is
> posted, fire-and-forget, so the same hop costs it almost nothing in
> bandwidth. That is exactly the measured shape: node 2 is the **worst reader
> (3156)** and the **best writer (5760)**.

Node 2 being the outright fastest writer rather than merely equal is less
certain. A plausible contributor is that PEX 8717 is a 1:1 switch serving node
2 alone, with its own buffers and an uncontended x8 uplink, while nodes 0 and
1 share the 8749's crossbar with each other's continuous BlockIO root-
filesystem traffic. Plausible, not proven.

**The diagram does not explain node 1.** Nodes 0 and 1 are structurally
identical in it — same switch, same x8 NTB, same distance from the host — yet
node 1 writes at 2124 and node 0 at ~3900. That absence is itself informative:
it pushes the cause away from topology and toward the one asymmetry the
diagram cannot show, the DMA engine's PCI function (`01:00.1` vs `01:00.3`),
which is set in the EEPROM.

The second switch also explains node 2's different A-LUT geometry
(`num_ntbs=1`, 256 x 16 MB).

**Unused capability spotted:** `04:09.0` is a live downstream port sitting at
gen1 x0 with nothing behind it — the **M.2 riser** in the diagram (Gen3 x2 off
the 8749). There is an unpopulated M.2 slot on this card, reachable from the
host side of the fabric rather than from any node.

## How to use the engine

### Getting a channel

The engine has **one channel per function** (`PLX_8733_NUM_CHAN`, and
`plx_dma_probe` adds a single embedded `plx_chan` to the list), and
`plx87xx` already claims it at probe:

```c
xdev->dma_ch = dma_request_channel(mask, plx_dma_filter, &xdev->pdev->dev);
```

It is `DMA_PRIVATE` and `in_use=1`, so `dma_request_channel()` from another
module **will fail**. You must share the existing channel — which is already
how the driver works, since every VOP instance uses the same one
(`vi->dma_ch = vpdev->dma_ch`).

Reach it without touching `struct plx_device` (big, config-dependent).
`plx_pci.c` does `pci_set_drvdata(pdev, plx_dma_dev)` and

```c
struct plx_dma_device { struct pci_dev *pdev; struct dma_device dma_dev; ... };
```

so only the first two members are needed and both are standard types:

```c
struct plx_dma_device_head { struct pci_dev *pdev; struct dma_device dma_dev; };
d  = pci_get_drvdata(dma_pdev);
dd = &d->dma_dev;
c  = list_first_entry(&dd->channels, struct dma_chan, device_node);
```

**Validate before use** — `d->pdev == dma_pdev`, `dd->dev == &dma_pdev->dev`,
`c->device == dd` — so a wrong assumption gives `-EINVAL` instead of an oops.

### Addressing

Exactly as `vop_common.c:922` forms it:

```c
dst = vdev->aper->pa + (remapped - vdev->aper->va);
```

- **local** memory: a `dma_addr_t` from `dma_alloc_coherent` / `dma_map_single`
- **peer** memory: `pci_resource_start(ntb_pdev, 2) + peer_off`, valid once
  somebody has programmed an A-LUT entry for that offset
- **64-byte alignment** on src, dst and len (`PLX_DMA_ALIGN_SHIFT 6`).
  Misaligned still works but the driver logs a rate-limited warning and
  performance drops.
- max transfer per descriptor is `PLX_DESC_SIZE_MASK` = 128 MiB − 1.

### The call sequence, and the trap

```c
tx = dd->device_prep_dma_memcpy(chan, dst, src, len, DMA_PREP_FENCE);
if (!tx) return -ENOMEM;
cookie = tx->tx_submit(tx);
if (dma_submit_error(cookie)) return -EIO;
dma_sync_wait(chan, cookie);       /* issues pending internally */
```

> **`plx_dma_prep_memcpy_lock()` takes `prep_lock` and releases it inside
> `tx_submit()`.** A successful prep must always be followed by `tx_submit`.
> Return early between the two and you leave a spinlock held and deadlock the
> channel. This is not the usual dmaengine contract; it is specific to this
> driver.

For pipelining, prep+submit each descriptor immediately and wait only on the
**last** cookie — completion is in order on a single channel, so that waits
for the whole batch.

## This is not what hung the card

The 2026-09-20 incident came from reading Intel's `plx87xx_dma` debugfs test
files (`dma_test`, `flush`, `cleanup`, `issue_pending`) — an ad-hoc harness
with NULL-deref bugs, where `open()`/`read()` performs the operation. See
[GOTCHAS.md](GOTCHAS.md); those remain off limits.

Everything here uses the documented `dmaengine` client API and the same call
sequence VOP uses in production. Across all runs the nodes stayed
`net_device_ready` and no module was left loaded.

## Code

| File | Role |
|---|---|
| `compute/dma/hostbuf.c` | Host: one `dma_alloc_coherent` buffer + its bus address. Deliberately touches no VCA driver, so no patched `plx87xx` and no host reboot needed. |
| `compute/dma/dmabench.c` | First benchmark, synchronous |
| `compute/dma/dmabench2.c` | Adds pipelining (queue depth) and a round-trip integrity check |
| `compute/dma/n2n.c` | Node-to-node send/recv with a content-addressed pattern |
| `compute/dma/run_guest_dma.sh` | Card-side benchmark driver |
| `compute/dma/run_host_dma.sh` | Host-side benchmark driver |
| `compute/dma/run_n2n.sh` | Node-to-node driver |

All runner scripts tear down in reverse order via a shell `trap`.

> **Unload order matters.** The guest A-LUT entry points at the host buffer's
> bus address. Free the host buffer while a guest window still points at it
> and the guest can write into recycled host memory. Always drop the guest
> window first. The scripts do this; do not do it by hand.

## What is still open

- **Chunked overlapped node↔node** to get from ~2200 to ~3900 MiB/s.
- **A real transport**: A-LUT window for addressing + DMA for bulk + doorbell
  for notify (35 us RTT, [ALUT_MBOX.md](ALUT_MBOX.md)). Every piece is now
  measured; nothing is missing but the ring protocol.
- (done — see the pipelined ring below)


# Node-to-node P2P: settled, and it is a NO

Tested 2026-09-21, three runs, ending in a controlled experiment. **True
peer-to-peer between nodes is not available on this hardware.** Host staging
is not a workaround, it is the answer on this platform.

## Method

Point node 0's A-LUT window at the **host-domain address of node 1's NTB
BAR2** instead of at host RAM:

```
node0 BAR2 + S  --NT0-->  0x383d1b000000  (= node1 host-side BAR2 + H)
                          --?-->  NT1  -->  node1 RAM
```

That is only an address argument to `plx_add_a_lut_entry`. No switch register
writes, no ACS change, nothing persistent. Write-only on purpose: a posted
write that nothing claims is dropped, whereas a read would sit on the root
port's 50 us–50 ms completion timeout and could wedge the engine.

## Three blockers, found in order

### 1. ACS — NOT the blocker

`04:11.0` and root port `00:03.2` both carry `ACSCtl: ReqRedir+ CmpltRedir+`,
so the obvious guess is that peer TLPs get bounced. **Wrong.** The first run
produced:

```
DMAR: [DMA Write NO_PASID] Request device [05:02.3]
      fault addr 0x383d1b001000 [fault reason 0x05] PTE Write access is not set
```

A *DMAR* fault means the TLP travelled from node 0's NT port, through both
switches, all the way to the host IOMMU. Routing was never the problem.

### 2. IOMMU — a real blocker, and fixable

`05:02.3` is a **phantom requester ID** synthesised by the NTB's RID LUT;
there is no host PCI device at that BDF. But fault reason 0x05 means VT-d
*did* resolve a context entry and page tables for it — the PTE merely lacked
write permission. All three NTBs and all three DMA engines share **IOMMU
group 35** (the switch gives no isolation), so one identity mapping in the
NTB's domain covers the phantom RID:

```c
dom = iommu_get_domain_for_dev(&ntb_pdev->dev);
iommu_map(dom, target_pa, target_pa, size, IOMMU_READ | IOMMU_WRITE, GFP_KERNEL);
```

`compute/dma/p2pmap.c`. Effect was unambiguous — DMAR faults stopped dead and
throughput went **260 → 3945 MiB/s**, i.e. the writes were now being accepted
at full rate instead of faulting on every page.

### 3. Root complex reflection — the real blocker, not fixable

Even with the IOMMU satisfied, **the data never arrives.** The controlled
experiment (`compute/dma/run_p2p_control.sh`) writes the *same* address two
ways:

| Writer | target | node 1's buffer afterwards |
|---|---|---|
| **Host** DMA engine (`03:00.2`) | `0x383d1b000000` | `c3c3c3c3`, **1048576 / 1048576 words** |
| **Node 0** DMA engine (`01:00.3`) | `0x383d1b000000` | **0 / 1048576 words** |

The window is provably correct, because the host writes through it perfectly.
The IOMMU is provably permitting it, because the fault count does not move.
Node 0 issues at full speed (3944 MiB/s). The writes are simply **absorbed
between node 0's NT port and node 1's** — the E5-2699 v3 root complex does
not reflect an upstream write back down to a peer's BAR. Posted write, so no
error is raised; it just disappears.

## Conclusion

| Layer | Verdict |
|---|---|
| Switch routing | Fine — TLP reaches the host |
| ACS redirect | Not the blocker |
| VT-d / IOMMU | Was a blocker; **solved** with one identity mapping |
| Root complex P2P reflection | **Does not happen. Dead end.** |

The only remaining route would be making the PEX forward NT-to-NT internally
so the TLP never goes upstream. That needs PLX 8700 routing registers which
are not in the Intel source and are under NDA, on a switch where a bad write
has whole-card blast radius and a plausible EEPROM-persistence brick mode.
**Not attempted and not recommended** — especially since the ceiling would be
the same root-complex path anyway.

Use host staging. It is measured, verified, and has headroom (below).

## Safety record

All three P2P runs were harmless: no AER errors on the root port or either
switch (`UESta` all clear), no hang, DMAR faults stopped cleanly at 9, all
three nodes `net_device_ready` throughout, every module unloaded afterwards.
The write-only design is what kept it safe — do not add a read to that path.


# The engines do not contend

Before building a pipeline, the question it rests on: if one node pushes while
another pulls, do they share bandwidth or run independently?

`compute/dma/run_concurrent.sh`, same host buffer, 4 MiB x 40:

| | alone | concurrent |
|---|---:|---:|
| node 0 push | 3830 | **3939** |
| node 1 pull | 4561 | **4179** |

Combined **8118 MiB/s** across the fabric at once. node 0 was not slowed at
all; node 1 gave up 8%. They are separate PCI functions on separate NT ports
and the host link (x8 here, full duplex — [FABRIC.md](FABRIC.md)) carries
both, one direction each, so a producer/consumer pipeline reaches
the **sender's** rate, not half of it.

# Pipelined node-to-node: up to 4423 MiB/s, verified

`compute/dma/ring.c`, driver `run_ring.sh`. One 4 MiB host buffer shared by
both nodes through their own A-LUT window:

```
+0x100    ready flag ('RING' once the sender has initialised)
+0x200    u32 prod[4]
+0x300    u32 cons[4]
+0x10000  4 slots x 512 KiB
```

Sender waits for `cons[slot] >= i-NSLOT+1`, DMAs the payload in, **then** sets
`prod[slot] = i+1`. Receiver waits for `prod[slot] >= i+1`, DMAs it out, checks
it, then sets `cons[slot] = i+1`.

Ring results across **all six directions**, 128 iterations, `sampled_bad=0`
throughout, last slot fully verified (0/131072 words) on every single run.
All three nodes on one identical image:

| Path | sender | receiver |
|---|---:|---:|
| node 0 → node 1 | 3745 | 3725 |
| node 1 → node 0 | 2047 | 2039 |
| node 0 → node 2 | 3161 | 3093 |
| node 2 → node 0 | 3875 | 3769 |
| node 1 → node 2 | 2029 | 2020 |
| **node 2 → node 1** | **4515** | **4423** |

Sender and receiver land within ~2% of each other everywhere, which is the
overlap working: both engines run flat out at once.

#### The whole fabric is two numbers per node

The six ring results are **predicted by the per-node send and recv constants
above**, as `min(sender's write rate, receiver's read rate)`:

| Path | min(tx send, rx recv) | ring | efficiency |
|---|---:|---:|---:|
| node 0 → node 1 | 3997 | 3725 | 93.2% |
| node 1 → node 0 | 2058 | 2039 | 99.1% |
| node 0 → node 2 | 3129 | 3093 | 98.8% |
| node 2 → node 0 | 3949 | 3769 | 95.4% |
| node 1 → node 2 | 2058 | 2020 | 98.2% |
| node 2 → node 1 | 4849 | 4423 | 91.2% |

**Mean 96.0%.** So the ring protocol has essentially no overhead left, and
any node-to-node rate on this card can be predicted from six numbers measured
once. The two least efficient rows are the two where sender and receiver are
most mismatched (5642 vs 4849, and 3997 vs 4849) — the faster side spends
more time waiting, which is what you would expect.

Practical consequence: **there is nothing left to win in the transport.**
Further gains have to come from the per-node asymmetry, not the protocol.

### Honest comparison against the IP path

Same pair, same day, same jumbo MTU (see [FABRIC.md](FABRIC.md)):

| node 1 ↔ node 2 | node 1 → node 2 | node 2 → node 1 |
|---|---:|---:|
| virtio-net, MTU 1500 | 2810 Mbit/s (344 MiB/s) | 2660 Mbit/s (317 MiB/s) |
| virtio-net, MTU 65521 | 7954–15934 Mbit/s | 12971–25583 Mbit/s |
| **DMA pipelined ring** | **2020 MiB/s** (16.9 Gbit/s) | **4423 MiB/s** (37.1 Gbit/s) |

So against the *old* MTU-1500 baseline the ring is 6.1x–13.8x. Against jumbo
virtio it is roughly **1.4x–2.2x on throughput** — much less dramatic. The
durable advantages are the other two:

- **It costs no CPU.** virtio at jumbo saturates a core; the ring is two DMA
  engines and a poll.
- **It is consistent.** virtio at jumbo ranged 7954–25583 Mbit/s run to run;
  the ring varied by under 2%.

## Three things that cost real time to learn

**1. Never assume a shared buffer starts zeroed.** The first version put
`prod[0]` at control offset 0 — where `hostbuf.c` stamps its `HBUF` magic. The
receiver read `prod[0] = 0x48425546`, concluded slot 0 was ready, raced ahead
writing `cons[]`, and then the sender's initialisation zeroed those values.
Both sides deadlocked. Hence the ready-flag handshake and the fields kept
clear of offset 0.

**2. Order the handshake: clear ready, zero counters, publish ready last.**
Otherwise a receiver already spinning acts on half-initialised state.

**3. The payload fill was the bottleneck, not the fabric.** The first working
version managed only 2025 MiB/s because the sender refilled 512 KiB of local
buffer with a CPU memset-style loop every iteration (~50 us against a ~134 us
DMA). Filling once and stamping three per-iteration signature words took it to
**3833**. Verification is just as strong: the signatures still catch a stale
or wrong slot, and the bulk is checked against a position-only pattern.

**Ordering note.** The flag is a CPU write through the BAR while the payload
goes via the DMA engine — two different paths, so the flag can overtake the
data. `dma_sync_wait()` the payload *before* writing the flag. The counters are
monotonic, so a late-visible flag only makes the peer spin a little longer; it
can never expose stale data.

## What a real transport still needs

Everything is now measured; only engineering remains.

- **Doorbells instead of polling.** Control reads are CPU reads across the NTB
  (~4 us each, the slow non-posted path). Fine at one per 512 KiB slot,
  wasteful for small messages. The doorbell path is already proven at ~35 us
  RTT ([ALUT_MBOX.md](ALUT_MBOX.md)).
- **Deeper DMA pipelining.** `ring.c` waits on each payload before flagging.
  Submitting several descriptors and flagging on completion callbacks would
  close the remaining gap to ~4000.
- **Multiple windows / queues** rather than one ring per node pair.
- **Persistence.** All of this is throwaway modules; a real driver would own
  the window, the doorbell and the ring.
