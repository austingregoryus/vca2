# SPDX-License-Identifier: MIT
"""smem as an MCP server: long-term memory for agents, served by the VCA 2.

    claude mcp add smem --scope user \
        -e SMEM_URL=http://<smem-host>:8900 -e SMEM_CONTAINER=claude \
        -- python /path/to/mcp_smem.py

Environment
    SMEM_URL        API base URL           (default http://127.0.0.1:8900)
    SMEM_CONTAINER  default container tag  (default "claude"). A container is
                    an isolated memory space; give each project its own.
    SMEM_TOKEN      bearer token, if the server was started with SMEM_TOKEN.

Standard library only, so it runs from any Python 3.8+ with nothing to
install. It speaks MCP over stdio (newline-delimited JSON-RPC 2.0) and
forwards every tool call to the smem HTTP API. Nothing here touches the card
directly; the API does.
"""

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SMEM_URL", "http://127.0.0.1:8900").rstrip("/")
CONTAINER = os.environ.get("SMEM_CONTAINER", "claude")
TOKEN = os.environ.get("SMEM_TOKEN", "")
VERSION = "0.2.0"

INSTRUCTIONS = f"""Long-term memory, stored on a local server (default container "{CONTAINER}").

- At the start of a task, call get_profile once: it returns the stable facts
  and preferences for this space (and what changed recently) in one call, so
  you know them without having to guess a search query that would find them.
- Before answering a question that may depend on something learned in an
  earlier session, call search_memories.
- When the user states a durable preference, decision or fact about their
  projects, call add_memory with ONE self-contained fact. A fact that
  contradicts an existing memory automatically supersedes it (the old version
  is kept as history); a restatement is dropped as a duplicate.
- search_memories is fast (~0.1 s). ask_memory writes a cited answer with a
  local model and takes 40-120 s -- prefer search_memories unless a
  synthesised answer is really needed."""


