# SPDX-License-Identifier: MIT
"""Numeric provenance: does every number in an extracted fact come from the source?

This is the guard that matters most in a *memory* store, and it is not the one
people reach for first. The obvious worry with a small extraction model is
malformed JSON -- but malformed JSON is loud, and a GBNF grammar removes it
entirely. The dangerous failure is a well-formed fact carrying a plausible
wrong number: a model reading "4581/4487 MiB/s" and writing "4851/4487".
Nothing downstream will ever catch that. It gets embedded, retrieved, ranked
first, and cited with full authority.

So: pull every number out of a candidate fact and require it to appear in the
text the fact was extracted from.

The check is deliberately REPORTING by default rather than enforcing, because
not every unsupported number is a hallucination. A model that reads "30.4
tok/s per node, three nodes" and writes "91.3 tok/s total" has derived a
number, not invented one. Derived numbers are usually fine and sometimes
valuable. Until the false-positive rate is measured on real output, gating on
this would silently throw away good facts to prevent bad ones -- which is the
same trade that made the reconciler eat 50 true measurements.
"""

import re

# Integers, decimals, hex, and dotted versions. The lookbehind keeps us from
# starting mid-identifier (the "14" in "4.14" is not a separate number), and
# the trailing group keeps version strings like 7.0.14-17 in one piece.
_NUM = re.compile(
    r"(?<![\w.])"
    r"(0[xX][0-9a-fA-F]+"          # 0x1002
    r"|\d[\d,]*(?:\.\d+)+(?:-\d+)*"  # 4.98, 7.0.14-17
    r"|\d[\d,]*)"                  # 4581, 65521
)

# Numbers that carry no provenance risk: they are structural, not measured.
_TRIVIAL = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "100", "1000"}


def _norm(s):
    """Strip thousands separators so 4,581 and 4581 compare equal."""
    return s.replace(",", "")


def numbers(text):
    """-> list of normalised numeric tokens in `text`."""
    return [_norm(m.group(1)) for m in _NUM.finditer(text or "")]


def check(fact, source, trivial=_TRIVIAL):
    """-> (ok, unsupported[])  for one fact against the text it came from.

    A number counts as supported if it appears anywhere in the source, with
    thousands separators normalised away on both sides. Small integers are
    exempt: "one claim per fact" and "three nodes" are not measurements, and
    demanding provenance for them only produces noise.
    """
    src = _norm(source or "")
    bad = []
    for n in numbers(fact):
        if n in trivial:
            continue
        if n in src:
            continue
        # "4.98" should also match a source that wrote "4.980" or "$4.98";
        # substring already covers that. A bare integer, though, must not
        # match inside a longer number -- 45 should not be satisfied by 4581.
        if re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", src):
            continue
        bad.append(n)
    return (not bad), bad


def audit(facts, source):
    """-> {total, clean, flagged, rate, detail[]} over a list of facts."""
    detail = []
    clean = 0
    for f in facts:
        ok, bad = check(f, source)
        clean += ok
        if not ok:
            detail.append({"fact": f, "unsupported": bad})
    n = len(facts)
    return {
        "total": n,
        "clean": clean,
        "flagged": n - clean,
        "rate": round(clean / n, 4) if n else 1.0,
        "detail": detail,
    }


# ---------------------------------------------------------------- prompt echo

def _toks(s):
    return set(re.findall(r"[a-z0-9.]+", (s or "").lower()))


def _sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", s or "")
            if len(x.strip()) > 20]


def echo_check(fact, prompt_text, threshold=0.70):
    """Is this "fact" really a sentence copied out of the prompt?

    Small models regurgitate the few-shot example. Given a system prompt that
    ended with ["The fan runs at 2100 rpm.", "The heatsink is copper."],
    Llama-3.2-1B returned exactly those two as extracted facts, and OLMoE
    returned a mutated version ("The link runs at 2100 rpm"). Both inflated
    their facts-per-call score for producing nothing at all.

    Containment rather than equality, because the echo is often mutated: one
    noun gets swapped for one from the document and the rest is copied. Scored
    as |fact ∩ sentence| / |fact| so a short echo of a long instruction still
    trips it.

    This catches what check() structurally cannot: check() only sees
    hallucinations that contain a number, and "The heatsink is copper" has
    none. The two are complementary, and neither alone is sufficient.
    """
    ft = _toks(fact)
    if not ft:
        return True, 0.0
    worst = 0.0
    for sent in _sentences(prompt_text):
        st = _toks(sent)
        if not st:
            continue
        worst = max(worst, len(ft & st) / len(ft))
    return (worst < threshold), round(worst, 3)


