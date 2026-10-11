"""BM25 retrieval with identifier boosting and a second hop.

Pure standard library so the per-question path stays fast and dependency-free.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# identifiers that connect files: ORR-1847, E7731, TQ-40, REV-C2, 4.3.2, Q3 FY27
ID_RE = re.compile(
    r"\b(?:[A-Z]{1,6}-?\d{2,6}(?:-[A-Z0-9]+)*|[A-Z]\d{3,6}|\d+\.\d+(?:\.\d+)+|Q[1-4]\s?FY\d{2,4})\b"
)

STOP = set(
    """a an the of in on at to for from by with and or is are was were be been
    what which who when where how does do did it its this that these those as
    into under over per than then there here you your we our they their
    ingest service version number default"""
    .split()
)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    # keep hyphenated identifiers together AND as parts
    toks = re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", text)
    out: list[str] = []
    for t in toks:
        if t in STOP:
            continue
        out.append(t)
        if any(ch in t for ch in "-_."):
            out.extend(p for p in re.split(r"[-_.]", t) if p and p not in STOP and len(p) > 1)
    return out


def identifiers(text: str) -> set[str]:
    return {m.group(0).upper().replace(" ", "") for m in ID_RE.finditer(text)}


class BM25Index:
    def __init__(self, chunks: list[dict], k1: float = 1.4, b: float = 0.6):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tok = [tokenize(c["text"] + " " + c["file"].replace("/", " ")) for c in chunks]
        self.tf = [Counter(t) for t in self.tok]
        self.avgdl = sum(len(t) for t in self.tok) / max(1, len(self.tok))
        df: Counter = Counter()
        for t in self.tok:
            df.update(set(t))
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.ids = [identifiers(c["text"]) for c in chunks]

    def score(self, query: str, boost_ids: set[str] | None = None) -> list[tuple[float, int]]:
        q = tokenize(query)
        qids = identifiers(query) | (boost_ids or set())
        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self.tf):
            dl = len(self.tok[i])
            s = 0.0
            for term in q:
                f = tf.get(term)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            # exact identifier match is a strong signal: it is how files chain
            hit = qids & self.ids[i]
            if hit:
                s += 6.0 * len(hit)
            # superseded / withdrawn documents lose priority
            low = self.chunks[i]["text"][:400].lower() + self.chunks[i]["file"].lower()
            if "withdrawn" in low or "superseded by" in low or "deprecated" in low:
                s *= 0.55
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: -x[0])
        return scored

    def search(self, query: str, k: int = 8) -> list[dict]:
        hits = self.score(query)[:k]
        return [dict(self.chunks[i], score=round(s, 2)) for s, i in hits]

    def second_hop(self, query: str, first: list[dict], k: int = 4) -> list[dict]:
        """Follow identifiers found in the first hop that the query did not mention."""
        qids = identifiers(query)
        # how many files mention each identifier: product names appear everywhere
        # and must not drive a hop; ticket ids and codes appear in one or two files
        files_per_id: dict[str, set[str]] = {}
        for ids, c in zip(self.ids, self.chunks):
            for i in ids:
                files_per_id.setdefault(i, set()).add(c["file"])
        new_ids: set[str] = set()
        for c in first[:3]:
            for i in identifiers(c["text"]) - qids:
                if len(files_per_id.get(i, ())) <= 3:
                    new_ids.add(i)
        if not new_ids:
            return []
        seen = {c["cid"] for c in first}
        out: list[dict] = []
        for s, i in self.score(" ".join(new_ids), boost_ids=new_ids):
            c = self.chunks[i]
            if c["cid"] in seen or c["file"] in {x["file"] for x in first[:1]}:
                continue
            if not (identifiers(c["text"]) & new_ids):
                continue
            out.append(dict(c, score=round(s, 2), hop=2))
            if len(out) >= k:
                break
        return out
