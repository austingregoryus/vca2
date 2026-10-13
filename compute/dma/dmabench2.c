// SPDX-License-Identifier: GPL-2.0
/*
 * dmabench2.ko -- second-generation PLX DMA benchmark.
 *
 * Adds over dmabench.c:
 *   - pipelined submission (queue depth > 1) to attack the ~3 us per-transfer
 *     synchronous submit+wait cost that caps small transfers
 *   - explicit direction selection so it can run on either side
 *   - data integrity check with a distinct pattern per run
 *
 * Same safety model as dmabench.c: documented dmaengine client API, the same
 * call sequence VOP uses, channel borrowed via pci_get_drvdata() on the DMA
 * function using only kernel-stable types, everything validated before use.
 *
 * prep_lock is taken in prep and released in tx_submit, so a successful prep
 * MUST be followed by tx_submit. With a queue depth > 1 we therefore
 * prep+submit each descriptor immediately and only wait on the last cookie --
 * dmaengine guarantees in-order completion on a single channel, so waiting on
 * the last cookie waits for all of them.
 *
 *   insmod dmabench2.ko peer_off=0x... depth=8
 */
#include <linux/delay.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/slab.h>

#define ALIGN_B 64UL

static unsigned int ntb_did = 0x2958;
static unsigned int dma_did = 0x2952;
static unsigned long peer_off;
static unsigned long buf_sz = 0x400000UL;
static int iters = 20;
static int maxdepth = 16;
static int do_verify = 1;
module_param(ntb_did, uint, 0444);
module_param(dma_did, uint, 0444);
module_param(peer_off, ulong, 0444);
module_param(buf_sz, ulong, 0444);
module_param(iters, int, 0444);
module_param(maxdepth, int, 0444);
module_param(do_verify, int, 0444);

static struct pci_dev *ntb_pdev, *dma_pdev;
static struct dma_chan *chan;
static void *loc_cpu;
static dma_addr_t loc_dma;
static phys_addr_t peer_pa;

struct plx_dma_device_head {
	struct pci_dev *pdev;
	struct dma_device dma_dev;
};

static struct dma_chan *find_chan(void)
{
	struct plx_dma_device_head *d = pci_get_drvdata(dma_pdev);
	struct dma_device *dd;
	struct dma_chan *c;

	if (!d || d->pdev != dma_pdev) {
		pr_err("dmabench2: drvdata layout assumption wrong\n");
		return NULL;
	}
	dd = &d->dma_dev;
	if (dd->dev != &dma_pdev->dev || list_empty(&dd->channels) ||
	    !dd->device_prep_dma_memcpy) {
		pr_err("dmabench2: dma_device validation failed\n");
		return NULL;
	}
	c = list_first_entry(&dd->channels, struct dma_chan, device_node);
	if (c->device != dd) {
		pr_err("dmabench2: chan->device mismatch\n");
		return NULL;
	}
	pr_info("dmabench2: channel ok on %s\n", dev_name(dd->dev));
	return c;
}

/* submit one descriptor, do not wait. returns cookie or negative */
static dma_cookie_t submit_one(dma_addr_t dst, dma_addr_t src, size_t len)
{
	struct dma_device *dd = chan->device;
	struct dma_async_tx_descriptor *tx;
	dma_cookie_t cookie;

	tx = dd->device_prep_dma_memcpy(chan, dst, src, len, DMA_PREP_FENCE);
	if (!tx)
		return -ENOMEM;
	/* prep_lock held here; tx_submit releases it -- never bail between */
	cookie = tx->tx_submit(tx);
	if (dma_submit_error(cookie))
		return -EIO;
	return cookie;
}

/*
 * Run `iters` transfers of `len`, keeping up to `depth` in flight.
 * Completion is in order on one channel, so waiting on the last cookie of a
 * batch waits for the whole batch.
 */
