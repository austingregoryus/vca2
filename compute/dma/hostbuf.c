// SPDX-License-Identifier: GPL-2.0
/*
 * hostbuf.ko -- host side. Allocate one coherent DMA buffer and publish its
 * bus address so a guest can program an A-LUT window onto it.
 *
 * This exists so the DMA experiments do NOT need the patched plx87xx with
 * plx_mbox.c. It touches no VCA driver, no debugfs of plx87xx/plx87xx_dma,
 * and no BAR. Loading and unloading it cannot affect the card.
 *
 *   insmod hostbuf.ko size=0x400000
 *   cat /sys/kernel/debug/vca_hostbuf/info     # dma address to give the guest
 *   mmap /dev/vca_hostbuf                      # verify patterns from userspace
 *   rmmod hostbuf                              # AFTER the guest drops its LUT
 *
 * WARNING: unload order matters. The guest A-LUT entry points at this
 * buffer's bus address. Free it while the guest window is still programmed
 * and the guest can write into recycled host memory. Always rmmod the guest
 * alut_gmap first.
 */
#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/seq_file.h>
#include <linux/uaccess.h>

#define HB_MAGIC 0x48425546u /* 'HBUF' */

static unsigned long size = 0x400000UL;	/* 4 MiB */
static unsigned int pci_did = 0x2954;	/* NTB whose DMA mask/dev we borrow */
module_param(size, ulong, 0444);
module_param(pci_did, uint, 0444);

static struct pci_dev *anchor;
static void *cpu;
static dma_addr_t dma;
static struct dentry *dbg;

static int hb_mmap(struct file *filp, struct vm_area_struct *vma)
{
	unsigned long len = vma->vm_end - vma->vm_start;

	if (!cpu)
		return -ENODEV;
	if (vma->vm_pgoff)
		return -EINVAL;
	if (len > size)
		return -EINVAL;
	return dma_mmap_coherent(&anchor->dev, vma, cpu, dma, len);
}

static const struct file_operations hb_fops = {
	.owner = THIS_MODULE,
	.mmap = hb_mmap,
};

static struct miscdevice hb_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "vca_hostbuf",
	.fops = &hb_fops,
};

static int hb_info_show(struct seq_file *s, void *p)
{
	seq_printf(s, "dma   0x%llx\n", (u64)dma);
	seq_printf(s, "size  0x%lx\n", size);
	seq_printf(s, "magic 0x%08x\n", *(u32 *)cpu);
	seq_printf(s, "word1 0x%08x\n", *((u32 *)cpu + 1));
	seq_puts(s, "\nguest: insmod alut_gmap.ko do_map=1 ");
	seq_printf(s, "host_dma=0x%llx host_sz=0x%lx pci_did=0x2958\n",
		   (u64)dma, size);
	return 0;
}

static int hb_info_open(struct inode *i, struct file *f)
{
	return single_open(f, hb_info_show, NULL);
}

static const struct file_operations hb_info_fops = {
	.owner = THIS_MODULE,
	.open = hb_info_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init hb_init(void)
{
	int err;

	if (size < PAGE_SIZE || size > (64UL << 20))
		return -EINVAL;

	anchor = pci_get_device(PCI_VENDOR_ID_INTEL, pci_did, NULL);
	if (!anchor) {
		pr_err("hostbuf: no 8086:%04x to anchor the mapping\n", pci_did);
		return -ENODEV;
	}

	cpu = dma_alloc_coherent(&anchor->dev, size, &dma, GFP_KERNEL);
	if (!cpu) {
		pci_dev_put(anchor);
		return -ENOMEM;
	}
	memset(cpu, 0, size);
	*(u32 *)cpu = HB_MAGIC;

	err = misc_register(&hb_misc);
	if (err)
		goto out_free;

	dbg = debugfs_create_dir("vca_hostbuf", NULL);
	if (!IS_ERR_OR_NULL(dbg))
		debugfs_create_file("info", 0444, dbg, NULL, &hb_info_fops);

	pr_info("hostbuf: dma=0x%llx size=0x%lx /dev/vca_hostbuf\n",
		(u64)dma, size);
	return 0;

out_free:
	dma_free_coherent(&anchor->dev, size, cpu, dma);
	cpu = NULL;
	pci_dev_put(anchor);
	return err;
}

static void __exit hb_exit(void)
{
	if (!IS_ERR_OR_NULL(dbg))
		debugfs_remove_recursive(dbg);
	misc_deregister(&hb_misc);
	if (cpu)
		dma_free_coherent(&anchor->dev, size, cpu, dma);
	if (anchor)
		pci_dev_put(anchor);
	pr_info("hostbuf: unloaded\n");
}

module_init(hb_init);
module_exit(hb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 host coherent buffer for A-LUT / DMA experiments");
