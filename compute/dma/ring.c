// SPDX-License-Identifier: GPL-2.0
/*
 * ring.ko -- pipelined node-to-node transfer over a host-staged ring buffer.
 *
 * run_concurrent.sh showed the two card-side engines do not contend (3939 +
 * 4179 = 8118 MiB/s of simultaneous traffic), so a producer/consumer pipeline
 * should reach the SENDER's rate rather than half of it. Serial send-then-
 * receive gave ~2180 MiB/s; this should get close to ~3900.
 *
 * Layout of the shared host buffer, addressed by both nodes through their own
 * A-LUT window:
 *
 *   +0x100    ready flag ('RING' once the sender has initialised)
 *   +0x200    u32 prod[NSLOT]
 *   +0x300    u32 cons[NSLOT]
 *   +CTRL_SZ  slot 0 .. slot NSLOT-1, SLOT_SZ each
 *
 * Sender:   wait cons[slot] >= i-NSLOT+1 ; DMA data in ; THEN set prod[slot]=i+1
 * Receiver: wait prod[slot] >= i+1       ; DMA data out ; THEN set cons[slot]=i+1
 *
 * ORDERING: the flag is written by the CPU through the BAR window while the
 * payload goes via the DMA engine -- two different paths, so the flag could
 * overtake the data. We therefore dma_sync_wait() the payload BEFORE writing
 * the flag. The counters are monotonic, so a late-visible flag only makes the
 * peer spin slightly longer; it can never expose stale data.
 *
 * Control reads are CPU reads across the NTB (~4 us each, the slow non-posted
 * path) but they are 4 bytes and only once per slot, so they do not matter.
 *
 *   node A: insmod ring.ko mode=0 peer_off=0x... ntb_did=0x2958 tag=0x5a
 *   node B: insmod ring.ko mode=1 peer_off=0x... ntb_did=0x2959 tag=0x5a
 */
#include <linux/delay.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/pci.h>

#define NSLOT    4
#define CTRL_SZ  0x10000UL		/* 64 KiB, keeps slots page aligned */
#define SLOT_SZ  0x80000UL		/* 512 KiB */
#define WIN_SZ   (CTRL_SZ + NSLOT * SLOT_SZ)

/*
 * Control page layout. Offset 0 is DELIBERATELY unused: hostbuf.c stamps its
 * own 'HBUF' magic there, and the first version of this code put prod[0] at
 * offset 0. The receiver read prod[0] = 0x48425546, decided slot 0 was ready,
 * ran ahead writing cons[] -- and then the sender's zeroing of the control
 * page wiped those cons values, deadlocking both sides. Keep our fields clear
 * of offset 0, and never assume a shared buffer starts out zeroed.
 */
#define RING_READY_OFF  0x100
#define PROD_OFF        0x200
#define CONS_OFF        0x300
#define RING_MAGIC      0x52494e47u	/* 'RING' */

static unsigned int ntb_did = 0x2958;
static unsigned int dma_did = 0x2952;
static unsigned long peer_off;
static int mode;			/* 0 = send, 1 = recv */
static unsigned int tag = 0x5a;
static int iters = 64;
static int spin_ms = 5000;
module_param(ntb_did, uint, 0444);
module_param(dma_did, uint, 0444);
module_param(peer_off, ulong, 0444);
module_param(mode, int, 0444);
module_param(tag, uint, 0444);
module_param(iters, int, 0444);
module_param(spin_ms, int, 0444);

static struct pci_dev *ntb_pdev, *dma_pdev;
static struct dma_chan *chan;
static void *loc_cpu;
static dma_addr_t loc_dma;
static phys_addr_t win_pa;
static void __iomem *ctrl;

struct plx_dma_device_head {
	struct pci_dev *pdev;
	struct dma_device dma_dev;
};

/*
 * Bulk pattern is position-only so the sender can fill the payload ONCE
 * instead of once per iteration -- a 512 KiB CPU fill per slot cost ~50 us
 * against a ~134 us DMA and was the pipeline's real bottleneck (2025 MiB/s).
 * The iteration number is carried by three signature words instead, which
 * still catches a stale or wrong slot.
 */
static u32 pat(unsigned int t, unsigned long w)
{
	return (t << 24) ^ (u32)(w * 40503u);
}

