#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Temperature, power and throttle history for the VCA nodes and the host.

  vcamon run                    the collector (vcamon.service runs this)
  vcamon now                    latest sample per node
  vcamon report [--days N]      per-node summary over the last N days (default 1)
  vcamon report --daily [--days 7]   the same, one row per node per day

HOW IT SAMPLES. One long-lived SSH session per node runs a small shell loop
that prints a sample every INTERVAL seconds, aligned to the wall clock so all
nodes sample at the same instant. One session instead of one login per sample
keeps the nodes' journals clean and costs nothing between samples. The host
itself is sampled the same way, through a local shell.

WHAT A SAMPLE HOLDS. Temperatures are instantaneous (package, hottest core,
ACPI zone; the host also logs its GPU). Power is the true average over the
interval, from the RAPL energy counters (package, cores, uncore/iGPU, DRAM),
so a short burst between samples is still counted. Throttle columns are the
kernel's thermal_throttle event counts since the previous sample -- an event
between samples is never missed even though the temperature peak may be.

A node that cannot be reached gets a row with up=0 every interval, so gaps in
the history are explicit (172.31.4.1, dead since 2026-09-28, logs as down).
"""
import argparse
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request

DB = os.environ.get("VCAMON_DB", "/var/lib/vcamon/vcamon.db")
INTERVAL = int(os.environ.get("VCAMON_INTERVAL", "30"))
RETAIN_DAYS = int(os.environ.get("VCAMON_RETAIN_DAYS", "180"))
NODE_PW = os.environ.get("NODE_PW", "vista1")   # Intel's stock node image default; host-only 172.31.x links
ALERT_C = float(os.environ.get("VCAMON_ALERT_C", "90"))
NTFY_URL = os.environ.get("VCAMON_NTFY_URL", "")     # e.g. http://127.0.0.1:8090/vca
ALERT_EVERY = 30 * 60                                   # per node, seconds

# node 0 is the host; 1-6 are card (n-1)//3, cpu (n-1)%3
NODES = {0: None, **{n: f"172.31.{n}.1" for n in range(1, 7)}}


def label(n):
    return "host" if n == 0 else f"n{n} c{(n - 1) // 3}/{(n - 1) % 3}"


# Runs on the node (and on the host). Prints key=value lines, then "@@".
# Keys whose source is missing print empty and are stored as NULL.
PROBE = r"""
t() { [ -r "$1" ] && cat "$1"; }
while :; do
  echo "now=$(date +%s.%N)"
  echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
  R=/sys/class/powercap/intel-rapl:0
  for z in $R $R/intel-rapl:0:*; do
    [ -d "$z" ] || continue
    n=$(t $z/name)
    echo "e|$n=$(t $z/energy_uj)"
    echo "emax|$n=$(t $z/max_energy_range_uj)"
  done
  echo "pl1=$(t $R/constraint_0_power_limit_uw)"
  for z in /sys/class/thermal/thermal_zone*; do echo "tz|$(t $z/type)=$(t $z/temp)"; done
  for h in /sys/class/hwmon/hwmon*; do
    hn=$(t $h/name)
    for f in $h/temp*_input; do
      [ -r "$f" ] && echo "hw|$hn|$(t ${f%_input}_label)=$(cat $f)"
    done
  done
  echo "thr_pkg=$(t /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count)"
  s=0
  for c in /sys/devices/system/cpu/cpu[0-9]*; do
    id=${c##*cpu}
    first=$(cut -d, -f1 $c/topology/thread_siblings_list 2>/dev/null | cut -d- -f1)
    [ "$first" = "$id" ] && s=$((s + $(t $c/thermal_throttle/core_throttle_count || echo 0)))
  done
  echo "thr_core=$s"
  awk '/^cpu MHz/{s+=$4;n++} END{if(n) print "mhz=" s/n}' /proc/cpuinfo
  echo "load1=$(cut -d' ' -f1 /proc/loadavg)"
  awk '/^MemTotal/{t=$2} /^MemAvailable/{a=$2} END{print "mem_mb=" int((t-a)/1024)}' /proc/meminfo
  echo @@
  sleep $(( INTERVAL - $(date +%s) % INTERVAL ))
done
"""

COLS = ["ts", "node", "up", "t_pkg", "t_core_max", "t_acpi", "t_gpu",
        "w_pkg", "w_core", "w_gfx", "w_dram", "pl1_w", "mhz", "load1",
        "mem_mb", "thr_pkg", "thr_core", "uptime"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
  ts INTEGER NOT NULL, node INTEGER NOT NULL, up INTEGER NOT NULL,
  t_pkg REAL, t_core_max REAL, t_acpi REAL, t_gpu REAL,
  w_pkg REAL, w_core REAL, w_gfx REAL, w_dram REAL, pl1_w REAL,
  mhz REAL, load1 REAL, mem_mb INTEGER,
  thr_pkg INTEGER, thr_core INTEGER, uptime REAL
);
CREATE INDEX IF NOT EXISTS samples_node_ts ON samples(node, ts);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
"""


def connect():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(lines):
    """Probe output -> dict of raw readings."""
    raw = {}
    for line in lines:
        k, sep, v = line.partition("=")
        if sep:
            raw.setdefault(k, []).append(v.strip())
    one = lambda k: num((raw.get(k) or [None])[0])
    mx = lambda vals: max([x for x in map(num, vals) if x is not None], default=None)
    s = {"now": one("now"), "uptime": one("uptime"), "pl1": one("pl1"),
         "thr_pkg": one("thr_pkg"), "thr_core": one("thr_core"),
         "mhz": one("mhz"), "load1": one("load1"), "mem_mb": one("mem_mb"),
         "energy": {}, "emax": {}}
    for k, vals in raw.items():
        if k.startswith("e|"):
            s["energy"][k[2:]] = num(vals[0])
        elif k.startswith("emax|"):
            s["emax"][k[5:]] = num(vals[0])
    milli = lambda v: None if v is None else v / 1000.0
    s["t_pkg"] = milli(mx(raw.get("tz|x86_pkg_temp", [])))
    s["t_acpi"] = milli(mx(raw.get("tz|acpitz", [])))
    core, pkg_hw = [], []
    for k, vals in raw.items():
        if k.startswith("hw|coretemp|Core"):
            core += vals
        elif k.startswith("hw|coretemp|Package"):
            pkg_hw += vals
    s["t_core_max"] = milli(mx(core))
    if s["t_pkg"] is None:
        s["t_pkg"] = milli(mx(pkg_hw))
    gpu = [v for k, vals in raw.items() if k.startswith("hw|amdgpu|") for v in vals]
    s["t_gpu"] = milli(mx(gpu))
    return s


def row(node, s, prev):
    """A DB row from sample s and the node's previous sample (or None)."""
    r = dict.fromkeys(COLS)
    # ts is the host's clock: the nodes have no NTP and drift by seconds.
    # Power uses the node's own clock (s["now"]), which is consistent per node.
    r.update(ts=int(round(time.time())), node=node, up=1,
             t_pkg=s["t_pkg"], t_core_max=s["t_core_max"], t_acpi=s["t_acpi"],
             t_gpu=s["t_gpu"], mhz=s["mhz"], load1=s["load1"],
             mem_mb=None if s["mem_mb"] is None else int(s["mem_mb"]),
             uptime=s["uptime"],
             pl1_w=None if s["pl1"] is None else s["pl1"] / 1e6)
    # Deltas need a previous sample from the same boot.
    if prev and s["uptime"] is not None and prev["uptime"] is not None \
            and s["uptime"] > prev["uptime"]:
        dt = s["now"] - prev["now"]
        if dt > 0:
            for zone, col in (("package-0", "w_pkg"), ("core", "w_core"),
                              ("uncore", "w_gfx"), ("dram", "w_dram")):
                a, b = prev["energy"].get(zone), s["energy"].get(zone)
                if a is None or b is None:
                    continue
                d = b - a
                if d < 0:                      # counter wrapped
                    d += s["emax"].get(zone) or 0
                r[col] = round(d / dt / 1e6, 2)
        for k in ("thr_pkg", "thr_core"):
            if s[k] is not None and prev[k] is not None:
                r[k] = int(max(s[k] - prev[k], 0))
    elif s["uptime"] is not None and prev and prev["uptime"] is not None:
        # rebooted since the last sample: everything counted since boot is new
        for k in ("thr_pkg", "thr_core"):
            r[k] = None if s[k] is None else int(s[k])
    return r


class Collector:
    def __init__(self):
        self.db = connect()
        self.lock = threading.Lock()
        self.last_alert = {}

    def insert(self, r):
        with self.lock:
            self.db.execute(f"INSERT INTO samples ({','.join(COLS)}) VALUES "
                            f"({','.join('?' * len(COLS))})", [r[c] for c in COLS])
            self.db.commit()
        self.check_alert(r)

    def check_alert(self, r):
        hot = r["t_pkg"] is not None and r["t_pkg"] >= ALERT_C
        thr = (r["thr_pkg"] or 0) > 0
        if not (hot or thr):
            return
        msg = (f"{label(r['node'])}: {r['t_pkg']} C pkg, {r['w_pkg']} W, "
               f"{r['thr_pkg'] or 0} package throttle events in the last {INTERVAL} s")
        print("ALERT", msg, flush=True)
        if not NTFY_URL or time.time() - self.last_alert.get(r["node"], 0) < ALERT_EVERY:
            return
        self.last_alert[r["node"]] = time.time()
        try:
            req = urllib.request.Request(NTFY_URL, data=msg.encode(),
                                         headers={"Title": "VCA thermal", "Tags": "thermometer"})
            urllib.request.urlopen(req, timeout=10).read()
        except OSError as e:
            print("ntfy failed:", e, flush=True)

    def stream(self, node):
        ip = NODES[node]
        if ip is None:
            cmd = ["sh", "-s"]
        else:
            # -e: the password goes in the environment, not argv (visible in ps)
            cmd = ["sshpass", "-e", "ssh", "-T",
                   "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                   "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5",
                   "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
                   f"root@{ip}", "sh -s"]
        script = f"INTERVAL={INTERVAL}\n" + PROBE
        while True:
            prev, got = None, False
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True,
                                     env={**os.environ, "SSHPASS": NODE_PW})
                p.stdin.write(script)
                p.stdin.close()
                block = []
                for line in p.stdout:
                    line = line.rstrip("\n")
                    if line != "@@":
                        block.append(line)
                        continue
                    s = parse(block)
                    block = []
                    if s["now"] is None:
                        continue
                    self.insert(row(node, s, prev))
                    prev, got = s, True
                p.wait()
            except Exception as e:                # never let one node kill the thread
                print(f"{label(node)}: {e!r}", flush=True)
            if got:
                print(f"{label(node)}: stream ended, reconnecting", flush=True)
            r = dict.fromkeys(COLS)
            r.update(ts=int(time.time()), node=node, up=0)
            self.insert(r)
            time.sleep(INTERVAL - time.time() % INTERVAL)

    def prune_loop(self):
        while True:
            with self.lock:
                self.db.execute("DELETE FROM samples WHERE ts < ?",
                                (int(time.time()) - RETAIN_DAYS * 86400,))
                self.db.commit()
            time.sleep(86400)

    def run(self):
        print(f"vcamon: {len(NODES)} sources every {INTERVAL} s -> {DB}", flush=True)
        threads = [threading.Thread(target=self.stream, args=(n,), daemon=True) for n in NODES]
        threads.append(threading.Thread(target=self.prune_loop, daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()


# ------------------------------------------------------------------ reports

def fmt(v, spec=".0f", none="-"):
    return none if v is None else format(v, spec)


def pct(vals, q):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(q * len(vals)))]


