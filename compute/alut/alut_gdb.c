// SPDX-License-Identifier: GPL-2.0
/*
 * Guest doorbell wait. Xenial 4.14. Does not touch PLX DMA/intr debugfs.
 *
 * insmod alut_gdb.ko pci_did=0x2958 db=-1
 *   db=-1: plx_next_db(); if it looks wrapped (<10) fall back to 15
 * /dev/vca_gdb  blocking read returns hit count as 4 bytes
 * rmmod alut_gdb
 */
#include <linux/fs.h>
#include <linux/interrupt.h>
#include <linux/kallsyms.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/poll.h>
#include <linux/sched.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

static unsigned int pci_did = 0x2958;
static int db = -1;
module_param(pci_did, uint, 0444);
module_param(db, int, 0444);

typedef int (*next_db_fn)(void *xdev);
typedef void *(*req_irq_fn)(void *xdev, irq_handler_t handler,
			    irq_handler_t thread_fn, const char *name,
			    void *data, int intr_src);
typedef void (*free_irq_fn)(void *xdev, void *cookie, void *data);

static void *xdev;
static struct pci_dev *g_pdev;
static void *irq_cookie;
static free_irq_fn free_irq_sym;
static atomic_t hits;
static wait_queue_head_t wq;
static int registered;

static irqreturn_t gdb_handler(int irq, void *data)
{
	atomic_inc(&hits);
	wake_up_interruptible(&wq);
	return IRQ_HANDLED;
}

static ssize_t gdb_read(struct file *f, char __user *buf, size_t len, loff_t *p)
{
	u32 n, last;

	if (len < 4)
		return -EINVAL;
	last = atomic_read(&hits);
	if (wait_event_interruptible(wq, atomic_read(&hits) != last))
		return -ERESTARTSYS;
	n = atomic_read(&hits);
	if (copy_to_user(buf, &n, 4))
		return -EFAULT;
	return 4;
}

static unsigned int gdb_poll(struct file *f, poll_table *wait)
{
	poll_wait(f, &wq, wait);
	return POLLIN | POLLRDNORM;
}

static const struct file_operations gdb_fops = {
	.owner = THIS_MODULE,
	.read = gdb_read,
	.poll = gdb_poll,
};

static struct miscdevice gdb_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "vca_gdb",
	.fops = &gdb_fops,
};

static int __init gdb_init(void)
{
	next_db_fn next_db;
	req_irq_fn req_irq;
	int chosen, err;
	void *cookie;

	init_waitqueue_head(&wq);
	atomic_set(&hits, 0);

	g_pdev = pci_get_device(PCI_VENDOR_ID_INTEL, pci_did, NULL);
	if (!g_pdev) {
		pr_err("alut_gdb: no 8086:%04x\n", pci_did);
		return -ENODEV;
	}
	xdev = pci_get_drvdata(g_pdev);
	if (!xdev) {
		pr_err("alut_gdb: no drvdata\n");
		err = -ENODEV;
		goto put;
	}

	next_db = (next_db_fn)kallsyms_lookup_name("plx_next_db");
	req_irq = (req_irq_fn)kallsyms_lookup_name("plx_request_threaded_irq");
	free_irq_sym = (free_irq_fn)kallsyms_lookup_name("plx_free_irq");
	if (!req_irq) {
		pr_err("alut_gdb: no plx_request_threaded_irq\n");
		err = -ENOENT;
		goto put;
	}

	chosen = db;
	if (chosen < 0) {
		if (!next_db) {
			pr_err("alut_gdb: no plx_next_db\n");
			err = -ENOENT;
			goto put;
		}
		chosen = next_db(xdev);
		if (chosen < 10) {
			pr_warn("alut_gdb: next_db=%d looks wrapped, using 15\n",
				chosen);
			chosen = 15;
		}
	}
	if (chosen < 1 || chosen > 15) {
		pr_err("alut_gdb: db %d reserved or out of range (0 is LBP)\n",
		       chosen);
		err = -EINVAL;
		goto put;
	}
	db = chosen;

	cookie = req_irq(xdev, gdb_handler, NULL, "alut_gdb", xdev, db);
	if (IS_ERR(cookie)) {
		pr_err("alut_gdb: request_irq db %d failed %ld\n",
		       db, PTR_ERR(cookie));
		err = PTR_ERR(cookie);
		goto put;
	}
	irq_cookie = cookie;
	registered = 1;

	err = misc_register(&gdb_misc);
	if (err)
		goto unreg;

	pr_info("alut_gdb: db=%d /dev/vca_gdb\n", db);
	return 0;

unreg:
	if (free_irq_sym)
		free_irq_sym(xdev, irq_cookie, xdev);
	registered = 0;
put:
	pci_dev_put(g_pdev);
	g_pdev = NULL;
	return err;
}

static void __exit gdb_exit(void)
{
	misc_deregister(&gdb_misc);
	if (registered && free_irq_sym)
		free_irq_sym(xdev, irq_cookie, xdev);
	if (g_pdev)
		pci_dev_put(g_pdev);
	pr_info("alut_gdb: exit hits=%d db=%d\n", atomic_read(&hits), db);
}

module_init(gdb_init);
module_exit(gdb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 guest doorbell wait");
