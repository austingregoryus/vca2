// SPDX-License-Identifier: GPL-2.0
/*
 * p2pmap.ko -- NOT A TOOL. This module exists only to document how a negative
 * result was proved. Do not run it expecting node-to-node P2P to work.
 *
 * VERDICT: peer-to-peer between nodes is IMPOSSIBLE on this platform. This
 * module removes the second of three blockers; the third has no software fix.
 *
 *   1. ACS      -- not a blocker. The TLP reaches the host fine.
 *   2. VT-d     -- a real blocker. THIS MODULE FIXES IT (faults stop, rate
 *                  goes 260 -> 3945 MiB/s).
 *   3. Root complex reflection -- the wall. The E5-2699 v3 will not reflect
 *                  an upstream write back down to a peer BAR. Proved with a
 *                  host-write control: same address, host engine lands
 *                  1048576/1048576 words, node 0's engine lands 0.
 *
 * Loading this buys you a weakened IOMMU and nothing else. It maps a range
 * identity-wise for an ENTIRE IOMMU group -- on this card that group holds
 * only VCA devices, but check yours before running it anywhere else.
 *
 * Blocker 3 is specific to this host's root complex. If your platform
 * behaves differently, that is a genuinely interesting result.
 *
 * See docs/DMA.md and SAFETY.md.
 *
 * --- original notes ---
 *
 * Add ONE identity IOMMU mapping so a card-side DMA engine may write into
 * another node's NTB BAR2 window.
 *
 * Background: the 4a probe showed node 0's DMA write DID route all the way to
 * the host root complex -- it was not dropped by ACS and not a switch routing
 * failure. VT-d rejected it:
 *
 *   DMAR: [DMA Write NO_PASID] Request device [05:02.3]
 *         fault addr 0x383d1b001000 [fault reason 0x05]
 *         PTE Write access is not set
 *
 * 05:02.3 is a phantom requester ID synthesised by the NTB's RID LUT, so
 * there is no struct device for it -- but fault reason 0x05 means the IOMMU
 * *did* resolve a context entry and page tables for it, the PTE just lacked
 * write permission. All three NTBs and the DMA engines share one IOMMU group
 * (35), so mapping into the NTB's domain should cover the phantom RID.
 *
 * Risk notes:
 *  - This is a software policy change only. No switch registers, no ACS
 *    change, nothing persistent, no EEPROM. It cannot damage hardware.
 *  - It does let group-35 devices write the mapped range. Keep it loaded only
 *    for the test.
 *  - IOVA collision: in a DMA-type domain the iova allocator does not know
 *    about a manually inserted mapping. iommu_map() returns -EEXIST if the
 *    range is already mapped, which catches the collision at load time; a
 *    later allocator collision would surface as a failed DMA mapping, not a
 *    brick. Unload promptly.
 *
 *   insmod p2pmap.ko ntb_did=0x2954 target_pa=0x383d1b000000 size=0x400000
 */
#include <linux/iommu.h>
#include <linux/module.h>
#include <linux/pci.h>

static unsigned int ntb_did = 0x2954;	/* host-side NTB of the SENDING node */
static unsigned long long target_pa;	/* host-domain address to allow */
static unsigned long size = 0x400000UL;
module_param(ntb_did, uint, 0444);
module_param(target_pa, ullong, 0444);
module_param(size, ulong, 0444);

static struct pci_dev *pdev;
static struct iommu_domain *dom;
static int mapped;

static const char *domtype(unsigned int t)
{
	switch (t) {
	case IOMMU_DOMAIN_BLOCKED:  return "BLOCKED";
	case IOMMU_DOMAIN_IDENTITY: return "IDENTITY (passthrough)";
	case IOMMU_DOMAIN_UNMANAGED:return "UNMANAGED";
	case IOMMU_DOMAIN_DMA:      return "DMA";
	default:                    return "other";
	}
}

static int __init p2p_init(void)
{
	int rc;

	if (!target_pa || (target_pa & ~PAGE_MASK) || (size & ~PAGE_MASK)) {
		pr_err("p2pmap: need page-aligned target_pa and size\n");
		return -EINVAL;
	}
	pdev = pci_get_device(PCI_VENDOR_ID_INTEL, ntb_did, NULL);
	if (!pdev) {
		pr_err("p2pmap: no 8086:%04x\n", ntb_did);
		return -ENODEV;
	}
	dom = iommu_get_domain_for_dev(&pdev->dev);
	if (!dom) {
		pr_err("p2pmap: no IOMMU domain for %s (VT-d off?)\n",
		       pci_name(pdev));
		pci_dev_put(pdev);
		return -ENODEV;
	}
	pr_info("p2pmap: %s domain type=%s\n", pci_name(pdev), domtype(dom->type));

	if (dom->type == IOMMU_DOMAIN_IDENTITY) {
		pr_info("p2pmap: already passthrough, nothing to map\n");
		return 0;
	}

	rc = iommu_map(dom, (unsigned long)target_pa, (phys_addr_t)target_pa,
		       size, IOMMU_READ | IOMMU_WRITE, GFP_KERNEL);
	if (rc) {
		pr_err("p2pmap: iommu_map(0x%llx, 0x%lx) failed %d%s\n",
		       target_pa, size, rc,
		       rc == -EEXIST ? " (already mapped)" : "");
		pci_dev_put(pdev);
		return rc;
	}
	mapped = 1;
	pr_info("p2pmap: mapped identity 0x%llx len 0x%lx RW\n", target_pa, size);
	return 0;
}

static void __exit p2p_exit(void)
{
	if (mapped && dom) {
		size_t n = iommu_unmap(dom, (unsigned long)target_pa, size);

		pr_info("p2pmap: unmapped %zu of 0x%lx\n", n, size);
	}
	if (pdev)
		pci_dev_put(pdev);
}

module_init(p2p_init);
module_exit(p2p_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2: identity IOMMU mapping for NTB peer-to-peer probing");
