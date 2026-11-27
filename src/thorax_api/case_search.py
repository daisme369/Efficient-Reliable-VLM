"""Case search matching: EN/VI synonyms, diacritic folding and negation over report text. Pure Python."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from fastapi import Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .db import connect, utc_now
from .reading import included

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
    "normal": ("Normal", ("no acute", "normal chest", "binh thuong")),
}

_CUE = re.compile(r"\b(?:no|without|negative for|resolved|khong co|khong thay|khong)\b")
_NOT_A_CUE = re.compile(r"no (?:significant )?(?:interval )?change|khong thay doi")
_POST_CUE = re.compile(r"\b(?:has resolved|resolved|is not seen|not seen|absent|no longer|khong con|da het)\b")
_STOP = re.compile(r"\b(?:but|however|although|nhung|tuy nhien)\b")
_SENTENCE = re.compile(r"(?:[^.;\n]|(?<=\d)\.(?=\d))+")  # a "." between digits is a decimal point


def fold(text: str) -> str:
    """Lower-case and strip diacritics char by char, so offsets in the folded text match the original."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0].lower()
        out.append("d" if base in "đĐ" or ch in "đĐ" else base)
    return "".join(out)


def _word(term: str) -> re.Pattern:
    stem, plural = (re.escape(term[:-1]) + "(?:y|ies)", "") if term.endswith("y") else (re.escape(term), "(?:e?s)?")
    return re.compile(r"(?<![a-z0-9])" + stem + plural + r"(?![a-z0-9])")


def _negated(folded: str, start: int, end: int) -> bool:
    before = any(
        m.end() <= start
        and not _NOT_A_CUE.match(folded, m.start())
        and not _STOP.search(folded, m.end(), start)  # "but"/"however" ends the negation's scope
        for m in _CUE.finditer(folded)
    )
    clause = _STOP.split(folded[end:], maxsplit=1)[0]  # a post-cue reaches back across commas, only but/however end it
    return before or bool(_POST_CUE.search(clause))


def _sentences(text: str):
    """Yield (stripped original sentence, its folded form)."""
    for m in _SENTENCE.finditer(unicodedata.normalize("NFC", text)):
        s = m.group().strip()
        if s:
            yield s, fold(s)


def _hits(folded: str) -> tuple[dict[str, list[list[int]]], bool]:
    """Un-negated spans per concept (where terms overlap the longest wins), and whether any term appeared at all."""
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
        if cid == "normal" or not _negated(folded, s, e):
            hits.setdefault(cid, []).append([s, e])
    return hits, bool(taken)


def detect(query: str) -> list[str]:
    ids, seen = set(), False
    for _, f in _sentences(query):
        hits, any_term = _hits(f)
        ids |= hits.keys()
        seen |= any_term
    # a fully negated query searches nothing; the literal fallback is only for queries with no concept term
    if len(ids) > 1:
        ids.discard("normal")  # "heart size normal" must not pull normal studies into a finding search
    return [cid for cid in CONCEPTS if cid in ids] if seen else ["query"]


def match(text: str, concepts: list[str], query: str) -> dict | None:
    phrase = " ".join(fold(unicodedata.normalize("NFC", query)).split())
    for sentence, folded in _sentences(text):
        hits, _ = _hits(folded)
        if "query" in concepts and phrase:
            hits["query"] = [[m.start(), m.end()] for m in _word(phrase).finditer(folded) if not _negated(folded, m.start(), m.end())]
        matched = [c for c in concepts if hits.get(c)]
        if matched:
            spans = sorted(sp for c in matched for sp in hits[c])
            return {"concepts": matched, "snippet": sentence, "spans": spans}
    return None


