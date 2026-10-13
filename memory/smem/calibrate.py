# SPDX-License-Identifier: MIT
"""Calibrate the background judges from accumulated labels.

The judges (engine.classify_batch, engine.judge_extends) return probabilities
over lettered options. A model's raw probabilities are rarely honest -- small
models are over-confident -- so every decision is logged (store.decisions)
and every correction a person makes in the console becomes a label
(store.labels). Pairing the two gives, per decision kind:

  temperature  one number. p_i -> softmax(log p_i / T). T > 1 softens an
               over-confident model, T < 1 sharpens a timid one. It never
               changes which option wins, only how sure the answer claims to
               be, so it is safe to fit on few labels. Fitted by minimising
               log loss over a grid.
  threshold    where to act. For extends: the lowest P(EXTENDS) whose links
               are right at least TARGET_PRECISION of the time, keeping the
               most true links. For types: the lowest confidence above which
               TARGET_TYPE_ACC of answers are right; below it a type is
               "uncertain" and kept out of the static profile.

Too few labels -> no fit, and the defaults measured in DECIDE.md apply.
Labels here are cheap to get wrong, so the fit is conservative: a threshold
is never set below the defaults' floor, and the stats record before/after so
a bad fit is visible in GET /v1/calibration.
"""
import json
import math
import time

MIN_LABELS = 20            # per kind before anything is fitted
MIN_POSITIVES = 5          # true extends links among them
TARGET_PRECISION = 0.85    # extends links
TARGET_TYPE_ACC = 0.90     # confident type answers
DEFAULTS = {"type": {"temperature": 1.0, "threshold": 0.80},
            "extends": {"temperature": 1.0, "threshold": 0.90}}
FLOOR = {"type": 0.5, "extends": 0.6}
GRID_T = [round(0.25 * 1.06 ** i, 4) for i in range(55)]      # 0.25 .. ~5.8
GRID_THR = [round(0.50 + 0.01 * i, 2) for i in range(50)]     # 0.50 .. 0.99


def apply(probs, temperature):
    """Temperature-scale a probability vector."""
    if not probs:
        return probs
    if temperature == 1.0:
        return list(probs)
    z = [math.log(max(p, 1e-9)) / temperature for p in probs]
    m = max(z)
    e = [math.exp(x - m) for x in z]
    s = sum(e)
    return [x / s for x in e]


def _log_loss(examples, t):
    tot = 0.0
    for opts, probs, label in examples:
        p = apply(probs, t)[opts.index(label)]
        tot -= math.log(max(p, 1e-9))
    return tot / len(examples)


def _ece(examples, t, bins=5):
    rows = []
    for opts, probs, label in examples:
        p = apply(probs, t)
        k = max(range(len(p)), key=p.__getitem__)
        rows.append((p[k], opts[k] == label))
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        ins = [r for r in rows if lo < r[0] <= hi]
        if ins:
            err += len(ins) / len(rows) * abs(
                sum(c for c, _ in ins) / len(ins) - sum(ok for _, ok in ins) / len(ins))
    return err


def fit(kind, examples):
    """examples: [(options, probs, label)] -> calibration dict, or None."""
    ex = [e for e in examples if e[2] in e[0] and len(e[1]) == len(e[0])]
    positives = sum(e[2] == "EXTENDS" for e in ex)
    if len(ex) < MIN_LABELS or (kind == "extends" and positives < MIN_POSITIVES):
        return None
    t = min(GRID_T, key=lambda x: _log_loss(ex, x))
    stats = {"n": len(ex), "logLossBefore": round(_log_loss(ex, 1.0), 4),
             "logLossAfter": round(_log_loss(ex, t), 4),
             "eceBefore": round(_ece(ex, 1.0), 4), "eceAfter": round(_ece(ex, t), 4),
             "accuracy": round(sum(max(zip(p, o))[1] == lab for o, p, lab in ex) / len(ex), 4)}

    thr = DEFAULTS[kind]["threshold"]
    if kind == "extends":
        stats["positives"] = positives
        scored = [(apply(p, t)[o.index("EXTENDS")], lab == "EXTENDS") for o, p, lab in ex]
        best = None
        for c in GRID_THR:
            took = [y for s, y in scored if s >= c]
            if not took:
                continue
            prec = sum(took) / len(took)
            if prec >= TARGET_PRECISION:
                rec = sum(took) / positives
                if best is None or rec > best[1]:
                    best = (c, rec, prec)
        if best:
            thr = max(FLOOR[kind], best[0])
            stats.update(precisionAtThreshold=round(best[2], 4),
                         recallAtThreshold=round(best[1], 4))
    else:
        scored = []
        for o, p, lab in ex:
            q = apply(p, t)
            k = max(range(len(q)), key=q.__getitem__)
            scored.append((q[k], o[k] == lab))
        for c in GRID_THR:
            took = [y for s, y in scored if s >= c]
            if took and sum(took) / len(took) >= TARGET_TYPE_ACC:
                thr = max(FLOOR[kind], c)
                stats.update(accuracyAboveThreshold=round(sum(took) / len(took), 4),
                             coverage=round(len(took) / len(scored), 4))
                break
    return {"temperature": t, "threshold": thr, "n": len(ex), "stats": stats}


DUP_CONF = 0.90