static u32 sig(unsigned int t, unsigned long i, unsigned long w)
{
	return (t << 24) ^ (u32)((i * 2654435761u) + w * 40503u);
}

static struct dma_chan *find_chan(void)
{
	struct plx_dma_device_head *d = pci_get_drvdata(dma_pdev);
	struct dma_device *dd;
	struct dma_chan *c;

	if (!d || d->pdev != dma_pdev)
		return NULL;
	dd = &d->dma_dev;
	if (dd->dev != &dma_pdev->dev || list_empty(&dd->channels) ||
	    !dd->device_prep_dma_memcpy)
		return NULL;
	c = list_first_entry(&dd->channels, struct dma_chan, device_node);
	return (c->device == dd) ? c : NULL;
}

static int xfer(dma_addr_t dst, dma_addr_t src, size_t len)
{
	struct dma_device *dd = chan->device;
	struct dma_async_tx_descriptor *tx;
	dma_cookie_t ck;

	tx = dd->device_prep_dma_memcpy(chan, dst, src, len, DMA_PREP_FENCE);
	if (!tx)
		return -ENOMEM;
	ck = tx->tx_submit(tx);		/* releases prep_lock */
	if (dma_submit_error(ck))
		return -EIO;
	return dma_sync_wait(chan, ck) == DMA_COMPLETE ? 0 : -ETIMEDOUT;
}

static u32 rd_prod(int s) { return ioread32(ctrl + PROD_OFF + 4 * s); }
static u32 rd_cons(int s) { return ioread32(ctrl + CONS_OFF + 4 * s); }
static void wr_prod(int s, u32 v) { iowrite32(v, ctrl + PROD_OFF + 4 * s); wmb(); }
static void wr_cons(int s, u32 v) { iowrite32(v, ctrl + CONS_OFF + 4 * s); wmb(); }
static u32 rd_ready(void) { return ioread32(ctrl + RING_READY_OFF); }
static void wr_ready(u32 v) { iowrite32(v, ctrl + RING_READY_OFF); wmb(); }

static int wait_until(int is_prod, int slot, u32 want)
{
	unsigned long end = jiffies + msecs_to_jiffies(spin_ms);

	for (;;) {
		u32 v = is_prod ? rd_prod(slot) : rd_cons(slot);

		if ((s32)(v - want) >= 0)
			return 0;
		if (time_after(jiffies, end)) {
			pr_err("ring: timeout slot %d want %u got %u\n",
			       slot, want, v);
			return -ETIMEDOUT;
		}
		cpu_relax();
	}
}