# --- the index: signed readings and history reports, scanned in Python ---------------------------

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS case_views (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users (id),
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        created_at TEXT NOT NULL
    )""",
)
LIMIT = 20


def _text(lines: list[str], content: dict) -> str:
    """A reading as one report text: the signed finding lines, then the Impression."""
    return "\n".join([*lines, f"Impression: {content.get('impression', '')}"])


class CaseIndex:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)

    @staticmethod
    def _finals(db) -> list[dict]:
        """The latest final report of each study that has one, newest signed first."""
        rows = db.execute(
            """SELECT r.id AS report_id, r.content, s.id AS session_id, st.id AS study_id, st.study_date, st.patient_id,
                      p.code, p.name, p.birth_year, p.sex,
                      (SELECT v.items FROM findings_versions v WHERE v.session_id = s.id AND v.status = 'approved'
                       ORDER BY v.version DESC LIMIT 1) AS items
               FROM reports r JOIN reading_sessions s ON s.id = r.session_id AND s.status = 'final'
                    JOIN studies st ON st.id = s.study_id JOIN patients p ON p.id = st.patient_id
               WHERE r.status = 'final' ORDER BY r.approved_at DESC, r.id DESC"""
        ).fetchall()
        seen, out = set(), []
        for row in rows:
            if row["study_id"] in seen:
                continue
            seen.add(row["study_id"])
            content = json.loads(row["content"])
            # the signed report's findings; the included items only when the report has none
            lines = [x.strip() for x in (content.get("findings") or "").splitlines() if x.strip()] \
                or [i["text"] for i in json.loads(row["items"] or "[]") if included(i)]
            out.append({**dict(row), "content": content, "lines": lines})
        return out

    def search(self, actor_id: int, study: dict, q: str) -> dict:
        q = " ".join(q.split())
        concepts = detect(q) if q else []
        if q and not concepts:  # every concept in the query is negated: nothing to find
            return {"concepts": [], "prior": [], "cases": []}
        shown = [{"id": c, "label": q if c == "query" else CONCEPTS[c][0]} for c in concepts]
        # ponytail: scans every final report in Python; fine up to a few thousand, then use PostgreSQL full-text or embeddings.
        with connect(self.path) as db:
            finals = self._finals(db)
            history = db.execute("SELECT report_date, text FROM history_reports WHERE patient_id = ? AND report_date < ?",
                                 (study["patient_id"], study["study_date"])).fetchall()
            assigned = {r[0] for r in db.execute("SELECT patient_id FROM assignments WHERE doctor_id = ? AND active = 1", (actor_id,))}
        prior = [{"source": "history", "date": h["report_date"], "text": h["text"], "study_id": None} for h in history]
        prior += [{"source": "reading", "date": f["study_date"], "text": _text(f["lines"], f["content"]), "study_id": f["study_id"]}
                  for f in finals if f["patient_id"] == study["patient_id"] and f["study_date"] < study["study_date"]]
        prior.sort(key=lambda p: p["date"], reverse=True)
        for p in prior:
            hit = match(p["text"], concepts, q) if q else None
            p.update(snippet=hit and hit["snippet"], spans=hit["spans"] if hit else [])
        if q:
            prior = [p for p in prior if p["snippet"]]
        cases = []
        for f in finals if q else []:
            if f["patient_id"] == study["patient_id"]:
                continue
            text = _text(f["lines"], f["content"])
            if not (hit := match(text, concepts, q)):
                continue
            decade = (int(f["study_date"][:4]) - f["birth_year"]) // 10 * 10 if f["birth_year"] else None
            case = {"case_id": f["session_id"], "study_month": f["study_date"][:7], "age_band": decade and f"{decade}–{decade + 9}",
                    "sex": f["sex"], "findings": f["lines"], "impression": f["content"].get("impression", ""), **hit}
            if f["patient_id"] in assigned:
                case.update(patient={"id": f["patient_id"], "code": f["code"], "name": f["name"]}, study_date=f["study_date"])
            cases.append((case, f["study_date"]))
        cases.sort(key=lambda c: c[1], reverse=True)
        cases.sort(key=lambda c: len(c[0]["concepts"]), reverse=True)  # stable: newest first within equal counts
        with connect(self.path) as db:
            out = []
            for case, _ in cases[:LIMIT]:
                image = self._image(db, case["case_id"])
                out.append({**case, "image": {k: image[k] for k in ("view", "width", "height", "media_type")}})
        return {"concepts": shown, "prior": prior, "cases": out}

    @staticmethod
    def _image(db, session_id: int) -> dict | None:
        s = db.execute("SELECT study_id, analyzed_image_id FROM reading_sessions WHERE id = ? AND status = 'final'", (session_id,)).fetchone()
        if s is None:
            return None
        row = db.execute(
            """SELECT * FROM images WHERE study_id = ?
               ORDER BY CASE WHEN id = ? THEN 0 WHEN view IN ('PA', 'AP') THEN 1 ELSE 2 END, id LIMIT 1""",
            (s["study_id"], s["analyzed_image_id"])).fetchone()
        return dict(row) if row else None

    def case_image(self, session_id: int) -> dict | None:
        """The image row (as catalog.image returns it) for a signed reading; None if the reading is not final."""
        with connect(self.path) as db:
            return self._image(db, session_id)

    def record_view(self, user_id: int, session_id: int) -> None:
        with connect(self.path) as db:
            db.execute("INSERT INTO case_views (user_id, session_id, created_at) VALUES (?, ?, ?)", (user_id, session_id, utc_now()))


def register_case_search(app, *, index: CaseIndex, catalog, actor, path_id, no_store, invalid, missing, error) -> None:
    @app.get("/api/v1/studies/{study_id}/case-search")
    async def case_search(request: Request, study_id: str):
        user = actor(request, "doctor", "annotator")
        study = catalog.study(path_id(study_id), user.id)
        if study is None or not catalog.is_assigned(user.id, study["patient_id"]):
            raise missing()
        q = request.query_params.get("q", "").strip()
        if len(q) > 500:
            raise invalid("Search must be at most 500 characters.")
        return no_store(JSONResponse(await run_in_threadpool(index.search, user.id, study, q)))

    @app.get("/api/v1/cases/{case_id}/image")
    async def case_image(request: Request, case_id: str):
        user = actor(request, "doctor", "annotator")
        image = index.case_image(path_id(case_id))
        if image is None:
            raise missing()
        try:
            data = await run_in_threadpool(catalog.image_bytes, image)
        except (OSError, ValueError):
            raise error(503, "ASSET_UNAVAILABLE", "The study image is unavailable. Try again.") from None
        await run_in_threadpool(index.record_view, user.id, int(case_id))
        return no_store(Response(data, media_type=image["media_type"]))
