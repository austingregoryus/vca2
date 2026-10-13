// SPDX-License-Identifier: GPL-2.0
/*
 * dmabench.ko -- measure the PLX DMA engine across the NTB.
 *
 * Runs on either side. Moves data between a local coherent buffer and a peer
 * window reached through the local BAR2 aperture, which is exactly how
 * vop_common.c:922 forms its peer address:
 *
 *     dst = vdev->aper->pa + (remapped - aper->va)
 *
 * so peer_pa = pci_resource_start(ntb, 2) + peer_off, valid once somebody has
 * programmed an A-LUT entry for that offset.
 *
 * Why this is not the thing that hung the card before: that was reading
 * Intel's plx87xx_dma debugfs test files (dma_test / flush / cleanup /
 * issue_pending), an ad-hoc harness with NULL-deref bugs. This uses the
 * documented dmaengine client API and the same call sequence as VOP.
 *
 * Channel acquisition: the engine has ONE channel per function and plx87xx
 * already claimed it (DMA_PRIVATE, in_use=1), so dma_request_channel() would
 * fail. We borrow the same struct dma_chan the driver holds -- which is
 * already shared between VOP instances, so sharing is the normal mode. It is
 * found without touching struct plx_device: plx_pci.c does
 *
 *     pci_set_drvdata(pdev, plx_dma_dev)
 *
 * and struct plx_dma_device begins { struct pci_dev *pdev; struct dma_device
 * dma_dev; ... }. Both are standard types, and struct dma_device comes from
 * the kernel headers we compile against. Everything is then validated before
 * use, so a wrong assumption yields -EINVAL instead of an oops.
 *
 * Locking note: plx_dma_prep_memcpy_lock() takes prep_lock and RELEASES IT IN
 * tx_submit(). A successful prep must always be followed by tx_submit or the
 * channel deadlocks.
 *
 *   insmod dmabench.ko ntb_did=0x2958 dma_did=0x2952 peer_off=0x8000000
 */
#include <linux/delay.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/slab.h>

#define ALIGN_B 64UL

static unsigned int ntb_did = 0x2958;	/* guest NTB; host side use 0x2954 */
static unsigned int dma_did = 0x2952;
static unsigned long peer_off;		/* offset into BAR2 of the A-LUT window */
static unsigned long buf_sz = 0x400000UL;	/* 4 MiB (guest MAX_ORDER limit) */
static int iters = 20;
static int verify = 1;
module_param(ntb_did, uint, 0444);
module_param(dma_did, uint, 0444);
module_param(peer_off, ulong, 0444);
module_param(buf_sz, ulong, 0444);
module_param(iters, int, 0444);
module_param(verify, int, 0444);

static struct pci_dev *ntb_pdev, *dma_pdev;
static struct dma_chan *chan;
static void *loc_cpu;
static dma_addr_t loc_dma;
static phys_addr_t peer_pa;

/* Only the first two members are needed and both are standard types. */
struct plx_dma_device_head {
	struct pci_dev *pdev;
	struct dma_device dma_dev;
};

static struct dma_chan *find_chan(void)
{
	struct plx_dma_device_head *d;
	struct dma_device *dd;
	struct dma_chan *c;

	d = pci_get_drvdata(dma_pdev);
	if (!d) {
		pr_err("dmabench: DMA function has no drvdata\n");
		return NULL;
	}
	if (d->pdev != dma_pdev) {
		pr_err("dmabench: drvdata->pdev mismatch (%p vs %p), layout assumption wrong\n",
		       d->pdev, dma_pdev);
		return NULL;
	}
	dd = &d->dma_dev;
	if (dd->dev != &dma_pdev->dev) {
		pr_err("dmabench: dma_device->dev mismatch, layout assumption wrong\n");
		return NULL;
	}
	if (list_empty(&dd->channels)) {
		pr_err("dmabench: no channels\n");
		return NULL;
	}
	c = list_first_entry(&dd->channels, struct dma_chan, device_node);
	if (c->device != dd) {
		pr_err("dmabench: chan->device mismatch\n");
		return NULL;
	}
	if (!dd->device_prep_dma_memcpy) {
		pr_err("dmabench: no prep_dma_memcpy\n");
		return NULL;
	}
	pr_info("dmabench: channel ok, dma dev %s, chan %p\n",
		dev_name(dd->dev), c);
	return c;
}

