// SPDX-License-Identifier: GPL-2.0
#ifndef _PLX_MBOX_H_
#define _PLX_MBOX_H_
struct plx_device;
extern const struct file_operations plx_mbox_info_ops;
extern const struct file_operations plx_mbox_alloc_ops;
void plx_mbox_remove(struct plx_device *xdev);
#endif
