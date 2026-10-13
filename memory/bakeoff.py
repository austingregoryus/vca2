# SPDX-License-Identifier: MIT
"""Compare extraction models on the real workload.

    python3 bakeoff.py [--probe-only] [--docs N] [--groups N]

Ingest is prefill-bound: FLOPs = tokens x ACTIVE params x 2, and the measured
budget is ~184 GFLOP/s per node. So a smaller active-parameter count is close
to a linear win on the slowest stage of the pipeline. The question this
answers is not "which model is fastest" -- that is arithmetic -- but whether
the fast ones still do the job.

Three things get measured, in increasing order of how much they matter:

  speed      prefill tok/s and decode tok/s, straight from the server's own
             timings rather than wall clock, so ssh and scheduling noise stay
             out of it.

  contract   does the model return usable JSON at all? gemma-4-E4B taught
             this the hard way: as a reasoning model it spends the whole
             token budget in reasoning_content and returns content="" with
             HTTP 200. A whole ingest reported success with zero facts. Every
             contender is probed for that trap before it is benchmarked.

  fidelity   do the numbers in the extracted facts actually appear in the
             source text? This is the one that decides it. A malformed reply
             is loud and a grammar removes it; a well-formed fact carrying a
             transposed digit is silent, and it gets retrieved and cited
             forever. See smem/verify.py.
"""

import argparse
import json

import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smem.chunk import chunk  # noqa: E402
from smem.engine import (EXTRACT_GRAMMAR, EXTRACT_SYSTEM,  # noqa: E402
                         EXTRACT_USER)
from smem.models import parse_json_loose  # noqa: E402
from smem.pool import PORT_LLM, Pool  # noqa: E402
from smem.verify import audit_all  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = "/root/supermem/corpus-docs"

# The grammar and prompt come from smem.engine so the bake-off measures
# exactly what production runs. A local copy drifted from it once already.

MODELS = [
    # (label, gguf basename, note)
    ("gemma-4-E4B  Q4", "gemma4.gguf", "baseline, ~4B active"),
    ("MiniCPM5-2B  Q4", "minicpm5.gguf", "1.98B non-emb dense, IFEval 86.7"),
    ("OLMoE-1B-7B  Q4", "olmoe.gguf", "6.9B total / 1.3B active MoE"),
    ("Llama-3.2-1B Q4", "llama32-1b.gguf", "1.24B dense"),
]


def swap(model, threads=8, ubatch=512, ctx=3072):
    r = subprocess.run(
        ["/bin/bash", os.path.join(HERE, "swap_llm.sh"),
         model, str(threads), str(ubatch), str(ctx)],
        capture_output=True, text=True, timeout=600)
    print(r.stdout.rstrip())
    return r.returncode == 0


def call(pool, body, think, timeout=600):
    """One extraction call. -> (content, reasoning, finish, timings, wall)"""
    p = dict(body)
    if not think:
        p["chat_template_kwargs"] = {"enable_thinking": False}
    t0 = time.time()
    r = pool.post(PORT_LLM, "/v1/chat/completions", p, attempts=3)
    wall = time.time() - t0
    ch = r["choices"][0]
    msg = ch["message"]
    return (msg.get("content") or "", msg.get("reasoning_content") or "",
            ch.get("finish_reason"), r.get("timings", {}), wall)