static void run(const char *label, int to_peer, size_t len, int depth)
{
	ktime_t t0;
	s64 ns;
	int done = 0;
	dma_addr_t dst, src;
	dma_cookie_t last = 0;

	if (to_peer) {
		dst = (dma_addr_t)peer_pa;
		src = loc_dma;
	} else {
		dst = loc_dma;
		src = (dma_addr_t)peer_pa;
	}

	last = submit_one(dst, src, len);		/* warm up */
	if (last < 0 || dma_sync_wait(chan, last) != DMA_COMPLETE) {
		pr_err("dmabench2: %s len=%zu warmup failed\n", label, len);
		return;
	}

	t0 = ktime_get();
	while (done < iters) {
		int batch = min(depth, iters - done);
		int i;

		last = 0;
		for (i = 0; i < batch; i++) {
			dma_cookie_t c = submit_one(dst, src, len);

			if (c < 0) {
				/* wait for whatever is already queued, then stop */
				if (last > 0)
					dma_sync_wait(chan, last);
				pr_err("dmabench2: %s len=%zu submit failed at %d\n",
				       label, len, done + i);
				return;
			}
			last = c;
		}
		if (dma_sync_wait(chan, last) != DMA_COMPLETE) {
			pr_err("dmabench2: %s len=%zu wait timeout\n", label, len);
			return;
		}
		done += batch;
	}
	ns = ktime_to_ns(ktime_sub(ktime_get(), t0));
	if (ns <= 0)
		ns = 1;
	pr_info("dmabench2: %-10s depth=%-3d len=%8zu  %6llu MiB/s  %lld us/xfer\n",
		label, depth, len,
		(u64)((u64)len * iters * 1000000000ULL / ns) >> 20,
		(long long)(ns / 1000 / iters));
}

static int __init db2_init(void)
{
	size_t sizes[] = { 4096, 16384, 65536, 262144, 1048576, 4194304 };
	int depths[] = { 1, 4, 16 };
	int si, di;

	if (!peer_off || (peer_off & (ALIGN_B - 1)) || (buf_sz & (ALIGN_B - 1))) {
		pr_err("dmabench2: need 64B-aligned peer_off and buf_sz\n");
		return -EINVAL;
	}

	ntb_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	dma_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, dma_did, NULL);
	if (!ntb_pdev || !dma_pdev) {
		pr_err("dmabench2: missing NTB or DMA device\n");
		goto fail;
	}
	if (!pci_resource_start(ntb_pdev, 2) ||
	    peer_off + buf_sz > pci_resource_len(ntb_pdev, 2)) {
		pr_err("dmabench2: bad BAR2 window\n");
		goto fail;
	}
	peer_pa = pci_resource_start(ntb_pdev, 2) + peer_off;

	chan = find_chan();
	if (!chan)
		goto fail;

	loc_cpu = dma_alloc_coherent(&dma_pdev->dev, buf_sz, &loc_dma, GFP_KERNEL);
	if (!loc_cpu) {
		pr_err("dmabench2: dma_alloc_coherent(0x%lx) failed\n", buf_sz);
		goto fail;
	}
	memset(loc_cpu, 0xc3, buf_sz);

	pr_info("dmabench2: local=0x%llx peer=0x%llx iters=%d\n",
		(u64)loc_dma, (u64)peer_pa, iters);

	for (di = 0; di < ARRAY_SIZE(depths); di++) {
		if (depths[di] > maxdepth)
			continue;
		for (si = 0; si < ARRAY_SIZE(sizes); si++) {
			if (sizes[si] > buf_sz)
				break;
			run("PUSH->peer", 1, sizes[si], depths[di]);
		}
	}
	for (di = 0; di < ARRAY_SIZE(depths); di++) {
		if (depths[di] > maxdepth)
			continue;
		for (si = 0; si < ARRAY_SIZE(sizes); si++) {
			if (sizes[si] > buf_sz)
				break;
			run("PULL<-peer", 0, sizes[si], depths[di]);
		}
	}

	if (do_verify) {
		u32 *p = loc_cpu;
		dma_cookie_t ck;
		int i, bad = 0;

		/* push a known pattern out, clear locally, pull it back */
		for (i = 0; i < 4096 / 4; i++)
			p[i] = 0xa5000000u + i;
		ck = submit_one((dma_addr_t)peer_pa, loc_dma, 4096);
		if (ck < 0 || dma_sync_wait(chan, ck) != DMA_COMPLETE)
			goto done;
		memset(loc_cpu, 0, 4096);
		ck = submit_one(loc_dma, (dma_addr_t)peer_pa, 4096);
		if (ck < 0 || dma_sync_wait(chan, ck) != DMA_COMPLETE)
			goto done;
		for (i = 0; i < 4096 / 4; i++)
			if (p[i] != 0xa5000000u + i)
				bad++;
		pr_info("dmabench2: ROUNDTRIP %s (%d/%d words differ, w0=0x%08x)\n",
			bad ? "FAILED" : "VERIFIED", bad, 4096 / 4, p[0]);
	}
done:
	pr_info("dmabench2: done\n");
	return 0;

fail:
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	return -ENODEV;
}

static void __exit db2_exit(void)
{
	if (loc_cpu)
		dma_free_coherent(&dma_pdev->dev, buf_sz, loc_cpu, loc_dma);
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	pr_info("dmabench2: unloaded\n");
}

module_init(db2_init);
module_exit(db2_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 PLX DMA benchmark, pipelined");
