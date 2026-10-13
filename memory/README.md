<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# smem — a local memory layer on the VCA 2

> **smem now has its own repository:
> [austingregoryus/smem-agent-memory](https://github.com/austingregoryus/smem-agent-memory).**
> Development continues there, and it runs on any machine with llama.cpp, not
> only on these cards; this deployment is its documented reference. This
> directory is kept as the record of how smem was built and measured on the
> VCA 2, as of 2026-10-13.

A working reimplementation of [Supermemory](https://supermemory.ai)'s core
ideas, served entirely by the Xeon E3-1585L v5 nodes on two of these
discontinued 2016 PCIe cards (three nodes per card; five alive since node 3
died on 2026-09-28). No API key, no network egress, no cloud model.

The card is doing something it is actually good at here. Everything in this
pipeline is either *embarrassingly parallel across nodes* (embedding,
reranking, fact extraction) or *tiny* (a numpy dot product on the host). That
is the opposite of the single-stream LLM chat workload where this hardware
loses badly to the host CPU. See [BENCHMARK.md](../docs/BENCHMARK.md) for why
that distinction decides everything.

---

## What Supermemory actually sells

Worth being precise about, because it determines what is worth copying.

Supermemory is not "a vector database with a nice API". The published pitch is
**95% Recall@15 on LongMemEval using about 720 tokens of context** — a 99.4%
reduction against stuffing the conversation in. The product is *context
compression*: give the model twelve one-line facts instead of forty pages and
it answers as well, far faster, for a fraction of the token cost.

Everything else follows from that:

| Supermemory concept | What it is for | Here |
|---|---|---|
| **Container tags** | Isolation boundary. Memories never leak between users/projects. | `container` column on every row; every query filters on it |
| **Memory vs document** | Documents are source material; memories are atomic extracted facts. Facts are what make answers short. | `documents`/`chunks` vs `memories` |
| **Temporal truth** | An updated fact does not overwrite the old one. The old version is retained with `isLatest=false`. | `is_latest`, `version`, `supersedes` |
| **Forgetting** | Soft delete, including mass-forget by natural-language query. | `forgotten_at`; `forget_matching()` |
| **Decay / recency bias** | Older facts score lower, so stale beliefs fade without being deleted. | `half_life_days` on search |
| **Console** | Web UI to browse, search and manage memories, with a graph view and usage stats. | `http://<host>:8900/` — see below |
| **Agent integration** | MCP server so agents read and write memory directly. | `mcp_smem.py` — see below |
| **Memory graph** | Visual map of how memories relate. | **partial** — links facts to their source, to what they superseded, and to each other by embedding similarity. No entity/relation extraction. |
| **Profile buckets** | Rolled-up per-user summaries. | **partial** — one static/dynamic profile per space (`GET /v4/profile`, v0.3); no buckets — see Limits |

---

## Architecture

```
                    HOST (Proxmox)
    ┌───────────────────────────────────────────────┐
    │  smem.server  :8900      HTTP API             │
    │  smem.engine             pipeline             │
    │  smem.store              SQLite + float32     │
    │                          vectors, numpy cosine│
    └───────────────┬───────────────────────────────┘
                    │  PCIe (172.31.x.1, virtio-net)
    ┌───────────────┴───────────────────────────────┐
    │  card 0           node0     node1     node2   │  interactive lane
    │  card 1           node3     node4     node5   │  background lane
    │                   (dead)                      │
    │  on every live node:                          │
    │  :8080 embedder   EmbeddingGemma 2      Q8_0  │
    │  :8081 reranker   Qwen3-Reranker-0.6B   Q8    │
    │  :8082 llm        gemma-4-E4B-it        Q4_K_M│
    └───────────────────────────────────────────────┘
        6.3 GB of 14.9 GB RAM used per node (measured
        with the earlier Qwen3-Embedding-0.6B embedder)
```

A **whole model per node**, requests distributed round-robin — not one model
split across nodes. That choice is the entire reason this is fast: layer-split
RPC across three nodes measured **2.4× slower** than a single node (every
token crosses the PCIe fabric serially), while three independent LLM workers
measured **2.96× faster** ([BENCHMARK.md](../docs/BENCHMARK.md)). The
embedder scales the same way: 2.92× on three nodes (Qwen3-Embedding-0.6B,
measured before the switch to EmbeddingGemma 2).

### Pipeline

**Ingest**

```
document
  → heading-aware chunks (~1400 chars, cut on paragraph boundaries)
  → embed, batched 16, fanned across the nodes        [parallel]
  → store chunks + vectors
  → LLM extracts atomic facts from ~3000-char groups  [parallel]
  → each fact reconciled against what is already known [serial, by necessity]
  → stored as memories
```

**Query**

```
question
  → embed with the `task: search result | query:` prefix
  → cosine over memories AND chunks → top 40           [numpy, sub-ms]
  → cross-encoder rerank the 40                        [parallel]
  → recency decay (memories only)
  → top k → compact numbered context
  → LLM answers with [n] citations
```

Two retrieval stages on purpose. The bi-encoder is cheap and recall-oriented
but scores a passage by one vector's distance; the cross-encoder reads query
and passage *together* and is much better at precision, but costs a forward
pass per pair. Cast wide with cosine, re-score only the survivors.

### Why decay applies to memories but not chunks

A paragraph of a design document is not less true for being six months old. A
remembered fact about a system that is actively being changed very often is.
`half_life_days` therefore weights memories only, and is off by default.

---

## Running it

```bash
# once: start the model servers (three per node; 15 on the five live nodes)
./services.sh start          # ~90s to load; status / stop also available

# CLI
python3 -m smem health
python3 -m smem -c mydocs ingest /path/to/docs
python3 -m smem -c mydocs search "why does node 1 write slowly"
python3 -m smem -c mydocs ask    "what is the DMA read/write asymmetry"
python3 -m smem -c mydocs remember "PL1 is now 56 W on every node"
python3 -m smem -c mydocs list --history
python3 -m smem -c mydocs forget "anything about the old cooler"   # dry run
python3 -m smem -c mydocs stats
python3 -m smem -c mydocs compress "what cooling change was made"

# HTTP API
python3 -m smem.server --db /root/supermem/smem.db --port 8900
curl -s localhost:8900/v4/search -d '{"q":"PEX 8749","containerTag":"mydocs"}'
```

The API mirrors Supermemory's route shape (`POST /v3/documents`,
`POST /v4/search`, `DELETE /v3/memories/{id}`) so client code ports with a
base-URL change.

### Two lanes (second card, 2026-09-26)

Searches and asks run on card 0 (nodes 0-2). The ingest/dreaming worker runs
on card 1 (nodes 3-5) and falls back to card 0 when card 1 is busy, down, or
lent to the render farm through `/run/smem/paused.json` (`{nodes, until}`,
expiry mandatory). The farm also borrows card 0's nodes 0-1 (2026-10-03), so
during a render node 2 (172.31.3.1) carries both lanes; searches measured
0.05-0.07 s there with the other live nodes rendering (at most four: node 3
is dead and node 2 is never lent). `GET /v1/lanes` shows both lanes, paused nodes and live
in-flight counts. See `smem/pool.py` and [../farm/README.md](../farm/README.md).

### Reboots

Everything comes back by itself. [`deploy/install_host.sh`](deploy/install_host.sh) sets up:

| unit | where | does |
|---|---|---|
| `vca2-card.service` ([`systemd/`](../systemd/vca2-card.service), runs [`scripts/vca2-boot.sh`](../scripts/vca2-boot.sh)) | host | at boot: resets each card, attaches the right node images, boots the nodes (card 0's three always; card 1's three when that card and `node3-5` images exist), loads jumbo MTU, checks every link. At shutdown: powers the nodes off cleanly and detaches their images before the host goes down. |
| `sm-emb`, `sm-rrk`, `sm-llm` | each node | the three model servers, as enabled units on the node's own disk, with the memory caps and `Restart=always` |
| `smem-api.service` | host | the API and console. Deliberately not ordered after the card, so the console is up while the nodes boot. |

Measured on a real reboot (2026-09-22): the host was back in ~1.5 min, the
nodes were `net_device_ready` ~80 s after that, and the model servers started
8 s after each node booted. The previous boot's journal showed all three nodes
reaching `power_off` ~50 s after the clean poweroff was sent. The first search
and ask after a reboot are slow (cold models); they settle within a minute.

Other VMs on the same host keep their own start-on-boot settings; this does
not change them.

Currently serving this repo's `docs/` at `http://<host>:8900`
(container `vca2-docs`).

---

## Console

Open **`http://<host>:8900/`**. It is served by the API itself — no
build step, one HTML file (`smem/ui/index.html`, d3 from a pinned CDN URL
with SRI). Dark by default, light when the OS asks or the toggle says.

The left rail is the frame for everything: the **space switcher** (a space is
a container tag; create one by typing a name) at the top, the views in the
middle, and the **substrate** at the bottom — the nodes' package
temperature and memory, and what the background worker is doing — so the card
that does the work is always in view.

| view | what it does |
|---|---|
| Home | the space's **profile** (static: preferences and facts; dynamic: recent episodes and updates), counts, type mix, latest memories, memories per hour, facts per document |
| Memories | filter by text, status (live / superseded / forgotten), type and document; detail panel with relations (extends / extended by), version timeline, **Update** (stores a new version), Forget and Restore |
| Documents | processing queue with live status, add by text / public URL / file, read a document and its extracted facts, delete (hard, with confirm) |
| Memory graph | documents as cards, memories as dots coloured by type, older versions and forgotten as rings; edges for source, updates (version chain), extends (dashed) and similar documents (dotted); legend with counts, highlight search, click for neighbours |
| Chat | ask the space; answers cite their sources inline and the sources expand under the answer; thread kept per space for the browser session |
| Activity | dreaming progress and pause/resume; requests per minute, per-endpoint p50/p95, recent requests |
| Connect | MCP setup, HTTP examples, endpoint list, token entry when auth is on |

Keyboard: `g` then `h m d g c a` switches view, `/` focuses the page's search.

Reads run on their own read-only SQLite connection and take no lock, so the
console stays responsive while a 60-second `ask` or a document ingest runs.
Node telemetry is sampled every 15 s over ssh (`vcactl temp` was timing out
with `LBP_IRQ_TIMEOUT`, so temperatures come from each node's `coretemp`).

**Authentication is off by default.** Start the server with
`SMEM_TOKEN=<secret>` to require `Authorization: Bearer <secret>` on every
`/v*` route; the console asks for the token on its Connect page.

### Background worker: ingest queue and dreaming

`POST /v3/documents` returns `202` with a job id straight away (pass
`"wait": true` for the old blocking behaviour); `GET /v3/jobs` reports each
job's phase. Documents can be given as `content` or as a public `url` —
fetched server-side with an SSRF guard (http/https only, every resolved
address must be public, re-checked on every redirect, 5 MB / 20 s).
`SMEM_ALLOW_PRIVATE_URLS=1` allows LAN pages.

When there are no jobs and the card has been quiet for 20 s (console polling
does not count), the worker **dreams**, Supermemory's word for building the
graph in the background:

1. **Type** each memory as FACT, PREFERENCE or EPISODE (grammar-constrained,
   9 per call). 12/12 on a labelled set; on the live corpus about 4 in 5 of
   the PREFERENCE labels are defensible (rules and decisions count), the rest
   are facts or events.
2. **Extends**: for each memory, ask whether it adds detail to an older
   neighbour on the same subject (cosine ≥ 0.80, up to 3 candidates). The
   judge is deliberately conservative — 7/11 on a labelled set, 0/5 false
   links between different subjects, about 2 in 5 true extends found.

Both judges answer by probability, not by generating a word: the options are
offered as letters, one token is generated, and each letter's probability is
read from the logprobs (`DECIDE.md` has the bake-off that chose this). Every
decision is logged; setting a type or pressing ✓ / ✗ on a link in the console
saves a label, and `smem/calibrate.py` re-fits how far to trust the model and
where each threshold sits, then re-applies that to every past decision
(`GET /v1/calibration`, `POST /v1/calibration/fit`, Activity → Calibration).

The profile (`GET /v4/profile`) is built from the types: static = preferences
then facts, ranked by how connected they are; dynamic = episodes and updated
memories from the last 14 days. `POST /v1/dreaming {"enabled": false}` pauses
it; `SMEM_DREAMING=0` starts with it off. Jobs live in memory — a restart
forgets the queue (documents already ingested are safe).

Memories can also be edited: `PATCH /v3/memories/{id}` stores a new version
and keeps the old one (`409` if the id is not the current version).

## Agent integration (MCP)

`mcp_smem.py` is an MCP server over stdio. Standard library only; it forwards
each tool call to the HTTP API.

```
claude mcp add smem --scope user -e SMEM_URL=http://<host>:8900 -e SMEM_CONTAINER=claude -- python /path/to/memory/mcp_smem.py
```

Tools: `get_profile`, `search_memories`, `add_memory`, `ask_memory`,
`list_memories`, `memory_history`, `forget_memory`, `restore_memory`,
`add_document` (text or URL; queued, returns a job id), `list_containers`.
The server sends usage instructions at initialize: call `get_profile` once at
the start of a task, search before answering, store one self-contained fact
at a time. `SMEM_CONTAINER` isolates memory per project or per agent.

---

## Limits, honestly

- **Partial Supermemory parity.** There are spaces, a static/dynamic profile,
  updates and extends relations, background dreaming, URL ingest and a
  Supermemory-shaped console. There are no *derives* relations (inferred
  facts), no profile buckets (only the one static/dynamic profile), no entity extraction, no connectors (Drive,
  Notion, …), no per-key API keys (one optional shared token), and no
  multi-user accounts. The profile ranking is a heuristic.
- **Extends coverage is low by design.** The judge would rather miss a link
  than invent one; expect it to find roughly two in five.

- **Brute-force vector scan.** Every query scores every vector in the
  container. At tens of thousands of chunks that is still sub-millisecond and
  utterly dominated by the model calls; at millions it would need an index.
- **Ingest is slow and prefill-bound.** See the numbers below. Extraction is
  one LLM call per ~3000 characters and the E3-1585L prefills at roughly
  23 tok/s. This is a batch operation, not an interactive one.
- **Reconciliation is serial.** Each new fact must be compared against a store
  that the previous fact may just have changed. Parallelising it would let two
  contradictory facts both insert.
- **Only the single nearest existing memory is reconciled.** A candidate fact
  is compared against its top-1 neighbour, not its top-k. If it updates one
  memory and duplicates another, only the first is acted on. Checking the top
  three would triple the cost of the second-slowest stage for a case that has
  not yet been observed to matter.
- **Single writer.** One SQLite connection behind one lock.

---

## Gotchas found building this

Both cost real time and both failed *silently* — the calls returned 200 and
the run reported success.

### gemma-4-E4B is a reasoning model, and that returns an empty string

Left to itself it writes its chain of thought into `reasoning_content` and
only then begins `content`. Every prompt here is long enough that it spent the
entire token budget thinking, returned `finish_reason: length`, and handed
back `content: ""`. A full 22-file ingest completed "successfully" with **0
facts extracted**.

The switch that works is `chat_template_kwargs: {"enable_thinking": false}`.
`reasoning_budget` and `thinking_budget` are ignored by this build, and
`reasoning_format: "none"` only stops llama-server *splitting* the channels —
the model still thinks, it just leaves raw `<|channel>thought` markers in the
content.

This also reframes the earlier benchmark: the 21.8 tok/s aggregate measured
for gemma-4 was largely reasoning tokens.

### A one-word classifier needs a grammar

Asked in free text to answer `DUPLICATE` / `UPDATE` / `DISTINCT`, the model
returned an empty string for *precisely the DUPLICATE cases* — which fell
through to `DISTINCT` and stored the duplicate. Constraining with GBNF
(`root ::= "DUPLICATE" | "UPDATE" | "DISTINCT"`) makes the wrong answer
unrepresentable. Accuracy on a 12-pair labelled set went from unusable to
**12/12**.

### Heading paths read as filenames

Chunks are embedded with their heading path prepended, which is a large
retrieval win. But handing the extractor a chunk starting `THERMAL.md > ...`
made the model reply *"Please provide the content of THERMAL.md so I can
extract the durable facts."* Wrapping the body in `<document source="...">`
tags fixes it.

---

## Measured

See [RESULTS.md](RESULTS.md).
