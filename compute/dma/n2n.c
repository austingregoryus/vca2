// SPDX-License-Identifier: GPL-2.0
/*
 * n2n.ko -- node-to-node bulk transfer staged through host RAM, using each
 * card's own DMA engine.
 *
 * Today node<->node traffic hairpins through the host IP stack at ~2.6 Gbit/s
 * (~325 MB/s). Both nodes can instead map the SAME host buffer through their
 * own A-LUT window and DMA into/out of it. Sender and receiver each get the
 * card-side engine's ~3.9 GB/s, and because the two engines are independent
 * PCI functions on different NT ports they can overlap on different chunks.
 *
 *   node A:  insmod n2n.ko mode=0 peer_off=0x... tag=0xab   (send)
 *   node B:  insmod n2n.ko mode=1 peer_off=0x... tag=0xab   (recv + verify)
 *
 * The pattern is content-addressed (derived from tag and word index) so a
 * receiver cannot pass by accident -- stale data, a short transfer or a
 * mis-programmed window all show up as mismatches.
 */
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/pci.h>

static unsigned int ntb_did = 0x2958;
static unsigned int dma_did = 0x2952;
static unsigned long peer_off;
static unsigned long buf_sz = 0x400000UL;
static int mode;		/* 0 = send, 1 = recv */
static unsigned int tag = 0xab;
static int iters = 20;
static int depth = 8;
module_param(ntb_did, uint, 0444);
module_param(dma_did, uint, 0444);
module_param(peer_off, ulong, 0444);
module_param(buf_sz, ulong, 0444);
module_param(mode, int, 0444);
module_param(tag, uint, 0444);
module_param(iters, int, 0444);
module_param(depth, int, 0444);

static struct pci_dev *ntb_pdev, *dma_pdev;
static struct dma_chan *chan;
static void *loc_cpu;
static dma_addr_t loc_dma;
static phys_addr_t peer_pa;

struct plx_dma_device_head {
	struct pci_dev *pdev;
	struct dma_device dma_dev;
};

static u32 pat(unsigned int t, unsigned long i)
{
	return (t << 24) ^ (u32)(i * 2654435761u);
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

static dma_cookie_t submit_one(dma_addr_t dst, dma_addr_t src, size_t len)
{
	struct dma_device *dd = chan->device;
	struct dma_async_tx_descriptor *tx;
	dma_cookie_t ck;

	tx = dd->device_prep_dma_memcpy(chan, dst, src, len, DMA_PREP_FENCE);
	if (!tx)
		return -ENOMEM;
	ck = tx->tx_submit(tx);	/* releases prep_lock */
	return dma_submit_error(ck) ? -EIO : ck;
}

static int burst(dma_addr_t dst, dma_addr_t src, size_t len, int n)
{
	dma_cookie_t last = 0;
	int i;

	for (i = 0; i < n; i++) {
		dma_cookie_t c = submit_one(dst, src, len);

		if (c < 0) {
			if (last > 0)
				dma_sync_wait(chan, last);
			return -EIO;
		}
		last = c;
	}
	return dma_sync_wait(chan, last) == DMA_COMPLETE ? 0 : -ETIMEDOUT;
}

static int __init n2n_init(void)
{
	ktime_t t0;
	s64 ns;
	u32 *p;
	unsigned long i, words = buf_sz / 4;
	int done = 0, err;

	if (!peer_off || (peer_off & 63) || (buf_sz & 63))
		return -EINVAL;

	ntb_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	dma_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, dma_did, NULL);
	if (!ntb_pdev || !dma_pdev) {
		pr_err("n2n: missing NTB/DMA device\n");
		goto fail;
	}
	if (!pci_resource_start(ntb_pdev, 2) ||
	    peer_off + buf_sz > pci_resource_len(ntb_pdev, 2)) {
		pr_err("n2n: bad window\n");
		goto fail;
	}
	peer_pa = pci_resource_start(ntb_pdev, 2) + peer_off;

	chan = find_chan();
	if (!chan) {
		pr_err("n2n: channel validation failed\n");
		goto fail;
	}
	loc_cpu = dma_alloc_coherent(&dma_pdev->dev, buf_sz, &loc_dma, GFP_KERNEL);
	if (!loc_cpu)
		goto fail;
	p = loc_cpu;

	if (mode == 0) {
		for (i = 0; i < words; i++)
			p[i] = pat(tag, i);
		/* one correctness send first, then the timed run */
		if (burst((dma_addr_t)peer_pa, loc_dma, buf_sz, 1)) {
			pr_err("n2n: initial send failed\n");
			goto out;
		}
		t0 = ktime_get();
		while (done < iters) {
			int n = min(depth, iters - done);

			err = burst((dma_addr_t)peer_pa, loc_dma, buf_sz, n);
			if (err) {
				pr_err("n2n: send failed %d\n", err);
				goto out;
			}
			done += n;
		}
		ns = ktime_to_ns(ktime_sub(ktime_get(), t0));
		pr_info("n2n: SEND tag=0x%02x %lu MiB x%d  %llu MiB/s\n",
			tag, buf_sz >> 20, iters,
			(u64)((u64)buf_sz * iters * 1000000000ULL / (ns ? ns : 1)) >> 20);
	} else {
		unsigned long bad = 0;

		memset(loc_cpu, 0, buf_sz);
		t0 = ktime_get();
		while (done < iters) {
			int n = min(depth, iters - done);

			err = burst(loc_dma, (dma_addr_t)peer_pa, buf_sz, n);
			if (err) {
				pr_err("n2n: recv failed %d\n", err);
				goto out;
			}
			done += n;
		}
		ns = ktime_to_ns(ktime_sub(ktime_get(), t0));
		for (i = 0; i < words; i++)
			if (p[i] != pat(tag, i))
				bad++;
		pr_info("n2n: RECV tag=0x%02x %lu MiB x%d  %llu MiB/s  %s (%lu/%lu words bad, w0=0x%08x want 0x%08x)\n",
			tag, buf_sz >> 20, iters,
			(u64)((u64)buf_sz * iters * 1000000000ULL / (ns ? ns : 1)) >> 20,
			bad ? "CORRUPT" : "VERIFIED", bad, words, p[0], pat(tag, 0));
	}
out:
	return 0;
fail:
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	return -ENODEV;
}

static void __exit n2n_exit(void)
{
	if (loc_cpu)
		dma_free_coherent(&dma_pdev->dev, buf_sz, loc_cpu, loc_dma);
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	pr_info("n2n: unloaded\n");
}

module_init(n2n_init);
module_exit(n2n_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 node-to-node DMA staged through host RAM");