def load_groups(n_docs, n_groups):
    """Same chunk-grouping the real ingest uses, over the same corpus."""
    files = sorted(f for f in os.listdir(CORPUS) if f.endswith(".md"))[:n_docs]
    out = []
    for fn in files:
        text = open(os.path.join(CORPUS, fn), encoding="utf-8").read()
        parts = chunk(text, title=fn)
        buf = ""
        for p in parts:
            piece = ((p["heading"] + "\n") if p["heading"] else "") + p["text"]
            if buf and len(buf) + len(piece) > 3000:
                out.append((fn, buf.strip()))
                buf = ""
            buf += piece + "\n\n"
        if buf.strip():
            out.append((fn, buf.strip()))
    # Spread across documents rather than taking the first N groups, which
    # would all come from one file and measure that file's style.
    step = max(1, len(out) // n_groups)
    return out[::step][:n_groups]


def probe(pool, label):
    """Can this model do the job, and in which configuration?

    Tests the 2x2 of {thinking on/off} x {grammar on/off}, because the first
    version of this probe only tested WITHOUT the grammar -- and then used the
    result to gate a benchmark that runs WITH it. MiniCPM5 and OLMoE both
    emitted perfectly good content, failed to parse as a bare JSON array, and
    were dropped from the comparison by the very check the grammar exists to
    make unnecessary. A probe has to run the configuration it is gating.

    The grammar column is also the interesting measurement in its own right:
    it is how much constrained decoding rescues a small model.
    """
    src = ("FABRIC.md > Uplink" + chr(10) +
           "The PEX 8749 presents an x16 Gen3 uplink to "
           "the host and fans out 1:3. Node 2 sits behind a cascaded PEX 8717, "
           "so its non-posted reads pay one extra switch hop and measure "
           "3129 MiB/s against 5642 MiB/s for writes.")
    base = {"messages": [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user",
         "content": EXTRACT_USER.format(src="FABRIC.md", body=src)}],
        "max_tokens": 320, "temperature": 0.1}

    res = {}
    print(f"  {label}")
    print(f"    {'thinking':9s} {'grammar':8s} {'content':>8s} {'reason':>7s} "
          f"{'finish':>7s} {'facts':>6s} {'wall':>7s}")
    for think in (True, False):
        for gram in (False, True):
            b = dict(base)
            if gram:
                b["grammar"] = EXTRACT_GRAMMAR
            try:
                c, rc, fin, tm, wall = call(pool, b, think)
                n = len([x for x in parse_json_loose(c) if str(x).strip()])
                res[(think, gram)] = {"facts": n, "content": len(c),
                                      "reason": len(rc), "wall": wall}
                print(f"    {'on' if think else 'off':9s} "
                      f"{'on' if gram else 'off':8s} {len(c):7d}ch "
                      f"{len(rc):6d}ch {str(fin):>7s} {n:6d} {wall:6.1f}s")
            except Exception as e:
                res[(think, gram)] = {"facts": 0, "content": 0, "reason": 0,
                                      "wall": 0.0}
                print(f"    {'on' if think else 'off':9s} "
                      f"{'on' if gram else 'off':8s}  ERROR {str(e)[:50]}")

    # The trap is judged in the configuration production uses: grammar on.
    trap = (res.get((True, True), {}).get("content", 0) == 0
            and res.get((False, True), {}).get("content", 0) > 0)
    if trap:
        print("    -> REASONING TRAP: content is empty unless thinking is off")

    # How much did constrained decoding buy, in the better thinking mode?
    tm_key = False if trap else True
    no_g = res.get((tm_key, False), {}).get("facts", 0)
    with_g = res.get((tm_key, True), {}).get("facts", 0)
    if with_g > no_g:
        print(f"    -> GRAMMAR RESCUE: {no_g} facts unconstrained "
              f"-> {with_g} constrained")

    works = any(v.get("facts", 0) > 0 for v in res.values())
    if not works:
        print("    -> produced no parseable facts in ANY configuration")
    return {"trap": trap, "works": works,
            "grammar_gain": with_g - no_g}