def summarise(rows):
    up = [r for r in rows if r["up"]]
    temps = [r["t_pkg"] for r in up if r["t_pkg"] is not None]
    hottest = max(up, key=lambda r: r["t_pkg"] or -1, default=None)
    watts = [r["w_pkg"] for r in up if r["w_pkg"] is not None]
    hot_s = sum(INTERVAL for r in up if (r["t_pkg"] or 0) >= 85)
    return {
        "up": 100.0 * len(up) / len(rows) if rows else None,
        "t_avg": sum(temps) / len(temps) if temps else None,
        "t_p95": pct(temps, 0.95), "t_max": hottest["t_pkg"] if hottest else None,
        "t_max_at": hottest["ts"] if hottest and hottest["t_pkg"] is not None else None,
        "w_avg": sum(watts) / len(watts) if watts else None,
        "w_max": max(watts, default=None),
        "thr_pkg": sum(r["thr_pkg"] or 0 for r in up),
        "thr_core": sum(r["thr_core"] or 0 for r in up),
        "hot_min": hot_s / 60,
    }


HEAD = (f"{'':10} {'up%':>5}  {'T avg':>5} {'p95':>4} {'max':>4} {'at':>11}  "
        f"{'W avg':>5} {'max':>5}  {'thr pkg/core':>12}  {'≥85°C':>6}")


