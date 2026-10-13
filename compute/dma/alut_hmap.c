// SPDX-License-Identifier: GPL-2.0
/*
 * Host: add one A-LUT entry for a guest DMA address (outbound to guest RAM).
 * Looks up plx_add_a_lut_entry via kprobe (not exported on PVE 7).
 * Does not rmmod plx87xx. Does not touch DMA debugfs.
 *
 * insmod alut_hmap.ko pci_did=0x2954 guest_dma=0x... size=0x2000000
 * dmesg | grep alut_hmap   # peer_off
 * mmap /sys/bus/pci/devices/0000:05:00.0/resource2 at peer_off
 * rmmod alut_hmap
 */
#include <linux/kprobes.h>
#include <linux/module.h>
#include <linux/pci.h>

static unsigned int pci_did = 0x2954;
static unsigned long guest_dma;
static unsigned long size = 0x2000000UL;
module_param(pci_did, uint, 0444);
module_param(guest_dma, ulong, 0444);
module_param(size, ulong, 0444);

typedef int (*add_fn)(void *xdev, dma_addr_t addr, size_t len, dma_addr_t *out);
typedef void (*del_fn)(void *xdev, dma_addr_t addr);

static void *xdev;
static dma_addr_t peer;
static add_fn add_entry;
static del_fn del_entry;
static int mapped;

static unsigned long ksym(const char *name)
{
	struct kprobe kp = { .symbol_name = name };
	unsigned long addr;

	if (register_kprobe(&kp) < 0)
		return 0;
	addr = (unsigned long)kp.addr;
	unregister_kprobe(&kp);
	return addr;
}

static int __init hmap_init(void)
{
	struct pci_dev *pdev;
	resource_size_t bar2;

	if (!guest_dma || !size) {
		pr_err("alut_hmap: need guest_dma and size\n");
		return -EINVAL;
	}
	add_entry = (add_fn)ksym("plx_add_a_lut_entry");
	del_entry = (del_fn)ksym("plx_del_a_lut_entry");
	if (!add_entry) {
		pr_err("alut_hmap: no plx_add_a_lut_entry\n");
		return -ENOENT;
	}
	pdev = pci_get_device(PCI_VENDOR_ID_INTEL, pci_did, NULL);
	if (!pdev) {
		pr_err("alut_hmap: no 8086:%04x\n", pci_did);
		return -ENODEV;
	}
	xdev = pci_get_drvdata(pdev);
	bar2 = pci_resource_start(pdev, 2);
	pci_dev_put(pdev);
	if (!xdev) {
		pr_err("alut_hmap: no drvdata\n");
		return -ENODEV;
	}
	if (add_entry(xdev, (dma_addr_t)guest_dma, (size_t)size, &peer)) {
		pr_err("alut_hmap: add_entry failed\n");
		return -EIO;
	}
	mapped = 1;
	pr_info("alut_hmap: LUT guest_dma=0x%lx -> peer_off=0x%llx bar2=0x%llx\n",
		guest_dma, (u64)peer, (u64)bar2);
	return 0;
}

static void __exit hmap_exit(void)
{
	if (mapped && del_entry) {
		del_entry(xdev, peer);
		pr_info("alut_hmap: del_entry peer_off=0x%llx\n", (u64)peer);
	}
}

module_init(hmap_init);
module_exit(hmap_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 host A-LUT map of guest DMA buffer");
