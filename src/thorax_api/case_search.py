"""Case search matching: EN/VI synonyms, diacritic folding and negation over report text. Pure Python."""

from __future__ import annotations

import re
import unicodedata

# id -> (English label, folded terms)
CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pleural_effusion": ("Pleural effusion", ("pleural effusion", "effusion", "tran dich mang phoi", "tran dich")),
    "pneumothorax": ("Pneumothorax", ("pneumothorax", "tran khi")),
    "consolidation": ("Consolidation", ("consolidation", "opacity", "pneumonia", "dong dac", "viem phoi")),
    "cardiomegaly": ("Cardiomegaly", ("cardiomegaly", "enlarged heart", "bong tim to")),
    "atelectasis": ("Atelectasis", ("atelectasis", "xep phoi")),
    "pulmonary_edema": ("Pulmonary edema", ("pulmonary edema", "pulmonary oedema", "phu phoi")),
    "nodule_mass": ("Nodule / mass", ("nodule", "mass", "khoi u")),
    "fracture": ("Fracture", ("fracture", "gay xuong")),
    "emphysema": ("Emphysema", ("emphysema", "khi phe thung")),
    "fibrosis": ("Fibrosis", ("fibrosis", "xo phoi")),
    "pleural_thickening": ("Pleural thickening", ("pleural thickening", "day mang phoi")),
    "hilar_enlargement": ("Hilar enlargement", ("hilar enlargement", "ron phoi to")),
    "interstitial": ("Interstitial pattern", ("interstitial", "reticular", "linear markings")),
    "support_device": ("Support device", ("tube", "catheter", "pacemaker")),
    "normal": ("Normal", ("no acute", "normal", "binh thuong")),
}

_CUE = re.compile(r"\b(?:no|without|negative for|resolved|khong co|khong thay|khong)\b")
_NOT_A_CUE = ("no change", "no interval change", "khong thay doi")
_SENTENCE = re.compile(r"[^.;\n]+")


def fold(text: str) -> str:
    """Lower-case and strip diacritics char by char, so offsets in the folded text match the original."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0].lower()
        out.append("d" if base in "đĐ" or ch in "đĐ" else base)
    return "".join(out)


def _word(term: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def _negated(folded: str, start: int) -> bool:
    return any(
        m.end() <= start and not folded.startswith(_NOT_A_CUE, m.start())
        for m in _CUE.finditer(folded)
    )


def _sentences(text: str):
    """Yield (stripped original sentence, its folded form)."""
    for m in _SENTENCE.finditer(text):
        s = m.group().strip()
        if s:
            yield s, fold(s)


def _hits(folded: str) -> dict[str, list[list[int]]]:
    """Un-negated spans per concept; where terms overlap the longest one wins."""
    found = [
        (m.start(), m.end(), cid)
        for cid, (_, terms) in CONCEPTS.items()
        for t in terms
        for m in _word(t).finditer(folded)
    ]
    found.sort(key=lambda f: f[0] - f[1])
    taken: list[tuple[int, int, str]] = []
    for s, e, cid in found:
        if all(e <= ts or s >= te for ts, te, _ in taken):
            taken.append((s, e, cid))
    hits: dict[str, list[list[int]]] = {}
    for s, e, cid in sorted(taken):
        if cid == "normal" or not _negated(folded, s):
            hits.setdefault(cid, []).append([s, e])
    return hits


def detect(query: str) -> list[str]:
    ids = {cid for _, f in _sentences(query) for cid in _hits(f)}
    return [cid for cid in CONCEPTS if cid in ids] or ["query"]


def match(text: str, concepts: list[str], query: str) -> dict | None:
    phrase = " ".join(fold(query).split())
    for sentence, folded in _sentences(text):
        hits = _hits(folded)
        if "query" in concepts and phrase:
            hits["query"] = [[m.start(), m.end()] for m in _word(phrase).finditer(folded)]
        matched = [c for c in concepts if hits.get(c)]
        if matched:
            spans = sorted(sp for c in matched for sp in hits[c])
            return {"concepts": matched, "snippet": sentence, "spans": spans}
    return None