def line(name, s):
    at = time.strftime("%a %H:%M", time.localtime(s["t_max_at"])) if s["t_max_at"] else "-"
    return (f"{name:10} {fmt(s['up']):>5}  {fmt(s['t_avg']):>5} {fmt(s['t_p95']):>4} "
            f"{fmt(s['t_max']):>4} {at:>11}  {fmt(s['w_avg'], '.1f'):>5} "
            f"{fmt(s['w_max'], '.1f'):>5}  "
            f"{s['thr_pkg']:>6}/{s['thr_core']:<5}  {fmt(s['hot_min'], '.0f'):>4} m")


def report(days, daily):
    db = connect()
    db.row_factory = sqlite3.Row
    since = int(time.time()) - days * 86400
    rows = db.execute("SELECT * FROM samples WHERE ts >= ? ORDER BY node, ts", (since,)).fetchall()
    if not rows:
        print("no samples yet")
        return
    first = min(r["ts"] for r in rows)
    print(f"since {time.strftime('%Y-%m-%d %H:%M', time.localtime(first))} "
          f"({len(rows)} samples; temps °C, package power W)")
    by = {}
    for r in rows:
        key = (time.strftime("%Y-%m-%d %a", time.localtime(r["ts"])) if daily else "", r["node"])
        by.setdefault(key, []).append(r)
    day = None
    for (d, n), rs in sorted(by.items()):
        if d != day:
            print(f"\n{d}\n{HEAD}" if daily else f"\n{HEAD}")
            day = d
        print(line(label(n), summarise(rs)))


