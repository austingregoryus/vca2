<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Remotion render bench on a VCA node

## ContextianStudioClaude (the live pipeline) -- measured 2026-09-26

A real episode from that day's pipeline run,
replayed from its own `props.json` / `props_short.json` and assets on card 1
(nodes 3-5, Xeon E3-1585L v5 4C/8T, 16 GB each), Remotion 4.0.458 from the
repo's lockfile, `--concurrency=4` like `studio/cli.py`. Frames checked
against the PC's finished mp4s: identical.

| Render | PC (file mtimes) | 1 node | 3 nodes (split + gather + concat) |
|---|---|---|---|
| Short video, 73 s, 2198 frames, 9:16 | ~1-2 min | 146 s (15.1 fps) | **60 s (36.6 fps)** |
| Long video, 516 s, 15474 frames, 16:9 | ~15 min | 1081 s (14.3 fps), 5 chunks x ~216 s | **363 s (42.6 fps)** |

One node is ~0.8x the PC; one card of three is ~2.5x the PC, and the PC is
left free. Split points were plain thirds (not beat boundaries) and still
concat cleanly with `-c copy`: seam frames 38-50 dB PSNR vs a single-node
render (re-encode noise), one frame bit-identical, audio 64 ms shorter over
two joins. Gathering the parts over the fabric took ~2 s.

Scripts: `contextian_setup.sh` (node), `contextian_run.sh` (1-node timing),
`clone_to_nodes.sh` + `contextian_farm.sh` (host: copy bench to nodes 4-5,
split render). Bundle = repo root `package.json`/`pnpm-lock.yaml`/
`pnpm-workspace.yaml`, `packages/render` minus `node_modules`/`public`/`out`,
`public/brand`, the episode's `public/auto/<name>{,_short}`, the `beds/` and
`shots/` files its props reference, and both props files.

All three scripts take the Remotion composition IDs of the two videos from
`SHORT_COMP` and `LONG_COMP`, and refuse to run without them. The node-side
scripts need them passed through SSH, e.g.
`ssh root@<node> SHORT_COMP=... LONG_COMP=... bash contextian_run.sh`.

## VideoPipeNew (older repo) -- first pass

Can a VCA 2 node render the VideoPipe shorts at a useful speed? Measured
2026-09-26 on node 3 (card 1, Xeon E3-1585L v5, 4C/8T, 16 GB), replaying a
real Daily Dose AI run (`agg_daily-dose-ai_032bb08c`: `AggregatorShort`,
1080x1920, 30 fps, 74 s = 2220 frames, voiceover + captions + card
backgrounds), Remotion 4.0.463 and its bundled chrome-headless-shell.

| What | Result |
|---|---|
| Full render, `--concurrency=4` | 86 s wall, **25.8 fps**, 0.86x realtime |
| Full render, `--concurrency=8` | 84 s wall, **26.4 fps** (CPU-bound; load 14) |
| Same, before the backgrounds loaded | 53-64 s, 35-42 fps (text-only frames -- not representative) |
| Stitch/mux | ~1 s |
| Encode only, H.264 on Quick Sync | 188 fps (6.3x realtime) |
| Encode only, H.264 x264 veryfast / medium | 187 / 97 fps |

Frame drawing (Chrome) is the whole cost; encoding is noise. Concurrency past
4 buys nothing on these 4-core parts.

**Fidelity caveat.** The PC's July render of this run also drew an article
card, logo and footer that neither the current code nor the last commit
before that render produces, so the node's frames are slightly lighter than
the original. And the current `AggregatorShort` sets the card background as
`url(${fallbackImage})` rather than via `staticFile()`, which 404s under
Remotion 4's static server -- `patch_aggregator_bg.sh` fixes that in the bench
copy only. Worth checking in VideoPipe itself.

## Files

- `setup.sh` -- runs on a node: Node 22, Chrome libs, pnpm (lockfile), headless shell. Expects `/root/rb.tar` (repo root `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `packages/remotion` without `node_modules`/`_public_archive`, the run's `public/<run>` + `public/brand`, and `props.json` = the run's `output.props.json`).
- `patch_aggregator_bg.sh` -- the `staticFile()` fix above, bench copy only.
- `run.sh` -- full render at `CONCS` (default `4 8`), results in `/opt/rbench/results.txt`.
- `encode.sh` -- encoder-only throughput (Quick Sync vs x264).
