# SPDX-License-Identifier: MIT
"""HTTP API and web console, shaped after Supermemory's.

    python3 -m smem.server [--db PATH] [--port 8900]

Open http://<host>:8900/ for the console. Set SMEM_TOKEN to require
`Authorization: Bearer <token>` on every /v* route (the console asks for it).

API
    GET    /health
    GET    /v1/containers                       spaces and their sizes
    GET    /v1/stats?containerTag=
    GET    /v1/overview?containerTag=           console home in one call
    GET    /v1/nodes                            live node telemetry
GET    /v1/lanes                            interactive/background nodes, paused, in-flight
    GET    /v1/activity                         request log + per-route stats
    GET    /v1/graph?containerTag=&docSim=&sim=&forgotten=1
    GET    /v1/dreaming                         background worker state
    POST   /v1/dreaming           {enabled}     pause / resume it

    POST   /v3/documents          {content | url, containerTag, title,
                                   customId, metadata, extract, wait}
                                   -> 202 {jobId}; wait=true blocks as before
    GET    /v3/jobs?containerTag=               ingest jobs and their status
    GET    /v3/documents?containerTag=
    GET    /v3/documents/{id}                   content + its memories
    DELETE /v3/documents/{id}                   hard delete, cascades

    POST   /v3/memories           {content, containerTag}
    GET    /v3/memories?containerTag=&status=&q=&docId=&type=&limit=&offset=
    GET    /v3/memories/{id}/history            versions + relations
    PATCH  /v3/memories/{id}      {content}     update = new version
    DELETE /v3/memories/{id}                    soft delete (forget)
    POST   /v3/memories/{id}/restore            undo a forget
    POST   /v3/memories/forget    {q, containerTag, confirm}

    GET    /v4/profile?containerTag=            static + dynamic profile
    POST   /v4/search             {q, containerTag, limit, rerank=false, halfLifeDays}
    POST   /v1/ask                {q, containerTag, limit, halfLifeDays}

CONCURRENCY. Writes from request handlers take one lock on the shared
connection. Reads open their own read-only connection and take no lock.
Ingest jobs and dreaming run in ONE background worker with its OWN write
connection (SQLite serialises the short write transactions; busy_timeout
covers the overlap), so an hour-long ingest never blocks the API.
"""

import argparse
import collections
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import calibrate
from .engine import Engine
from .fetch import FetchError, fetch_url
from .models import Models
from .pool import BG_NODES, Pool, paused_nodes

_LOCK = threading.Lock()          # writers on ENGINE.store only
ENGINE: Engine
BG_MODELS: Models        # background lane (card 1), see pool.py
DB_PATH = ""
TOKEN = os.environ.get("SMEM_TOKEN") or None
UI_DIR = Path(__file__).parent / "ui"
CONNECT_DIR = Path(__file__).parent / "connect"
STARTED = time.time()

# ------------------------------------------------------------ request log

LOG = collections.deque(maxlen=5000)
_ID = re.compile(r"/(doc|mem|chk|job)_[0-9a-f]{12,16}")
# Last time a request needed the MODELS. Dreaming waits for this to go quiet;
# console polling (GETs) deliberately does not count, or an open dashboard
# would stop the background work forever.
_MODEL_ACTIVITY = [0.0]


def _route(method, path):
    """Collapse ids so /v3/memories/mem_abc and /v3/memories/mem_def group."""
    return f"{method} {_ID.sub(lambda m: '/{' + m.group(1) + '}', path)}"


