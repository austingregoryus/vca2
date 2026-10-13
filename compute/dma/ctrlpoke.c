// SPDX-License-Identifier: GPL-2.0
/* ctrlpoke.ko -- read or write one u32 in a node's A-LUT window, to test
 * whether two nodes mapping the same host buffer really see each other.
 *   insmod ctrlpoke.ko peer_off=0x.. off=0  val=0xdead do_write=1
 *   insmod ctrlpoke.ko peer_off=0x.. off=16            (read)
 */
#include <linux/io.h>
#include <linux/module.h>
#include <linux/pci.h>

static unsigned int ntb_did = 0x2958;
static unsigned long peer_off;
static unsigned int off;
static unsigned int val;
static int do_write;
module_param(ntb_did, uint, 0444);
module_param(peer_off, ulong, 0444);
module_param(off, uint, 0444);
module_param(val, uint, 0444);
module_param(do_write, int, 0444);

static int __init cp_init(void)
{
	struct pci_dev *p = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	void __iomem *w;
	phys_addr_t pa;

	if (!p) { pr_err("ctrlpoke: no NTB\n"); return -ENODEV; }
	pa = pci_resource_start(p, 2) + peer_off;
	w = ioremap(pa, 0x1000);
	if (!w) { pci_dev_put(p); return -ENOMEM; }
	if (do_write) {
		iowrite32(val, w + off);
		wmb();
		pr_info("ctrlpoke: WROTE 0x%08x at pa 0x%llx off %u (readback 0x%08x)\n",
			val, (u64)pa, off, ioread32(w + off));
	} else {
		pr_info("ctrlpoke: READ pa 0x%llx off %u = 0x%08x\n",
			(u64)pa, off, ioread32(w + off));
	}
	iounmap(w);
	pci_dev_put(p);
	return -EAGAIN;   /* do not stay loaded */
}
module_init(cp_init);
MODULE_LICENSE("GPL");
