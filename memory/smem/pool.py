# SPDX-License-Identifier: MIT
"""Dispatch across the VCA nodes, in two lanes.

Card 0 (nodes 0-2) is the INTERACTIVE lane: search and ask, so a query never
queues behind a 60 s extraction. Card 1 (nodes 3-5, the second VCA 2 added
2026-09-26) is the BACKGROUND lane: the ingest/dreaming worker. The
background pool falls back to card 0 when card 1 is paused (the render farm
borrows it -- see PAUSE_FILE), penalised, or absent. Since 2026-10-03 the farm
also borrows card 0's nodes 0-1, so during a render both lanes run on node 2.

Every stage of the pipeline is embarrassingly parallel across nodes: a whole
model lives on each node, so requests are distributed rather than the model
being split. That distinction is the whole reason this is worth running on
this card -- llama.cpp RPC layer-splitting across nodes measured 2.4x SLOWER
than a single node, because every token traverses the pipeline serially.
Independent workers measured 2.92x FASTER (embedder; 2.96x for LLM workers).
See docs/LLAMA.md and docs/BENCHMARK.md.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

def _env_nodes(var, default):
    v = os.environ.get(var, "").strip()
    return [n.strip() for n in v.split(",") if n.strip()] if v else default


NODES = _env_nodes("SMEM_NODES", ["172.31.1.1", "172.31.2.1", "172.31.3.1"])
BG_NODES = _env_nodes("SMEM_BG_NODES", ["172.31.4.1", "172.31.5.1", "172.31.6.1"])

# Another service (the render farm) takes nodes away from smem by writing
#   {"nodes": ["172.31.4.1", ...], "until": <epoch seconds>, "by": "renderfarm"}
# here. `until` is mandatory: a holder that crashes cannot keep a node forever.
PAUSE_FILE = os.environ.get("SMEM_PAUSE_FILE", "/run/smem/paused.json")
_PAUSE = {"mtime": None, "nodes": frozenset(), "until": 0.0}


def paused_nodes():
    """Nodes currently lent out, from PAUSE_FILE (re-read only when it changes)."""
    try:
        m = os.stat(PAUSE_FILE).st_mtime
    except OSError:
        return frozenset()
    if m != _PAUSE["mtime"]:
        try:
            with open(PAUSE_FILE) as f:
                d = json.load(f)
            _PAUSE.update(mtime=m, nodes=frozenset(d.get("nodes", [])),
                          until=float(d.get("until", 0)))
        except (OSError, ValueError):
            _PAUSE.update(mtime=m, nodes=frozenset(), until=0.0)
    return _PAUSE["nodes"] if time.time() < _PAUSE["until"] else frozenset()
PORT_EMB = 8080
PORT_RRK = 8081
PORT_LLM = 8082


class NodeError(RuntimeError):
    pass


class Pool:
    """Round-robin over nodes, skipping ones that just failed.

    A node that errors is parked for `cooldown` dispatches rather than removed:
    the failure is usually a transient timeout under load, not a dead node, and
    permanently evicting it would quietly drop a third of the capacity for the
    rest of a long ingest.
    """

    def __init__(self, nodes=None, timeout=600, cooldown=6, fallback=None,
                 name="interactive"):
        self.nodes = list(nodes or NODES)
        self.fallback = fallback
        self.name = name
        self.timeout = timeout
        self.cooldown = cooldown
        self._i = 0
        self._penalty = {n: 0 for n in self.nodes}
        self._inflight = {n: 0 for n in self.nodes}
        self._lock = threading.Lock()
        self.calls = {n: 0 for n in self.nodes}

    def _next(self):
        """Pick the node with the least work in flight.

        This was plain round-robin, and round-robin is not a work queue. When a
        call finished on node0, the next request still went to whichever node
        was next in the rotation -- often one still busy, where it waited
        behind a ~58 s extraction while node0 sat idle. Each server runs one
        request at a time, so the difference is real idle time.

        In-flight counts are per node, not per port: the embedder, reranker and
        LLM on one node share its four cores, so load on any of them counts.
        Ties go to rotation order, so idle nodes are still shared evenly.
        """
        paused = paused_nodes()
        with self._lock:
            k = self._i % len(self.nodes)
            self._i += 1
            order = [n for n in self.nodes[k:] + self.nodes[:k] if n not in paused]
            healthy = [n for n in order if self._penalty[n] == 0]
            # Penalties tick down on every dispatch, including ones handed to
            # the fallback, so a lane that failed over does get retried.
            for n in self.nodes:
                if self._penalty[n] > 0:
                    self._penalty[n] -= 1
            if not healthy:
                if self.fallback is not None:
                    return self.fallback._next()
                healthy = order or self.nodes
            node = min(healthy, key=lambda n: self._inflight[n])
            self._inflight[node] += 1
            self.calls[node] += 1
            return self, node

    def _done(self, node):
        with self._lock:
            self._inflight[node] -= 1

    def inflight(self):
        """Live in-flight counts for this pool's nodes (and its fallback's)."""
        with self._lock:
            out = dict(self._inflight)
        if self.fallback is not None:
            for n, v in self.fallback.inflight().items():
                out[n] = out.get(n, 0) + v
        return out

    def _penalise(self, node):
        with self._lock:
            self._penalty[node] = self.cooldown

    def post(self, port, path, payload, attempts=8, backoff=4.0):
        """POST json to `path` on some healthy node; retry elsewhere on failure.

        Retries have to survive ALL THREE nodes being down at once, not just
        one. The reranker in this llama.cpp build leaks, so it is capped and
        restarted by systemd -- and because the three nodes do identical work
        they hit the cap within seconds of each other. Three attempts against
        three restarting servers failed the whole run. Now the retry sleeps
        once it has tried every node, which covers the ~10 s a 0.64 GB model
        takes to come back.
        """
        body = json.dumps(payload).encode()
        last = None
        for i in range(attempts):
            if i and i % len(self.nodes) == 0:
                time.sleep(min(backoff * (i // len(self.nodes)), 30.0))
            owner, node = self._next()
            url = f"http://{node}:{port}{path}"
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
                owner._penalise(node)
                last = f"{node}: {e}"
            finally:
                owner._done(node)
        raise NodeError(f"all {attempts} attempts failed, last={last}")

    def health(self):
        out = {}
        for n in self.nodes:
            st = []
            for label, port in (("emb", PORT_EMB), ("rrk", PORT_RRK), ("llm", PORT_LLM)):
                try:
                    with urllib.request.urlopen(
                        f"http://{n}:{port}/health", timeout=5
                    ) as r:
                        st.append(label if b"ok" in r.read() else label + "?")
                except Exception:
                    st.append(label.upper() + "-DOWN")
            out[n] = st
        return out
