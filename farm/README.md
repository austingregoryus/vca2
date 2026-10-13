<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Render farm (VCA card 1 + card 0 nodes 0-1)

Remotion renders for ContextianStudio and DocuGen on up to five VCA nodes (four
while node 3 is dead), shared with smem:

| Nodes | smem lane when idle | Notes |
|---|---|---|
| card 1, nodes 3-5 (`172.31.4-6.1`) | background (ingest, extraction, dreaming) | capped at 35 W; node 3 is dead |
| card 0, nodes 0-1 (`172.31.1-2.1`) | interactive (search, ask) | added 2026-10-03; uncapped, 2×120 mm |
| card 0, node 2 (`172.31.3.1`) | interactive | **never lent**: smem keeps it for searches |

A job borrows its nodes through smem's pause file and hands them back when
done; meanwhile both smem lanes run on node 2. `FARM_NODES` in
`/etc/renderfarm.env` overrides the node list.

| | |
|---|---|
| Service | `renderfarm.service` on the Proxmox host, `:8910`, optionally behind a LAN reverse proxy (`https://<farm-host>`) |
| Auth | `Authorization: Bearer <FARM_TOKEN>` from `/etc/renderfarm.env`. `/health` is open. |
| State | `/tank-red/vmdata/vca/renderfarm` (asset mirror, code bundles, job outputs kept 24 h) |
| On nodes | `/opt/farm/{code/<hash>,public,work/<job>}`; prepared by `node_setup.sh` |
| Clients | ContextianStudio and DocuGen, each `studio/render_farm.py` (farm first, local fallback; docs in each repo's `docs/RENDER_FARM.md`) |
| Namespaces | assets per app: ContextianStudio `default`, DocuGen `docugen` (both use names like `brand/...`, `shots/...` for different files) |

## Measured (2026-09-26, a real episode from that day, via the real client)

| Render | PC | Farm |
|---|---|---|
| Short video, 73 s, 2198 frames | ~1-2 min | **70 s** end to end (57.6 s rendering on 3 parts) |
| Long video, 8.6 min, 15474 frames | ~15 min | **7 min 16 s** end to end (6 min 20 s rendering on 6 parts) |

**Card 0 nodes 0-1 added (2026-10-03),** same real episodes replayed through the
client's own job builder, node 3 still dead:

| Render | 2 nodes (card 1, capped) | 4 nodes (+ card 0 nodes 0-1) |
|---|---|---|
| Short video, 2119 frames | 116 s (render 99 s) | **83 s** (render 64 s) |
| Long video, 15817 frames | 714 s (2026-10-01 production run) | **424 s** (render 361 s on 8 parts) |

Per part, card 0 renders 13.5-14.6 fps against card 1's 10.9-12.5 fps (35 W cap),
so card 0 finishes first and idles ~80 s at the end of a long render. Card 0
peaked at 78 C with no throttle events. Output: frame counts exact, 2-node vs
4-node short at 41 dB average PSNR (encoder noise).

Output checked against the PC's own mp4s: same frame count and size, frames
35-45 dB PSNR (encoder noise), audio correlation 0.9999.

**Audio sync is better than the PC's.** Located against the source narration
wav, the farm's soundtrack sits at a constant +42.6 ms (AAC priming, identical
to the PC's at the start) all the way through. The PC's chunked long-form
render drifts ~55 ms later at each of its four `concat -c copy` seams, reaching
+261 ms by the end. The farm renders parts as h264-mkv with PCM audio, which
joins sample-exactly, and encodes AAC once; see the docstring in
`renderfarm.py`.

## API

```
GET  /health                              open
GET  /v1/code/<sha256>                    {ready}
PUT  /v1/code/<sha256>   X-Render-Dir     tar.gz of the render package -> installed on all nodes
POST /v1/assets/missing  {ns?, files:{rel:sha}} -> {missing:[rel]}
PUT  /v1/assets/<rel>?ns=<ns>  X-Sha256   one file; stored once, mirrored to nodes per job
POST /v1/jobs            {code, composition, props, ns?, totalFrames?, bounds?, files, entry?, concurrency?} -> {id}
GET  /v1/jobs[/<id>[/output]]
```

A job whose render logs show a 404 fails with `missing: [paths]`; the client
uploads those and retries once, then falls back to rendering locally.

## Install / update

```sh
bash farm/install_host.sh      # on the host, from a checkout; keeps the token
```

## Gotchas

- Parts are frame ranges of the same composition, so they can be cut anywhere;
  client-supplied beat-aligned bounds are honoured and split further so every
  node gets work (largest part first; nodes pull parts from a queue, so a fast
  node takes more of them).
- One job at a time, across both apps: a render uses every core of every farm node.
- **A node can drop out.** Node 3 (card 1 cpu 0, 172.31.4.1) went `link_down, dma_hang` mid-render on
  2026-09-28 (plx87xx_dma `dma hang detect 0x10009`). Each job probes the nodes first
  and runs on those that answer. If a node dies mid-render, its parts are requeued.
  That includes parts it already finished, because their files live on it. The other
  nodes then render them. `/health` lists unreachable nodes under `down` (probed every
  60 s). Code is installed on the reachable nodes and kept on the host, so a node that
  comes back gets it in the background before its next job.
- `concurrency` per job (1-8, default 4): DocuGen sends 1 for `PersonalDocumentary`, as it
  renders locally. A failed part retries once at half.
- A 404 fails the job with `missing: [paths]` (trailing punctuation from the log line is
  stripped). If the file exists on the client it re-uploads and retries once; otherwise the
  client renders locally, where the same file is missing too (e.g. a DocuGen `videoBed`
  whose clip was deleted: `BedLayer` has no still fallback).
- **Card 1 is power-capped to 35 W** (`scripts/card1_power_cap.sh`) because its 2×80 mm
  fans let renders hold two nodes at the thermal limit, and one node died after that.
  Renders there are somewhat slower until the card gets the 120 mm shroud. See [docs/THERMAL.md](../docs/THERMAL.md).
- Peak render memory is ~1.3 GB per node beside smem's ~9.5 GB of models.
- A 404 is the only reliable "missing asset" signal: Remotion keeps rendering
  with a blank image.
- **No WebGL in headless Chrome on the nodes.** three.js scenes (DocuGen's spatial
  beats) fail with `Error creating WebGL context` on every node (`--gl=angle` too).
  A part that fails that way is retried with `--gl=swangle` (SwiftShader, software):
  correct output, but ~1 fps against ~11 fps for 2D, so a 3D-heavy part sets the
  job's length. Measured 2026-10-03: a DocuGen short video, 1564 frames,
  412 s, with the two 3D parts at 1.0 fps. Before this fix such jobs failed and the
  client rendered them locally.
- **Code packages are kept on the host** (`code/<hash>/code.tgz`) so new or recovered
  nodes can be caught up. Versions installed before that (no `code.tgz`) report
  `ready: false`, so the client's next upload is kept and installed on the nodes
  that lack it (`restore_code`).
