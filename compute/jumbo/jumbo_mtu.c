// SPDX-License-Identifier: GPL-2.0
/*
 * Lift max_mtu on live VCA virtio NICs so ip can set jumbo.
 * vca_virtio_net already has ndo_change_mtu up to 65535; PVE 7
 * defaults max_mtu to 1500. Does not touch plx87xx.
 *
 * insmod jumbo_mtu.ko ifnames=eth0,eth1,eth2 mtu=65521
 * rmmod jumbo_mtu   # restores previous mtu and max_mtu
 *
 * ---------------------------------------------------------------------------
 * LOCKING -- read this before touching the notifier.
 *
 * register_netdevice() invokes the netdev notifier chain with rtnl ALREADY
 * HELD. rtnl is not recursive, so calling rtnl_lock() from a NETDEV_REGISTER
 * handler self-deadlocks:
 *
 *   rtnl_lock
 *   jumbo_netdev_event [jumbo_mtu]
 *   notifier_call_chain
 *   register_netdevice+0x6ff
 *   virtnet_probe [vca_virtio_net]
 *   vop_bh_handler [vop]
 *   INFO: task kworker/31:1:279 is blocked on a mutex likely owned by
 *         task kworker/31:1:279.
 *
 * That deadlock happens inside the VOP workqueue and leaves rtnl held
 * forever, so every process on the host that needs rtnl piles up behind it --
 * including sshd, which is why the box goes "pings but SSH never completes".
 * It cost three power cycles on 2026-09-21 before the persistent journal gave
 * up the stack trace.
 *
 * So: apply_dev() takes an explicit rtnl_held flag, and uses netif_set_mtu()
 * (the rtnl-held variant) rather than dev_set_mtu() (which acquires rtnl).
 * It only fires when a NIC registers while this module is loaded, i.e. when a
 * node reboots -- which is why plain benchmarking never tripped it.
 * ---------------------------------------------------------------------------
 */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/notifier.h>
#include <linux/rtnetlink.h>
#include <linux/string.h>

#define MAX_IF 8

static char *ifnames = "eth0,eth1,eth2";
static int mtu = 65521;
module_param(ifnames, charp, 0444);
module_param(mtu, int, 0444);

static struct net_device *devs[MAX_IF];
static unsigned int old_mtu[MAX_IF];
static unsigned int old_max[MAX_IF];
static int n_dev;

/*
 * Names we were asked to manage, so a NIC that comes back after a node reboot
 * can be recognised and re-jumboed. Without this the device silently returns
 * at MTU 1500.
 */
static char want_name[MAX_IF][IFNAMSIZ];
static int n_want;

static struct notifier_block jumbo_nb;
static int nb_registered;

/*
 * Apply jumbo to one device.
 * @rtnl_held: true when the caller already owns rtnl (notifier context).
 */
static int apply_dev(struct net_device *dev, int slot, bool rtnl_held)
{
	int err;

	old_mtu[slot] = dev->mtu;
	old_max[slot] = dev->max_mtu;

	if (!rtnl_held)
		rtnl_lock();

	/*
	 * Cap max_mtu at exactly the MTU we want, not 65535. The host's
	 * vca_eth_ifup.sh tries to set 65535 when a node's link comes up; with
	 * max_mtu at 65535 that now SUCCEEDS and leaves the host side at 65535
	 * against a guest at 65521 -- a silent mismatch that breaks jumbo
	 * traffic on that link. Capping here makes that attempt fail harmlessly.
	 */
	dev->max_mtu = mtu;
	err = netif_set_mtu(dev, mtu);	/* rtnl-held variant; never dev_set_mtu here */
	if (err)
		dev->max_mtu = old_max[slot];

	if (!rtnl_held)
		rtnl_unlock();

	if (err) {
		pr_err("jumbo_mtu: %s set mtu %d err=%d\n", dev->name, mtu, err);
		return err;
	}
	pr_info("jumbo_mtu: %s mtu %u -> %u (max_mtu %u -> %u)\n",
		dev->name, old_mtu[slot], dev->mtu, old_max[slot], dev->max_mtu);
	return 0;
}

