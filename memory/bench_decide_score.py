# SPDX-License-Identifier: MIT
"""Score bench_decide.py output.  python3 bench_decide_score.py decide/*.json"""
import json
import statistics as st
import sys


def ece(rows, correct, bins=5):
    """Expected calibration error: |confidence - accuracy| weighted by bin size."""
    tot, err = len(rows), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        ins = [r for r in rows if lo < r["conf"] <= hi or (b == 0 and r["conf"] == 0)]
        if ins:
            acc = sum(correct(r) for r in ins) / len(ins)
            err += len(ins) / tot * abs(st.mean(r["conf"] for r in ins) - acc)
    return err


def auc(pos, neg):
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def lat(rows):
    w = [r["wall"] for r in rows]
    return f"{st.mean(w):5.2f} s mean, {st.median(w):5.2f} median"


def score_types(name, rows):
    ok = lambda r: r["pred"] in r["ok"]
    strict = [r for r in rows if len(r["ok"]) == 1]
    print(f"  {name:22s} lenient {sum(map(ok, rows))}/{len(rows)}"
          f"  strict {sum(map(ok, strict))}/{len(strict)}   {lat(rows)}")
    for c in ("preference", "episode"):
        said = [r for r in rows if r["pred"] == c]
        truly = [r for r in rows if r["gold"] == c]
        prec = sum(c in r["ok"] for r in said)
        rec = sum(r["pred"] in r["ok"] for r in truly)
        print(f"      {c:10s} precision {prec}/{len(said)}   recall {rec}/{len(truly)}")
    if rows[0].get("conf") is not None:
        print(f"      ECE {ece(rows, ok):.3f}", end="")
        for t in (0.8, 0.9):
            hi = [r for r in rows if r["conf"] >= t]
            if hi:
                print(f"   conf>={t}: {sum(map(ok, hi))}/{len(hi)} right, covers {len(hi)}/{len(rows)}", end="")
        print()


def score_pairs(name, rows):
    four = sum(r["pred"] in r["ok"] for r in rows)
    clear = [r for r in rows if r["link"] in ("yes", "no")]
    tp = sum(r["pred"] == "EXTENDS" and r["link"] == "yes" for r in clear)
    fp = sum(r["pred"] == "EXTENDS" and r["link"] == "no" for r in clear)
    fn = sum(r["pred"] != "EXTENDS" and r["link"] == "yes" for r in clear)
    yes = sum(r["link"] == "yes" for r in clear)
    print(f"  {name:22s} link: precision {tp}/{tp + fp}  recall {tp}/{yes}  "
          f"correct {len(clear) - fp - fn}/{len(clear)}   4-way {four}/{len(rows)}   {lat(rows)}")
    if rows[0].get("p_ext") is not None:
        a = auc([r["p_ext"] for r in clear if r["link"] == "yes"],
                [r["p_ext"] for r in clear if r["link"] == "no"])
        print(f"      AUC {a:.3f}", end="")
        for t in (0.5, 0.8, 0.9):
            tp = sum(r["p_ext"] >= t and r["link"] == "yes" for r in clear)
            fp = sum(r["p_ext"] >= t and r["link"] == "no" for r in clear)
            print(f"   p>={t}: {tp} right / {fp} wrong links", end="")
        print()


def cascade(rr, llm, lo, hi):
    """Reranker decides outside [lo, hi); the LLM decides inside."""
    clear = [i for i, r in enumerate(rr) if r["link"] in ("yes", "no")]
    sent = right = tp = fp = 0
    wall = 0.0
    for i in clear:
        s = rr[i]["p_ext"]
        wall += rr[i]["wall"]
        if s >= hi:
            link = True
        elif s < lo:
            link = False
        else:
            sent += 1
            wall += llm[i]["wall"]
            link = llm[i]["pred"] == "EXTENDS"
        truth = rr[i]["link"] == "yes"
        right += link == truth
        tp += link and truth
        fp += link and not truth
    yes = sum(rr[i]["link"] == "yes" for i in clear)
    print(f"      cascade [{lo}, {hi}): {right}/{len(clear)} correct, precision {tp}/{tp + fp}, "
          f"recall {tp}/{yes}, {sent}/{len(clear)} sent to LLM, {wall / len(clear):.2f} s/decision")


def main():
    res = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:]]
    for task, fn in (("types", score_types), ("pairs", score_pairs)):
        print(f"\n== {task} ==")
        # the floor any method must beat: always answer the majority class
        base = None
        for r in res:
            k = next((k for k in r["runs"] if k.startswith(task + "/")), None)
            if k:
                base = r["runs"][k]["rows"]
                break
        if base:
            dumb = [dict(row, pred="fact" if task == "types" else "UNRELATED",
                         conf=None, p_ext=None, wall=0.0) for row in base]
            fn("baseline (always " + dumb[0]["pred"] + ")", dumb)
        for r in res:
            for k, v in r["runs"].items():
                if k.startswith(task + "/"):
                    fn(f"{r['name']} {k.split('/')[1]}", v["rows"])
    for r in res:
        runs = r["runs"]
        if "pairs/rerank" in runs:
            rr = runs["pairs/rerank"]["rows"]
            print(f"\n== cascade: reranker screen, then {r['name']} ==")
            for m in ("grammar", "letters1"):
                if f"pairs/{m}" in runs:
                    print(f"    LLM = {m}")
                    for lo, hi in ((0.1, 0.9), (0.05, 0.95), (0.2, 0.8)):
                        cascade(rr, runs[f"pairs/{m}"]["rows"], lo, hi)


if __name__ == "__main__":
    main()
