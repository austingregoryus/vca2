<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Connect to smem (instructions for an AI agent)

You are being asked to connect yourself to **smem**, a long-term memory
service on the local network. Follow these steps in order, then report what
you did and the result of the test in step 5.

- smem API: `http://<smem-host>:8900` (no authentication; LAN only).
  `<smem-host>` is the host you fetched this file from; substitute it
  everywhere below.
- Web console for the human: `http://<smem-host>:8900/`
- Your memory space ("container"): **`hermes`**

These steps assume Hermes Agent (config in `~/.hermes/config.yaml`, identity
in `~/.hermes/SOUL.md`). If you are a different agent, apply the same idea to
your own MCP config and standing-instructions file.

## 1. Check you can reach smem

```sh
curl -s http://<smem-host>:8900/health
```

Expect JSON containing `"ok": true`. If this fails, stop and tell the human:
the network path to <smem-host>:8900 is the problem, not your config.

## 2. Download the MCP bridge

`mcp_smem.py` is a stdio MCP server (Python 3 standard library only, no pip
installs) that forwards tool calls to the smem HTTP API.

```sh
mkdir -p ~/.hermes
curl -fsS -o ~/.hermes/mcp_smem.py http://<smem-host>:8900/connect/mcp_smem.py
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('ok')" ~/.hermes/mcp_smem.py
```

## 3. Register it

Add this under `mcp_servers:` in `~/.hermes/config.yaml`. Keep any servers
already there; use the **absolute** path (expand `~` yourself, e.g.
`~/.hermes/mcp_smem.py`).

```yaml
mcp_servers:
  smem:
    command: python3
    args: ["/home/<user>/.hermes/mcp_smem.py"]
    env:
      SMEM_URL: "http://<smem-host>:8900"
      SMEM_CONTAINER: "hermes"
    timeout: 180          # ask_memory can take 40-120 s
    connect_timeout: 30
```

Then run `/reload-mcp` (or ask the human to restart Hermes). The tools appear
as `mcp_smem_<tool>`: `get_profile`, `search_memories`, `add_memory`,
`ask_memory`, `list_memories`, `memory_history`, `forget_memory`,
`restore_memory`, `add_document`, `list_containers`.

## 4. Add standing instructions

Append the block below to `~/.hermes/SOUL.md` (create the file if it does not
exist; do not remove what is already there). It is also available on its own
at `http://<smem-host>:8900/connect/soul-snippet.md`.

```markdown
## Long-term memory (smem)

You have a long-term memory service, smem, through the mcp_smem_* tools. It
runs on a separate server and persists across sessions and restarts. Your
space is "hermes"; others exist ("claude", "vca2-docs") and every tool
accepts a `container` argument to read them.

- At the start of a task, call get_profile once (~0.01 s). It returns what
  is stable ("always known": facts, preferences, rules) and what is recent.
- Before answering anything that may depend on an earlier session, call
  search_memories. It is fast: 0.1-0.2 s (measured over 30 calls). Leave
  `rerank` off (the default); rerank=true re-scores results with a
  cross-encoder and costs 5-17 s, so use it only when the first results are
  close but not quite right. Only use ask_memory when you need a written,
  cited answer; it runs a model and takes 40-120 s.
- When the user states a durable fact, preference or decision, call
  add_memory with ONE self-contained sentence ("The user wants X because Y",
  not "he said yes"). A new fact that contradicts an old one replaces it
  automatically (the old one is kept as history); a repeat is ignored.
- For whole documents, notes or web pages, use add_document (text or URL).
  It is processed in the background, about a minute per 3,000 characters.
- If something is wrong or outdated, use forget_memory (reversible with
  restore_memory) rather than adding a contradicting note.
- Your built-in memory is for short working notes about the current setup.
  Anything worth remembering across machines and agents goes in smem.
```

## 5. Test

1. Call `mcp_smem_get_profile`. The first time, the `hermes` space is empty;
   that is expected.
2. Call `mcp_smem_add_memory` with:
   `Hermes Agent runs on <this machine's hostname> and is connected to smem as the "hermes" space.`
3. Call `mcp_smem_search_memories` with the query `where does Hermes run`
   and confirm the memory from step 2 comes back.

Report the three results to the human. They can also see the new memory in
the web console: `http://<smem-host>:8900/` → space switcher → `hermes`.

## How smem works (background)

- **Memories** are single facts. Adding one runs a check against the closest
  existing memory: a duplicate is dropped, a contradiction creates a new
  version and keeps the old one as history.
- **Documents** are split into chunks and mined for facts in the background.
- **Search** ranks both memories and document passages; newer facts win ties.
- A background worker types every memory (fact / preference / episode) and
  links related ones; the profile is built from that.
- The models behind it run on an Intel VCA 2 card in the Proxmox host
  (`<smem-host>`), not on your machine, so memory calls do not use your GPU.