def activity():
    now = time.time()
    snap = list(LOG)   # one snapshot; the deque keeps growing under us
    by = {}
    for ts, route, status, ms in snap:
        r = by.setdefault(route, {"route": route, "count": 0, "errors": 0,
                                  "ms": []})
        r["count"] += 1
        r["errors"] += status >= 400
        r["ms"].append(ms)
    routes = []
    for r in by.values():
        ms = sorted(r.pop("ms"))
        r["p50"] = round(ms[len(ms) // 2], 1)
        r["p95"] = round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1)
        routes.append(r)
    routes.sort(key=lambda r: -r["count"])
    per_min = [0] * 60
    for ts, *_ in snap:
        age = int((now - ts) // 60)
        if 0 <= age < 60:
            per_min[59 - age] += 1
    recent = [{"ts": ts, "route": route, "status": st, "ms": round(ms, 1)}
              for ts, route, st, ms in snap[-60:]][::-1]
    return {"uptimeS": round(now - STARTED), "total": len(snap),
            "routes": routes, "perMinute": per_min, "recent": recent}


# ---------------------------------------------------------- node telemetry

NODE_PW = os.environ.get("NODE_PW", "vista1")   # Intel's documented default
NODES = {"updated": 0, "card": [], "nodes": []}

_PROBE = r'''
for h in /sys/class/hwmon/hwmon*; do
  [ "$(cat $h/name 2>/dev/null)" = coretemp ] || continue
  echo "pkg=$(cat $h/temp1_input)"
  echo "cores=$(cat $h/temp[2-9]_input 2>/dev/null | tr '\n' ,)"
done
awk '/MemTotal/{print "memtotal="$2} /MemAvailable/{print "memavail="$2}' /proc/meminfo
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
for u in sm-emb sm-rrk sm-llm; do
  echo "$u=$(systemctl is-active $u),$(cat /sys/fs/cgroup/memory/system.slice/$u.service/memory.usage_in_bytes 2>/dev/null || echo 0),$(systemctl show $u -p NRestarts --value)"
done
'''


def _probe_node(ip):
    try:
        r = subprocess.run(
            ["sshpass", "-p", NODE_PW, "ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", ip, _PROBE],
            capture_output=True, text=True, timeout=15)
    except Exception as e:
        return {"ip": ip, "reachable": False, "error": str(e)[:80]}
    kv = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    if not kv:
        return {"ip": ip, "reachable": False, "error": r.stderr.strip()[-80:]}
    services = {}
    for key, label in (("sm-emb", "embedder"), ("sm-rrk", "reranker"),
                       ("sm-llm", "llm")):
        state, mem, restarts = (kv.get(key, "unknown,0,0").split(",") + ["0", "0"])[:3]
        services[label] = {"state": state, "memBytes": int(mem or 0),
                           "restarts": int(restarts or 0)}
    cores = [int(x) / 1000 for x in kv.get("cores", "").split(",") if x.strip()]
    return {
        "ip": ip, "reachable": True,
        "pkgC": int(kv.get("pkg", "0")) / 1000,
        "hottestCoreC": max(cores) if cores else None,
        "memTotal": int(kv.get("memtotal", 0)) * 1024,
        "memAvail": int(kv.get("memavail", 0)) * 1024,
        "load": kv.get("load", ""),
        "uptimeS": float(kv.get("uptime", 0) or 0),
        "services": services,
    }


def _sampler(interval=15):
    pools = [ENGINE.models.pool, BG_MODELS.pool]
    ips = [ip for p in pools for ip in p.nodes]
    with ThreadPoolExecutor(max_workers=len(ips)) as ex:
        while True:
            try:
                nodes = list(ex.map(_probe_node, ips))
                paused = paused_nodes()
                for n in nodes:
                    owner = next(p for p in pools if n["ip"] in p.nodes)
                    n["lane"] = owner.name
                    n["paused"] = n["ip"] in paused
                    n["inflight"] = sum(p._inflight.get(n["ip"], 0) for p in pools)
                    n["requests"] = sum(p.calls.get(n["ip"], 0) for p in pools)
                card = []
                try:
                    out = subprocess.run(["vcactl", "status"], capture_output=True,
                                         text=True, timeout=10).stdout
                    card = [m.group(1) for m in re.finditer(r"STATE:\s*(\S+)", out)]
                except Exception:
                    pass
                NODES.update(updated=time.time(), nodes=nodes, card=card)
            except Exception as e:
                NODES["error"] = str(e)[:120]
            time.sleep(interval)


# ---------------------------------------------- background worker: jobs + dreaming

JOBS = collections.OrderedDict()      # jobId -> job dict, newest last
JOBQ = queue.Queue()
WORKER = {"enabled": os.environ.get("SMEM_DREAMING", "1") != "0",
          "state": "starting", "since": time.time(), "last": None,
          "done": collections.Counter(), "idle_after_s": 20,
          "labels_at_fit": 0, "last_fit": None}
REFIT_EVERY = 5      # new labels between automatic calibration refits


def _job_public(j):
    return {k: j.get(k) for k in ("id", "container", "title", "url", "status",
                                  "phase", "createdAt", "startedAt", "finishedAt",
                                  "result", "error", "chars")}


def _enqueue(content=None, url=None, **kw):
    jid = f"job_{uuid.uuid4().hex[:12]}"
    j = {"id": jid, "content": content, "url": url, "status": "queued",
         "phase": "waiting", "createdAt": time.time(), "startedAt": None,
         "finishedAt": None, "result": None, "error": None,
         "chars": len(content) if content else None,
         "done_event": threading.Event(), **kw}
    j["title"] = kw.get("title") or url or "Untitled"
    JOBS[jid] = j
    while len(JOBS) > 200:
        JOBS.popitem(last=False)
    JOBQ.put(jid)
    return j


def _run_job(eng, j):
    j["status"], j["startedAt"] = "processing", time.time()
    WORKER.update(state="ingesting", since=time.time())
    try:
        content = j["content"]
        if j.get("url"):
            j["phase"] = "fetching page"
            title, content = fetch_url(j["url"])
            if not j.get("title_given"):
                j["title"] = title
            j["chars"] = len(content)
        j["phase"] = "chunking, embedding and extracting facts"
        r = eng.ingest(content, title=j["title"], source=j.get("url"),
                       custom_id=j.get("custom_id") or j.get("url"),
                       container=j["container"], metadata=j.get("metadata"),
                       extract=j.get("extract", True),
                       progress=lambda s: j.update(phase=s.strip()))
        r["elapsed"] = round(time.time() - j["startedAt"], 1)
        j.update(status="done", result=r, phase="done")
        WORKER["done"]["documents"] += 1
    except FetchError as e:
        j.update(status="failed", error=str(e), phase="failed")
    except Exception as e:
        j.update(status="failed", error=f"{type(e).__name__}: {e}", phase="failed")
    finally:
        j["finishedAt"] = time.time()
        j["done_event"].set()


def _worker():
    """Ingest jobs first; when idle, dream. One thread, one DB connection."""
    # The background lane: card 1, falling back to card 0 (see pool.py).
    eng = Engine(DB_PATH, models=BG_MODELS, container=ENGINE.container)
    while True:
        try:
            jid = JOBQ.get(timeout=5)
        except queue.Empty:
            jid = None
        if jid and jid in JOBS:
            _run_job(eng, JOBS[jid])
            continue
        quiet = time.time() - _MODEL_ACTIVITY[0]
        if not WORKER["enabled"]:
            WORKER.update(state="paused")
            continue
        if quiet < WORKER["idle_after_s"]:
            WORKER.update(state="waiting for the card to go quiet")
            continue
        try:
            # Count labels that pair with a logged decision: a label on an
            # item the judge has not seen yet (e.g. right after a re-run
            # started) teaches nothing until the decision exists.
            n_labels = (len(eng.store.labelled_decisions("type"))
                        + len(eng.store.labelled_decisions("extends")))
            if n_labels - WORKER["labels_at_fit"] >= REFIT_EVERY:
                WORKER["last_fit"] = {"at": time.time(), "labels": n_labels,
                                      "result": calibrate.refit(eng.store)}
                WORKER["labels_at_fit"] = n_labels
        except Exception as e:
            # Never leave a transaction open on this connection: it would hold
            # the write lock and fail every API write ("database is locked").
            eng.store.db.rollback()
            WORKER["last_fit"] = {"at": time.time(), "error": str(e)[:100]}
        try:
            WORKER.update(state="dreaming", since=time.time())
            r = eng.dream_step()
            if r["did"] == "idle":
                WORKER.update(state="idle: every memory is typed and checked")
                time.sleep(30)
            else:
                r["at"] = time.time()
                WORKER["last"] = r
                if r["did"] == "classify":
                    WORKER["done"]["classified"] += r["n"]
                else:
                    WORKER["done"]["checked"] += r["memories"]
                    WORKER["done"]["extends"] += r["found"]
        except Exception as e:
            eng.store.db.rollback()
            WORKER.update(state=f"error: {str(e)[:100]}")
            time.sleep(30)


# --------------------------------------------------------------- the graph

def build_graph(st, container, sim=0.0, doc_sim=0.90, history=True,
                forgotten=False, max_related=2):
    """Documents, memories and the relations between them.

    Edge kinds, following Supermemory's console legend:
      source   memory -> the document it was extracted from
      updates  newer memory -> the version it replaced     (version chain)
      extends  memory -> memory it adds detail to          (from dreaming)
      docsim   document <-> document, centroid cosine >= doc_sim
      similar  memory <-> memory by raw cosine >= sim (off unless sim > 0)
    """
    now = time.time()
    docs = st.document_summary(container)
    nodes = [{"id": d["id"], "type": "document", "label": d["title"] or d["id"],
              "facts": d["facts"], "chars": d["chars"],
              "created": d["created_at"]} for d in docs]
    where = "container=? AND (is_latest=1 OR forgotten_at IS NOT NULL"
    where += " OR is_latest=0)" if history else ")"
    rows = st.db.execute(
        "SELECT id, content, doc_id, version, is_latest, forgotten_at, created_at,"
        " mtype, supersedes FROM memories WHERE " + where, (container,)).fetchall()
    for r in rows:
        if r["forgotten_at"] is not None:
            if not forgotten:
                continue
            t = "forgotten"
        else:
            t = "memory" if r["is_latest"] else "superseded"
        nodes.append({"id": r["id"], "type": t, "label": r["content"],
                      "doc": r["doc_id"], "version": r["version"],
                      "mtype": r["mtype"], "created": r["created_at"],
                      "new": now - r["created_at"] < 86400})
    ids = {n["id"] for n in nodes}
    edges = [{"s": r["id"], "t": r["doc_id"], "kind": "source"}
             for r in rows if r["doc_id"] and r["id"] in ids]
    edges += [{"s": r["id"], "t": r["supersedes"], "kind": "updates"}
              for r in rows if r["supersedes"]]
    edges += [{"s": e["src"], "t": e["dst"], "kind": "extends",
               "w": e["score"]} for e in st.edges(container, "extends")]

    drows, dmat = st.doc_vectors(container)
    if len(drows) > 1:
        S = dmat @ dmat.T
        np.fill_diagonal(S, -1.0)
        seen = set()
        for i in range(len(drows)):
            for j in np.argsort(-S[i])[:2]:
                j = int(j)
                if S[i, j] < doc_sim:
                    break
                a, b = sorted((i, j))
                if (a, b) not in seen:
                    seen.add((a, b))
                    edges.append({"s": drows[a]["id"], "t": drows[b]["id"],
                                  "kind": "docsim", "w": round(float(S[i, j]), 3)})

    if sim > 0:
        mrows, mat = st.matrix(container, "memories")
        if len(mrows) > 1 and mat.shape[1] > 1:
            S = mat @ mat.T
            np.fill_diagonal(S, -1.0)
            seen = set()
            k = min(max_related, len(mrows) - 1)
            top = np.argpartition(-S, k, axis=1)[:, :k]
            for i in range(len(mrows)):
                for j in top[i]:
                    j = int(j)
                    if S[i, j] < sim:
                        continue
                    a, b = sorted((i, j))
                    if (a, b) not in seen:
                        seen.add((a, b))
                        edges.append({"s": mrows[a]["id"], "t": mrows[b]["id"],
                                      "kind": "similar", "w": round(float(S[i, j]), 3)})

    edges = [e for e in edges if e["s"] in ids and e["t"] in ids]
    return {"nodes": nodes, "edges": edges,
            "counts": dict(collections.Counter(n["type"] for n in nodes)),
            "edgeCounts": dict(collections.Counter(e["kind"] for e in edges)),
            "docSim": doc_sim}


# ---------------------------------------------------------------- handlers

def _hit_json(e, h):
    d = e.store.get_document(h["doc_id"]) if h["doc_id"] else None
    return {
        "id": h["id"], "type": h["kind"], "content": h["text"],
        "heading": h.get("heading"),
        "score": round(h["score"], 6),
        "vectorScore": round(h["vector_score"], 6),
        "rerankScore": round(h["rerank_score"], 6),
        "decay": h["decay"], "documentId": h["doc_id"],
        "title": (d["title"] if d else None),
    }


def _mem_json(r):
    keys = r.keys()
    g = lambda k: r[k] if k in keys else None
    return {
        "id": r["id"], "content": r["content"], "version": r["version"],
        "isLatest": bool(r["is_latest"]),
        "forgotten": r["forgotten_at"] is not None,
        "supersedes": r["supersedes"],
        "supersededBy": g("superseded_by"),
        "createdAt": r["created_at"], "documentId": r["doc_id"],
        "documentTitle": g("doc_title"),
        "type": g("mtype"),
        "typeConf": g("mtype_conf"),
        "degree": g("degree"),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "smem/0.3"

    def log_message(self, format, *args):  # noqa: A002 (base class name)
        pass   # requests go to LOG; the journal only needs errors

    # ------------------------------------------------------------ plumbing

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)
        path = urlparse(self.path).path
        if path.startswith("/v"):
            LOG.append((time.time(), _route(self.command, path.rstrip("/")),
                        code, (time.time() - self._t0) * 1000))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except json.JSONDecodeError:
            return {}

    def _q(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _authorised(self, path):
        if not TOKEN or not path.startswith("/v"):
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _dispatch(self, fn):
        self._t0 = time.time()
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._authorised(path):
            return self._send(401, {"error": "missing or wrong bearer token"})
        try:
            fn(path)
        except BrokenPipeError:
            pass
        except Exception as ex:
            try:
                self._send(500, {"error": f"{type(ex).__name__}: {ex}"})
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch(self._get)

    def do_POST(self):
        self._dispatch(self._post)

    def do_PATCH(self):
        self._dispatch(self._patch)

    def do_DELETE(self):
        self._dispatch(self._delete)

    # --------------------------------------------------------------- reads

    def _connect_file(self, name):
        """Setup files an agent on another machine can fetch for itself:
        the MCP bridge and the instructions in smem/connect/. A fixed set of
        names only -- nothing here reads an arbitrary path."""
        files = {"mcp_smem.py": Path(__file__).parent.parent / "mcp_smem.py"}
        files.update({f.name: f for f in CONNECT_DIR.glob("*.md")})
        if not name:
            listing = "\n".join(f"{n}" for n in sorted(files) if files[n].exists())
            return self._send(200, f"smem setup files. Agents: start with hermes.md\n\n{listing}\n".encode(),
                              "text/plain; charset=utf-8")
        f = files.get(name)
        if not f or not f.exists():
            return self._send(404, {"error": "no such setup file"})
        ctype = "text/x-python" if name.endswith(".py") else "text/markdown"
        return self._send(200, f.read_bytes(), f"{ctype}; charset=utf-8")

    def _get(self, p):
        if p in ("/", "/index.html"):
            page = UI_DIR / "index.html"
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        if p == "/connect" or p.startswith("/connect/"):
            return self._connect_file(p[len("/connect/"):] if p != "/connect" else "")
        if p == "/health":
            return self._send(200, {"ok": True, "auth": bool(TOKEN),
                                    "nodes": ENGINE.models.pool.health()})
        if p == "/v1/nodes":
            return self._send(200, NODES)
        if p == "/v1/lanes":
            return self._send(200, {
                "interactive": ENGINE.models.pool.nodes,
                "background": BG_MODELS.pool.nodes,
                "paused": sorted(paused_nodes()),
                "inflight": BG_MODELS.pool.inflight()})
        if p == "/v1/activity":
            return self._send(200, activity())

        q = self._q()
        c = q.get("containerTag", ENGINE.container)
        e = ENGINE.reader()
        try:
            st = e.store
            if p == "/v1/dreaming":
                return self._send(200, {
                    "enabled": WORKER["enabled"], "state": WORKER["state"],
                    "since": WORKER["since"], "last": WORKER["last"],
                    "done": dict(WORKER["done"]),
                    "queue": sum(1 for j in JOBS.values()
                                 if j["status"] in ("queued", "processing")),
                    "backlog": st.enrichment_counts(calibrate.current(st, "type")[1]),
                    "lastFit": WORKER["last_fit"],
                    "idleAfterS": WORKER["idle_after_s"]})
            if p == "/v1/calibration":
                kinds = {}
                for k in ("type", "extends"):
                    t, thr, fitted = calibrate.current(st, k)
                    c_ = st.calibration(k)
                    kinds[k] = {"temperature": t, "threshold": thr, "fitted": fitted,
                                "fittedAt": c_["fitted_at"] if c_ else None,
                                "stats": c_["stats"] if c_ else None,
                                "labelled": len(st.labelled_decisions(k)),
                                "minLabels": calibrate.MIN_LABELS}
                dec = {r_[0]: r_[1] for r_ in st.db.execute(
                    "SELECT kind, COUNT(*) FROM decisions GROUP BY kind")}
                return self._send(200, {"kinds": kinds, "labels": st.label_counts(),
                                        "decisions": dec,
                                        "targets": {"extendsPrecision": calibrate.TARGET_PRECISION,
                                                    "typeAccuracy": calibrate.TARGET_TYPE_ACC}})
            if p == "/v1/containers":
                out = []
                for name in st.containers():
                    s = st.stats(name)
                    out.append({"name": name, "documents": s["documents"],
                                "memories": s["memories_live"]})
                return self._send(200, {"containers": out,
                                        "default": ENGINE.container})
            if p == "/v1/stats":
                return self._send(200, st.stats(c))
            if p == "/v1/overview":
                docs = st.document_summary(c)
                return self._send(200, {
                    "container": c,
                    "stats": st.stats(c),
                    "types": st.type_counts(c),
                    "extends": len(st.edges(c, "extends")),
                    "created": [{"t": b, "n": n}
                                for b, n in st.created_series(c, 3600)],
                    "documents": [{"id": d["id"], "title": d["title"],
                                   "facts": d["facts"], "chunks": d["chunks"],
                                   "superseded": d["superseded"],
                                   "chars": d["chars"]} for d in docs],
                    "nodes": NODES,
                    "activity": activity(),
                })
            if p == "/v4/profile":
                pr = e.profile(c, n_static=int(q.get("static", 12)),
                               n_dynamic=int(q.get("dynamic", 10)))
                return self._send(200, {
                    "container": c,
                    "static": [_mem_json(r) for r in pr["static"]],
                    "dynamic": [_mem_json(r) for r in pr["dynamic"]],
                    "types": pr["types"], "unclassified": pr["unclassified"],
                    "uncertain": pr["uncertain"], "typeThreshold": pr["typeThreshold"]})
            if p == "/v1/graph":
                return self._send(200, build_graph(
                    st, c, sim=float(q.get("sim", 0)),
                    # 0.90 under EmbeddingGemma 2 = 0.78 under Qwen3 (EMBED_BAKEOFF.md)
                    doc_sim=float(q.get("docSim", 0.90)),
                    history=q.get("history", "1") in ("1", "true"),
                    forgotten=q.get("forgotten", "0") in ("1", "true")))
            if p == "/v3/jobs":
                js = [_job_public(j) for j in reversed(JOBS.values())
                      if not q.get("containerTag") or j["container"] == c]
                return self._send(200, {"jobs": js[:50]})
            if p == "/v3/documents":
                return self._send(200, {"documents": [
                    {"id": d["id"], "title": d["title"], "customId": d["custom_id"],
                     "chars": d["chars"], "chunks": d["chunks"],
                     "facts": d["facts"], "superseded": d["superseded"],
                     "createdAt": d["created_at"]}
                    for d in st.document_summary(c)]})
            if p.startswith("/v3/documents/"):
                r = st.get_document(p.rsplit("/", 1)[-1])
                if not r:
                    return self._send(404, {"error": "not found"})
                _, mems = st.memory_page(r["container"], status="all",
                                         doc_id=r["id"], limit=500)
                return self._send(200, {
                    "id": r["id"], "title": r["title"], "content": r["content"],
                    "customId": r["custom_id"], "container": r["container"],
                    "source": r["source"], "createdAt": r["created_at"],
                    "metadata": json.loads(r["metadata"] or "{}"),
                    "memories": [_mem_json(m) for m in mems]})
            if p.endswith("/history") and p.startswith("/v3/memories/"):
                mid = p.split("/")[3]
                versions = []
                for r in st.history(mid):
                    m = _mem_json(r)
                    d = st.get_document(r["doc_id"]) if r["doc_id"] else None
                    m["documentTitle"] = d["title"] if d else None
                    nxt = st.db.execute("SELECT id FROM memories WHERE supersedes=?",
                                        (r["id"],)).fetchone()
                    m["supersededBy"] = nxt["id"] if nxt else None
                    versions.append(m)
                rels = [{"kind": r["kind"], "direction": r["dir"],
                         "src": r["src"], "dst": r["dst"],
                         "id": r["other_id"], "content": r["other_content"],
                         "score": r["score"], "conf": r["conf"],
                         "isLatest": bool(r["other_latest"])}
                        for r in st.relations(mid)]
                return self._send(200, {"versions": versions, "relations": rels})
            if p == "/v3/memories":
                status = q.get("status", "live")
                if q.get("history") in ("1", "true"):
                    status = "all"
                total, rows = st.memory_page(
                    c, status=status, q=q.get("q") or None,
                    doc_id=q.get("docId") or None,
                    limit=min(int(q.get("limit", 50)), 500),
                    offset=int(q.get("offset", 0)), mtype=q.get("type") or None)
                return self._send(200, {"total": total,
                                        "memories": [_mem_json(r) for r in rows]})
        finally:
            e.close()
        self._send(404, {"error": "no such route"})

    # -------------------------------------------------------------- writes

    def _post(self, p):
        b = self._body()
        c = b.get("containerTag") or ENGINE.container
        # The query field is `q` (Supermemory's name). `query` is accepted too:
        # the MCP tools call it that, and a caller using it used to get an
        # EMPTY search -- the embedding of the instruction prefix alone, i.e.
        # the same top hit at the same score for every "query". A missing
        # query is now an error, never a silent constant.
        q = str(b.get("q") or b.get("query") or "").strip()
        b["q"] = q
        if p in ("/v4/search", "/v1/ask", "/v3/memories/forget") and not q:
            return self._send(400, {"error": "q (the query text) is required"})

        if p == "/v4/search":
            _MODEL_ACTIVITY[0] = time.time()
            e = ENGINE.reader()
            try:
                hits = e.search(b.get("q", ""), container=c,
                                k=int(b.get("limit", 8)),
                                # Opt-in: vector search answers in ~0.1 s; the cross-encoder
                                # adds 5-17 s on these CPUs (measured 2026-09-24).
                                rerank=bool(b.get("rerank", False)),
                                half_life_days=b.get("halfLifeDays"))
                return self._send(200, {
                    "results": [_hit_json(e, h) for h in hits],
                    "timing": {"vectorMs": hits[0]["vector_ms"] if hits else 0,
                               "rerankMs": hits[0]["rerank_ms"] if hits else 0}})
            finally:
                _MODEL_ACTIVITY[0] = time.time()
                e.close()

        if p == "/v1/ask":
            _MODEL_ACTIVITY[0] = time.time()
            e = ENGINE.reader()
            try:
                r = e.ask(b.get("q", ""), container=c, k=int(b.get("limit", 8)),
                          half_life_days=b.get("halfLifeDays"))
                return self._send(200, {
                    "answer": r["answer"], "contextTokens": r["context_tokens"],
                    "sources": [_hit_json(e, h) for h in r["hits"]],
                    "timing": {"retrieveS": r.get("retrieve_s"),
                               "generateS": r.get("generate_s"),
                               "totalS": r.get("total_s")}})
            finally:
                _MODEL_ACTIVITY[0] = time.time()
                e.close()

        if p == "/v3/memories/forget" and not b.get("confirm"):
            e = ENGINE.reader()
            try:
                hits = e.forget_matching(b.get("q", ""), container=c,
                                         threshold=float(b.get("threshold", 0.60)),
                                         dry_run=True)
                return self._send(200, {"matched": hits, "forgotten": False})
            finally:
                e.close()

        if p == "/v1/dreaming":
            WORKER["enabled"] = bool(b.get("enabled", True))
            return self._send(200, {"enabled": WORKER["enabled"]})

        if p == "/v1/calibration/fit":
            with _LOCK:
                out = calibrate.refit(ENGINE.store)
            return self._send(200, out)

        if p == "/v3/documents":
            content, url = b.get("content"), (b.get("url") or "").strip()
            if not content and not url:
                return self._send(400, {"error": "content or url required"})
            j = _enqueue(content=content, url=url or None, container=c,
                         title=b.get("title") or None,
                         title_given=bool(b.get("title")),
                         custom_id=b.get("customId"), metadata=b.get("metadata"),
                         extract=b.get("extract", True))
            if b.get("wait"):
                j["done_event"].wait()
                code = 201 if j["status"] == "done" else 422
                return self._send(code, {**_job_public(j),
                                         **(j["result"] or {})})
            return self._send(202, _job_public(j))

        with _LOCK:
            if p == "/v3/memories":
                if not b.get("content"):
                    return self._send(400, {"error": "content required"})
                _MODEL_ACTIVITY[0] = time.time()
                n = ENGINE.remember(b["content"], container=c)
                return self._send(201, {"stored": n, "duplicate": n == 0})
            if p == "/v3/memories/forget":
                hits = ENGINE.forget_matching(
                    b.get("q", ""), container=c,
                    threshold=float(b.get("threshold", 0.60)), dry_run=False)
                return self._send(200, {"matched": hits, "forgotten": True})
            if p.startswith("/v3/memories/") and p.endswith("/type"):
                # A person setting (or confirming) a type: store it with full
                # confidence and keep it as a calibration label.
                mid = p.split("/")[3]
                t = (b.get("type") or "").lower()
                if t not in ("fact", "preference", "episode"):
                    return self._send(400, {"error": "type must be fact, preference or episode"})
                if not ENGINE.store.memory(mid):
                    return self._send(404, {"error": "not found"})
                ENGINE.store.set_mtype(mid, t, 1.0)
                ENGINE.store.add_label("type", mid, "", t.upper(), "user")
                return self._send(200, {"id": mid, "type": t, "labelled": True})
            if p == "/v1/relations/feedback":
                # Ground truth for a judged pair: src is the newer memory, dst
                # the older, as the judge sees them. The edge is corrected
                # immediately and the label feeds calibration.
                src, dst = b.get("src"), b.get("dst")
                lab = (b.get("label") or "").upper()
                if lab not in ("EXTENDS", "DUPLICATE", "UPDATE", "UNRELATED"):
                    return self._send(400, {"error": "label must be EXTENDS, DUPLICATE, UPDATE or UNRELATED"})
                a_, b_ = ENGINE.store.memory(src), ENGINE.store.memory(dst)
                if not a_ or not b_:
                    return self._send(404, {"error": "not found"})
                if (a_["created_at"], a_["id"]) < (b_["created_at"], b_["id"]):
                    src, dst = dst, src          # the judge always sees newer -> older
                ENGINE.store.delete_edges(src, dst)
                if lab in ("EXTENDS", "DUPLICATE"):
                    ENGINE.store.add_edge(src, dst, lab.lower(), None, conf=1.0)
                ENGINE.store.add_label("extends", src, dst, lab, "user")
                return self._send(200, {"src": src, "dst": dst, "label": lab})
            if p.startswith("/v3/memories/") and p.endswith("/restore"):
                mid = p.split("/")[3]
                if not ENGINE.store.memory(mid):
                    return self._send(404, {"error": "not found"})
                ENGINE.store.unforget(mid)
                return self._send(200, {"id": mid, "forgotten": False})
        self._send(404, {"error": "no such route"})

    def _patch(self, p):
        """Update a memory: store a new version, keep the old as history."""
        if not p.startswith("/v3/memories/"):
            return self._send(404, {"error": "no such route"})
        mid = p.rsplit("/", 1)[-1]
        b = self._body()
        text = (b.get("content") or "").strip()
        if not text:
            return self._send(400, {"error": "content required"})
        with _LOCK:
            old = ENGINE.store.memory(mid)
            if not old:
                return self._send(404, {"error": "not found"})
            if not old["is_latest"] or old["forgotten_at"] is not None:
                return self._send(409, {"error": "only the current version of a "
                                        "live memory can be updated"})
            _MODEL_ACTIVITY[0] = time.time()
            v = ENGINE.models.embed([text])[0]
            new = ENGINE.store.add_memory(old["container"], text, v,
                                          doc_id=old["doc_id"], supersedes=mid)
        return self._send(200, {"id": new, "supersedes": mid,
                                "version": old["version"] + 1})

    def _delete(self, p):
        if p.startswith("/v3/memories/"):
            mid = p.rsplit("/", 1)[-1]
            with _LOCK:
                if not ENGINE.store.memory(mid):
                    return self._send(404, {"error": "not found"})
                # Soft: the row survives with forgotten_at set, so this is
                # reversible and the history stays readable.
                ENGINE.store.forget(mid)
            return self._send(200, {"id": mid, "forgotten": True,
                                    "reversible": True})
        if p.startswith("/v3/documents/"):
            did = p.rsplit("/", 1)[-1]
            with _LOCK:
                d = ENGINE.store.get_document(did)
                if not d:
                    return self._send(404, {"error": "not found"})
                db = ENGINE.store.db
                # Hard delete, like Supermemory's document delete: the source
                # and everything extracted from it go. Explicit deletes
                # because the shared connection predates foreign_keys=ON in
                # some deployments.
                n_mem = db.execute("DELETE FROM memories WHERE doc_id=?", (did,)).rowcount
                db.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
                db.execute("DELETE FROM documents WHERE id=?", (did,))
                db.commit()
            return self._send(200, {"id": did, "deleted": True,
                                    "memoriesDeleted": n_mem})
        self._send(404, {"error": "no such route"})


def main(argv=None):
    global ENGINE, BG_MODELS, DB_PATH
    ap = argparse.ArgumentParser(prog="smem.server")
    ap.add_argument("--db", default="/root/supermem/smem.db")
    ap.add_argument("--container", default="default")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--no-telemetry", action="store_true",
                    help="skip the background node sampler")
    ap.add_argument("--no-worker", action="store_true",
                    help="skip ingest jobs and dreaming (POST /v3/documents "
                         "then only works with wait=true... and never runs)")
    a = ap.parse_args(argv)

    DB_PATH = a.db
    ENGINE = Engine(a.db, models=Models(pool=Pool()), container=a.container)
    BG_MODELS = Models(pool=Pool(BG_NODES, fallback=ENGINE.models.pool,
                                 name="background"))
    if not a.no_telemetry:
        threading.Thread(target=_sampler, daemon=True, name="telemetry").start()
    if not a.no_worker:
        threading.Thread(target=_worker, daemon=True, name="worker").start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"smem on http://{a.host}:{a.port}  db={a.db}  container={a.container}"
          f"  auth={'on' if TOKEN else 'off'}  dreaming={WORKER['enabled']}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ENGINE.close()


if __name__ == "__main__":
    main()
