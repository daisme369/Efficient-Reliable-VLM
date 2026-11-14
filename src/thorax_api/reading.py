"""Doctor reading workflow: sessions, findings, regions, chat, reports, audit events and AI jobs.

A port of the web demo's mock API (src/thorax-web/lib/server/mock-clinical.ts) onto SQLite, so the
frontend works unchanged against this backend. Each request loads one reading, applies the same rules
as the mock, and writes it back inside a single IMMEDIATE transaction; the reading's revision (sent as
If-Match) rejects stale writes.

AI is the mock's fixed demo output for now ("provider_mode": "mock"). Jobs settle by elapsed time when
a reading is next read, like the mock. Replacing them with calls to scripts/serve_vlm.py is a later step.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from .db import connect

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS id_sequence (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS reading_sessions (
        id INTEGER PRIMARY KEY,
        doctor_id INTEGER NOT NULL REFERENCES users (id),
        patient_id INTEGER NOT NULL REFERENCES patients (id),
        study_id INTEGER NOT NULL REFERENCES studies (id),
        status TEXT NOT NULL CHECK (status IN ('active', 'findings_approved', 'final')),
        revision INTEGER NOT NULL,
        analyzed_image_id INTEGER REFERENCES images (id),
        UNIQUE (doctor_id, study_id)
    )""",
    """CREATE TABLE IF NOT EXISTS findings_versions (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        items TEXT NOT NULL,
        ai_items TEXT NOT NULL,
        no_abnormality_confirmed INTEGER NOT NULL,
        annotation_snapshot TEXT,
        approved_by INTEGER REFERENCES users (id),
        approved_at TEXT,
        UNIQUE (session_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS regions (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        image_id INTEGER NOT NULL REFERENCES images (id),
        x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
        label TEXT, note TEXT,
        source TEXT NOT NULL,
        review_action TEXT NOT NULL,
        revision INTEGER NOT NULL,
        deleted_at TEXT,
        ai_request_id INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        image_id INTEGER NOT NULL REFERENCES images (id),
        box_id INTEGER,
        box_snapshot TEXT,
        model_version TEXT,
        latency_ms INTEGER,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        findings_version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('draft', 'stale', 'final')),
        content TEXT NOT NULL,
        approved_by INTEGER REFERENCES users (id),
        approved_at TEXT,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS reading_events (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        user_id INTEGER NOT NULL REFERENCES users (id),
        type TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ai_jobs (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES reading_sessions (id),
        status TEXT NOT NULL,
        request TEXT NOT NULL,
        context TEXT NOT NULL,
        submitted_at REAL NOT NULL,
        failure TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS idempotency_receipts (
        scope TEXT PRIMARY KEY,
        body_hash TEXT NOT NULL,
        status INTEGER NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
)

MODEL_ARTIFACTS = [{"id": name, "version": "fixture-v1", "sha256": hashlib.sha256(f"{name}:fixture-v1".encode()).hexdigest()}
                   for name in ("mock-vlm", "mock-cv")]
ANALYZE_MS, RUNNING_MS, DEADLINE_MS = 1400, 250, 60_000
NARRATIVE = ("comparison", "findings", "impression", "recommendation")


class ReadingError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def fail(status: int, code: str, message: str):
    raise ReadingError(status, code, message)


def invalid(message: str):
    fail(422, "VALIDATION_ERROR", message)


def missing():
    fail(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fields(body: dict, keys: list[str]) -> None:
    if set(body) - set(keys):
        invalid("Unexpected request field.")


def integer(value, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 2**53 - 1:
        invalid(f"Invalid {label}.")
    return value


def string(value, label: str, low: int, high: int) -> str:
    if not isinstance(value, str) or len(value.strip()) < low or len(value) > high:
        invalid(f"{label} must be {low}–{high} characters.")
    return value


def nullable_string(value, label: str, high: int):
    return None if value is None else string(value, label, 0, high)


def included(item: dict) -> bool:
    return item["action"] in ("accepted", "edited", "added")


def active_box(box: dict) -> bool:
    return not box["deleted_at"] and box["review_action"] != "rejected"


def box_snapshot(box: dict) -> dict:
    return {"box_id": box["id"], "revision": box["revision"], "image_id": box["image_id"], "bbox_xyxy": list(box["bbox_xyxy"])}


class ReadingStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float], secret: bytes, is_allowed: Callable[[int, int], bool],
                 ai_paused: Callable[[], bool], record_ai: Callable[..., None]):
        self.path, self.clock, self.secret = str(path), clock, secret
        self.is_allowed, self.ai_paused, self.record_ai = is_allowed, ai_paused, record_ai
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("INSERT OR IGNORE INTO id_sequence (name, value) VALUES ('global', 10000)")

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # --- loading and saving one reading ----------------------------------------------------
    @staticmethod
    def _next_id(db) -> int:
        db.execute("UPDATE id_sequence SET value = value + 1 WHERE name = 'global'")
        return db.execute("SELECT value FROM id_sequence WHERE name = 'global'").fetchone()[0]

    def _load(self, db, session_id: int) -> dict:
        row = db.execute("SELECT * FROM reading_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            missing()
        reading = {"session": {k: row[k] for k in ("id", "doctor_id", "patient_id", "study_id", "status", "revision")},
                   "analyzed_image_id": row["analyzed_image_id"], "new_events": []}
        reading["findings"] = [{
            "id": r["id"], "version": r["version"], "status": r["status"], "items": json.loads(r["items"]), "ai_items": json.loads(r["ai_items"]),
            "no_abnormality_confirmed": bool(r["no_abnormality_confirmed"]),
            "annotation_snapshot": json.loads(r["annotation_snapshot"]) if r["annotation_snapshot"] else None,
            "approved_by": r["approved_by"], "approved_at": r["approved_at"],
        } for r in db.execute("SELECT * FROM findings_versions WHERE session_id = ? ORDER BY version", (session_id,))]
        reading["boxes"] = [{
            "id": r["id"], "image_id": r["image_id"], "bbox_xyxy": [r["x1"], r["y1"], r["x2"], r["y2"]], "label": r["label"], "note": r["note"],
            "source": r["source"], "review_action": r["review_action"], "revision": r["revision"], "deleted_at": r["deleted_at"],
            "ai_request_id": r["ai_request_id"],
        } for r in db.execute("SELECT * FROM regions WHERE session_id = ? ORDER BY id", (session_id,))]
        reading["messages"] = [{
            "id": r["id"], "role": r["role"], "text": r["text"], "image_id": r["image_id"], "box_id": r["box_id"],
            "box_snapshot": json.loads(r["box_snapshot"]) if r["box_snapshot"] else None, "model_version": r["model_version"],
            "latency_ms": r["latency_ms"], "created_at": r["created_at"],
        } for r in db.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,))]
        reading["reports"] = [{
            "id": r["id"], "findings_version": r["findings_version"], "status": r["status"], "content": json.loads(r["content"]),
            "approved_by": r["approved_by"], "approved_at": r["approved_at"], "updated_at": r["updated_at"],
        } for r in db.execute("SELECT * FROM reports WHERE session_id = ? ORDER BY id", (session_id,))]
        reading["jobs"] = [{"request": json.loads(r["request"]), "context": r["context"], "submitted_at": r["submitted_at"], "failure": r["failure"]}
                           for r in db.execute("SELECT * FROM ai_jobs WHERE session_id = ? ORDER BY id", (session_id,))]
        return reading

    def _save(self, db, reading: dict) -> None:
        # ponytail: rewrites the reading's child rows on every change; fine at demo scale (a reading has tens of rows).
        sid, s = reading["session"]["id"], reading["session"]
        db.execute("UPDATE reading_sessions SET status = ?, revision = ?, analyzed_image_id = ? WHERE id = ?",
                   (s["status"], s["revision"], reading["analyzed_image_id"], sid))
        for table in ("findings_versions", "regions", "messages", "reports", "ai_jobs"):
            db.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
        db.executemany("""INSERT INTO findings_versions (id, session_id, version, status, items, ai_items, no_abnormality_confirmed,
                          annotation_snapshot, approved_by, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       [(f["id"], sid, f["version"], f["status"], json.dumps(f["items"]), json.dumps(f["ai_items"]), int(f["no_abnormality_confirmed"]),
                         json.dumps(f["annotation_snapshot"]) if f["annotation_snapshot"] is not None else None, f["approved_by"], f["approved_at"])
                        for f in reading["findings"]])
        db.executemany("""INSERT INTO regions (id, session_id, image_id, x1, y1, x2, y2, label, note, source, review_action, revision,
                          deleted_at, ai_request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       [(b["id"], sid, b["image_id"], *b["bbox_xyxy"], b["label"], b["note"], b["source"], b["review_action"], b["revision"],
                         b["deleted_at"], b["ai_request_id"]) for b in reading["boxes"]])
        db.executemany("""INSERT INTO messages (id, session_id, role, text, image_id, box_id, box_snapshot, model_version, latency_ms, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       [(m["id"], sid, m["role"], m["text"], m["image_id"], m["box_id"], json.dumps(m["box_snapshot"]) if m["box_snapshot"] else None,
                         m["model_version"], m["latency_ms"], m["created_at"]) for m in reading["messages"]])
        db.executemany("""INSERT INTO reports (id, session_id, findings_version, status, content, approved_by, approved_at, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       [(r["id"], sid, r["findings_version"], r["status"], json.dumps(r["content"]), r["approved_by"], r["approved_at"], r["updated_at"])
                        for r in reading["reports"]])
        db.executemany("INSERT INTO ai_jobs (id, session_id, status, request, context, submitted_at, failure) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       [(j["request"]["request_id"], sid, j["request"]["status"], json.dumps(j["request"]), j["context"], j["submitted_at"], j["failure"])
                        for j in reading["jobs"]])
        db.executemany("INSERT INTO reading_events (id, session_id, user_id, type, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       [(self._next_id(db), sid, s["doctor_id"], kind, json.dumps(data), at) for kind, data, at in reading["new_events"]])
        reading["new_events"] = []

    def _event(self, reading: dict, kind: str, data: dict) -> None:
        reading["new_events"].append((kind, data, self.now_iso()))

    def _record_edit(self, reading: dict, kind: str, data: dict) -> None:
        reading["session"]["revision"] += 1
        self._event(reading, kind, data)

    # --- catalog lookups -------------------------------------------------------------------
    @staticmethod
    def _study(db, study_id: int) -> dict:
        row = db.execute("SELECT * FROM studies WHERE id = ?", (study_id,)).fetchone()
        if row is None:
            missing()
        images = [{**dict(r), "content_url": f"/api/v1/images/{r['id']}/content"} for r in db.execute(
            "SELECT id, study_id, view, width, height, sha256, media_type FROM images WHERE study_id = ? ORDER BY id", (study_id,))]
        return {**dict(row), "images": images}

    @staticmethod
    def _patient(db, patient_id: int) -> dict:
        return dict(db.execute("SELECT id, code, name, birth_year, sex FROM patients WHERE id = ?", (patient_id,)).fetchone())

    @staticmethod
    def _history(db, study: dict) -> list[dict]:
        rows = db.execute("SELECT id, report_date, text FROM history_reports WHERE patient_id = ? AND report_date < ? ORDER BY id LIMIT 5",
                          (study["patient_id"], study["study_date"])).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def study_states(db, study_ids: list[int], actor_id: int) -> dict[int, dict]:
        """Worklist status per study: final if any reading is final, in progress if any reading exists."""
        result = {}
        for sid in study_ids:
            rows = db.execute("SELECT id, doctor_id, status FROM reading_sessions WHERE study_id = ?", (sid,)).fetchall()
            own = next((r for r in rows if r["doctor_id"] == actor_id), None)
            result[sid] = {"status": "final" if any(r["status"] == "final" for r in rows) else "in_progress" if rows else "new",
                           "own_session_id": own["id"] if own else None, "own_session_status": own["status"] if own else None}
        return result

    def _study_dto(self, db, study: dict, actor_id: int) -> dict:
        return {"id": study["id"], "patient_id": study["patient_id"], "study_date": study["study_date"], "indication": study["indication"],
                "image_count": len(study["images"]), **self.study_states(db, [study["id"]], actor_id)[study["id"]]}

    @staticmethod
    def _image(study: dict, image_id: int) -> dict:
        return next((i for i in study["images"] if i["id"] == image_id), None) or missing()

    # --- gates and workspace ---------------------------------------------------------------
    @staticmethod
    def _blocker(gate, code, resource_type, message, resource_id=None):
        return {"gate": gate, "code": code, "resource_type": resource_type, "resource_id": resource_id, "message": message}

    def _gate1(self, reading: dict) -> list[dict]:
        findings = reading["findings"][-1] if reading["findings"] else None
        if findings and findings["status"] == "approved":
            return []
        if not findings:
            return [self._blocker("gate1", "ANALYSIS_REQUIRED", "findings", "Run the initial analysis.")]
        pending = []
        if findings["status"] != "in_review":
            pending.append(self._blocker("gate1", "SAVE_REQUIRED", "findings", "Save findings before approval."))
        for item in findings["items"]:
            if item["action"] == "pending":
                pending.append(self._blocker("gate1", "UNRESOLVED_REVIEW", "finding", "Review this AI finding.", item["id"]))
            if included(item) and any(not any(b["id"] == i and active_box(b) for b in reading["boxes"]) for i in item["box_ids"]):
                pending.append(self._blocker("gate1", "INVALID_EVIDENCE", "finding", "Remove the unavailable region link.", item["id"]))
        for box in reading["boxes"]:
            if box["review_action"] == "pending" and active_box(box):
                pending.append(self._blocker("gate1", "UNRESOLVED_REVIEW", "box", "Review this AI region.", box["id"]))
        if not any(included(i) for i in findings["items"]) and not findings["no_abnormality_confirmed"]:
            pending.append(self._blocker("gate1", "NORMAL_CONFIRMATION_REQUIRED", "findings", "Include a finding or explicitly confirm no abnormal findings."))
        return pending

    def _gate2(self, reading: dict) -> list[dict]:
        status = reading["session"]["status"]
        if status == "final":
            return []
        if status != "findings_approved":
            return [self._blocker("gate2", "GATE1_REQUIRED", "findings", "Approve the latest findings before drafting a report.")]
        report = next((r for r in reading["reports"] if r["status"] == "draft"), None)
        if report is None:
            return [self._blocker("gate2", "DRAFT_REQUIRED", "report", "Generate a draft report.")]
        if not report["content"]["findings"].strip() or not report["content"]["impression"].strip():
            return [self._blocker("gate2", "REQUIRED_FIELDS", "report", "Save a findings section and an impression before signing.", report["id"])]
        return []

    @staticmethod
    def _active_job(reading: dict):
        return next((j for j in reading["jobs"] if j["request"]["status"] in ("queued", "running")), None)

    def _workspace(self, db, reading: dict, messages_page: dict) -> dict:
        study = self._study(db, reading["session"]["study_id"])
        live = self._active_job(reading)
        initial = next((j["request"] for j in reading["jobs"] if j["request"]["operation"] == "analyze" and j["request"]["status"] == "succeeded"), None)
        return {
            "session": reading["session"], "patient": self._patient(db, reading["session"]["patient_id"]),
            "study": self._study_dto(db, study, reading["session"]["doctor_id"]), "images": study["images"], "history": self._history(db, study),
            "latest_findings": reading["findings"][-1] if reading["findings"] else None, "reports": reading["reports"], "boxes": reading["boxes"],
            "messages": messages_page, "live_request_id": live["request"]["request_id"] if live else None,
            "latest_ai_request": live["request"] if live else (reading["jobs"][-1]["request"] if reading["jobs"] else None),
            "initial_analysis": initial, "blockers": self._gate1(reading) + self._gate2(reading),
            "capabilities": {"provider_mode": "mock", "can_detect": True, "can_ask_roi": True, "model_artifacts": MODEL_ARTIFACTS},
        }

    # --- paging (signed offset cursors, like the mock) ----------------------------------------
    def _signed(self, scope: str, index: int) -> str:
        return f"{index}." + hmac.new(self.secret, f"cursor:{scope}:{index}".encode(), hashlib.sha256).hexdigest()

    def _start(self, scope: str, cursor: str | None, default: int) -> int:
        if not cursor:
            return default
        head = cursor.split(".")[0]
        if not head.isdigit() or not hmac.compare_digest(cursor, self._signed(scope, int(head))):
            invalid("Invalid page cursor.")
        return int(head)

    @staticmethod
    def _limit(raw: str | None, default: int, maximum: int) -> int:
        limit = default if raw is None else (int(raw) if raw.isdigit() else 0)
        if limit < 1:
            invalid("Invalid page limit.")
        if limit > maximum:
            invalid("The page size is too large.")
        return limit

    def messages_page(self, reading: dict, actor_id: int, cursor: str | None, raw_limit: str | None) -> dict:
        items = reading["messages"]
        scope = f"{actor_id}:/api/v1/sessions/{reading['session']['id']}/messages:"
        end = self._start(scope, cursor, len(items))
        start = max(0, end - self._limit(raw_limit, 50, 50))
        return {"items": items[start:end], "next_cursor": self._signed(scope, start) if start else None}

    # --- AI jobs ---------------------------------------------------------------------------
    def _settle(self, db, reading: dict) -> bool:
        """Advance the running AI job by elapsed time. Returns whether anything changed."""
        job = self._active_job(reading)
        if job is None:
            return False
        now_ms = self.clock() * 1000
        request, elapsed = job["request"], now_ms - job["submitted_at"]
        data = request["submitted_input"]
        session = reading["session"]
        if not self.is_allowed(session["doctor_id"], session["patient_id"]):
            request["status"] = "cancelled"
            request["error"] = {"code": "AUTHORIZATION_REVOKED", "message": "Access to this study was revoked.", "retryable": False}
        elif now_ms >= job["submitted_at"] + DEADLINE_MS:
            request["status"] = "timed_out"
            request["error"] = {"code": "AI_DEADLINE_EXCEEDED", "message": "The AI request reached its time limit.", "retryable": True}
        elif job["failure"] != "timeout" and elapsed >= ANALYZE_MS:
            if job["failure"] == "detector":
                request["status"] = "failed"
                request["error"] = {"code": "AI_UNAVAILABLE", "message": "The mock detector did not finish. No suggestions were saved.", "retryable": True}
            else:
                if data["operation"] == "analyze":
                    request["output_refs"] = self._complete_analysis(db, reading, request, data["image_id"])
                elif data["operation"] == "ask":
                    request["output_refs"] = self._complete_answer(db, reading, request, data)
                else:
                    request["output_refs"] = {"report_id": self._draft(db, reading)}
                request["status"] = "succeeded"
        else:
            if elapsed >= RUNNING_MS and request["status"] == "queued":
                request["status"] = "running"
                request["provenance"]["started_at"] = self._iso_ms(job["submitted_at"] + RUNNING_MS)
                return True
            return False
        request["provenance"]["started_at"] = request["provenance"].get("started_at") or self._iso_ms(job["submitted_at"] + RUNNING_MS)
        request["provenance"]["completed_at"] = self.now_iso()
        latency = ANALYZE_MS if request["status"] == "succeeded" else round(elapsed)
        request["provenance"]["latency_ms"] = latency
        self._record_edit(reading, f"ai_request_{request['status']}", {
            "request_id": request["request_id"], "operation": request["operation"], "provider_mode": "mock",
            **({"image_id": data["image_id"]} if "image_id" in data else {}), "model_version": MODEL_ARTIFACTS[0]["version"],
            "output_refs": request["output_refs"], "error_code": (request["error"] or {}).get("code"),
        })
        request["session_revision"] = session["revision"]
        self.record_ai(session["doctor_id"], request["operation"], request["status"], model_version=MODEL_ARTIFACTS[0]["version"],
                       error_code=(request["error"] or {}).get("code"), latency_ms=latency, db=db)
        return True

    @staticmethod
    def _iso_ms(ms: float) -> str:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _complete_analysis(self, db, reading: dict, request: dict, image_id: int) -> dict:
        image = self._image(self._study(db, reading["session"]["study_id"]), image_id)
        boxes, suggestions = [], []
        if image["view"] == "PA":
            boxes.append({"id": self._next_id(db), "image_id": image_id, "bbox_xyxy": [501.25, 542.5, 663.75, 685], "label": "Left basilar region",
                          "note": None, "source": "ai", "review_action": "pending", "revision": 1, "deleted_at": None, "ai_request_id": request["request_id"]})
        if image["view"] != "LAT":
            rid = request["request_id"]
            suggestions = [
                {"id": f"ai-{rid}-1", "text": "Possible mild left basilar opacity.", "location": "Left lower zone", "image_id": image_id,
                 "source": "ai", "action": "pending", "box_ids": [b["id"] for b in boxes]},
                {"id": f"ai-{rid}-2", "text": "Cardiomediastinal silhouette appears mildly prominent.", "location": "Mediastinum", "image_id": image_id,
                 "source": "ai", "action": "pending", "box_ids": []},
            ]
        findings = {"id": self._next_id(db), "version": 1, "status": "ai_proposed", "items": suggestions, "ai_items": copy.deepcopy(suggestions),
                    "no_abnormality_confirmed": False, "annotation_snapshot": None, "approved_by": None, "approved_at": None}
        reading["analyzed_image_id"] = image_id
        reading["findings"].append(findings)
        reading["boxes"].extend(boxes)
        return {"findings_version": 1, "box_ids": [b["id"] for b in boxes]}

    def _complete_answer(self, db, reading: dict, request: dict, data: dict) -> dict:
        snapshot = request["provenance"].get("box_snapshot")
        image = self._image(self._study(db, reading["session"]["study_id"]), data["image_id"])
        answer = ("Mock response for the selected region. The synthetic crop may contain overlapping lung and rib markings. Review the original image and the saved region before drawing a conclusion."
                  if snapshot else "Mock response for the lateral view. The fixture supplies no additional observation for this image." if image["view"] == "LAT"
                  else "Mock response for the selected frontal image. The fixture marks a possible left basilar opacity. Review the image and current findings to determine whether it should be included.")
        context = {"image_id": data["image_id"], "box_id": data.get("box_id"), "box_snapshot": snapshot, "created_at": self.now_iso()}
        question = {**context, "id": self._next_id(db), "role": "user", "text": data["question"], "model_version": None, "latency_ms": None}
        response = {**context, "id": self._next_id(db), "role": "assistant", "text": answer, "model_version": MODEL_ARTIFACTS[0]["version"], "latency_ms": ANALYZE_MS}
        reading["messages"].extend([question, response])
        return {"message_ids": [question["id"], response["id"]]}

    def _draft(self, db, reading: dict) -> int:
        study = self._study(db, reading["session"]["study_id"])
        source = reading["findings"][-1]
        items = [i for i in source["items"] if included(i)]
        observations = "\n".join(i["text"].strip() for i in items)
        report = {"id": self._next_id(db), "findings_version": source["version"], "status": "draft", "approved_by": None, "approved_at": None,
                  "updated_at": self.now_iso(), "content": {
                      "comparison": "", "findings": observations or "No abnormal findings were confirmed on review.",
                      "impression": observations or "No abnormal findings were confirmed.", "recommendation": "",
                      "patient": self._patient(db, study["patient_id"]),
                      "study": {"id": study["id"], "study_date": study["study_date"], "indication": study["indication"],
                                "image_ids": [i["id"] for i in study["images"]], "analyzed_image_id": reading["analyzed_image_id"]},
                      "source_item_ids": [i["id"] for i in items]}}
        for previous in reading["reports"]:
            if previous["status"] == "draft":
                previous["status"] = "stale"
        reading["reports"].append(report)
        return report["id"]

    # --- public operations -----------------------------------------------------------------
    def open_reading(self, study_id: int, actor_id: int, messages_args: tuple) -> tuple[dict, int]:
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            study = self._study(db, study_id)
            row = db.execute("SELECT id FROM reading_sessions WHERE doctor_id = ? AND study_id = ?", (actor_id, study_id)).fetchone()
            status = 200
            if row is None:
                sid = self._next_id(db)
                db.execute("INSERT INTO reading_sessions (id, doctor_id, patient_id, study_id, status, revision) VALUES (?, ?, ?, ?, 'active', 1)",
                           (sid, actor_id, study["patient_id"], study_id))
                reading = self._load(db, sid)
                self._event(reading, "session_opened", {"study_id": study_id})
                status = 201
            else:
                reading = self._load(db, row["id"])
            if self._settle(db, reading) or status == 201:
                self._save(db, reading)
            return self._workspace(db, reading, self.messages_page(reading, actor_id, *messages_args)), status

    def session_owner(self, session_id: int) -> tuple[int, int] | None:
        with connect(self.path) as db:
            row = db.execute("SELECT doctor_id, patient_id FROM reading_sessions WHERE id = ?", (session_id,)).fetchone()
        return (row["doctor_id"], row["patient_id"]) if row else None

    def read(self, session_id: int, actor_id: int, view: str, *args):
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            reading = self._load(db, session_id)
            if self._settle(db, reading):
                self._save(db, reading)
            if view == "workspace":
                return self._workspace(db, reading, self.messages_page(reading, actor_id, *args))
            if view == "messages":
                return self.messages_page(reading, actor_id, *args)
            if view == "request":
                return next((j["request"] for j in reading["jobs"] if j["request"]["request_id"] == args[0]), None) or missing()
            events = [{"id": r["id"], "type": r["type"], "created_at": r["created_at"], "user_id": r["user_id"], "data": json.loads(r["data"])}
                      for r in db.execute("SELECT * FROM reading_events WHERE session_id = ? ORDER BY id DESC", (session_id,))]
            cursor, raw_limit = args
            scope = f"{actor_id}:/api/v1/sessions/{session_id}/events:"
            start = self._start(scope, cursor, 0)
            end = start + self._limit(raw_limit, 25, 100)
            return {"items": events[start:end], "next_cursor": self._signed(scope, end) if end < len(events) else None}

    def mutate(self, session_id: int, actor_id: int, kind: str, body: dict, *, if_match: str | None, idempotency_key: str | None,
               path: str, method: str, target: int | None = None) -> tuple[dict, int]:
        required = kind in ("ai", "add_box", "approve_findings", "approve_report")
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            scope = body_hash = None
            if idempotency_key is not None or required:
                if not idempotency_key or not (16 <= len(idempotency_key) <= 128) or not all(32 <= ord(c) <= 126 for c in idempotency_key):
                    invalid("A valid action key is required.")
                scope = hmac.new(self.secret, f"{actor_id}:{method}:{path}:{idempotency_key}".encode(), hashlib.sha256).hexdigest()
                body_hash = hmac.new(self.secret, json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
                saved = db.execute("SELECT * FROM idempotency_receipts WHERE scope = ?", (scope,)).fetchone()
                if saved:
                    if saved["body_hash"] != body_hash:
                        fail(409, "IDEMPOTENCY_CONFLICT", "This action key was already used with different input.")
                    return json.loads(saved["data"]), saved["status"]
            reading = self._load(db, session_id)
            self._settle(db, reading)
            if not if_match:
                fail(428, "PRECONDITION_REQUIRED", "Reload this reading before saving.")
            if if_match != f'"{reading["session"]["revision"]}"':
                fail(412, "REVISION_CONFLICT", "This reading changed in another tab. Reload it before saving.")
            if reading["session"]["status"] == "final":
                fail(409, "FINAL_READ_ONLY", "This final reading is read-only.")
            if self._active_job(reading):
                fail(409, "AI_BUSY", "Wait for the current AI request before making changes.")
            if kind in ("add_box", "change_box", "delete_box", "save_findings", "approve_findings") and reading["analyzed_image_id"] is None:
                fail(409, "ANALYSIS_REQUIRED", "Run the initial analysis before making this change.")
            study = self._study(db, reading["session"]["study_id"])
            handler = {"ai": self._submit, "add_box": self._create_box, "change_box": self._edit_box, "delete_box": self._edit_box,
                       "save_findings": self._save_findings, "approve_findings": self._approve_findings,
                       "save_report": self._edit_report, "approve_report": self._edit_report}[kind]
            data, status = handler(db, reading, study, body, kind, target)
            self._save(db, reading)
            if scope:
                db.execute("INSERT INTO idempotency_receipts (scope, body_hash, status, data, created_at) VALUES (?, ?, ?, ?, ?)",
                           (scope, body_hash, status, json.dumps(data), self.now_iso()))
            return data, status

    # --- mutation handlers (same rules as the mock) ----------------------------------------
    def _receipt(self, reading, resource_type, resource_id):
        return {"resource_type": resource_type, "resource_id": resource_id, "session_revision": reading["session"]["revision"]}

    def _invalidate_approval(self, db, reading) -> None:
        latest = reading["findings"][-1] if reading["findings"] else None
        if not latest or latest["status"] != "approved":
            return
        reading["findings"].append({**copy.deepcopy(latest), "id": self._next_id(db), "version": latest["version"] + 1, "status": "in_review",
                                    "approved_at": None, "approved_by": None, "annotation_snapshot": None})
        reading["session"]["status"] = "active"
        for report in reading["reports"]:
            if report["status"] == "draft":
                report["status"] = "stale"
        self._event(reading, "findings_reopened", {"previous_version": latest["version"]})

    @staticmethod
    def _geometry(value, image) -> list[float]:
        if not isinstance(value, list) or len(value) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf") for v in value):
            invalid("Enter four finite region coordinates.")
        x1, y1, x2, y2 = (float(v) for v in value)
        if x1 < 0 or y1 < 0 or x1 >= x2 or y1 >= y2 or x2 > image["width"] or y2 > image["height"]:
            invalid("The region must be inside the original image and have positive width and height.")
        return [x1, y1, x2, y2]

    def _create_box(self, db, reading, study, body, kind, target):
        fields(body, ["image_id", "bbox_xyxy", "label", "note"])
        image = self._image(study, integer(body.get("image_id"), "image"))
        box = {"id": self._next_id(db), "image_id": image["id"], "bbox_xyxy": self._geometry(body.get("bbox_xyxy"), image),
               "label": nullable_string(body.get("label"), "Region label", 200), "note": nullable_string(body.get("note"), "Region note", 2000),
               "source": "doctor", "review_action": "added", "revision": 1, "deleted_at": None, "ai_request_id": None}
        reading["boxes"].append(box)
        self._invalidate_approval(db, reading)
        self._record_edit(reading, "box_created", {"box_id": box["id"], "image_id": image["id"]})
        return self._receipt(reading, "box", box["id"]), 201

    def _edit_box(self, db, reading, study, body, kind, target):
        deleting = kind == "delete_box"
        box = next((b for b in reading["boxes"] if b["id"] == target), None) or missing()
        if box["deleted_at"]:
            return (self._receipt(reading, "box", box["id"]), 200) if deleting else missing()
        before = box_snapshot(box)
        if deleting:
            fields(body, [])
            box["deleted_at"], box["review_action"] = self.now_iso(), "rejected"
        else:
            fields(body, ["bbox_xyxy", "label", "note", "review_action"])
            if not body:
                invalid("Enter at least one region change.")
            if "bbox_xyxy" in body:
                box["bbox_xyxy"] = self._geometry(body["bbox_xyxy"], self._image(study, box["image_id"]))
            if "label" in body:
                box["label"] = nullable_string(body["label"], "Region label", 200)
            if "note" in body:
                box["note"] = nullable_string(body["note"], "Region note", 2000)
            if "review_action" in body:
                allowed = ["accepted", "edited", "rejected"] if box["source"] == "ai" else ["added", "rejected"]
                if body["review_action"] not in allowed:
                    invalid("Invalid region review decision.")
                box["review_action"] = body["review_action"]
            if any(k in body for k in ("bbox_xyxy", "label", "note")) and box["source"] == "ai" and box["review_action"] != "rejected":
                box["review_action"] = "edited"
        box["revision"] += 1
        self._invalidate_approval(db, reading)
        self._record_edit(reading, "box_deleted" if deleting else "box_updated", {
            "box_id": box["id"], "before": before, "after": box_snapshot(box),
            **({"review_action": box["review_action"]} if "review_action" in body and not deleting else {})})
        return self._receipt(reading, "box", box["id"]), 200

    def _save_findings(self, db, reading, study, body, kind, target):
        fields(body, ["items", "no_abnormality_confirmed"])
        items_in, normal = body.get("items"), body.get("no_abnormality_confirmed")
        if not isinstance(items_in, list) or len(items_in) > 50 or not isinstance(normal, bool):
            invalid("Save up to 50 findings and an explicit normal confirmation state.")
        latest, ids, items = reading["findings"][-1], set(), []
        for row in items_in:
            if not isinstance(row, dict):
                invalid("Invalid finding.")
            fields(row, ["id", "text", "location", "image_id", "source", "action", "box_ids"])
            fid = string(row.get("id"), "Finding ID", 1, 128)
            text = string(row.get("text"), "Finding text", 1, 2000)
            location = nullable_string(row.get("location"), "Finding location", 200)
            image_id = self._image(study, integer(row.get("image_id"), "finding image"))["id"]
            if fid in ids:
                invalid("Finding IDs must be unique.")
            ids.add(fid)
            original = next((i for i in latest["ai_items"] if i["id"] == fid), None)
            allowed = ["pending", "accepted", "edited", "rejected"] if original else ["added", "rejected"]
            if row.get("action") not in allowed:
                invalid("Invalid finding review decision.")
            if original:
                if row.get("source") != "ai" or image_id != original["image_id"]:
                    invalid("AI finding provenance is immutable.")
                if row["action"] in ("accepted", "pending") and (text != original["text"] or location != original["location"]):
                    invalid("Mark changed AI text or location as edited.")
            elif row.get("source") != "doctor":
                invalid("Unknown AI finding.")
            if not isinstance(row.get("box_ids"), list):
                invalid("Provide the list of linked regions.")
            box_ids = [integer(v, "region link") for v in row["box_ids"]]
            if len(set(box_ids)) != len(box_ids) or any(not any(b["id"] == i and b["image_id"] == image_id and active_box(b) for b in reading["boxes"]) for i in box_ids):
                invalid("Link only available regions on this finding’s image.")
            items.append({"id": fid, "text": text, "location": location, "image_id": image_id, "source": "ai" if original else "doctor",
                          "action": row["action"], "box_ids": box_ids})
        if any(i["id"] not in ids for i in latest["ai_items"]):
            invalid("Keep every original AI finding in the review.")
        if normal and any(included(i) for i in items):
            invalid("Normal confirmation requires no included findings.")
        self._invalidate_approval(db, reading)
        target_findings = reading["findings"][-1]
        target_findings.update(items=items, no_abnormality_confirmed=normal, status="in_review")
        self._record_edit(reading, "findings_saved", {"findings_version": target_findings["version"]})
        return self._receipt(reading, "findings", target_findings["id"]), 200

    def _approve_findings(self, db, reading, study, body, kind, target):
        fields(body, ["findings_version"])
        version = integer(body.get("findings_version"), "findings version")
        latest = reading["findings"][-1] if reading["findings"] else None
        if not latest or latest["version"] != version:
            fail(409, "FINDINGS_VERSION_MISMATCH", "Review the latest findings revision.")
        if latest["status"] == "approved":
            return self._receipt(reading, "findings", latest["id"]), 200
        if self._gate1(reading):
            fail(409, "UNRESOLVED_REVIEW", "Resolve the outstanding findings and region review before approving.")
        latest.update(status="approved", approved_at=self.now_iso(), approved_by=reading["session"]["doctor_id"],
                      annotation_snapshot=[box_snapshot(b) for b in reading["boxes"] if active_box(b)])
        reading["session"]["status"] = "findings_approved"
        self._record_edit(reading, "gate1_approved", {"findings_version": latest["version"]})
        return self._receipt(reading, "findings", latest["id"]), 200

    def _edit_report(self, db, reading, study, body, kind, target):
        approving = kind == "approve_report"
        report = next((r for r in reading["reports"] if r["id"] == target), None) or missing()
        latest = reading["findings"][-1] if reading["findings"] else None
        if report["status"] != "draft" or not latest or latest["status"] != "approved" or report["findings_version"] != latest["version"]:
            fail(409, "REPORT_STALE", "The report is no longer based on the latest approved findings.")
        if approving:
            fields(body, ["findings_version"])
            if body.get("findings_version") != latest["version"]:
                fail(409, "REPORT_STALE", "Review the latest approved findings revision.")
            if reading["session"]["status"] != "findings_approved":
                fail(409, "GATE1_REQUIRED", "Approve the latest findings first.")
            if not report["content"]["findings"].strip() or not report["content"]["impression"].strip():
                invalid("Findings and impression are required.")
            report.update(status="final", approved_by=reading["session"]["doctor_id"], approved_at=self.now_iso())
            reading["session"]["status"] = "final"
        else:
            fields(body, list(NARRATIVE))
            if not body:
                invalid("Enter at least one report change.")
            for key in NARRATIVE:
                if key in body:
                    report["content"][key] = string(body[key], key, 0, 8000)
        report["updated_at"] = self.now_iso()
        self._record_edit(reading, "gate2_approved" if approving else "report_saved", {"report_id": report["id"], "findings_version": latest["version"]})
        return self._receipt(reading, "report", report["id"]), 200

    @staticmethod
    def _command(body: dict) -> dict:
        retry = {"retry_of": integer(body["retry_of"], "retry request")} if "retry_of" in body else {}
        op = body.get("operation")
        if op == "analyze":
            fields(body, ["operation", "image_id", "retry_of"])
            return {"operation": "analyze", "image_id": integer(body.get("image_id"), "image"), **retry}
        if op == "ask":
            fields(body, ["operation", "image_id", "question", "box_id", "retry_of"])
            return {"operation": "ask", "image_id": integer(body.get("image_id"), "image"), "question": string(body.get("question"), "Question", 1, 2000).strip(),
                    **({"box_id": integer(body["box_id"], "region")} if "box_id" in body else {}), **retry}
        if op == "draft_report":
            fields(body, ["operation", "findings_version", "retry_of"])
            return {"operation": "draft_report", "findings_version": integer(body.get("findings_version"), "findings version"), **retry}
        invalid("Choose a supported AI operation.")

    def _submit(self, db, reading, study, body, kind, target):
        data = self._command(body)
        if self.ai_paused():
            fail(503, "AI_PAUSED", "An administrator has paused AI. You can still review, edit and sign by hand.")
        patient = self._patient(db, reading["session"]["patient_id"])
        history = self._history(db, study)
        image = self._image(study, data["image_id"]) if "image_id" in data else None
        last_messages = reading["messages"][-10:] if data["operation"] == "ask" else []
        box = None
        if data["operation"] == "analyze" and reading["analyzed_image_id"] is not None:
            fail(409, "ALREADY_ANALYZED", "Initial analysis already succeeded for this reading.")
        if data["operation"] == "ask":
            if reading["analyzed_image_id"] is None:
                fail(409, "ANALYSIS_REQUIRED", "Run the initial analysis before asking a question.")
            text = unicodedata.normalize("NFKC", data["question"]).lower()
            if any(unicodedata.normalize("NFKC", token).lower() in text for token in (patient["name"], patient["code"])):
                fail(422, "PHI_IN_MODEL_INPUT", "Remove patient names and identifiers before sending your question.")
            if data.get("box_id"):
                region = next((b for b in reading["boxes"] if b["id"] == data["box_id"] and b["image_id"] == data["image_id"] and active_box(b)), None)
                if region is None:
                    if data.get("retry_of"):
                        fail(409, "CONTEXT_CHANGED", "The original region changed. Send a new question with the current region.")
                    missing()
                box = box_snapshot(region)
        if data["operation"] == "draft_report":
            latest = reading["findings"][-1] if reading["findings"] else None
            if not latest or latest["status"] != "approved" or latest["version"] != data["findings_version"]:
                if data.get("retry_of"):
                    fail(409, "CONTEXT_CHANGED", "The original approved findings changed. Start a new draft.")
                fail(409, "GATE1_REQUIRED", "Approve the latest findings before drafting a report.")
        original_input = {k: v for k, v in data.items() if k != "retry_of"}
        uses_history = data["operation"] != "draft_report"
        context = sha(json.dumps({"originalInput": original_input, "imageHash": image["sha256"] if image else None, "box": box,
                                  "messageIds": [m["id"] for m in last_messages], "historyIds": [h["id"] for h in history] if uses_history else []},
                                 sort_keys=True))
        if data.get("retry_of"):
            original = next((j for j in reading["jobs"] if j["request"]["request_id"] == data["retry_of"]), None) or missing()
            if original["request"]["status"] not in ("failed", "timed_out") or not (original["request"]["error"] or {}).get("retryable"):
                fail(409, "CONTEXT_CHANGED", "This request cannot be retried. Submit a new request.")
            if original["context"] != context:
                fail(409, "CONTEXT_CHANGED", "The original context changed. Submit a new request.")
        request_id = self._next_id(db)
        submitted_ms = self.clock() * 1000
        self._record_edit(reading, "ai_request_queued", {"request_id": request_id, "operation": data["operation"], "retry_of": data.get("retry_of")})
        request = {
            "request_id": request_id, "operation": data["operation"], "status": "queued",
            "poll_url": f"/api/v1/sessions/{reading['session']['id']}/ai-requests/{request_id}",
            "session_revision": reading["session"]["revision"], "deadline_at": self._iso_ms(submitted_ms + DEADLINE_MS),
            "retry_of": data.get("retry_of"), "error": None, "output_refs": {}, "submitted_input": data,
            "provenance": {
                "provider_mode": "mock", "vlm_artifact": MODEL_ARTIFACTS[0], "cv_artifact": MODEL_ARTIFACTS[1] if data["operation"] == "analyze" else None,
                "prompt_version": "roi-v1" if box else f"{data['operation']}-v1", "preprocessing_version": "canonical-v1",
                "image_sha256": image["sha256"] if image else None,
                "history_report_ids": [h["id"] for h in history] if uses_history else [],
                "history_hashes": [sha(h["text"]) for h in history] if uses_history else [],
                "message_ids": [m["id"] for m in last_messages], "box_snapshot": box,
                "source_findings_version": data["findings_version"] if data["operation"] == "draft_report" else None,
            },
        }
        fault = os.environ.get("THORAX_DEMO_AI_FAULT")
        reading["jobs"].append({"request": request, "context": context, "submitted_at": submitted_ms,
                                "failure": fault if fault == "timeout" or (fault == "detector" and data["operation"] == "analyze") else None})
        return request, 202


def register_reading(app, *, store: ReadingStore, catalog, actor, path_id, read_body, no_store, error) -> None:
    """Routes for the doctor reading workflow (the contract src/thorax-web expects)."""

    @app.exception_handler(ReadingError)
    async def reading_error(request: Request, problem: ReadingError):
        return await app.exception_handlers[error](request, error(problem.status, problem.code, problem.message))

    def reply(data: dict, status: int = 200, revision: int | None = None):
        response = no_store(JSONResponse(data, status_code=status))
        if revision is not None:
            response.headers["ETag"] = f'"{revision}"'
        return response

    def owned(request: Request, session_id: str):
        user = actor(request, "doctor")
        sid = path_id(session_id)
        owner = store.session_owner(sid)
        if owner is None or owner[0] != user.id or not catalog.is_assigned(user.id, owner[1]):
            missing()
        return user, sid

    def message_args(request: Request):
        return request.query_params.get("cursor"), request.query_params.get("limit")

    @app.post("/api/v1/studies/{study_id}/sessions")
    async def open_session(request: Request, study_id: str):
        user = actor(request, "doctor")
        found = catalog.study(path_id(study_id))
        if found is None or not catalog.is_assigned(user.id, found["patient_id"]):
            missing()
        if await read_body(request):
            invalid("Unexpected request field.")
        workspace, status = store.open_reading(found["id"], user.id, message_args(request))
        return reply(workspace, status, workspace["session"]["revision"])

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(request: Request, session_id: str):
        user, sid = owned(request, session_id)
        workspace = store.read(sid, user.id, "workspace", *message_args(request))
        return reply(workspace, revision=workspace["session"]["revision"])

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_messages(request: Request, session_id: str):
        user, sid = owned(request, session_id)
        return reply(store.read(sid, user.id, "messages", *message_args(request)))

    @app.get("/api/v1/sessions/{session_id}/events")
    async def get_events(request: Request, session_id: str):
        user, sid = owned(request, session_id)
        return reply(store.read(sid, user.id, "events", request.query_params.get("cursor"), request.query_params.get("limit")))

    @app.get("/api/v1/sessions/{session_id}/ai-requests/{request_id}")
    async def get_ai_request(request: Request, session_id: str, request_id: str):
        user, sid = owned(request, session_id)
        return reply(store.read(sid, user.id, "request", path_id(request_id)))

    async def change(request: Request, session_id: str, kind: str, target: str | None = None):
        user, sid = owned(request, session_id)
        body = await read_body(request)
        data, status = store.mutate(sid, user.id, kind, body, if_match=request.headers.get("if-match"),
                                    idempotency_key=request.headers.get("idempotency-key"), path=request.url.path, method=request.method,
                                    target=path_id(target) if target is not None else None)
        return reply(data, status, data["session_revision"])

    @app.post("/api/v1/sessions/{session_id}/ai-requests")
    async def submit_ai(request: Request, session_id: str):
        return await change(request, session_id, "ai")

    @app.post("/api/v1/sessions/{session_id}/boxes")
    async def add_box(request: Request, session_id: str):
        return await change(request, session_id, "add_box")

    @app.patch("/api/v1/sessions/{session_id}/boxes/{box_id}")
    async def change_box(request: Request, session_id: str, box_id: str):
        return await change(request, session_id, "change_box", box_id)

    @app.delete("/api/v1/sessions/{session_id}/boxes/{box_id}")
    async def delete_box(request: Request, session_id: str, box_id: str):
        return await change(request, session_id, "delete_box", box_id)

    @app.patch("/api/v1/sessions/{session_id}/findings")
    async def save_findings(request: Request, session_id: str):
        return await change(request, session_id, "save_findings")

    @app.post("/api/v1/sessions/{session_id}/findings/approve")
    async def approve_findings(request: Request, session_id: str):
        return await change(request, session_id, "approve_findings")

    @app.patch("/api/v1/sessions/{session_id}/reports/{report_id}")
    async def save_report(request: Request, session_id: str, report_id: str):
        return await change(request, session_id, "save_report", report_id)

    @app.post("/api/v1/sessions/{session_id}/reports/{report_id}/approve")
    async def approve_report(request: Request, session_id: str, report_id: str):
        return await change(request, session_id, "approve_report", report_id)
