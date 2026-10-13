# SPDX-License-Identifier: MIT
"""The pipeline: ingest -> extract -> reconcile, and query -> rerank -> answer.

INGEST
    document -> heading-aware chunks -> embeddings (fanned across 3 nodes)
             -> LLM extracts atomic facts -> each fact is reconciled against
                what is already remembered -> stored as a memory

QUERY
    query -> embedding -> cosine over memories AND chunks -> cross-encoder
          -> recency decay -> compact context -> LLM answer with citations

The point of the extract step is compression, not retrieval. Chunk retrieval
alone already answers most questions; what it cannot do is answer them in 700
tokens instead of 70,000. Memories are one-line facts, so a dozen of them cost
almost nothing to put in a prompt. Supermemory's published LongMemEval figure
is 95% Recall@15 at ~720 tokens; the thing being sold is the token count, and
measure_compression() below reports ours so the claim can be checked rather
than repeated.
"""

import collections
import concurrent.futures as cf
import math
import time

import numpy as np

from . import calibrate
from .chunk import chunk as chunk_text
from .models import Models, parse_json_loose
from .store import Store

EXTRACT_SYSTEM = """You extract durable facts from technical documentation.

The text to work from is given between <document> tags. It is the document
itself, not a reference to one. Never ask for more of it.

Rules:
- Every fact must come from the document. Never repeat a rule, an instruction
  or an example from this message as though it were a fact.
- Each fact must stand alone: understandable with no access to the document.
- Keep the specific numbers, part numbers, names and identifiers. A fact that
  says something is slow, or was improved, is worthless without the value.
- One claim per fact. Split compound sentences.
- Skip to-dos, open questions, section headings and pure formatting.
- Extract every distinct claim the document makes, up to 6. Fewer good facts
  beat padded ones, and an empty array is correct for boilerplate.

Output a JSON array of strings and nothing else."""

# NO CONCRETE EXAMPLE OF A FACT APPEARS ABOVE, DELIBERATELY.
#
# The first version ended with a sample array,
# ["The fan runs at 2100 rpm.", "The heatsink is copper."], and small models
# extracted THOSE as facts about whatever document they were handed.
# Llama-3.2-1B returned both verbatim; OLMoE returned a mutated
# "The link runs at 2100 rpm". Both scored well on facts-per-call for
# producing nothing at all, and the numeric provenance check only caught the
# ones carrying a number -- "The heatsink is copper" has none. See
# smem/verify.py:echo_check.
#
# The example was there to pin the output format. The grammar below does that
# job properly, so the example is not merely harmful, it is redundant.
EXTRACT_GRAMMAR = r"""
root   ::= "[" ws (string (ws "," ws string)*)? ws "]"
string ::= "\"" ([^"\\] | "\\" ["\\/bfnrt])* "\""
ws     ::= [ \t\n]*
"""

EXTRACT_USER = """<document source="{src}">
{body}
</document>

Extract the durable facts from the document above."""

RECONCILE_SYSTEM = """You compare two remembered facts, A and B, and classify B.

Apply this test first, before anything else:

    CAN A AND B BOTH BE TRUE AT THE SAME TIME?
    If yes, the answer is DUPLICATE or DISTINCT. It is never UPDATE.

DUPLICATE - B states the same thing as A. Both true, no new information.
UPDATE    - B REPLACES A. They cannot both be true, because B measures the
            same quantity under the same conditions, or corrects A, or
            describes a later state that makes A no longer the case.
DISTINCT  - Both can be true. Different quantities, different conditions,
            different directions, different configurations, or a cause and
            its remedy.

Examples:
A: The fan runs at 2100 rpm. B: Fan speed is about 2100 rpm. -> DUPLICATE
A: The fan runs at 2100 rpm. B: The fan now runs at 3400 rpm. -> UPDATE
A: The fan runs at 2100 rpm. B: The heatsink is copper. -> DISTINCT
A: At MTU 1500 the link does 2.8 Gbit/s.
B: At MTU 65521 the link does 15.9 Gbit/s. -> DISTINCT
A: Host to node reaches 14.6 Gbit/s.
B: Node to host reaches 25.6 Gbit/s. -> DISTINCT
A: Unloading the driver causes a hang on next boot.
B: Recovering from that hang needs a host reboot. -> DISTINCT
A: The link trains at x8. B: The link trains at x16 after reseating. -> UPDATE

Answer with exactly one word."""