/* one transfer; returns 0 on success */
static int xfer(dma_addr_t dst, dma_addr_t src, size_t len)
{
	struct dma_device *dd = chan->device;
	struct dma_async_tx_descriptor *tx;
	dma_cookie_t cookie;

	tx = dd->device_prep_dma_memcpy(chan, dst, src, len, DMA_PREP_FENCE);
	if (!tx) {
		pr_err("dmabench: prep failed len=%zu\n", len);
		return -ENOMEM;
	}
	/* prep_lock is held here; tx_submit releases it. Must not bail out. */
	cookie = tx->tx_submit(tx);
	if (dma_submit_error(cookie)) {
		pr_err("dmabench: submit error %d\n", cookie);
		return -EIO;
	}
	if (dma_sync_wait(chan, cookie) != DMA_COMPLETE) {
		pr_err("dmabench: wait did not complete\n");
		return -ETIMEDOUT;
	}
	return 0;
}

static void run(const char *label, int to_peer, size_t len)
{
	ktime_t t0;
	s64 ns;
	int i, err = 0;
	dma_addr_t dst, src;

	if (to_peer) {
		dst = (dma_addr_t)peer_pa;
		src = loc_dma;
	} else {
		dst = loc_dma;
		src = (dma_addr_t)peer_pa;
	}

	/* warm up */
	if (xfer(dst, src, len))
		return;

	t0 = ktime_get();
	for (i = 0; i < iters; i++) {
		err = xfer(dst, src, len);
		if (err)
			break;
	}
	ns = ktime_to_ns(ktime_sub(ktime_get(), t0));
	if (err) {
		pr_err("dmabench: %s len=%zu FAILED %d\n", label, len, err);
		return;
	}
	if (ns <= 0)
		ns = 1;
	pr_info("dmabench: %-10s len=%8zu  %6llu MiB/s  (%lld us/xfer)\n",
		label, len,
		(u64)((u64)len * iters * 1000000000ULL / ns) >> 20,
		(long long)(ns / 1000 / iters));
}

static int __init db_init(void)
{
	size_t sizes[] = { 4096, 16384, 65536, 262144, 1048576, 4194304 };
	int i;

	if (buf_sz & (ALIGN_B - 1) || !peer_off) {
		pr_err("dmabench: need peer_off, buf_sz 64B aligned\n");
		return -EINVAL;
	}
	if (peer_off & (ALIGN_B - 1)) {
		pr_err("dmabench: peer_off must be 64B aligned\n");
		return -EINVAL;
	}

	ntb_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	if (!ntb_pdev) {
		pr_err("dmabench: no NTB 8086:%04x\n", ntb_did);
		return -ENODEV;
	}
	dma_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, dma_did, NULL);
	if (!dma_pdev) {
		pr_err("dmabench: no DMA 8086:%04x\n", dma_did);
		pci_dev_put(ntb_pdev);
		return -ENODEV;
	}

	peer_pa = pci_resource_start(ntb_pdev, 2) + peer_off;
	if (!pci_resource_start(ntb_pdev, 2)) {
		pr_err("dmabench: NTB BAR2 not assigned\n");
		goto fail;
	}
	if (peer_off + buf_sz > pci_resource_len(ntb_pdev, 2)) {
		pr_err("dmabench: window past end of BAR2\n");
		goto fail;
	}

	chan = find_chan();
	if (!chan)
		goto fail;

	loc_cpu = dma_alloc_coherent(&dma_pdev->dev, buf_sz, &loc_dma, GFP_KERNEL);
	if (!loc_cpu) {
		pr_err("dmabench: dma_alloc_coherent(0x%lx) failed\n", buf_sz);
		goto fail;
	}
	memset(loc_cpu, 0x5a, buf_sz);

	pr_info("dmabench: local dma=0x%llx  peer BAR2=0x%llx + 0x%lx = 0x%llx  iters=%d\n",
		(u64)loc_dma, (u64)pci_resource_start(ntb_pdev, 2), peer_off,
		(u64)peer_pa, iters);

	for (i = 0; i < ARRAY_SIZE(sizes); i++) {
		if (sizes[i] > buf_sz)
			break;
		run("PUSH->peer", 1, sizes[i]);
	}
	for (i = 0; i < ARRAY_SIZE(sizes); i++) {
		if (sizes[i] > buf_sz)
			break;
		run("PULL<-peer", 0, sizes[i]);
	}

	if (verify) {
		u32 *p = loc_cpu;

		memset(loc_cpu, 0, buf_sz);
		if (!xfer(loc_dma, (dma_addr_t)peer_pa, 4096))
			pr_info("dmabench: verify: first words after pull 0x%08x 0x%08x\n",
				p[0], p[1]);
	}

	pr_info("dmabench: done\n");
	return 0;

fail:
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	return -ENODEV;
}

static void __exit db_exit(void)
{
	if (loc_cpu)
		dma_free_coherent(&dma_pdev->dev, buf_sz, loc_cpu, loc_dma);
	if (dma_pdev)
		pci_dev_put(dma_pdev);
	if (ntb_pdev)
		pci_dev_put(ntb_pdev);
	pr_info("dmabench: unloaded\n");
}

module_init(db_init);
module_exit(db_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 PLX DMA engine benchmark across the NTB");
