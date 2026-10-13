// SPDX-License-Identifier: GPL-2.0
/*
 * Host doorbell kick. PVE 7. Looks up plx_send_intr via kprobe.
 * Does not use plx87xx debugfs (intr_send fires all 16).
 *
 * insmod alut_hdb.ko pci_did=0x2954 db=15
 * echo 1 > /sys/kernel/debug/alut_hdb/kick
 * rmmod alut_hdb
 */
#include <linux/debugfs.h>
#include <linux/kprobes.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/uaccess.h>

static unsigned int pci_did = 0x2954;
static int db = 15;
module_param(pci_did, uint, 0444);
module_param(db, int, 0444);

typedef void (*send_fn)(void *xdev, int doorbell);

static void *xdev;
static send_fn send_intr;
static struct dentry *dbg_dir;
static atomic_t kicks;

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

static ssize_t kick_write(struct file *f, const char __user *buf, size_t len,
			  loff_t *ppos)
{
	if (db < 1 || db > 15 || !send_intr || !xdev)
		return -EINVAL;
	send_intr(xdev, db);
	atomic_inc(&kicks);
	return len;
}

static const struct file_operations kick_fops = {
	.owner = THIS_MODULE,
	.write = kick_write,
};

static int __init hdb_init(void)
{
	struct pci_dev *pdev;

	if (db < 1 || db > 15) {
		pr_err("alut_hdb: db %d invalid (0 is LBP)\n", db);
		return -EINVAL;
	}
	send_intr = (send_fn)ksym("plx_send_intr");
	if (!send_intr) {
		pr_err("alut_hdb: no plx_send_intr\n");
		return -ENOENT;
	}
	pdev = pci_get_device(PCI_VENDOR_ID_INTEL, pci_did, NULL);
	if (!pdev) {
		pr_err("alut_hdb: no 8086:%04x\n", pci_did);
		return -ENODEV;
	}
	xdev = pci_get_drvdata(pdev);
	pci_dev_put(pdev);
	if (!xdev) {
		pr_err("alut_hdb: no drvdata\n");
		return -ENODEV;
	}
	dbg_dir = debugfs_create_dir("alut_hdb", NULL);
	if (IS_ERR_OR_NULL(dbg_dir))
		return -ENOMEM;
	if (IS_ERR_OR_NULL(debugfs_create_file("kick", 0200, dbg_dir, NULL,
					      &kick_fops))) {
		debugfs_remove_recursive(dbg_dir);
		return -ENOMEM;
	}
	pr_info("alut_hdb: db=%d /sys/kernel/debug/alut_hdb/kick\n", db);
	return 0;
}

static void __exit hdb_exit(void)
{
	debugfs_remove_recursive(dbg_dir);
	pr_info("alut_hdb: exit kicks=%d db=%d\n", atomic_read(&kicks), db);
}

module_init(hdb_init);
module_exit(hdb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 host doorbell kick");
