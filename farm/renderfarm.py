#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Remotion render farm on the VCA cards: card 1 (nodes 3-5) plus two of
card 0's nodes (0-1). Node 2 stays with smem so searches never wait on a render.

A client (ContextianStudio's studio/render_farm.py) sends:

  1. its render package, content-addressed:  PUT /v1/code/<sha256>  (tar.gz)
     -> unpacked on every node, `pnpm install --frozen-lockfile`, Chrome ready
  2. the assets its props reference:  POST /v1/assets/missing {files:{rel:sha}}
     then PUT /v1/assets/<rel> for each one the farm lacks (mirrored to nodes)
  3. a job:  POST /v1/jobs {code, composition, props, ...}  -> {id}
  4. polls GET /v1/jobs/<id>, then fetches GET /v1/jobs/<id>/output (mp4)

The farm borrows its nodes from smem for the length of a job (smem's
/run/smem/paused.json, with an expiry it keeps refreshing): card 1 from the
background lane, nodes 0-1 from the interactive lane, which keeps node 2.
It splits the frames across the nodes and joins the parts.

HOW PARTS ARE JOINED. Each part renders as h264-mkv with pcm-16 audio. The
video is concatenated as-is; the audio, being PCM, joins sample-exactly and
is encoded to AAC once. Remotion's own single-pass mp4 sits 2048 samples
later than that raw PCM (AAC priming), so the join is delayed by 2048
samples to match it. Verified 2026-09-26 against a real single-pass render:
0.0 ms offset at every point including the seams, difference -62 dB.
Joining AAC parts instead (what a chunked local render does) drifts ~21 ms
per seam.

Everything is stdlib; node work goes over ssh (sshpass, Intel's default node
password, as the rest of this repo does).
"""
import hashlib
import json
import math
import os
import queue
import re
import shlex
import shutil
import subprocess
import tarfile
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# Card 0 first: uncapped, better cooled, so it takes the first (largest) parts.
NODES = [n for n in os.environ.get(
    "FARM_NODES", "172.31.1.1,172.31.2.1,172.31.4.1,172.31.5.1,172.31.6.1").split(",") if n]
NODE_PW = os.environ.get("NODE_PW", "vista1")
STATE = Path(os.environ.get("FARM_STATE", "/tank-red/vmdata/vca/renderfarm"))
NODE_ROOT = "/opt/farm"                      # on each node: code/<hash>, public/, work/<job>
PORT = int(os.environ.get("FARM_PORT", "8910"))
TOKEN = os.environ.get("FARM_TOKEN", "")
SMEM = os.environ.get("FARM_SMEM", "http://127.0.0.1:8900")
PAUSE_FILE = os.environ.get("SMEM_PAUSE_FILE", "/run/smem/paused.json")
PART_FRAMES = int(os.environ.get("FARM_PART_FRAMES", "3000"))   # the PC's chunk size
CONCURRENCY = int(os.environ.get("FARM_CONCURRENCY", "4"))
KEEP_CODE = 4                                 # code versions kept per node
KEEP_JOBS_S = 24 * 3600
AUDIO_DELAY_SAMPLES = 2048                    # see module docstring

# Assets live per NAMESPACE (one per client app: ContextianStudio uses
# "default", DocuGen "docugen"), because apps reuse the same relative names
# (brand/..., shots/...) for different files.
PUBLIC = STATE / "public"                     # <ns>/<rel>
JOBDIR = STATE / "jobs"
CODEDIR = STATE / "code"
INDEXDIR = STATE / "assets"                   # <ns>.json: rel -> sha256 of the host mirror
NODE_INDEX = STATE / "node-assets"            # <ip>-<ns>.json: rel -> sha256 on that node
SAFE_NS = re.compile(r"^[a-z0-9_-]{1,32}$")

LOCK = threading.Lock()                       # guards INDEX / NODE_INDEX / JOBS
CODE_LOCK = threading.Lock()                  # one code install at a time
JOBS = {}
JOBQ = queue.Queue()
SAFE_REL = re.compile(r"^[A-Za-z0-9._\-/ ]+$")


def safe_ns(ns):
    ns = ns or "default"
    if not SAFE_NS.match(ns):
        raise ValueError(f"bad namespace: {ns!r}")
    return ns


def index_path(ns):
    return INDEXDIR / f"{ns}.json"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ node ssh

def ssh(node, cmd, stdin=None, timeout=3600, text=True):
    return subprocess.run(
        ["sshpass", "-p", NODE_PW, "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30", f"root@{node}", cmd],
        input=stdin, capture_output=True, text=text, timeout=timeout)


def ssh_ok(node, cmd, **kw):
    r = ssh(node, cmd, **kw)
    if r.returncode != 0:
        err = r.stderr if kw.get("text", True) else r.stderr.decode(errors="replace")
        raise RuntimeError(f"{node}: {cmd[:60]}... rc={r.returncode}: {err.strip()[-400:]}")
    return r.stdout


def all_nodes(fn, *a, nodes=None):
    nodes = NODES if nodes is None else nodes
    if not nodes:
        return []
    with ThreadPoolExecutor(len(nodes)) as ex:
        return list(ex.map(lambda n: fn(n, *a), nodes))


# A node can drop out on its own (card 1 node 0 went `link_down, dma_hang`
# mid-render on 2026-09-28). Jobs run on whichever nodes answer; the probe
# also tells whether a node has the job's code installed.
NODE_UP: dict = {n: None for n in NODES}          # last probe: True/False, None = not yet


def probe(node, code=None):
    """'up' (and has `code`, if given), 'nocode', or 'down'."""
    cmd = f"test -d {NODE_ROOT}/code/{code}" if code else "true"
    try:
        rc = ssh(node, cmd, timeout=20).returncode
    except subprocess.TimeoutExpired:
        rc = 255
    NODE_UP[node] = rc != 255
    return "down" if rc == 255 else "up" if rc == 0 else "nocode"


def prober():
    while True:
        all_nodes(probe)
        time.sleep(60)


# ------------------------------------------------------------------ indexes

def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}


def _save(p, d):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def safe_rel(rel):
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/") or not SAFE_REL.match(rel):
        raise ValueError(f"bad asset path: {rel!r}")
    return rel


# ------------------------------------------------------------------ code

def code_ready(h):
    return (CODEDIR / h / "READY").exists()


def package_hash(tgz_path):
    """The clients' content hash of a render package (studio/render_farm.py
    code_bundle: sha256 over each member's path, NUL, sha256 of its bytes, in
    archive order). An upload must match the hash it is filed under, or a kept
    package could put different code on nodes caught up later."""
    h = hashlib.sha256()
    with tarfile.open(tgz_path, "r:gz") as tar:
        for m in tar:
            if not m.isfile():
                raise ValueError(f"unexpected tar member: {m.name!r}")
            h.update(m.name.encode() + b"\0" + hashlib.sha256(tar.extractfile(m).read()).digest())
    return h.hexdigest()


def code_kept(h):
    """Installed AND its package is kept on the host, so any node that lacks
    it (new, or back from down) can be caught up. Clients upload when this is
    false."""
    return code_ready(h) and (CODEDIR / h / "code.tgz").exists()


def install_on(node, h, data, render_dir):
    """Unpack one render package on one node and install its dependencies.

    Chrome (chrome-headless-shell) is seeded from the newest existing install
    on the node when there is one, so a code change does not re-download it.
    """
    d = f"{NODE_ROOT}/code/{h}"
    ssh_ok(node, f"rm -rf {d}.tmp && mkdir -p {d}.tmp && tar xzf - -C {d}.tmp",
           stdin=data, text=False, timeout=600)
    script = f"""set -e
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0 CI=1
cd {d}.tmp
pnpm install --frozen-lockfile --filter ./{render_dir} >/tmp/farm-install.log 2>&1 \\
  || {{ tail -20 /tmp/farm-install.log; exit 1; }}