def log(*a):
    print("[smem-mcp]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP

def call(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"smem API {e.code}: {msg or e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach smem at {URL}: {e.reason}")


def _c(args):
    return args.get("container") or CONTAINER


def _hits(results):
    out = []
    for i, r in enumerate(results, 1):
        kind = "fact" if r["type"] == "memory" else "passage"
        src = f" (from {r['title']})" if r.get("title") else ""
        text = r["content"] if len(r["content"]) < 700 else r["content"][:700] + "..."
        out.append(f"[{i}] {kind}{src} id={r['id']} score={r['score']:.3f}\n{text}")
    return "\n\n".join(out) or "No matching memories."


# ---------------------------------------------------------------- tools

CONTAINER_PROP = {"type": "string",
                  "description": f'Memory space to use. Default "{CONTAINER}".'}

TOOLS = [
    {"name": "search_memories",
     "description": "Search long-term memory. Returns ranked facts and "
                    "source passages. Fast: ~0.1 s (rerank off, the default).",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to look for, in natural language."},
         "limit": {"type": "integer", "description": "Max results (default 8).", "default": 8},
         "rerank": {"type": "boolean", "default": False,
                    "description": "Cross-encoder rerank: more precise, adds 5-17 s. Only when the default results are close but not right."},
         "container": CONTAINER_PROP},
         "required": ["query"]}},
    {"name": "add_memory",
     "description": "Remember one durable, self-contained fact. If it "
                    "contradicts an existing memory, the old one is superseded "
                    "(kept as history); if it restates one, nothing is stored.",
     "inputSchema": {"type": "object", "properties": {
         "content": {"type": "string", "description": "The fact, as one standalone sentence."},
         "container": CONTAINER_PROP},
         "required": ["content"]}},
    {"name": "ask_memory",
     "description": "Answer a question from memory with citations, written "
                    "by a local model. Slow (40-120 s); prefer search_memories.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string"},
         "container": CONTAINER_PROP},
         "required": ["question"]}},
    {"name": "list_memories",
     "description": "List stored memories, newest first, optionally filtered "
                    "by a substring.",
     "inputSchema": {"type": "object", "properties": {
         "filter": {"type": "string", "description": "Substring to match."},
         "status": {"type": "string", "enum": ["live", "superseded", "forgotten", "all"],
                    "default": "live"},
         "limit": {"type": "integer", "default": 20},
         "container": CONTAINER_PROP}}},
    {"name": "memory_history",
     "description": "Every version of a memory, newest first -- what was "
                    "believed before, and when it changed.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "A memory id (mem_...)."}},
         "required": ["id"]}},
    {"name": "forget_memory",
     "description": "Forget a memory by id. Soft delete: reversible with "
                    "restore_memory.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "A memory id (mem_...)."}},
         "required": ["id"]}},
    {"name": "restore_memory",
     "description": "Undo forget_memory.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "add_document",
     "description": "Ingest a document or web page: it is chunked for "
                    "retrieval and facts are extracted from it. Runs in the "
                    "background (about a minute per 3,000 characters); this "
                    "returns a job id straight away unless wait is true. "
                    "Re-adding the same title replaces it.",
     "inputSchema": {"type": "object", "properties": {
         "content": {"type": "string", "description": "Text to ingest. Give this or url."},
         "url": {"type": "string", "description": "A public web page to fetch and ingest."},
         "wait": {"type": "boolean", "default": False,
                  "description": "Block until processing finishes."},
         "title": {"type": "string", "description": "Also used as its id; same title replaces."},
         "extract": {"type": "boolean", "default": True,
                     "description": "Extract facts (slow). False = retrieval only, fast."},
         "container": CONTAINER_PROP}}},
    {"name": "get_profile",
     "description": "The profile of a memory space: static facts and "
                    "preferences that should always be known, plus dynamic "
                    "recent context. One call, no query needed.",
     "inputSchema": {"type": "object", "properties": {
         "container": CONTAINER_PROP}}},
    {"name": "list_containers",
     "description": "List memory spaces and their sizes.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def run_tool(name, a):
    if name == "search_memories":
        r = call("POST", "/v4/search", {"q": a["query"], "containerTag": _c(a),
                                        "limit": int(a.get("limit", 8)),
                                        "rerank": bool(a.get("rerank", False))})
        return _hits(r["results"])
    if name == "add_memory":
        r = call("POST", "/v3/memories", {"content": a["content"], "containerTag": _c(a)})
        return ("Already known; nothing new stored." if r.get("duplicate")
                else f"Remembered in container {_c(a)}.")
    if name == "ask_memory":
        r = call("POST", "/v1/ask", {"q": a["question"], "containerTag": _c(a)})
        return f"{r['answer']}\n\nSources:\n{_hits(r['sources'])}"
    if name == "list_memories":
        from urllib.parse import urlencode
        q = urlencode({k: v for k, v in {
            "containerTag": _c(a), "status": a.get("status", "live"),
            "q": a.get("filter", ""), "limit": int(a.get("limit", 20))}.items() if v != ""})
        r = call("GET", "/v3/memories?" + q)
        lines = [f"{m['id']}  {'(superseded) ' if not m['isLatest'] and not m['forgotten'] else ''}"
                 f"{'(forgotten) ' if m['forgotten'] else ''}{m['content']}"
                 for m in r["memories"]]
        return f"{r['total']} match; showing {len(lines)}.\n" + "\n".join(lines)
    if name == "memory_history":
        r = call("GET", f"/v3/memories/{a['id']}/history")
        return "\n".join(f"v{v['version']} {'(current) ' if v['isLatest'] else ''}{v['content']}"
                         for v in r["versions"]) or "Not found."
    if name == "forget_memory":
        call("DELETE", f"/v3/memories/{a['id']}")
        return f"Forgot {a['id']}. Reversible with restore_memory."
    if name == "restore_memory":
        call("POST", f"/v3/memories/{a['id']}/restore", {})
        return f"Restored {a['id']}."
    if name == "add_document":
        if not a.get("content") and not a.get("url"):
            raise RuntimeError("give content or url")
        title = a.get("title")
        wait = bool(a.get("wait", False))
        r = call("POST", "/v3/documents", {
            "content": a.get("content"), "url": a.get("url"), "title": title,
            "customId": title, "containerTag": _c(a),
            "extract": bool(a.get("extract", True)), "wait": wait},
            timeout=3600 if wait else 60)
        if wait:
            if r.get("status") != "done":
                raise RuntimeError(r.get("error") or "ingest failed")
            return (f"Ingested '{r.get('title')}' into {_c(a)}: {r['chunks']} chunks, "
                    f"{r['memories']} facts extracted, {r.get('elapsed', '?')} s.")
        return (f"Queued '{r.get('title')}' as {r['id']} in {_c(a)}. It is processed "
                f"in the background; its facts become searchable when done.")
    if name == "get_profile":
        r = call("GET", "/v4/profile?containerTag=" + _c(a))
        nl = chr(10)
        def fmt(ms):
            return nl.join(f"- {m['content']}" for m in ms) or "- (none yet)"
        note = ""
        if r.get("unclassified"):
            note = (nl + nl + f"({r['unclassified']} memories not typed yet; the "
                    "profile fills in as the background worker classifies them.)")
        return (f"Profile for {_c(a)}" + nl + nl
                + "Static (stable facts and preferences):" + nl + fmt(r["static"])
                + nl + nl + "Dynamic (recent context):" + nl + fmt(r["dynamic"])
                + note)
    if name == "list_containers":
        r = call("GET", "/v1/containers")
        return "\n".join(f"{c['name']}: {c['memories']} memories, {c['documents']} documents"
                         for c in r["containers"]) or "No containers yet."
    raise RuntimeError(f"unknown tool {name}")


# ------------------------------------------------------------ JSON-RPC

def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, id_ = msg.get("method"), msg.get("id")
    if id_ is None:                     # notification: nothing to answer
        return
    p = msg.get("params") or {}
    if method == "initialize":
        return reply(id_, {
            "protocolVersion": p.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "smem", "version": VERSION},
            "instructions": INSTRUCTIONS})
    if method == "ping":
        return reply(id_, {})
    if method == "tools/list":
        return reply(id_, {"tools": TOOLS})
    if method in ("resources/list", "prompts/list"):
        return reply(id_, {method.split("/")[0]: []})
    if method == "tools/call":
        name, args = p.get("name"), p.get("arguments") or {}
        try:
            text = run_tool(name, args)
            return reply(id_, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            log(f"{name} failed: {e}")
            return reply(id_, {"content": [{"type": "text", "text": f"Error: {e}"}],
                               "isError": True})
    reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    # Newline-delimited JSON on the wire; Windows must not turn \n into \r\n.
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
    log(f"serving {URL} (container {CONTAINER})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        try:
            handle(msg)
        except Exception as e:
            log("handler error:", e)
            if msg.get("id") is not None:
                reply(msg["id"], error={"code": -32603, "message": str(e)})


if __name__ == "__main__":
    main()