/*
 * We hold a dev_hold() reference on each NIC. Without releasing it when the
 * device goes away, unregister_netdevice() spins forever:
 *
 *   unregister_netdevice: waiting for eth2 to become free. Usage count = 2
 *
 * and anything touching that node wedges in D state inside
 * vop_virtio_del_card_device() -> flush_work().
 */
static void drop_one(int i)
{
	if (i < 0 || i >= n_dev || !devs[i])
		return;
	pr_info("jumbo_mtu: releasing %s (device going away)\n", devs[i]->name);
	dev_put(devs[i]);
	devs[i] = NULL;
}

static int jumbo_netdev_event(struct notifier_block *nb,
			      unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	int i;

	if (event == NETDEV_UNREGISTER) {
		for (i = 0; i < n_dev; i++)
			if (devs[i] == dev)
				drop_one(i);
		return NOTIFY_DONE;
	}

	/*
	 * A VCA NIC reappears every time its node reboots, at the driver
	 * default MTU. Re-apply so jumbo survives node restarts.
	 * rtnl is held by register_netdevice() -- do NOT take it again.
	 */
	if (event == NETDEV_REGISTER) {
		for (i = 0; i < n_want; i++) {
			if (strcmp(dev->name, want_name[i]))
				continue;
			if (devs[i])		/* already holding it */
				break;
			dev_hold(dev);
			devs[i] = dev;
			if (i >= n_dev)
				n_dev = i + 1;
			if (apply_dev(dev, i, true)) {
				dev_put(dev);
				devs[i] = NULL;
			}
			break;
		}
	}
	return NOTIFY_DONE;
}

static int apply_one(const char *name)
{
	struct net_device *dev;
	int err;

	dev = dev_get_by_name(&init_net, name);
	if (!dev) {
		pr_err("jumbo_mtu: no iface %s\n", name);
		return -ENODEV;
	}
	if (n_want < MAX_IF) {
		strncpy(want_name[n_want], name, IFNAMSIZ - 1);
		want_name[n_want][IFNAMSIZ - 1] = 0;
		n_want++;
	}
	err = apply_dev(dev, n_dev, false);	/* not in notifier: we take rtnl */
	if (err) {
		dev_put(dev);
		return err;
	}
	devs[n_dev++] = dev;
	return 0;
}

static void restore_all(void)
{
	int i, err;

	for (i = n_dev - 1; i >= 0; i--) {
		if (!devs[i])		/* notifier already released it */
			continue;
		rtnl_lock();
		err = netif_set_mtu(devs[i], old_mtu[i]);
		devs[i]->max_mtu = old_max[i];
		rtnl_unlock();
		pr_info("jumbo_mtu: restore %s mtu=%u max=%u err=%d\n",
			devs[i]->name, old_mtu[i], old_max[i], err);
		dev_put(devs[i]);
		devs[i] = NULL;
	}
	n_dev = 0;
	n_want = 0;
}

static int __init jumbo_init(void)
{
	char buf[128];
	char *p, *n;
	int err;

	if (mtu < 68 || mtu > 65535)
		return -EINVAL;
	strscpy(buf, ifnames, sizeof(buf));
	p = buf;
	while (p && *p) {
		while (*p == ' ' || *p == ',')
			p++;
		if (!*p)
			break;
		n = p;
		while (*p && *p != ',')
			p++;
		if (*p)
			*p++ = 0;
		if (n_dev >= MAX_IF) {
			restore_all();
			return -EINVAL;
		}
		err = apply_one(n);
		if (err) {
			restore_all();
			return err;
		}
	}
	if (!n_dev)
		return -ENODEV;

	jumbo_nb.notifier_call = jumbo_netdev_event;
	if (register_netdevice_notifier(&jumbo_nb) == 0) {
		nb_registered = 1;
	} else {
		pr_warn("jumbo_mtu: notifier registration failed; a node reset "
			"while loaded can wedge unregister_netdevice\n");
	}
	return 0;
}

static void __exit jumbo_exit(void)
{
	if (nb_registered)
		unregister_netdevice_notifier(&jumbo_nb);
	restore_all();
}

module_init(jumbo_init);
module_exit(jumbo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("VCA2 live jumbo max_mtu on virtio-net");