def reapply(store):
    """Re-derive every judged type confidence and edge from the logged raw
    probabilities under the CURRENT calibration. No model calls: a refit
    reaches decisions made before it, so the whole store follows one
    standard. Anything a person set (conf 1.0, or labelled) is left alone.
    -> counts of what changed.

    On any error the transaction is rolled back. Left open, it held the write
    lock on the background worker's connection, and every API write failed with
    "database is locked" until a restart (2026-10-04)."""
    try:
        return _reapply(store)
    except Exception:
        store.db.rollback()
        raise


def _reapply(store):
    db = store.db
    # Decisions and labels are a log with no foreign key, so they outlive a
    # memory that is later deleted. Re-linking one of those would violate
    # memory_edges' foreign key (and did: "FOREIGN KEY constraint failed").
    alive = {r[0] for r in db.execute("SELECT id FROM memories")}
    t_temp, _, _ = current(store, "type")
    e_temp, e_thr, _ = current(store, "extends")
    latest = ("SELECT d.* FROM decisions d JOIN (SELECT MAX(id) AS id FROM decisions"
              " WHERE kind=? GROUP BY subject, other) x ON x.id = d.id")
    labelled = {(r[0], r[1], r[2]): r[3] for r in db.execute(
        "SELECT kind, subject, other, label FROM labels")}
    n_type = 0
    # Labels are ground truth: they win over the judge, at full confidence.
    for (kind, subj, _), lab in labelled.items():
        if kind == "type":
            n_type += db.execute(
                "UPDATE memories SET mtype=?, mtype_conf=1.0 WHERE id=?"
                " AND (mtype IS NOT ? OR mtype_conf IS NOT 1.0)",
                (lab.lower(), subj, lab.lower())).rowcount
    for d in db.execute(latest, ("type",)).fetchall():
        if ("type", d["subject"], "") in labelled:
            continue
        opts, p = json.loads(d["options"]), apply(json.loads(d["probs"]), t_temp)
        k = max(range(len(p)), key=p.__getitem__)
        n_type += db.execute(
            "UPDATE memories SET mtype=?, mtype_conf=? WHERE id=?"
            " AND (mtype_conf IS NULL OR mtype_conf < 1)",
            (opts[k].lower(), round(p[k], 4), d["subject"])).rowcount
    added = removed = 0
    for (kind, src, dst), lab in labelled.items():
        if kind != "extends" or src not in alive or dst not in alive:
            continue
        want = lab.lower() if lab in ("EXTENDS", "DUPLICATE") else None
        have = [r[0] for r in db.execute(
            "SELECT kind FROM memory_edges WHERE src=? AND dst=? AND kind IN ('extends','duplicate')",
            (src, dst))]
        if have != ([want] if want else []):
            removed += db.execute("DELETE FROM memory_edges WHERE src=? AND dst=?"
                                  " AND kind IN ('extends','duplicate')", (src, dst)).rowcount
            if want:
                db.execute("INSERT INTO memory_edges (src,dst,kind,score,created_at,conf)"
                           " VALUES (?,?,?,NULL,?,1.0)", (src, dst, want, time.time()))
                added += 1
    for d in db.execute(latest, ("extends",)).fetchall():
        src, dst = d["subject"], d["other"]
        if ("extends", src, dst) in labelled or src not in alive or dst not in alive:
            continue
        opts, p = json.loads(d["options"]), apply(json.loads(d["probs"]), e_temp)
        pe, pd = p[opts.index("EXTENDS")], p[opts.index("DUPLICATE")]
        want = ("extends", pe) if pe >= e_thr else ("duplicate", pd) if pd >= DUP_CONF else None
        have = db.execute("SELECT kind, score FROM memory_edges WHERE src=? AND dst=?"
                          " AND kind IN ('extends','duplicate')", (src, dst)).fetchall()
        score = have[0]["score"] if have else None
        if have and (not want or have[0]["kind"] != want[0] or len(have) > 1):
            removed += db.execute("DELETE FROM memory_edges WHERE src=? AND dst=?"
                                  " AND kind IN ('extends','duplicate') AND (conf IS NULL OR conf < 1)",
                                  (src, dst)).rowcount
        if want:
            cur = db.execute("SELECT 1 FROM memory_edges WHERE src=? AND dst=? AND kind=?",
                             (src, dst, want[0])).fetchone()
            db.execute("INSERT OR REPLACE INTO memory_edges (src,dst,kind,score,created_at,conf)"
                       " VALUES (?,?,?,?,?,?)",
                       (src, dst, want[0], score, time.time(), round(want[1], 4)))
            added += 0 if cur else 1
    db.commit()
    return {"typesUpdated": n_type, "edgesAdded": added, "edgesRemoved": removed}


def refit(store):
    """Fit every kind that has enough labels, then re-apply the result to
    every past decision; -> {kind: result or reason, "reapplied": counts}."""
    out = {}
    for kind in ("type", "extends"):
        r = fit(kind, store.labelled_decisions(kind))
        if r:
            store.set_calibration(kind, r["temperature"], r["threshold"], r["n"], r["stats"])
            out[kind] = r
        else:
            out[kind] = "not enough labels yet"
    out["reapplied"] = reapply(store)
    return out


def current(store, kind):
    """-> (temperature, threshold, fitted?)"""
    c = store.calibration(kind)
    if c:
        return c["temperature"], c["threshold"], True
    d = DEFAULTS[kind]
    return d["temperature"], d["threshold"], False