# Constrained decoding is not a nicety here. Asked in free text, gemma-4
# returns an EMPTY string for precisely the DUPLICATE cases -- which fell
# through to DISTINCT and stored the duplicate. The bug was invisible: every
# call "succeeded". The grammar makes the wrong answer unrepresentable.
RECONCILE_GRAMMAR = 'root ::= "DUPLICATE" | "UPDATE" | "DISTINCT"'

TYPE_SYSTEM = """Classify one remembered statement.

FACT       - a durable statement about the world, a system, a project or a
             person: what something is, has, does, costs or measured.
PREFERENCE - what someone wants, likes, prefers or decided, or how they want
             things done.
EPISODE    - something that happened at a particular time, or a temporary
             state that will stop being true soon.

Examples:
"The PEX 8749 uplink trains at x16 Gen3." -> FACT
"Austin prefers terse, decision-oriented updates." -> PREFERENCE
"The repo stays private and must not be made public yet." -> PREFERENCE
"The host was rebooted on 2026-09-22 to test the new units." -> EPISODE
"Node 0 is currently out of memory." -> EPISODE

Answer with exactly one word."""
TYPE_GRAMMAR = 'root ::= "FACT" | "PREFERENCE" | "EPISODE"'

# The extends judge runs in the background (see dream_step), NOT inside the
# ingest-time reconcile. Folding a fourth class into the reconcile prompt
# would put its measured 19/22 at risk for a feature that can be computed
# later, off the critical path.
EXTENDS_SYSTEM = """A and B are two remembered statements. B is the newer one.

Apply this test first:

    CAN A AND B BOTH BE TRUE AT THE SAME TIME?
    If yes, the answer is EXTENDS, DUPLICATE or UNRELATED. It is never UPDATE.
    Two sizes, two speeds or two settings of the same thing are both true.

EXTENDS   - B is about the SAME specific subject as A and adds a new detail
            about it. Both stay true. Reading A, you would want B next.
DUPLICATE - B says the same thing as A.
UPDATE    - B replaces A: they cannot both be true.
UNRELATED - different subjects, or only the same broad topic.

Examples:
A: The host runs Proxmox on kernel 7.0.14-17-pve.
B: Kernel 7.0.14-17-pve brings up all three virtio NICs. -> EXTENDS
A: Node 2 sits behind a cascaded PEX 8717 switch.
B: Node 2's reads cost one extra switch hop. -> EXTENDS
A: The card idles at about 12 W.
B: The reranker leaks memory under sustained load. -> UNRELATED
A: PL1 is 45 W. B: PL1 is now 56 W. -> UPDATE

Answer with exactly one word."""
EXTENDS_GRAMMAR = 'root ::= "EXTENDS" | "DUPLICATE" | "UPDATE" | "UNRELATED"'

# The same two decisions asked as multiple choice and read as probabilities
# (Models.choose). The system prompt is the definitions only and never
# changes, so llama-server keeps it cached; the statement and the lettered
# options go last. This layout is the one measured in DECIDE.md -- change it
# and re-run bench_decide.py.
TYPE_OPTIONS = ["FACT", "PREFERENCE", "EPISODE"]
EXTENDS_OPTIONS = ["EXTENDS", "DUPLICATE", "UPDATE", "UNRELATED"]


def _definitions(system):
    return system.rsplit("Answer with exactly one word.", 1)[0].rstrip()


TYPE_CHOICE_SYSTEM = _definitions(TYPE_SYSTEM)
EXTENDS_CHOICE_SYSTEM = _definitions(EXTENDS_SYSTEM)


def choice_prompt(body, options):
    opts = "\n".join(f"{chr(65 + i)}) {o}" for i, o in enumerate(options))
    return f"{body}\n\nOptions:\n{opts}\n\nAnswer with the letter only."

ANSWER_SYSTEM = """You answer using ONLY the numbered context provided.

- Cite the sources you used as [1], [2] inline.
- If the context does not contain the answer, say so plainly. Do not guess and
  do not fill the gap from general knowledge.
- Be concise and concrete. Prefer the specific number over a description."""


