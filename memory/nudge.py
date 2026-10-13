# SPDX-License-Identifier: MIT
"""Can a prompt change close MiniCPM5-2B's fact-count gap to gemma-4?

    python3 nudge.py

The bake-off left one question open. MiniCPM5 produced the cleanest facts of
the small models but only 2.7 per call against gemma-4's 6.0 -- which looked
like prompt conservatism ("up to 6 ... fewer good facts beat padded ones")
rather than a capability ceiling. This tests that on the same workload.

Fact COUNT alone would reward splitting one claim into three, so two more
things are measured:

  usable    facts surviving all three verify.py checks (numeric provenance,
            prompt echo, well-formedness).
  coverage  of the non-trivial numbers in the source text, the fraction that
            appear in at least one extracted fact. A cheap, deterministic
            proxy for "did it capture what the document actually says" --
            numbers are where this corpus keeps its facts. gemma-4's figure
            is computed from the facts saved by the last bake-off, so it
            costs no model time.

Decision rule, fixed before running: a variant qualifies for the full
end-to-end retrieval test if it reaches >= 60 usable facts (gemma-4: 72) with
coverage within 10 points of gemma-4's.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bakeoff  # noqa: E402
from smem.engine import EXTRACT_SYSTEM  # noqa: E402
from smem.pool import Pool  # noqa: E402
from smem.verify import _TRIVIAL, numbers  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

_COUNT_LINE = ("- Extract every distinct claim the document makes, up to 6. "
               "Fewer good facts\n  beat padded ones, and an empty array is "
               "correct for boilerplate.")
assert _COUNT_LINE in EXTRACT_SYSTEM, "production prompt changed; update nudge.py"

VARIANTS = {
    "A current": EXTRACT_SYSTEM,
    "B expect 4-8": EXTRACT_SYSTEM.replace(
        _COUNT_LINE,
        "- A section of technical documentation typically states 4 to 8\n"
        "  distinct facts. Extract all of them, up to 8. Only return an empty\n"
        "  array if the text is pure boilerplate."),
    "C sentence sweep": EXTRACT_SYSTEM.replace(
        _COUNT_LINE,
        "- Work through the document sentence by sentence. Every sentence\n"
        "  that states a number, a part name, a measured result, a cause or a\n"
        "  consequence yields a fact. Up to 8 facts. Only return an empty\n"
        "  array if the text is pure boilerplate."),
}


def source_numbers(groups):
    out = set()
    for _, body in groups:
        out |= {n for n in numbers(body) if n not in _TRIVIAL}
    return out


def coverage(facts, src_nums):
    got = set()
    for f in facts:
        got |= set(numbers(f))
    return len(src_nums & got) / len(src_nums) if src_nums else 1.0


def main():
    pool = Pool()
    groups = bakeoff.load_groups(10, 12)   # identical workload to the bake-off
    src_nums = source_numbers(groups)
    print(f"workload: {len(groups)} groups, {len(src_nums)} distinct "
          f"non-trivial numbers in the source\n")

    base = json.load(open(os.path.join(HERE, "bakeoff-results.json")))
    g = next(r for r in base["rows"] if r["label"].startswith("gemma"))
    g_usable = g["fid_total"] - g["fid_numeric"] - g["fid_echo"] - g["fid_form"]
    g_cov = coverage(g["all_facts"], src_nums)
    print(f"gemma-4 baseline (saved run): {g['facts']} facts, {g_usable} usable, "
          f"{g_cov * 100:.1f}% number coverage, {g['wall']:.1f}s/call\n")

    if not bakeoff.swap("minicpm5.gguf"):
        print("MiniCPM5 did not come up")
        return 1

    rows = []
    for name, prompt in VARIANTS.items():
        # run_model and its echo check both read bakeoff.EXTRACT_SYSTEM, so
        # swapping the module global swaps the prompt everywhere at once.
        bakeoff.EXTRACT_SYSTEM = prompt
        print(f"[{name}]")
        r = bakeoff.run_model(pool, name, groups, think=False, grammar=True)
        usable = r["fid_total"] - r["fid_numeric"] - r["fid_echo"] - r["fid_form"]
        cov = coverage(r["all_facts"], src_nums)
        r.update(usable=usable, coverage=cov)
        rows.append(r)
        print(f"    {r['facts']} facts, {usable} usable, "
              f"{cov * 100:.1f}% coverage, {r['wall']:.1f}s/call, "
              f"form {r['form_kinds'] or '-'}")
        for s in r["all_facts"][:4]:
            print(f"      + {s[:92]}")
        print()
    bakeoff.EXTRACT_SYSTEM = EXTRACT_SYSTEM

    with open(os.path.join(HERE, "nudge-results.json"), "w") as fh:
        json.dump({"gemma": {"facts": g["facts"], "usable": g_usable,
                             "coverage": g_cov, "wall": g["wall"]},
                   "rows": rows}, fh, indent=1)

    print("=" * 84)
    print("  %-18s %6s %7s %9s %7s %9s  %s" % (
        "variant", "facts", "usable", "coverage", "s/call", "vs gemma", "qualifies"))
    print("  " + "-" * 82)
    print("  %-18s %6d %7d %8.1f%% %6.1fs %9s  %s" % (
        "gemma-4 (base)", g["facts"], g_usable, g_cov * 100, g["wall"],
        "1.00x", "-"))
    for r in rows:
        ok = r["usable"] >= 60 and r["coverage"] >= g_cov - 0.10
        print("  %-18s %6d %7d %8.1f%% %6.1fs %8.2fx  %s" % (
            r["label"], r["facts"], r["usable"], r["coverage"] * 100,
            r["wall"], g["wall"] / r["wall"] if r["wall"] else 0,
            "YES" if ok else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
