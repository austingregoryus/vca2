// SPDX-License-Identifier: GPL-2.0
/*
 * Guest A-LUT helper. Xenial 4.14. Does not touch DMA debugfs.
 *
 * do_peek=1  phys_to_virt(host_dma) first word (outbound-proof)
 * do_map=1   plx_add_a_lut_entry(host_dma) so BAR2 sees host mailbox
 * do_alloc=1 dma_alloc_coherent one segment + /dev/vca_gbuf mmap
 *
 * pci_did: 0x2958 node0, 0x2959 node1, 0x295A node2
 * host_sz: 0x2000000 nodes 0/1, 0x1000000 node 2
 */
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/gfp.h>
#include <linux/kallsyms.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/uaccess.h>

#define MAGIC 0x414c5554u

static unsigned long host_dma = 0x4c000000UL;
static unsigned long host_sz = 0x2000000UL;
static unsigned int pci_did = 0x2958;
static int do_map;
static int do_alloc;
static int do_peek;
module_param(host_dma, ulong, 0444);
module_param(host_sz, ulong, 0444);
module_param(pci_did, uint, 0444);
module_param(do_map, int, 0444);
module_param(do_alloc, int, 0444);
module_param(do_peek, int, 0444);

typedef int (*add_fn)(void *xdev, dma_addr_t addr, size_t size, dma_addr_t *out);
typedef void (*del_fn)(void *xdev, dma_addr_t addr);

static struct pci_dev *g_pdev;
static void *xdev;
static dma_addr_t peer;
static add_fn add_entry;
static del_fn del_entry;
static int mapped;

static void *gbuf_cpu;
static unsigned long gbuf_phys;
static unsigned int gbuf_order;
static struct miscdevice gbuf_misc;
static char gbuf_name[] = "vca_gbuf";
static int gbuf_ok;

static int gbuf_mmap(struct file *filp, struct vm_area_struct *vma)
{
	unsigned long pfn;
	unsigned long len = vma->vm_end - vma->vm_start;

	if (!gbuf_ok)
		return -ENODEV;
	if (len > host_sz)
		return -EINVAL;
	pfn = gbuf_phys >> PAGE_SHIFT;
	return remap_pfn_range(vma, vma->vm_start, pfn, len, vma->vm_page_prot);
}

static const struct file_operations gbuf_fops = {
	.owner = THIS_MODULE,
	.mmap = gbuf_mmap,
};

static int __init gmap_init(void)
{
	resource_size_t bar2;
	int err;

	pr_info("alut_gmap: did=0x%x do_map=%d do_alloc=%d do_peek=%d dma=0x%lx sz=0x%lx\n",
		pci_did, do_map, do_alloc, do_peek, host_dma, host_sz);

	g_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, pci_did, NULL);
	if (!g_pdev) {
		pr_err("alut_gmap: no 8086:%04x\n", pci_did);
		return -ENODEV;
	}
	xdev = pci_get_drvdata(g_pdev);
	bar2 = pci_resource_start(g_pdev, 2);
	if (!xdev) {
		pr_err("alut_gmap: no drvdata\n");
		pci_dev_put(g_pdev);
		g_pdev = NULL;
		return -ENODEV;
	}

	if (do_peek) {
		u32 v = 0;
		void *va = phys_to_virt(host_dma);

		if (probe_kernel_read(&v, va, sizeof(v)))
			pr_info("alut_gmap: guest_pa 0x%lx unreadable\n", host_dma);
		else
			pr_info("alut_gmap: guest_pa 0x%lx word0=0x%08x%s\n",
				host_dma, v, v == MAGIC ? " MAGIC" : "");
	}

	if (do_alloc) {
		unsigned long i, npages;

		gbuf_order = get_order((unsigned long)host_sz);
		gbuf_cpu = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO,
						    gbuf_order);
		if (!gbuf_cpu) {
			pr_err("alut_gmap: __get_free_pages order %u failed\n",
			       gbuf_order);
			err = -ENOMEM;
			goto fail;
		}
		gbuf_phys = virt_to_phys(gbuf_cpu);
		npages = 1UL << gbuf_order;
		for (i = 0; i < npages; i++)
			SetPageReserved(virt_to_page(gbuf_cpu) + i);
		*(u32 *)gbuf_cpu = MAGIC;
		gbuf_misc.minor = MISC_DYNAMIC_MINOR;
		gbuf_misc.name = gbuf_name;
		gbuf_misc.fops = &gbuf_fops;
		err = misc_register(&gbuf_misc);
		if (err) {
			for (i = 0; i < npages; i++)
				ClearPageReserved(virt_to_page(gbuf_cpu) + i);
			free_pages((unsigned long)gbuf_cpu, gbuf_order);
			gbuf_cpu = NULL;
			goto fail;
		}
		gbuf_ok = 1;
		pr_info("alut_gmap: gbuf dma=0x%lx /dev/%s order=%u\n",
			gbuf_phys, gbuf_name, gbuf_order);
	}

	if (do_map) {
		add_entry = (add_fn)kallsyms_lookup_name("plx_add_a_lut_entry");
		del_entry = (del_fn)kallsyms_lookup_name("plx_del_a_lut_entry");
		if (!add_entry) {
			pr_err("alut_gmap: plx_add_a_lut_entry not in kallsyms\n");
			err = -ENOENT;
			goto fail;
		}
		if (add_entry(xdev, (dma_addr_t)host_dma, (size_t)host_sz, &peer)) {
			pr_err("alut_gmap: add_entry failed\n");
			err = -EIO;
			goto fail;
		}
		mapped = 1;
		pr_info("alut_gmap: LUT host_dma=0x%lx -> peer_off=0x%llx bar2=0x%llx\n",
			host_dma, (u64)peer, (u64)bar2);
	}
	return 0;

fail:
	if (gbuf_ok) {
		unsigned long i, npages = 1UL << gbuf_order;

		misc_deregister(&gbuf_misc);
		for (i = 0; i < npages; i++)
			ClearPageReserved(virt_to_page(gbuf_cpu) + i);
		free_pages((unsigned long)gbuf_cpu, gbuf_order);
		gbuf_ok = 0;
		gbuf_cpu = NULL;
	}
	pci_dev_put(g_pdev);
	g_pdev = NULL;
	return err;
}

static void __exit gmap_exit(void)
{
	if (mapped && del_entry) {
		del_entry(xdev, peer);
		pr_info("alut_gmap: del_entry peer_off=0x%llx\n", (u64)peer);
	}
	if (gbuf_ok) {
		unsigned long i, npages = 1UL << gbuf_order;

		misc_deregister(&gbuf_misc);
		for (i = 0; i < npages; i++)
			ClearPageReserved(virt_to_page(gbuf_cpu) + i);
		free_pages((unsigned long)gbuf_cpu, gbuf_order);
		pr_info("alut_gmap: freed gbuf dma=0x%lx\n", gbuf_phys);
	}
	if (g_pdev)
		pci_dev_put(g_pdev);
}

module_init(gmap_init);
module_exit(gmap_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 guest A-LUT map / dma buffer");
