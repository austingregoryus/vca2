<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# vcamon: temperature, power and throttle history

A small collector on the Proxmox host that logs every VCA node (and the host)
every 30 s into SQLite. Started 2026-10-09, after a week in which the only
evidence of how hot the cards got was the kernel's throttle counters.

```
vcamon now                  # latest sample per node
vcamon report               # last 24 h, one line per node
vcamon report --days 7      # last 7 days
vcamon report --daily       # one block per day (default 7 days)
```

`report` gives, per node: uptime %, package temperature (avg, p95, max and
when), package power (avg, max), thermal-throttle events (package / core),
and minutes spent at or above 85 °C.

## What is logged

Table `samples` in `/var/lib/vcamon/vcamon.db`, one row per node per 30 s:

| Column | Meaning |
|---|---|
| `ts`, `node`, `up` | host clock (the nodes have no NTP), node 0 = host, 1-6 = global node n-1 (card (n-1)/3 cpu (n-1)%3, IP 172.31.n.1); `up=0` rows mark a node that could not be reached |
| `t_pkg`, `t_core_max`, `t_acpi`, `t_gpu` | °C, instantaneous. `t_gpu` is the host's AMD GPU |
| `w_pkg`, `w_core`, `w_gfx`, `w_dram` | W, **averaged over the interval** from the RAPL energy counters (`w_gfx` is the uncore/iGPU zone) |
| `pl1_w` | the package power cap in force (45 W card 0, 35 W card 1) |
| `thr_pkg`, `thr_core` | thermal-throttle events since the previous sample |
| `mhz`, `load1`, `mem_mb`, `uptime` | average core clock, load, used memory, seconds since boot |

Power and throttle are cumulative counters, so nothing between two samples is
lost. Temperature is a snapshot: a spike shorter than 30 s can be missed, but
if it mattered it shows up in `thr_*`.

Kept 180 days (about 300 MB). Ad-hoc queries (`node=6` is global node 5,
172.31.6.1):

```
sqlite3 -header /var/lib/vcamon/vcamon.db \
  "select datetime(ts,'unixepoch','localtime') t, t_pkg, w_pkg, thr_pkg
   from samples where node=6 and ts > strftime('%s','now','-1 hour')"
```

## How it works

`vcamon run` (the `vcamon.service` unit) holds one SSH session per node. Each
runs a shell loop on the node that prints a sample at every 30 s wall-clock
boundary. One session instead of a login every 30 s keeps the nodes' journals
clean. When a session drops (node down, reboot) the collector writes an
`up=0` row and reconnects on the next interval.

It reads only `/sys` and `/proc` on the nodes and installs nothing there.

## Install / update

```
bash monitor/install_host.sh      # on the host, from the repo root
```

Optional `/etc/vcamon.env`:

| Variable | Default | |
|---|---|---|
| `VCAMON_INTERVAL` | 30 | seconds |
| `VCAMON_RETAIN_DAYS` | 180 | |
| `VCAMON_ALERT_C` | 90 | package °C that counts as an alert |
| `VCAMON_NTFY_URL` | unset | e.g. `http://127.0.0.1:8090/vca`; alerts post here, at most once per 30 min per node |

Alerts (package ≥ `VCAMON_ALERT_C`, or any package throttle event) always go
to the journal (`journalctl -u vcamon | grep ALERT`). ntfy is off until the
URL is set.