prev=$(ls -td {NODE_ROOT}/code/*/{render_dir}/node_modules/.remotion 2>/dev/null | head -1)
if [ -n "$prev" ] && [ ! -d {render_dir}/node_modules/.remotion ]; then cp -a "$prev" {render_dir}/node_modules/; fi
cd {render_dir} && npx remotion browser ensure </dev/null >/tmp/farm-browser.log 2>&1 \\
  || {{ tail -5 /tmp/farm-browser.log; exit 1; }}
cd {NODE_ROOT}/code && rm -rf {h} && mv {h}.tmp {h}
ls -td {NODE_ROOT}/code/*/ | tail -n +{KEEP_CODE + 1} | xargs -r rm -rf
"""
    ssh_ok(node, "bash -s", stdin=script, timeout=1800)
    return node


def install_code(h, tgz_path, render_dir):
    """Install a render package on every node that answers. The package is kept
    on the host so a node that was down gets it when it is back."""
    # Interpolated into the node install script: a plain relative path only.
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", render_dir or "") \
            or ".." in render_dir.split("/"):
        raise ValueError(f"bad render dir: {render_dir!r}")
    data = Path(tgz_path).read_bytes()
    with CODE_LOCK:
        if code_ready(h):
            return
        up = [n for n, s in zip(NODES, all_nodes(probe)) if s != "down"]
        if not up:
            raise RuntimeError("no farm node is reachable")
        all_nodes(install_on, h, data, render_dir, nodes=up)
        (CODEDIR / h).mkdir(parents=True, exist_ok=True)
        (CODEDIR / h / "code.tgz").write_bytes(data)
        (CODEDIR / h / "meta.json").write_text(json.dumps({"render_dir": render_dir,
                                                           "at": time.time()}))
        (CODEDIR / h / "READY").touch()
        for old in sorted((p for p in CODEDIR.glob("*/code.tgz")),
                          key=lambda p: p.stat().st_mtime)[:-2 * KEEP_CODE]:
            old.unlink()
    log(f"code {h[:12]} installed on {len(up)} of {len(NODES)} nodes"
        + (f" (down: {sorted(set(NODES) - set(up))})" if len(up) < len(NODES) else ""))


def restore_code(h, tgz_path):
    """A version installed before packages were kept on the host: keep this
    upload and install it on the reachable nodes that lack it (e.g. nodes
    added to the farm since)."""
    data = Path(tgz_path).read_bytes()
    with CODE_LOCK:
        if code_kept(h):
            return
        render_dir = json.loads((CODEDIR / h / "meta.json").read_text())["render_dir"]
        lacking = [n for n, st in zip(NODES, all_nodes(probe, h)) if st == "nocode"]
        all_nodes(install_on, h, data, render_dir, nodes=lacking)
        (CODEDIR / h / "code.tgz").write_bytes(data)
    log(f"code {h[:12]} package kept; installed on {lacking or 'no new nodes'}")


INSTALLING = set()


def install_later(node, h):
    """Background install on a node that came back without this code."""
    tgz = CODEDIR / h / "code.tgz"
    with LOCK:
        if (node, h) in INSTALLING or not tgz.exists():
            return
        INSTALLING.add((node, h))

    def run():
        try:
            meta = json.loads((CODEDIR / h / "meta.json").read_text())
            install_on(node, h, tgz.read_bytes(), meta["render_dir"])
            log(f"code {h[:12]} installed on {node} (catch-up)")
        except Exception as e:
            log(f"code {h[:12]} catch-up install on {node} failed: {e}")
        finally:
            with LOCK:
                INSTALLING.discard((node, h))
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ assets

def missing_assets(ns, files):
    idx = _load(index_path(ns))
    return sorted(rel for rel, sha in files.items() if idx.get(safe_rel(rel)) != sha)


def store_asset(ns, rel, sha, body_iter, length):
    rel = safe_rel(rel)
    dst = PUBLIC / ns / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".{uuid.uuid4().hex[:6]}.part")
    h = hashlib.sha256()
    with open(tmp, "wb") as f:
        for chunk in body_iter(length):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha:
        tmp.unlink()
        raise ValueError(f"sha256 mismatch for {rel}")
    tmp.replace(dst)
    with LOCK:
        idx = _load(index_path(ns))
        idx[rel] = sha
        _save(index_path(ns), idx)


def sync_assets_to(node, ns, rels):
    """Push the files a job needs to one node, skipping ones it already has."""
    idx = _load(index_path(ns))
    p = NODE_INDEX / f"{node}-{ns}.json"
    have = _load(p)
    need = [r for r in rels if have.get(r) != idx.get(r)]
    if not need:
        return 0
    listing = "\n".join(need) + "\n"
    tar = subprocess.run(["tar", "cf", "-", "-C", str(PUBLIC / ns), "-T", "-"],
                         input=listing.encode(), capture_output=True, check=True).stdout
    ssh_ok(node, f"mkdir -p {NODE_ROOT}/public/{ns} && tar xf - -C {NODE_ROOT}/public/{ns}",
           stdin=tar, text=False, timeout=1200)
    with LOCK:
        have = _load(p)
        for r in need:
            have[r] = idx[r]
        _save(p, have)
    return len(need)


# ------------------------------------------------------------------ smem lane

def lend_card(job_id, seconds=1800):
    """Take the farm nodes from smem: background work falls back to card 0,
    and both lanes keep node 2 (172.31.3.1)."""
    Path(PAUSE_FILE).parent.mkdir(parents=True, exist_ok=True)
    d = {"nodes": NODES, "until": time.time() + seconds, "by": f"renderfarm:{job_id}"}
    tmp = Path(PAUSE_FILE + ".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(PAUSE_FILE)


def return_card(job_id):
    try:
        d = json.loads(Path(PAUSE_FILE).read_text())
        if d.get("by") == f"renderfarm:{job_id}":
            Path(PAUSE_FILE).unlink()
    except (OSError, ValueError):
        pass


def wait_for_smem_drain(max_s=180):
    """Let smem calls already running on the farm nodes finish (an extraction
    can take ~60 s). Proceeds anyway after max_s."""
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            with urllib.request.urlopen(f"{SMEM}/v1/lanes", timeout=5) as r:
                inflight = json.loads(r.read()).get("inflight", {})
            if sum(inflight.get(n, 0) for n in NODES) == 0:
                return round(time.time() - t0, 1)
        except Exception:
            return None                      # smem down: nothing to wait for
        time.sleep(2)
    return round(time.time() - t0, 1)


# ------------------------------------------------------------------ jobs

def plan_parts(total, bounds=None, n=None):
    """Frame ranges [a, b) to render. Client bounds (beat-aligned, ~3000
    frames) are honoured but split further so every node (n) gets work."""
    n = n or len(NODES)
    want = n * max(1, math.ceil(total / (n * PART_FRAMES)))
    if not bounds:
        cuts = [round(total * k / want) for k in range(want + 1)]
        return [(cuts[k], cuts[k + 1]) for k in range(want) if cuts[k + 1] > cuts[k]]
    ranges = [(int(a), int(b)) for a, b in bounds]
    while len(ranges) < want:
        ranges.sort(key=lambda r: r[0] - r[1])          # biggest first
        a, b = ranges.pop(0)
        if b - a < 60:
            ranges.append((a, b))
            break
        m = (a + b) // 2
        ranges += [(a, m), (m, b)]
    return sorted(ranges)


def render_part(node, job, i, a, b):
    meta = json.loads((CODEDIR / job["code"] / "meta.json").read_text())
    q = shlex.quote          # every value below also passed validation on the way in
    cwd = q(f"{NODE_ROOT}/code/{job['code']}/{meta['render_dir']}")
    work = f"{NODE_ROOT}/work/{job['id']}"
    out, logf = q(f"{work}/part_{i:03d}.mkv"), q(f"{work}/part_{i:03d}.log")
    last, gl = "", ""
    c = job.get("concurrency") or CONCURRENCY
    tries = list(dict.fromkeys((c, max(1, c // 2))))     # e.g. 4 then 2; 1 only once
    while tries:
        conc = tries[0]
        cmd = (f"cd {cwd} && npx remotion render {q(job['entry'])} {q(job['composition'])} {out} "
               f"--codec=h264-mkv --audio-codec=pcm-16 --props={q(work + '/props.json')} "
               f"--frames={a}-{b - 1} --concurrency={conc}{gl} --public-dir={q(NODE_ROOT + '/public/' + job['ns'])} "
               f"--log=verbose </dev/null >{logf} 2>&1; rc=$?; "
               f"grep ' 404 ' {logf} | grep -oE 'localhost:[0-9]+/[^] ]+' | sed -E 's#localhost:[0-9]+/(public/)?##; s#[?].*##; s#[].,;:)(]+$##' | sort -u > {logf}.404; "
               f"echo RC=$rc; grep -q 'WebGL context' {logf} && echo NO_WEBGL; "
               f"tail -c 600 {logf} | tr '\\r' '\\n' | tail -4")
        r = ssh(node, cmd, timeout=4 * 3600)
        m404 = ssh(node, f"cat {q(work + f'/part_{i:03d}.log.404')}").stdout.split()
        if m404:
            raise MissingAssets(m404)
        if "RC=0" in r.stdout:
            return node
        last = r.stdout[-500:]
        # Headless Chrome on the nodes has no WebGL, so three.js scenes (DocuGen's
        # spatial beats) fail. SwiftShader renders them in software: correct but
        # ~10x slower, so only parts that need it get it, at the same concurrency.
        if "NO_WEBGL" in r.stdout and not gl:
            gl = " --gl=swangle"
            log(f"{job['id']} part {i}: no WebGL on {node}; retrying with --gl=swangle")
            continue
        tries.pop(0)
    raise RuntimeError(f"part {i} ({a}-{b}) failed on {node}: {last}")


class MissingAssets(RuntimeError):
    def __init__(self, paths):
        super().__init__(f"missing assets: {paths[:10]}")
        self.paths = sorted(set(unquote(p) for p in paths))


def on_nodes(nodes, fn):
    """Run fn(node) on each node in parallel. A node that fails AND no longer
    answers is dropped; a failure on a node that still answers is a real error.
    Returns the nodes that are left."""
    def one(n):
        try:
            fn(n)
            return n
        except Exception:
            if probe(n) == "down":
                log(f"{n} is down; carrying on without it")
                return None
            raise
    return [n for n in all_nodes(one, nodes=nodes) if n]


def run_job(job):
    jid = job["id"]
    jd = JOBDIR / jid
    t0 = time.time()
    nodes = []
    job.update(status="running", startedAt=t0)
    try:
        if not code_ready(job["code"]):
            raise RuntimeError("code not installed; PUT /v1/code/<hash> first")
        missing = missing_assets(job["ns"], job["files"])
        if missing:
            raise MissingAssets(missing)

        job["phase"] = "checking nodes"
        state = dict(zip(NODES, all_nodes(probe, job["code"])))
        nodes = [n for n in NODES if state[n] == "up"]
        for n in NODES:
            if state[n] == "nocode":                  # back after being down
                install_later(n, job["code"])
        if not nodes:
            raise RuntimeError(f"no farm node can take the job: {state}")
        job["nodes"] = nodes

        job["phase"] = "syncing assets"
        pushed = []
        nodes = on_nodes(nodes, lambda n: pushed.append(
            sync_assets_to(n, job["ns"], list(job["files"]))))
        job["timings"]["syncS"] = round(time.time() - t0, 1)
        job["assetsPushed"] = sum(pushed)

        props = json.dumps(job["props"]).encode()
        nodes = on_nodes(nodes, lambda n: ssh_ok(
            n, f"mkdir -p {NODE_ROOT}/work/{jid} && cat > {NODE_ROOT}/work/{jid}/props.json",
            stdin=props, text=False))
        if not nodes:
            raise RuntimeError("every farm node dropped out while preparing the job")

        job["phase"] = "waiting for smem to hand over the nodes"
        lend_card(jid)
        job["timings"]["drainS"] = wait_for_smem_drain()
        keep = threading.Event()

        def heartbeat():
            while not keep.wait(300):
                lend_card(jid)
        threading.Thread(target=heartbeat, daemon=True).start()

        try:
            parts = plan_parts(job["totalFrames"], job.get("bounds"), len(nodes))
            job.update(phase="rendering", nodes=nodes,
                       parts=[{"a": a, "b": b, "node": None, "done": False} for a, b in parts])
            work = queue.Queue()                  # largest first: shortest makespan
            for i, (a, b) in sorted(enumerate(parts), key=lambda x: x[1][0] - x[1][1]):
                work.put((i, a, b))
            errors, dead = [], set()
            tr = time.time()

            def node_lost(node):
                """Requeue everything the node held: its finished parts live on it."""
                with LOCK:
                    dead.add(node)
                    for k, p in enumerate(job["parts"]):
                        if p["node"] == node:
                            p.update(node=None, done=False)
                            work.put((k, p["a"], p["b"]))
                log(f"{node} dropped out of {jid}; its parts go to the other nodes")

            def node_loop(node):
                while not errors and node not in dead:
                    try:
                        i, a, b = work.get_nowait()
                    except queue.Empty:
                        return
                    job["parts"][i]["node"] = node
                    t = time.time()
                    try:
                        render_part(node, job, i, a, b)
                    except MissingAssets as e:
                        errors.append(e)
                        return
                    except Exception as e:
                        if probe(node) == "down":
                            node_lost(node)
                            return
                        errors.append(e)           # stop handing out work
                        return
                    job["parts"][i].update(done=True, seconds=round(time.time() - t, 1))

            # A lost node requeues work after the others may have finished, so
            # go round again with the survivors until the queue is empty.
            while not errors:
                live = [n for n in nodes if n not in dead]
                if work.empty() or not live:
                    break
                with ThreadPoolExecutor(len(live)) as ex:
                    list(ex.map(node_loop, live))
            if errors:
                raise errors[0]
            if not work.empty():
                raise RuntimeError("every farm node dropped out mid-render")
            nodes = [n for n in nodes if n not in dead]
            job["timings"]["renderS"] = round(time.time() - tr, 1)
        finally:
            keep.set()
            return_card(jid)

        job["phase"] = "joining"
        tj = time.time()
        jd.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(job["parts"]):
            data = ssh_ok(p["node"], f"cat {NODE_ROOT}/work/{jid}/part_{i:03d}.mkv",
                          text=False, timeout=1200)
            (jd / f"part_{i:03d}.mkv").write_bytes(data)
        (jd / "list.txt").write_text("".join(f"file '{jd}/part_{i:03d}.mkv'\n"
                                             for i in range(len(job["parts"]))))
        out = jd / "output.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(jd / "list.txt"), "-c:v", "copy",
                        "-af", f"adelay={AUDIO_DELAY_SAMPLES}S:all=1", "-c:a", "aac",
                        "-b:a", "320k", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True, timeout=1800)
        for f in jd.glob("part_*.mkv"):
            f.unlink()
        job["timings"]["joinS"] = round(time.time() - tj, 1)
        job.update(status="done", phase="done", outputBytes=out.stat().st_size)
    except MissingAssets as e:
        job.update(status="failed", phase="failed", error=str(e), missing=e.paths)
    except Exception as e:
        job.update(status="failed", phase="failed", error=f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        job["finishedAt"] = time.time()
        job["timings"]["totalS"] = round(time.time() - t0, 1)
        all_nodes(lambda n: ssh(n, f"rm -rf {NODE_ROOT}/work/{jid}"), nodes=nodes)
        log(f"job {jid} {job['status']} {job['composition']} "
            f"{job['totalFrames']} frames in {job['timings'].get('totalS')}s "
            f"{job.get('error') or ''}")


def worker():
    while True:
        jid = JOBQ.get()
        job = JOBS.get(jid)
        if job:
            run_job(job)
        cutoff = time.time() - KEEP_JOBS_S
        with LOCK:
            for k in [k for k, j in JOBS.items() if (j.get("finishedAt") or 1e18) < cutoff]:
                JOBS.pop(k)
                shutil.rmtree(JOBDIR / k, ignore_errors=True)


def public_job(j):
    return {k: j.get(k) for k in ("id", "status", "phase", "composition", "totalFrames",
                                  "parts", "timings", "error", "missing", "createdAt",
                                  "startedAt", "finishedAt", "outputBytes", "assetsPushed", "ns", "concurrency",
                                  "queuePosition", "nodes")}


# ------------------------------------------------------------------ http

class Handler(BaseHTTPRequestHandler):
    server_version = "renderfarm/1"

    def log_message(self, format, *args):  # noqa: A002 -- quiet access log
        pass

    def _send(self, code, obj=None, body=None, ctype="application/json"):
        if body is None:
            body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if TOKEN and self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _body_iter(self, length, bs=1 << 20):
        left = length
        while left > 0:
            chunk = self.rfile.read(min(bs, left))
            if not chunk:
                raise ValueError("short body")
            left -= len(chunk)
            yield chunk

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = urlparse(self.path).path.rstrip("/")
        if p == "/health":
            busy = [j["id"] for j in JOBS.values() if j["status"] == "running"]
            return self._send(200, {"ok": True, "nodes": NODES, "running": busy,
                                    "queued": JOBQ.qsize(),
                                    "down": [n for n, up in NODE_UP.items() if up is False]})
        if not self._auth():
            return
        m = re.fullmatch(r"/v1/code/([0-9a-f]{64})", p)
        if m:
            return self._send(200, {"ready": code_kept(m.group(1))})
        m = re.fullmatch(r"/v1/jobs/(job_[0-9a-f]+)(/output)?", p)
        if m:
            j = JOBS.get(m.group(1))
            if not j:
                return self._send(404, {"error": "no such job"})
            if not m.group(2):
                return self._send(200, public_job(j))
            out = JOBDIR / j["id"] / "output.mp4"
            if j["status"] != "done" or not out.exists():
                return self._send(409, {"error": f"job is {j['status']}"})
            size = out.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(out, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return
        if p == "/v1/jobs":
            return self._send(200, [public_job(j) for j in JOBS.values()])
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        if not self._auth():
            return
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        try:
            m = re.fullmatch(r"/v1/code/([0-9a-f]{64})", p)
            if m:
                h = m.group(1)
                if code_kept(h):
                    self.rfile.read(n)
                    return self._send(200, {"ready": True})
                CODEDIR.mkdir(parents=True, exist_ok=True)
                tgz = CODEDIR / f"{h}.tgz"
                with open(tgz, "wb") as f:
                    for c in self._body_iter(n):
                        f.write(c)
                try:
                    if package_hash(tgz) != h:
                        raise ValueError("package does not match its hash")
                    if code_ready(h):
                        restore_code(h, tgz)
                    else:
                        install_code(h, tgz, self.headers.get("X-Render-Dir", "packages/render"))
                finally:
                    tgz.unlink(missing_ok=True)
                return self._send(200, {"ready": True})
            if p.startswith("/v1/assets/"):
                rel = unquote(p[len("/v1/assets/"):])
                ns = safe_ns(parse_qs(urlparse(self.path).query).get("ns", ["default"])[0])
                store_asset(ns, rel, self.headers.get("X-Sha256", ""), self._body_iter, n)
                return self._send(200, {"stored": rel})
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return
        p = urlparse(self.path).path.rstrip("/")
        try:
            if p == "/v1/assets/missing":
                b = self._json()
                return self._send(200, {"missing": missing_assets(safe_ns(b.get("ns")),
                                                                  b.get("files", {}))})
            if p == "/v1/jobs":
                b = self._json()
                props = b["props"]
                total = int(b.get("totalFrames") or props.get("durationInFrames") or 0)
                if total <= 0:
                    raise ValueError("totalFrames unknown (pass it, or props.durationInFrames)")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", b["composition"]):
                    raise ValueError("bad composition id")
                # `code` becomes a path in commands run on the nodes: a hash, nothing else.
                if not re.fullmatch(r"[0-9a-f]{64}", str(b.get("code", ""))):
                    raise ValueError("bad code hash")
                entry = safe_rel(b.get("entry", "src/index.ts"))
                ns = safe_ns(b.get("ns"))
                conc = int(b.get("concurrency") or CONCURRENCY)
                if not 1 <= conc <= 8:
                    raise ValueError("concurrency must be 1-8")
                files = {safe_rel(k): v for k, v in b.get("files", {}).items()}
                jid = f"job_{uuid.uuid4().hex[:12]}"
                JOBS[jid] = {"id": jid, "status": "queued", "phase": "queued",
                             "code": b["code"], "composition": b["composition"],
                             "entry": entry, "props": props, "totalFrames": total,
                             "ns": ns, "concurrency": conc,
                             "bounds": b.get("bounds"), "files": files,
                             "createdAt": time.time(), "timings": {}}
                JOBS[jid]["queuePosition"] = JOBQ.qsize()
                JOBQ.put(jid)
                return self._send(202, {"id": jid})
        except (KeyError, ValueError) as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})


def main():
    for d in (PUBLIC, JOBDIR, CODEDIR, NODE_INDEX, INDEXDIR):
        d.mkdir(parents=True, exist_ok=True)
    # Code dirs whose node copies may be gone after a reinstall are re-checked
    # lazily: a missing node dir makes the render fail and the client falls back.
    threading.Thread(target=worker, daemon=True, name="worker").start()
    threading.Thread(target=prober, daemon=True, name="prober").start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log(f"renderfarm on :{PORT} nodes={NODES} state={STATE} auth={'on' if TOKEN else 'off'}")
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