def run_model(pool, label, groups, think, grammar, reps=1):
    pre, dec, walls, per_doc = [], [], [], []
    facts_all, empty, unparsed = [], 0, 0
    fid = {"total": 0, "clean": 0, "numeric_bad": 0, "echo_bad": 0,
           "form_bad": 0, "form_kinds": {}, "detail": []}
    # Everything in the prompt EXCEPT the document, so an "extracted fact"
    # that is really a copied instruction can be identified as one.
    prompt_text = EXTRACT_SYSTEM + chr(10) + EXTRACT_USER.format(src="x", body="")

    for _ in range(reps):
        for fn, body_text in groups:
            b = {"messages": [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user",
                 "content": EXTRACT_USER.format(src=fn, body=body_text)}],
                "max_tokens": 320, "temperature": 0.1}
            if grammar:
                b["grammar"] = EXTRACT_GRAMMAR
            try:
                c, rc, fin, tm, wall = call(pool, b, think)
            except Exception as e:
                print(f"    call failed: {str(e)[:70]}")
                continue
            walls.append(wall)
            if tm.get("prompt_per_second"):
                pre.append(tm["prompt_per_second"])
            if tm.get("predicted_per_second"):
                dec.append(tm["predicted_per_second"])
            if not c.strip():
                empty += 1
                per_doc.append(0)
                continue
            fx = [str(x).strip() for x in parse_json_loose(c)
                  if isinstance(x, (str, int, float)) and 15 < len(str(x)) < 400]
            if not fx:
                unparsed += 1
            per_doc.append(len(fx))
            facts_all += fx
            a = audit_all(fx, body_text, prompt_text)
            fid["total"] += a["total"]
            fid["clean"] += a["clean"]
            fid["numeric_bad"] += a["numeric_bad"]
            fid["echo_bad"] += a["echo_bad"]
            fid["form_bad"] += a["form_bad"]
            for k, v in a["form_kinds"].items():
                fid["form_kinds"][k] = fid["form_kinds"].get(k, 0) + v
            fid["detail"] += a["detail"][:2]

    med = statistics.median
    return {
        "label": label,
        "calls": len(walls),
        "prefill": med(pre) if pre else 0.0,
        "decode": med(dec) if dec else 0.0,
        "wall": med(walls) if walls else 0.0,
        "total_wall": sum(walls),
        "facts": len(facts_all),
        "facts_per_call": (statistics.mean(per_doc) if per_doc else 0),
        "empty": empty,
        "unparsed": unparsed,
        "fid_rate": (fid["clean"] / fid["total"]) if fid["total"] else 1.0,
        "fid_numeric": fid["numeric_bad"],
        "fid_echo": fid["echo_bad"],
        "fid_form": fid["form_bad"],
        "form_kinds": fid["form_kinds"],
        "all_facts": facts_all,
        "fid_total": fid["total"],
        "examples": fid["detail"][:4],
        "sample": facts_all[:6],
    }


