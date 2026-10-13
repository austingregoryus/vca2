# SPDX-License-Identifier: MIT
"""Heading-aware chunking.

Two choices here are worth stating, because both are easy to get wrong in a
way that silently degrades retrieval rather than breaking anything:

1. Chunks are cut at paragraph boundaries inside a heading section, never
   mid-sentence. A chunk that starts halfway through a thought embeds to a
   vector for a thought nobody had.

2. Each chunk is embedded with its heading path prepended ("FABRIC.md > PEX
   8749 > Link training"). A bare paragraph that says "it runs at x8 instead
   of x16" is nearly unretrievable; the same paragraph under its heading path
   is trivially retrievable. The heading text is *not* included in the stored
   chunk body, only in the text handed to the embedder, so the context the
   model reads back is not cluttered with breadcrumbs.
"""

import re

MAX_CHARS = 1400
MIN_CHARS = 220
OVERLAP = 160

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$", re.M)


def _sections(text):
    """-> [(heading_path, body)] following markdown heading nesting."""
    marks = [(m.start(), m.end(), len(m.group(1)), m.group(2))
             for m in _HEADING.finditer(text)]
    if not marks:
        return [("", text)]
    out = []
    pre = text[: marks[0][0]].strip()
    if pre:
        out.append(("", pre))
    stack = []
    for i, (_, hend, level, title) in enumerate(marks):
        stack = stack[: level - 1]
        while len(stack) < level - 1:
            stack.append("")
        stack.append(title)
        body_end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[hend:body_end].strip()
        path = " > ".join(p for p in stack if p)
        if body:
            out.append((path, body))
        elif i + 1 < len(marks) and marks[i + 1][2] <= level:
            # A heading with no body and no deeper heading under it still
            # carries information (it is a label for something absent).
            out.append((path, title))
    return out


def _split_body(body):
    """Pack paragraphs up to MAX_CHARS, splitting oversized ones on sentences."""
    paras = []
    for p in re.split(r"\n\s*\n", body):
        p = p.strip()
        if not p:
            continue
        if len(p) <= MAX_CHARS:
            paras.append(p)
            continue
        # Long block (a table, a code fence, a wall of prose): cut on sentence
        # ends where possible, hard-cut only if a single "sentence" is huge.
        buf = ""
        for s in re.split(r"(?<=[.!?;:])\s+|\n", p):
            if len(buf) + len(s) + 1 > MAX_CHARS and buf:
                paras.append(buf.strip())
                buf = ""
            while len(s) > MAX_CHARS:
                paras.append(s[:MAX_CHARS])
                s = s[MAX_CHARS:]
            buf += s + " "
        if buf.strip():
            paras.append(buf.strip())
    out, buf = [], ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > MAX_CHARS:
            out.append(buf.strip())
            # Carry a little tail forward so a fact split across the seam is
            # still retrievable from at least one of the two chunks -- but
            # only when the next paragraph leaves room for it. Adding the tail
            # unconditionally pushed full-size chunks to MAX_CHARS + OVERLAP,
            # which is over the embedder's budget rather than under it.
            tail = buf[-OVERLAP:] if len(buf) > OVERLAP else ""
            buf = (tail + "\n\n") if len(tail) + len(p) + 2 <= MAX_CHARS else ""
        buf += p + "\n\n"
    if buf.strip():
        out.append(buf.strip())
    return out


def chunk(text, title=None):
    """-> [{heading, text, embed_text}]"""
    out = []
    for path, body in _sections(text):
        full = " > ".join(x for x in (title, path) if x)
        for piece in _split_body(body):
            if len(piece) < MIN_CHARS and out and out[-1]["heading"] == full:
                # Too small to stand alone and it belongs to the chunk before
                # it -- merging beats emitting a vector for one sentence.
                out[-1]["text"] += "\n\n" + piece
                out[-1]["embed_text"] = (full + "\n\n" + out[-1]["text"]).strip()
                continue
            out.append({
                "heading": full or None,
                "text": piece,
                "embed_text": (full + "\n\n" + piece).strip() if full else piece,
            })
    return out
