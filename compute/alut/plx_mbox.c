// SPDX-License-Identifier: GPL-2.0
/*
 * Host ALUT shared-memory mailbox. Compiled into plx87xx.
 * Allocates one A-LUT segment (32 MiB on node 0/1) of DMA memory so the
 * card can see it. Does NOT touch DMA debugfs. Polling only (no doorbell).
 */
#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#include "plx_device.h"
#include "plx_hw.h"

#define MBOX_MAGIC 0x414c5554u /* 'ALUT' */

struct plx_mbox {
	struct plx_device *xdev;
	void *cpu;
	dma_addr_t dma;
	size_t size;
	dma_addr_t peer_off;
	u32 segment_id;
	struct miscdevice misc;
	char misc_name[32];
	int allocated;
};

#define MBOX_MAX 8
static struct plx_mbox mboxes[MBOX_MAX];

static struct plx_mbox *mbox_for(struct plx_device *xdev)
{
	int i;
	struct plx_mbox *empty = NULL;

	for (i = 0; i < MBOX_MAX; i++) {
		if (mboxes[i].xdev == xdev)
			return &mboxes[i];
		if (!empty && !mboxes[i].xdev)
			empty = &mboxes[i];
	}
	if (empty)
		empty->xdev = xdev;
	return empty;
}

static int mbox_mmap(struct file *filp, struct vm_area_struct *vma)
{
	struct plx_mbox *m = filp->private_data;

	if (!m || !m->allocated)
		return -ENODEV;
	return dma_mmap_coherent(&m->xdev->pdev->dev, vma, m->cpu, m->dma,
				 m->size);
}

static int mbox_open(struct inode *inode, struct file *filp)
{
	int i;
	int minor = iminor(inode);

	for (i = 0; i < MBOX_MAX; i++) {
		if (mboxes[i].allocated && mboxes[i].misc.minor == minor) {
			filp->private_data = &mboxes[i];
			return 0;
		}
	}
	return -ENODEV;
}

static const struct file_operations mbox_misc_fops = {
	.owner = THIS_MODULE,
	.open = mbox_open,
	.mmap = mbox_mmap,
};

static int mbox_alloc(struct plx_device *xdev)
{
	struct plx_mbox *m = mbox_for(xdev);
	size_t sz;
	int err;
	dma_addr_t peer = 0;

	if (!m)
		return -ENOMEM;
	if (m->allocated)
		return -EEXIST;
	if (!xdev->a_lut)
		return -ENODEV;

	sz = (size_t)xdev->a_lut_manager.segment_size;
	if (sz < PAGE_SIZE || sz > (64ul << 20))
		return -EINVAL;

	m->cpu = dma_alloc_coherent(&xdev->pdev->dev, sz, &m->dma, GFP_KERNEL);
	if (!m->cpu)
		return -ENOMEM;
	memset(m->cpu, 0, PAGE_SIZE);
	*(u32 *)m->cpu = MBOX_MAGIC;

	err = plx_add_a_lut_entry(xdev, m->dma, sz, &peer);
	if (err) {
		dma_free_coherent(&xdev->pdev->dev, sz, m->cpu, m->dma);
		m->cpu = NULL;
		return err;
	}

	m->size = sz;
	m->peer_off = peer;
	m->segment_id = (u32)(peer / sz);
	snprintf(m->misc_name, sizeof(m->misc_name), "vca_alut_%s",
		 dev_name(&xdev->pdev->dev));
	/* pci names contain ':' which miscdev dislikes; replace */
	{
		char *p;
		for (p = m->misc_name; *p; p++)
			if (*p == ':' || *p == '.')
				*p = '_';
	}
	m->misc.minor = MISC_DYNAMIC_MINOR;
	m->misc.name = m->misc_name;
	m->misc.fops = &mbox_misc_fops;
	err = misc_register(&m->misc);
	if (err) {
		plx_del_a_lut_entry(xdev, peer);
		dma_free_coherent(&xdev->pdev->dev, sz, m->cpu, m->dma);
		m->cpu = NULL;
		return err;
	}
	m->allocated = 1;
	dev_info(&xdev->pdev->dev,
		 "ALUT mbox %s dma=%pad peer_off=0x%llx size=0x%zx seg=%u\n",
		 m->misc_name, &m->dma, (u64)peer, sz, m->segment_id);
	return 0;
}

static int mbox_info_show(struct seq_file *s, void *pos)
{
	struct plx_device *xdev = s->private;
	struct plx_mbox *m = mbox_for(xdev);

	if (!m || !m->allocated) {
		seq_puts(s, "unallocated\n");
		return 0;
	}
	seq_printf(s, "dev /dev/%s\n", m->misc_name);
	seq_printf(s, "size 0x%zx\n", m->size);
	seq_printf(s, "dma %pad\n", &m->dma);
	seq_printf(s, "peer_off 0x%llx\n", (u64)m->peer_off);
	seq_printf(s, "segment %u\n", m->segment_id);
	seq_printf(s, "magic 0x%x\n", *(u32 *)m->cpu);
	return 0;
}

static int mbox_info_open(struct inode *inode, struct file *file)
{
	return single_open(file, mbox_info_show, inode->i_private);
}

const struct file_operations plx_mbox_info_ops = {
	.owner = THIS_MODULE,
	.open = mbox_info_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static ssize_t mbox_alloc_write(struct file *file, const char __user *buf,
				size_t len, loff_t *ppos)
{
	struct plx_device *xdev = file_inode(file)->i_private;
	int err;

	if (!xdev)
		return -ENODEV;
	err = mbox_alloc(xdev);
	if (err)
		return err;
	return len;
}

const struct file_operations plx_mbox_alloc_ops = {
	.owner = THIS_MODULE,
	.write = mbox_alloc_write,
};

void plx_mbox_remove(struct plx_device *xdev)
{
	struct plx_mbox *m = mbox_for(xdev);

	if (!m || m->xdev != xdev)
		return;
	if (m->allocated) {
		misc_deregister(&m->misc);
		plx_del_a_lut_entry(xdev, m->peer_off);
		dma_free_coherent(&xdev->pdev->dev, m->size, m->cpu, m->dma);
	}
	memset(m, 0, sizeof(*m));
}