class Engine:
    def __init__(self, db_path, models=None, container="default"):
        self.store = Store(db_path)
        self.models = models or Models()
        self.container = container

    def close(self):
        self.store.close()

    def reader(self):
        """A read-only view sharing this engine's model pool.

        For the HTTP server: search, ask and every console read run on their
        own read-only SQLite connection, so they need neither the write lock
        nor to wait behind an ingest. Close it when done.
        """
        e = Engine.__new__(Engine)
        e.store = Store(self.store.path, readonly=True)
        e.models = self.models
        e.container = self.container
        return e

    # ================================================================ ingest

    def ingest(self, content, title=None, source=None, custom_id=None,
               container=None, extract=True, metadata=None, doc_date=None,
               progress=None):
        """Ingest one document. -> {doc_id, chunks, memories, timings}"""
        c = container or self.container
        t0 = time.time()
        parts = chunk_text(content, title=title)
        if not parts:
            parts = [{"heading": title, "text": content, "embed_text": content}]

        vecs = self.models.embed([p["text"] for p in parts],
                                 titles=[p["heading"] for p in parts])
        t_embed = time.time() - t0

        doc_id = self.store.add_document(
            c, content, title=title, source=source, custom_id=custom_id,
            metadata=metadata, doc_date=doc_date)
        self.store.add_chunks(doc_id, c, parts, vecs)

        n_mem = 0
        t_extract = 0.0
        if extract:
            te = time.time()
            n_mem = self._extract(c, doc_id, parts, progress=progress,
                                  src=title or source or "document")
            t_extract = time.time() - te

        return {"doc_id": doc_id, "chunks": len(parts), "memories": n_mem,
                "embed_s": round(t_embed, 2), "extract_s": round(t_extract, 2),
                "total_s": round(time.time() - t0, 2)}

    @staticmethod
    def _groups(parts):
        """Pack chunks into ~3000-char groups, one extraction call each.

        One call per group rather than per chunk roughly halves the slowest
        stage of the pipeline, and 3000 chars is ~750 tokens against the
        server's 3072-token context.
        """
        groups, buf = [], ""
        for p in parts:
            piece = ((p["heading"] + "\n") if p["heading"] else "") + p["text"]
            if buf and len(buf) + len(piece) > 3000:
                groups.append(buf)
                buf = ""
            buf += piece + "\n\n"
        if buf.strip():
            groups.append(buf.strip())
        return groups

    @staticmethod
    def _extract_msgs(src, group):
        return [{"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": EXTRACT_USER.format(src=src, body=group)}]

    @staticmethod
    def _facts_from(replies):
        cands = []
        for r in replies:
            for f in parse_json_loose(r):
                if isinstance(f, dict):
                    f = f.get("fact") or f.get("text") or f.get("content") or ""
                f = str(f).strip()
                if 15 < len(f) < 400:
                    cands.append(f)
        return cands

    def _extract(self, container, doc_id, parts, progress=None, src="document"):
        """LLM-extract facts from one document's groups, then reconcile them."""
        groups = self._groups(parts)
        replies = self.models.chat_many(
            [self._extract_msgs(src, g) for g in groups],
            max_tokens=320, temperature=0.1, grammar=EXTRACT_GRAMMAR)
        cands = self._facts_from(replies)
        if progress:
            progress(f"    {len(groups)} extract calls -> {len(cands)} candidate facts")
        return self.remember_many(cands, container=container, doc_id=doc_id,
                                  progress=progress)

    # ============================================================ batch ingest

    def ingest_many(self, docs, container=None, extract=True, progress=None):
        """Ingest a batch of documents with every node working one shared queue.

        docs: [{content, title, source, custom_id, metadata, doc_date}]

        WHY THIS EXISTS. ingest() is document-at-a-time, and the journals from
        the first 72-minute ingest of docs/ show what that costs: all three
        nodes were working at once only 47% of the time, and 13% of the time
        none were. The work itself was 145 node-minutes, i.e. 48 minutes if
        spread evenly. Most documents produce only 1-2 extraction calls, so
        they cannot fill three nodes, and every document's embedding ran as a
        single batch on a single node.

        Here the batch runs in three phases:

          1. chunk everything, embed every chunk in one fanned-out call, store.
          2. every extraction call from every document goes into ONE queue,
             longest first, with one worker per node. Longest-first because a
             queue drains fastest when the slow jobs start early and short
             ones fill the gaps at the end, rather than one 90 s call running
             alone while two nodes idle.
          3. reconcile, in document order, after extraction has finished.

        Phase 3 is deliberately NOT overlapped with phase 2. Each node serves
        one request at a time (-np 1), so a reconcile call issued while three
        extraction calls are running waits behind a ~58 s call. Reconcile is
        serial by necessity, so 200 of them each waiting ~30 s would add
        ~100 minutes -- far worse than the ~9 minutes they take on their own.

        Reconciling in document order (not completion order) keeps the
        supersede semantics identical to calling ingest() on each document in
        turn.
        """
        c = container or self.container
        say = progress or (lambda msg: None)
        t0 = time.time()

        prepared = []
        for d in docs:
            parts = chunk_text(d["content"], title=d.get("title"))
            if not parts:
                parts = [{"heading": d.get("title"), "text": d["content"],
                          "embed_text": d["content"]}]
            prepared.append((d, parts))

        flat = [p for _, parts in prepared for p in parts]
        vecs = self.models.embed([p["text"] for p in flat],
                                 titles=[p["heading"] for p in flat])
        t_embed = time.time() - t0

        doc_ids, k = [], 0
        for d, parts in prepared:
            v = vecs[k:k + len(parts)]
            k += len(parts)
            did = self.store.add_document(
                c, d["content"], title=d.get("title"), source=d.get("source"),
                custom_id=d.get("custom_id"), metadata=d.get("metadata"),
                doc_date=d.get("doc_date"))
            self.store.add_chunks(did, c, parts, v)
            doc_ids.append(did)
        say(f"  phase 1: {len(flat)} chunks from {len(docs)} documents "
            f"embedded in {t_embed:.1f}s")

        out = {"documents": len(docs), "chunks": len(flat), "memories": 0,
               "embed_s": round(t_embed, 1), "extract_s": 0.0,
               "reconcile_s": 0.0, "calls": 0}
        if not extract:
            out["total_s"] = round(time.time() - t0, 1)
            return out

        jobs = []
        for di, (d, parts) in enumerate(prepared):
            src = d.get("title") or d.get("source") or "document"
            for gi, g in enumerate(self._groups(parts)):
                jobs.append((di, gi, src, g))
        jobs.sort(key=lambda j: -len(j[3]))

        replies = [[] for _ in docs]
        t1 = time.time()
        workers = len(self.models.pool.nodes)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.models.chat, self._extract_msgs(src, g),
                              max_tokens=320, temperature=0.1,
                              grammar=EXTRACT_GRAMMAR): (di, gi)
                    for di, gi, src, g in jobs}
            for n, f in enumerate(cf.as_completed(futs), 1):
                di, gi = futs[f]
                try:
                    replies[di].append((gi, f.result()))
                except Exception as e:
                    say(f"    extraction call failed: {str(e)[:80]}")
                if n % 10 == 0 or n == len(jobs):
                    say(f"  phase 2: {n}/{len(jobs)} extraction calls "
                        f"({time.time() - t1:.0f}s)")
        t_extract = time.time() - t1

        t2 = time.time()
        n_mem = 0
        for di, did in enumerate(doc_ids):
            ordered = [r for _, r in sorted(replies[di])]
            n_mem += self.remember_many(self._facts_from(ordered),
                                        container=c, doc_id=did)
        t_rec = time.time() - t2
        say(f"  phase 3: reconciled into {n_mem} memories in {t_rec:.1f}s")

        out.update(memories=n_mem, calls=len(jobs),
                   extract_s=round(t_extract, 1), reconcile_s=round(t_rec, 1),
                   total_s=round(time.time() - t0, 1))
        return out

    # --------------------------------------------------------- temporal truth

    # Cosine thresholds are embedder-specific. EmbeddingGemma 2 packs scores
    # high (unrelated memories ~0.69, Qwen3 ~0.35), so these were re-derived
    # 2026-10-07 by matching the pairs Qwen's values selected (EMBED_BAKEOFF.md).
    NEAR = 0.89          # cosine above which two facts are worth comparing (Qwen 0.82)
    # Trivial edits (a full stop, spacing) score >= 0.99; one changed word
    # 0.95-0.98 and must reach the judge -- it may be a "not".
    SAME = 0.99          # cosine above which they are the same string, near enough (Qwen 0.985)

    def remember_many(self, facts, container=None, doc_id=None, progress=None,
                      event_date=None):
        """Store facts, superseding or dropping ones that collide with existing.

        This is where "temporal truth" actually happens. A new fact that
        contradicts an old one does not overwrite it: the old row is demoted to
        is_latest=0 and the new row points back at it, so the store can still
        answer "what did we believe in June, and when did that change".
        """
        c = container or self.container
        facts = [f for f in facts if f]
        if not facts:
            return 0
        vecs = self.models.embed(facts)

        rows, mat = self.store.matrix(c, "memories")
        n_new = n_upd = n_dup = 0

        for i, fact in enumerate(facts):
            v = vecs[i]
            sup = None
            if len(rows):
                sims = mat @ v
                j = int(np.argmax(sims))
                best = float(sims[j])
                if best >= self.SAME:
                    n_dup += 1
                    continue
                if best >= self.NEAR:
                    verdict = self._reconcile(rows[j]["content"], fact)
                    if verdict == "DUPLICATE":
                        n_dup += 1
                        continue
                    if verdict == "UPDATE":
                        sup = rows[j]["id"]
            mid = self.store.add_memory(c, fact, v, doc_id=doc_id,
                                        supersedes=sup, event_date=event_date)
            if sup:
                n_upd += 1
            else:
                n_new += 1
            # Keep the in-memory matrix current so two near-identical facts in
            # the SAME batch collide with each other, not just with old rows.
            # Without this, ingesting a document that repeats itself stores the
            # repetition.
            row = self.store.memory(mid)
            rows = list(rows) + [row]
            mat = (np.vstack([mat, v]) if len(mat) and mat.shape[1] == v.shape[0]
                   else v.reshape(1, -1))

        if progress and (n_new or n_upd or n_dup):
            progress(f"    memories: +{n_new} new, ~{n_upd} updated, "
                     f"{n_dup} duplicate")
        return n_new + n_upd

    def _reconcile(self, old, new):
        try:
            r = self.models.chat(
                [{"role": "system", "content": RECONCILE_SYSTEM},
                 {"role": "user", "content": f"A: {old}\nB: {new}"}],
                max_tokens=8, temperature=0.0,
                grammar=RECONCILE_GRAMMAR).strip().upper()
        except Exception:
            return "DISTINCT"
        for w in ("DUPLICATE", "UPDATE", "DISTINCT"):
            if w in r:
                return w
        return "DISTINCT"

    def remember(self, fact, container=None, event_date=None):
        return self.remember_many([fact], container=container,
                                  event_date=event_date)

    # ============================================================ dreaming
    #
    # Work done AFTER ingest, in the background, while the card is idle.
    # Supermemory calls this dreaming: the graph keeps growing after a
    # document lands. Here it does two things:
    #   1. types every memory as fact / preference / episode (feeds profiles)
    #   2. finds `extends` relations between memories on the same subject
    # Both are batched across the three nodes with chat_many.

    EXT_LOW = 0.875       # cosine floor for an extends candidate (Qwen 0.80; see NEAR)
    EXT_MAX = 3           # candidates judged per memory
    # Decisions are read as probabilities over lettered options (DECIDE.md).
    # At P >= 0.9 the judge kept all 11 true links and cut false ones 7 -> 3.
    # The extends and type thresholds (0.90 / 0.80) and the probability
    # temperature live in calibrate.py: defaults from DECIDE.md, re-fitted
    # from labels as people correct types and links in the console.
    DUP_CONF = calibrate.DUP_CONF   # record a duplicate edge at or above this

    def classify_batch(self, rows):
        """rows: [(id, content)]
        -> {id: (type, calibrated confidence or None, raw probs or None)}"""
        temp, _, _ = calibrate.current(self.store, "type")
        jobs = [(TYPE_CHOICE_SYSTEM, choice_prompt(f"Statement: {content}", TYPE_OPTIONS))
                for _, content in rows]
        probs = self.models.choose_many(jobs, len(TYPE_OPTIONS))
        res, retry = {}, []
        for (mid, content), raw in zip(rows, probs):
            if raw:
                p = calibrate.apply(raw, temp)
                k = max(range(len(p)), key=p.__getitem__)
                res[mid] = (TYPE_OPTIONS[k].lower(), round(p[k], 4), raw)
            else:
                retry.append((mid, content))
        if retry:
            # No logprobs (a server that ignores the option): the old one-word
            # path still gives a label, without a confidence.
            out = self.models.chat_many(
                [[{"role": "system", "content": TYPE_SYSTEM},
                  {"role": "user", "content": c}] for _, c in retry],
                max_tokens=6, temperature=0.0, grammar=TYPE_GRAMMAR)
            for (mid, _), r in zip(retry, out):
                r = (r or "").strip().upper()
                if r in TYPE_OPTIONS:
                    res[mid] = (r.lower(), None, None)
        return res

    def extends_candidates(self, mem, rows, mat, ids):
        """Older live neighbours of `mem` worth asking the extends judge about.

        Only neighbours OLDER than mem are considered, so every pair is judged
        exactly once, from the side of the newer memory. Pairs already linked
        by an update or an existing edge are skipped.
        """
        if not len(rows):
            return []
        v = np.frombuffer(mem["vec"], dtype=np.float32)
        if v.shape[0] != mat.shape[1]:
            return []
        sims = mat @ v
        linked = {mem["supersedes"]}
        for r in self.store.relations(mem["id"]):
            linked.add(r["other_id"])
        out = []
        for j in np.argsort(-sims):
            j = int(j)
            s = float(sims[j])
            if s < self.EXT_LOW:
                break
            o = rows[j]
            if o["id"] == mem["id"] or o["id"] in linked or s >= self.SAME:
                continue
            if o["container"] != mem["container"]:
                continue
            if (o["created_at"], o["id"]) >= (mem["created_at"], mem["id"]):
                continue
            out.append((o, s))
            if len(out) >= self.EXT_MAX:
                break
        return out

    def judge_extends(self, pairs):
        """pairs: [(older_row, newer_row, sim)] -> [{verdict: p} or None]"""
        jobs = [(EXTENDS_CHOICE_SYSTEM,
                 choice_prompt(f"A: {a['content']}\nB: {b['content']}", EXTENDS_OPTIONS))
                for a, b, _ in pairs]
        out = []
        for p in self.models.choose_many(jobs, len(EXTENDS_OPTIONS)):
            out.append(dict(zip(EXTENDS_OPTIONS, p)) if p else None)
        return out

    def dream_step(self, batch=9):
        """One unit of background work. -> dict describing what was done."""
        todo = self.store.unclassified(limit=batch)
        if todo:
            _, type_thr, _ = calibrate.current(self.store, "type")
            types = self.classify_batch([(r["id"], r["content"]) for r in todo])
            for mid, (t, conf, _) in types.items():
                self.store.set_mtype(mid, t, conf)
            self.store.log_decisions(
                [("type", mid, "", TYPE_OPTIONS, raw, t.upper())
                 for mid, (t, _, raw) in types.items() if raw], model="llm")
            # Anything the model failed to answer is marked 'fact' with no
            # confidence rather than retried forever; a stuck row would stall
            # the queue, and no confidence keeps it out of the static profile.
            for r in todo:
                if r["id"] not in types:
                    self.store.set_mtype(r["id"], "fact", 0.0)
            confs = [c for _, c, _ in types.values() if c is not None]
            return {"did": "classify", "n": len(todo),
                    "types": dict(collections.Counter(t for t, _, _ in types.values())),
                    "uncertain": sum(c < type_thr for c in confs)}

        mems = self.store.undreamed(limit=max(1, batch // self.EXT_MAX))
        if not mems:
            return {"did": "idle"}
        pairs = []
        by_container = {}
        for m in mems:
            c = m["container"]
            if c not in by_container:
                rows, mat = self.store.matrix(c, "memories")
                by_container[c] = (rows, mat)
            rows, mat = by_container[c]
            for o, sim in self.extends_candidates(m, rows, mat, None):
                pairs.append((o, m, sim))
        verdicts = self.judge_extends(pairs) if pairs else []
        temp, ext_thr, _ = calibrate.current(self.store, "extends")
        found = dups = 0
        tally = {}
        log = []
        for (a, b, sim), raw in zip(pairs, verdicts):
            if not raw:
                tally["NO_ANSWER"] = tally.get("NO_ANSWER", 0) + 1
                continue
            p = dict(zip(EXTENDS_OPTIONS, calibrate.apply(
                [raw[o] for o in EXTENDS_OPTIONS], temp)))
            top = max(p, key=p.get)
            tally[top] = tally.get(top, 0) + 1
            log.append(("extends", b["id"], a["id"], EXTENDS_OPTIONS,
                        [raw[o] for o in EXTENDS_OPTIONS], top))
            if p["EXTENDS"] >= ext_thr:
                self.store.add_edge(b["id"], a["id"], "extends", round(sim, 4),
                                    conf=round(p["EXTENDS"], 4))
                found += 1
            elif p["DUPLICATE"] >= self.DUP_CONF:
                self.store.add_edge(b["id"], a["id"], "duplicate", round(sim, 4),
                                    conf=round(p["DUPLICATE"], 4))
                dups += 1
        if log:
            self.store.log_decisions(log, model="llm")
        self.store.mark_dreamed([m["id"] for m in mems])
        return {"did": "extends", "memories": len(mems), "pairs": len(pairs),
                "found": found, "duplicates": dups, "verdicts": tally}

    # ============================================================= profiles

    def profile(self, container=None, n_static=12, n_dynamic=10, days=14):
        """Supermemory-style profile: what an agent should always know.

        static   stable facts and preferences. Preferences first (they should
                 colour every answer), then the facts with the most relations
                 -- a crude but honest proxy for "central to this space".
        dynamic  what is recent or changing: episodes, plus facts that
                 superseded an older version within `days`.

        Ranking is heuristic. Supermemory's own profile is built by a model;
        this one is built from the graph that dreaming produces, so it
        improves as dreaming works through a space.
        """
        c = container or self.container
        db = self.store.db
        live = "container=? AND is_latest=1 AND forgotten_at IS NULL"
        # Only extends edges say "central"; duplicate edges mark restatements.
        deg = ("(SELECT COUNT(*) FROM memory_edges e WHERE e.kind='extends'"
               "   AND (e.src=m.id OR e.dst=m.id)) + (m.version > 1)")
        # An uncertain type stays out of the static profile: that is where a
        # mislabelled "preference" does the most damage (DECIDE.md).
        _, thr, _ = calibrate.current(self.store, "type")
        sure = "(mtype_conf IS NULL OR mtype_conf >= ?)"
        prefs = db.execute(
            f"SELECT m.*, {deg} AS degree FROM memories m WHERE {live}"
            f" AND mtype='preference' AND {sure} ORDER BY created_at DESC LIMIT ?",
            (c, thr, n_static)).fetchall()
        facts = db.execute(
            f"SELECT m.*, {deg} AS degree FROM memories m WHERE {live}"
            f" AND mtype='fact' AND {sure} ORDER BY degree DESC, created_at DESC LIMIT ?",
            (c, thr, max(0, n_static - len(prefs)))).fetchall()
        cutoff = time.time() - days * 86400
        dyn = db.execute(
            f"SELECT m.*, {deg} AS degree FROM memories m WHERE {live}"
            " AND (mtype='episode' OR (version > 1 AND created_at > ?))"
            " ORDER BY created_at DESC LIMIT ?",
            (c, cutoff, n_dynamic)).fetchall()
        counts = self.store.type_counts(c)
        uncertain = db.execute(
            f"SELECT COUNT(*) FROM memories WHERE {live} AND mtype_conf < ?",
            (c, thr)).fetchone()[0]
        return {"container": c, "static": list(prefs) + list(facts),
                "dynamic": list(dyn), "types": counts,
                "unclassified": counts.get("unclassified", 0),
                "uncertain": uncertain, "typeThreshold": thr}

    # ================================================================= search

    def search(self, query, container=None, k=8, candidates=40, rerank=True,
               half_life_days=None, include_chunks=True, include_memories=True):
        """-> list of hits, best first.

        Two stages on purpose. The bi-encoder scan is cheap and recall-oriented
        but scores a chunk by how close its single vector is to the query; the
        cross-encoder reads the query and the text together and is far better
        at precision, but costs a forward pass per pair. So: cast wide with
        cosine, then re-score only the survivors.
        """
        c = container or self.container
        t0 = time.time()
        qv = self.models.embed([query], is_query=True)[0]

        tables = []
        if include_memories:
            tables.append("memories")
        if include_chunks:
            tables.append("chunks")

        pool = []
        for table in tables:
            rows, mat = self.store.matrix(c, table)
            if not len(rows) or mat.shape[1] != qv.shape[0]:
                continue
            sims = mat @ qv
            top = np.argsort(-sims)[:candidates]
            for j in top:
                r = rows[int(j)]
                pool.append({
                    "id": r["id"],
                    "kind": "memory" if table == "memories" else "chunk",
                    "text": r["content"] if table == "memories" else r["text"],
                    "heading": None if table == "memories" else r["heading"],
                    "doc_id": r["doc_id"],
                    "vector_score": float(sims[int(j)]),
                    "date": (r["event_date"] or 0.0) if table == "memories" else 0.0,
                })
        t_vec = time.time() - t0

        if not pool:
            return []

        pool.sort(key=lambda h: -h["vector_score"])
        if len(tables) > 1:
            # Each kind is guaranteed an equal share of the candidates; the
            # rest go by score. EmbeddingGemma 2 scores short memories above
            # longer chunks (median top-40 cosine 0.738 vs 0.709), so a pure
            # cosine merge gave memories ~30 of 40 slots and crowded out the
            # chunk holding an answer (eval.py: systemd-run, chunk rank 14).
            share = candidates // len(tables)
            keep = []
            for kind in ("memory", "chunk"):
                keep += [h for h in pool if h["kind"] == kind][:share]
            taken = {id(h) for h in keep}
            keep += [h for h in pool if id(h) not in taken][:candidates - len(keep)]
            pool = sorted(keep, key=lambda h: -h["vector_score"])
        else:
            pool = pool[:candidates]

        t1 = time.time()
        if rerank:
            scores = self.models.rerank(query, [h["text"] for h in pool])
            for h, s in zip(pool, scores):
                h["rerank_score"] = s
        else:
            for h in pool:
                h["rerank_score"] = h["vector_score"]
        t_rrk = time.time() - t1

        now = time.time()
        for h in pool:
            # Decay applies to memories only. A chunk of a design doc is not
            # less true for being six months old; a remembered fact about a
            # changing system often is.
            d = 1.0
            if half_life_days and h["kind"] == "memory" and h["date"]:
                age_d = max(0.0, (now - h["date"]) / 86400.0)
                d = math.exp(-math.log(2) * age_d / half_life_days)
            h["decay"] = round(d, 4)
            h["score"] = h["rerank_score"] * d

        pool.sort(key=lambda h: -h["score"])
        hits = pool[:k]
        for h in hits:
            h["vector_ms"] = round(t_vec * 1000, 1)
            h["rerank_ms"] = round(t_rrk * 1000, 1)
        return hits

    def context(self, hits, doc_titles=True):
        """Render hits as a numbered block for a prompt, plus its token cost."""
        lines = []
        for i, h in enumerate(hits, 1):
            src = ""
            if doc_titles and h["doc_id"]:
                d = self.store.get_document(h["doc_id"])
                if d:
                    src = f" ({d['title'] or d['source'] or ''})".rstrip(" ()")
            tag = "fact" if h["kind"] == "memory" else "passage"
            head = f" -- {h['heading']}" if h.get("heading") else ""
            lines.append(f"[{i}] {tag}{src}{head}\n{h['text']}")
        return "\n\n".join(lines)

    # ================================================================ answer

    def ask(self, query, container=None, k=8, half_life_days=None, **kw):
        t0 = time.time()
        hits = self.search(query, container=container, k=k,
                           half_life_days=half_life_days, **kw)
        if not hits:
            return {"answer": "Nothing in this container matches that.",
                    "hits": [], "context_tokens": 0,
                    "total_s": round(time.time() - t0, 2)}
        ctx = self.context(hits)
        t1 = time.time()
        ans = self.models.chat(
            [{"role": "system", "content": ANSWER_SYSTEM},
             {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {query}"}],
            max_tokens=400, temperature=0.2)
        return {
            "answer": ans.strip(),
            "hits": hits,
            "context": ctx,
            "context_tokens": self.models.count_tokens(ctx),
            "retrieve_s": round(t1 - t0, 2),
            "generate_s": round(time.time() - t1, 2),
            "total_s": round(time.time() - t0, 2),
        }

    # ============================================================== forgetting

    def forget_matching(self, query, container=None, threshold=0.60, dry_run=True):
        """Forget every memory the query matches -- Supermemory's mass-forget.

        Scored with the cross-encoder rather than cosine: this deletes things,
        and the bi-encoder is loose enough that "everything about the cooler"
        would also catch neighbouring thermal facts. Defaults to dry_run so the
        caller sees what would go before anything goes.
        """
        c = container or self.container
        rows, mat = self.store.matrix(c, "memories")
        if not len(rows):
            return []
        qv = self.models.embed([query], is_query=True)[0]
        sims = mat @ qv
        # Loose floor; the cross-encoder decides. 0.55 keeps ~95% of
        # query-memory pairs under EmbeddingGemma 2, as 0.25 did under Qwen.
        cand_ix = [int(j) for j in np.argsort(-sims)[:60] if sims[int(j)] > 0.55]
        if not cand_ix:
            return []
        scores = self.models.rerank(query, [rows[j]["content"] for j in cand_ix])
        out = []
        for j, s in zip(cand_ix, scores):
            if s >= threshold:
                out.append({"id": rows[j]["id"], "content": rows[j]["content"],
                            "score": round(s, 4)})
        out.sort(key=lambda x: -x["score"])
        if not dry_run:
            for m in out:
                self.store.forget(m["id"])
        return out

    # ============================================================= measurement

    def measure_compression(self, query, container=None, k=8):
        """Context tokens for an answer vs tokens for the whole corpus.

        This is the number that decides whether any of this was worth building.
        """
        c = container or self.container
        docs = self.store.documents(c)
        full = "\n\n".join(d["content"] for d in docs)
        hits = self.search(query, container=c, k=k)
        ctx = self.context(hits)
        full_t = self.models.count_tokens(full)
        ctx_t = self.models.count_tokens(ctx)
        return {
            "corpus_tokens": full_t,
            "context_tokens": ctx_t,
            "reduction_pct": round(100 * (1 - ctx_t / full_t), 2) if full_t else 0,
            "hits": len(hits),
        }