def audit_full(facts, source, prompt_text):
    """Numeric provenance AND prompt-echo, together.

    -> {total, clean, numeric_bad, echo_bad, rate, detail[]}
    A fact is clean only if it passes both.
    """
    detail, clean, nbad, ebad = [], 0, 0, 0
    for f in facts:
        num_ok, bad = check(f, source)
        ech_ok, score = echo_check(f, prompt_text)
        if num_ok and ech_ok:
            clean += 1
            continue
        if not num_ok:
            nbad += 1
        if not ech_ok:
            ebad += 1
        detail.append({"fact": f, "unsupported": bad,
                       "echo": score if not ech_ok else None})
    n = len(facts)
    return {"total": n, "clean": clean, "numeric_bad": nbad, "echo_bad": ebad,
            "rate": round(clean / n, 4) if n else 1.0, "detail": detail}


# ------------------------------------------------------------ well-formedness

_MD = re.compile(r"^\s*[>#|*\-]+\s|\*\*|\*[^*]+\*|^\s*`[^`]+`\s*$")
_WH = re.compile(r"^(what|why|how|when|where|who|which|is|are|does|do|can|"
                 r"should|could|would)\b", re.I)
# Sentence-initial modal or bare imperative: the shape of a to-do, not a fact.
_IMP = re.compile(r"^(must|should|use|add|set|run|keep|avoid|do|don't|check|"
                  r"verify|ensure|allocate|note|see|read|prefer|never|always)\b",
                  re.I)


def form_check(fact):
    """Is this actually a standalone factual claim?

    The third failure mode, and the one the other two checks structurally
    cannot see. OLMoE returned "Is this card worth the slot?" and "What the
    card is *not* for" -- verbatim section headings. Llama-3.2-1B returned
    "Must allocate a whole segment" -- a fragment with no subject. Both scored
    100% on numeric provenance (no numbers to get wrong) and 100% on echo
    (copied from the DOCUMENT, not the prompt), and both are worthless in a
    memory store: retrieved out of context they say nothing.

    -> (ok, reason)  where reason is "", "question", "markdown" or "fragment".
    """
    f = (fact or "").strip()
    if not f:
        return False, "empty"
    if f.endswith("?") or (_WH.match(f) and " is " not in f.lower()[:40]):
        return False, "question"
    if _MD.search(f):
        return False, "markdown"
    # 4 words, not 5: "mbox_alloc is 0200 (write-only)" is terse but is a
    # complete claim with a subject, a copula and a value. A floor that
    # rejects it is throwing away exactly the dense facts worth keeping.
    if len(f.split()) < 4 or _IMP.match(f):
        return False, "fragment"
    return True, ""


def audit_all(facts, source, prompt_text):
    """All three checks. -> counts plus per-fact detail.

    Every one of these was discovered by looking at output, not by predicting
    it. Automated checks find the failure modes you already thought of; the
    ones you did not are found by reading what the model actually wrote.
    """
    detail, clean = [], 0
    n_num = n_echo = n_form = 0
    forms = {}
    for f in facts:
        num_ok, bad = check(f, source)
        ech_ok, score = echo_check(f, prompt_text)
        frm_ok, why = form_check(f)
        if num_ok and ech_ok and frm_ok:
            clean += 1
            continue
        n_num += (not num_ok)
        n_echo += (not ech_ok)
        n_form += (not frm_ok)
        if why:
            forms[why] = forms.get(why, 0) + 1
        detail.append({"fact": f, "unsupported": bad,
                       "echo": score if not ech_ok else None,
                       "form": why or None})
    n = len(facts)
    return {"total": n, "clean": clean, "numeric_bad": n_num,
            "echo_bad": n_echo, "form_bad": n_form, "form_kinds": forms,
            "rate": round(clean / n, 4) if n else 1.0, "detail": detail}