static int __init ring_init(void)
{
	ktime_t t0;
	s64 ns;
	unsigned long i, w, words = SLOT_SZ / 4;
	u32 *p;
	int slot, err = 0, s;
	unsigned long bad = 0;

	if (!peer_off || (peer_off & 63))
		return -EINVAL;

	ntb_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	dma_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, dma_did, NULL);
	if (!ntb_pdev || !dma_pdev) {
		pr_err("ring: missing NTB/DMA\n");
		goto fail;
	}
	if (peer_off + WIN_SZ > pci_resource_len(ntb_pdev, 2)) {
		pr_err("ring: window past BAR2\n");
		goto fail;
	}
	win_pa = pci_resource_start(ntb_pdev, 2) + peer_off;

	chan = find_chan();
	if (!chan) {
		pr_err("ring: channel validation failed\n");
		goto fail;
	}
	/* Only the control page is CPU-mapped; payload never is. */
	ctrl = ioremap(win_pa, CTRL_SZ);
	if (!ctrl) {
		pr_err("ring: ioremap ctrl failed\n");
		goto fail;
	}
	loc_cpu = dma_alloc_coherent(&dma_pdev->dev, SLOT_SZ, &loc_dma, GFP_KERNEL);
	if (!loc_cpu)
		goto fail_unmap;
	p = loc_cpu;
	if (mode == 0)
		for (w = 0; w < words; w++)	/* fill once, not per iteration */
			p[w] = pat(tag, w);

	pr_info("ring: mode=%s win=0x%llx slots=%d x %luKiB iters=%d\n",
		mode ? "RECV" : "SEND", (u64)win_pa, NSLOT, SLOT_SZ >> 10, iters);

	if (mode == 0) {
		/*
		 * Sender owns the control page. Clear the ready flag FIRST so a
		 * receiver already spinning cannot act on a stale value, then
		 * zero the counters, then publish ready LAST.
		 */
		wr_ready(0);
		for (s = 0; s < NSLOT; s++) {
			wr_prod(s, 0);
			wr_cons(s, 0);
		}
		wr_ready(RING_MAGIC);
		pr_info("ring: ctrl initialised and published, starting\n");
	} else {
		unsigned long end = jiffies + msecs_to_jiffies(spin_ms);

		/* Wait on the handshake, never on a data flag. */
		while (rd_ready() != RING_MAGIC) {
			if (time_after(jiffies, end)) {
				pr_err("ring: sender never published ready (saw 0x%08x)\n",
				       rd_ready());
				goto out;
			}
			cpu_relax();
		}
		pr_info("ring: sender ready, starting\n");
	}

	t0 = ktime_get();
	for (i = 0; i < (unsigned long)iters; i++) {
		slot = i % NSLOT;
		if (mode == 0) {
			if (i >= NSLOT) {
				err = wait_until(0, slot, i - NSLOT + 1);
				if (err)
					break;
			}
			/* payload already filled; just stamp the signatures */
			p[0] = sig(tag, i, 0);
			p[words / 2] = sig(tag, i, words / 2);
			p[words - 1] = sig(tag, i, words - 1);
			err = xfer((dma_addr_t)(win_pa + CTRL_SZ + slot * SLOT_SZ),
				   loc_dma, SLOT_SZ);
			if (err)
				break;
			wr_prod(slot, i + 1);	/* only after the payload landed */
		} else {
			err = wait_until(1, slot, i + 1);
			if (err)
				break;
			err = xfer(loc_dma,
				   (dma_addr_t)(win_pa + CTRL_SZ + slot * SLOT_SZ),
				   SLOT_SZ);
			if (err)
				break;
			/* signatures prove this is slot i, not a stale one */
			if (p[0] != sig(tag, i, 0) ||
			    p[words / 2] != sig(tag, i, words / 2) ||
			    p[words - 1] != sig(tag, i, words - 1))
				bad++;
			wr_cons(slot, i + 1);
		}
	}
	ns = ktime_to_ns(ktime_sub(ktime_get(), t0));
	if (ns <= 0)
		ns = 1;

	if (err) {
		pr_err("ring: aborted at iter %lu err %d\n", i, err);
		goto out;
	}

	if (mode == 1) {
		/* full verify of the last slot: bulk is position-only, plus sigs */
		unsigned long full_bad = 0;

		for (w = 1; w < words - 1; w++) {
			if (w == words / 2)
				continue;
			if (p[w] != pat(tag, w))
				full_bad++;
		}
		if (p[0] != sig(tag, iters - 1, 0) ||
		    p[words / 2] != sig(tag, iters - 1, words / 2) ||
		    p[words - 1] != sig(tag, iters - 1, words - 1))
			full_bad++;
		pr_info("ring: RECV %d x %luKiB  %llu MiB/s  sampled_bad=%lu  last_slot_full=%s (%lu/%lu)\n",
			iters, SLOT_SZ >> 10,
			(u64)((u64)SLOT_SZ * iters * 1000000000ULL / ns) >> 20,
			bad, full_bad ? "CORRUPT" : "VERIFIED", full_bad, words);
	} else {
		pr_info("ring: SEND %d x %luKiB  %llu MiB/s\n",
			iters, SLOT_SZ >> 10,
			(u64)((u64)SLOT_SZ * iters * 1000000000ULL / ns) >> 20);
	}
out:
	return 0;

fail_unmap:
	iounmap(ctrl);
	ctrl = NULL;
fail:
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	return -ENODEV;
}

static void __exit ring_exit(void)
{
	if (loc_cpu)
		dma_free_coherent(&dma_pdev->dev, SLOT_SZ, loc_cpu, loc_dma);
	if (ctrl)
		iounmap(ctrl);
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	pr_info("ring: unloaded\n");
}

module_init(ring_init);
module_exit(ring_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 pipelined node-to-node ring over host-staged DMA");