def now():
    db = connect()
    db.row_factory = sqlite3.Row
    print(f"{'':10} {'age':>4}  {'T pkg':>5} {'core':>4}  {'W pkg':>5} {'cores':>5} "
          f"{'iGPU':>5} {'cap':>4}  {'MHz':>5} {'load':>5} {'mem MB':>6}")
    for n in NODES:
        r = db.execute("SELECT * FROM samples WHERE node=? ORDER BY ts DESC LIMIT 1", (n,)).fetchone()
        if not r:
            print(f"{label(n):10} no samples")
            continue
        age = f"{int(time.time() - r['ts'])}s"
        if not r["up"]:
            print(f"{label(n):10} {age:>4}  down")
            continue
        print(f"{label(n):10} {age:>4}  {fmt(r['t_pkg']):>5} {fmt(r['t_core_max']):>4}  "
              f"{fmt(r['w_pkg'], '.1f'):>5} {fmt(r['w_core'], '.1f'):>5} {fmt(r['w_gfx'], '.1f'):>5} "
              f"{fmt(r['pl1_w']):>4}  {fmt(r['mhz']):>5} {fmt(r['load1'], '.2f'):>5} "
              f"{fmt(r['mem_mb']):>6}")


def main():
    ap = argparse.ArgumentParser(prog="vcamon")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("now")
    rp = sub.add_parser("report")
    rp.add_argument("--days", type=float, default=None)
    rp.add_argument("--daily", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        Collector().run()
    elif a.cmd == "now":
        now()
    else:
        report(a.days or (7 if a.daily else 1), a.daily)


if __name__ == "__main__":
    main()