def table(rows, baseline_wall=None):
    hdr = ("model", "prefill", "s/call", "vs base", "facts", "bad#",
           "echo", "form", "USABLE", "clean")
    print()
    print("  %-17s %8s %7s %8s %6s %5s %5s %5s %7s %7s" % hdr)
    print("  " + "-" * 88)
    for r in rows:
        spd = (baseline_wall / r["wall"]) if (baseline_wall and r["wall"]) else 1.0
        usable = r["fid_total"] - r["fid_numeric"] - r["fid_echo"] \
            - r["fid_form"]
        print("  %-17s %7.1ft/s %6.1fs %7.2fx %6d %5d %5d %5d %7d %6.1f%%"
              % (r["label"], r["prefill"], r["wall"], spd, r["facts"],
                 r["fid_numeric"], r["fid_echo"], r["fid_form"], usable,
                 r["fid_rate"] * 100))
        if r.get("form_kinds"):
            kinds = ", ".join("%s %d" % (k, v)
                              for k, v in sorted(r["form_kinds"].items()))
            print("%s(form: %s)" % (" " * 21, kinds))
    print()
    print("  bad#   = fact contains a number absent from the source")
    print("  echo   = fact is a sentence copied from the prompt, not the document")
    print("  form   = a heading, a question or a fragment, not a standalone claim")
    print("  USABLE = survives all three checks. The only column that matters.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--docs", type=int, default=8)
    ap.add_argument("--groups", type=int, default=9)
    ap.add_argument("--no-grammar", action="store_true")
    a = ap.parse_args()

    pool = Pool()
    groups = load_groups(a.docs, a.groups)
    chars = sum(len(g[1]) for g in groups)
    print(f"workload: {len(groups)} chunk groups from {a.docs} documents, "
          f"{chars:,} chars\n")

    print("=" * 100)
    print("PHASE 1 -- capability probe (does it return content at all?)")
    print("=" * 100)
    caps = {}
    for label, model, note in MODELS:
        print(f"\n[{model}] {note}")
        if not swap(model):
            print("  server did not come up; skipping")
            caps[model] = {"trap": False, "works": False}
            continue
        caps[model] = probe(pool, label)

    if a.probe_only:
        return 0

    print("\n" + "=" * 100)
    print("PHASE 2 -- extraction bake-off"
          f"{'' if a.no_grammar else ' (JSON constrained by GBNF grammar)'}")
    print("=" * 100)
    rows = []
    base_wall = None
    for label, model, note in MODELS:
        if not caps.get(model, {}).get("works"):
            print(f"\n[{label}] skipped -- failed the capability probe")
            continue
        print(f"\n[{label}] {note}")
        if not swap(model):
            continue
        # Send enable_thinking=false ONLY to models that actually showed
        # the trap. Llama-3.2-1B has no thinking mode, and in the probe it
        # did WORSE with the flag set (910 chars, finish=length, zero
        # valid JSON) than without it. Forcing an unused template kwarg on
        # every model would be handing one contender a config it never
        # asked for and calling that a fair comparison.
        trapped = caps.get(model, {}).get("trap", False)
        print("    enable_thinking=false: " +
              ("yes" if trapped else "no (no thinking mode)"))
        r = run_model(pool, label, groups,
                      think=not trapped, grammar=not a.no_grammar)
        rows.append(r)
        if base_wall is None:
            base_wall = r["wall"]
        print(f"    {r['calls']} calls, {r['facts']} facts, "
              f"{r['wall']:.1f}s median, fidelity "
              f"{r['fid_rate'] * 100:.1f}%")
        for s in r["sample"][:5]:
            print(f"      + {s[:96]}")
        for ex in r["examples"][:3]:
            tag = (("echo %.2f" % ex["echo"]) if ex.get("echo")
                   else ("unsupported %s" % ex["unsupported"]))
            print(f"      ! {tag}: {ex['fact'][:78]}")

    print("\n" + "=" * 100)
    print("RESULTS")
    print("=" * 100)
    # Write the raw numbers out BEFORE formatting them. A KeyError in the
    # table function threw away a completed 30-minute run once already.
    with open(os.path.join(HERE, "bakeoff-results.json"), "w") as fh:
        json.dump({"rows": rows, "caps": {k: v for k, v in caps.items()}},
                  fh, indent=1)
    # Write the raw numbers out BEFORE formatting them. A KeyError in
    # table() discarded a completed 30-minute run once already.
    with open(os.path.join(HERE, "bakeoff-results.json"), "w") as fh:
        json.dump({"rows": rows, "caps": caps}, fh, indent=1)
    table(rows, base_wall)

    if rows:
        print("\n  Projected full-corpus ingest (extraction is ~85% of 71.9 min):")
        for r in rows:
            spd = base_wall / r["wall"] if r["wall"] else 1
            mins = 61.1 / spd + 10.8
            usable = (r["fid_total"] - r["fid_numeric"] - r["fid_echo"]
                      - r["fid_form"])
            fps = usable / r["total_wall"] if r["total_wall"] else 0
            print("    %-17s %5.1f min   %5.3f usable facts/s   (%s)" % (
                r["label"], mins, fps,
                "baseline" if abs(spd - 1) < 0.01 else "%.2fx speed" % spd))
    print(f"\n  node dispatch: {pool.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
