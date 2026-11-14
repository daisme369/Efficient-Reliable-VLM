"""Region-box labeling for the annotator role: tasks, a labeler -> reviewer flow, and export.

Flow per image: open -> in_progress (claimed) -> submitted -> approved, or -> returned -> submitted again.
A reviewer must be a different annotator from the labeler and may fix the boxes of a submitted task
before deciding; such tasks are flagged reviewer_edited. Approved tasks are immutable.

Export writes only approved labels, in the region-record shape used by
cxr_research.preprocessing.annotations (region, xyxy in original pixels, finding attributes).
It is a versioned file for a human to hand to training; nothing here feeds a model.
Using these labels for training needs the scope-change record in docs/agents/RESEARCH-SCOPE.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .db import connect, utc_now

VOCABULARY_PATH = Path(__file__).resolve().parents[2] / "configs" / "labeling_vocabulary.json"
STATUSES = ("open", "in_progress", "submitted", "returned", "approved")
MAX_BOXES = 50

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS label_tasks (
        id INTEGER PRIMARY KEY,
        image_id INTEGER NOT NULL UNIQUE REFERENCES images (id),
        status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'submitted', 'returned', 'approved')),
        labeler_id INTEGER REFERENCES users (id),
        reviewer_id INTEGER REFERENCES users (id),
        review_comment TEXT,
        reviewer_edited INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        approved_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS label_boxes (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES label_tasks (id),
        region TEXT NOT NULL,
        finding TEXT NOT NULL,
        state TEXT NOT NULL,
        x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS label_events (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES label_tasks (id),
        user_id INTEGER NOT NULL REFERENCES users (id),
        action TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
)


class LabelError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def load_vocabulary(path: Path = VOCABULARY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class LabelStore:
    def __init__(self, path: str | Path, vocabulary: dict | None = None):
        self.path = str(path)
        self.vocabulary = vocabulary or load_vocabulary()
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)
            columns = {row[1] for row in db.execute("PRAGMA table_info(label_tasks)")}
            if "reviewer_edited" not in columns:  # databases created before reviewers could edit
                db.execute("ALTER TABLE label_tasks ADD COLUMN reviewer_edited INTEGER NOT NULL DEFAULT 0")

    # --- task lifecycle -------------------------------------------------------------------
    def create_tasks(self, image_ids: list[int]) -> int:
        now = utc_now()
        with connect(self.path) as db:
            before = db.execute("SELECT COUNT(*) FROM label_tasks").fetchone()[0]
            db.executemany("INSERT OR IGNORE INTO label_tasks (image_id, status, revision, updated_at) VALUES (?, 'open', 1, ?)",
                           [(image_id, now) for image_id in image_ids])
            return db.execute("SELECT COUNT(*) FROM label_tasks").fetchone()[0] - before

    def _row(self, db, task_id: int) -> dict:
        row = db.execute(
            """SELECT t.*, i.width, i.height, i.view, i.study_id FROM label_tasks t JOIN images i ON i.id = t.image_id
               WHERE t.id = ?""", (task_id,)).fetchone()
        if row is None:
            raise LabelError(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")
        return dict(row)

    def _event(self, db, task_id: int, user_id: int, action: str) -> None:
        db.execute("INSERT INTO label_events (task_id, user_id, action, created_at) VALUES (?, ?, ?, ?)", (task_id, user_id, action, utc_now()))

    def task(self, task_id: int, user_id: int) -> dict:
        with connect(self.path) as db:
            task = self._row(db, task_id)
            if not self._can_see(task, user_id):
                raise LabelError(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")
            boxes = db.execute("SELECT region, finding, state, x1, y1, x2, y2 FROM label_boxes WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return self._public(task, [dict(b) for b in boxes], user_id)

    def annotator_can_view(self, image_id: int, user_id: int) -> bool:
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM label_tasks WHERE image_id = ?", (image_id,)).fetchone()
        return row is not None and self._can_see(dict(row), user_id)

    @staticmethod
    def _can_see(task: dict, user_id: int) -> bool:
        # Your own tasks, anything waiting for review, and tasks you reviewed. Open tasks come only via "next".
        return task["labeler_id"] == user_id or task["reviewer_id"] == user_id or (task["status"] == "submitted" and task["labeler_id"] != user_id)

    @staticmethod
    def _public(task: dict, boxes: list[dict], user_id: int) -> dict:
        mine = task["labeler_id"] == user_id
        return {
            "id": task["id"], "image_id": task["image_id"], "status": task["status"], "revision": task["revision"],
            "image": {"id": task["image_id"], "view": task["view"], "width": task["width"], "height": task["height"],
                      "content_url": f"/api/v1/images/{task['image_id']}/content"},
            "role": "labeler" if mine else "reviewer",
            "can_edit": (mine and task["status"] in ("in_progress", "returned")) or (not mine and task["status"] == "submitted"),
            "reviewer_edited": bool(task["reviewer_edited"]),
            "can_review": not mine and task["status"] == "submitted",
            "review_comment": task["review_comment"],
            "boxes": [{"region": b["region"], "finding": b["finding"], "state": b["state"],
                       "bbox_xyxy": [b["x1"], b["y1"], b["x2"], b["y2"]]} for b in boxes],
            "updated_at": task["updated_at"],
        }

    def queue(self, user_id: int) -> dict:
        with connect(self.path) as db:
            mine = db.execute("""SELECT id, image_id, status, updated_at, review_comment FROM label_tasks
                                 WHERE labeler_id = ? AND status != 'approved' ORDER BY updated_at""", (user_id,)).fetchall()
            review = db.execute("""SELECT id, image_id, status, updated_at FROM label_tasks
                                   WHERE status = 'submitted' AND labeler_id != ? ORDER BY updated_at""", (user_id,)).fetchall()
            counts = dict(db.execute("SELECT status, COUNT(*) FROM label_tasks GROUP BY status").fetchall())
        return {"my_tasks": [dict(r) for r in mine], "to_review": [dict(r) for r in review],
                "counts": {status: counts.get(status, 0) for status in STATUSES}}

    def claim_next(self, user_id: int) -> dict:
        with connect(self.path) as db:
            # Finish your own unfinished work first, then take the oldest open task.
            row = db.execute("""SELECT id FROM label_tasks WHERE labeler_id = ? AND status IN ('in_progress', 'returned')
                                ORDER BY updated_at LIMIT 1""", (user_id,)).fetchone()
            if row is None:
                row = db.execute("SELECT id FROM label_tasks WHERE status = 'open' ORDER BY id LIMIT 1").fetchone()
                if row is None:
                    raise LabelError(404, "NO_TASKS", "There are no images left to label.")
                claimed = db.execute("""UPDATE label_tasks SET status = 'in_progress', labeler_id = ?, revision = revision + 1, updated_at = ?
                                        WHERE id = ? AND status = 'open'""", (user_id, utc_now(), row["id"])).rowcount
                if not claimed:
                    raise LabelError(409, "TASK_TAKEN", "Another annotator took that image. Try again.")
                self._event(db, row["id"], user_id, "claimed")
        return self.task(row["id"], user_id)

    def _validated(self, task: dict, boxes: object) -> list[tuple]:
        if not isinstance(boxes, list) or len(boxes) > MAX_BOXES:
            raise LabelError(422, "VALIDATION_ERROR", f"Send a list of at most {MAX_BOXES} boxes.")
        vocab, rows = self.vocabulary, []
        for box in boxes:
            if not isinstance(box, dict) or set(box) != {"region", "finding", "state", "bbox_xyxy"}:
                raise LabelError(422, "VALIDATION_ERROR", "Each box needs region, finding, state and bbox_xyxy.")
            if box["region"] not in vocab["regions"] or box["finding"] not in vocab["findings"] or box["state"] not in vocab["states"]:
                raise LabelError(422, "VALIDATION_ERROR", "Use a region, finding and state from the labeling vocabulary.")
            xyxy = box["bbox_xyxy"]
            if not (isinstance(xyxy, list) and len(xyxy) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in xyxy)):
                raise LabelError(422, "VALIDATION_ERROR", "bbox_xyxy must be four numbers.")
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            if not (0 <= x1 < x2 <= task["width"] and 0 <= y1 < y2 <= task["height"]):
                raise LabelError(422, "VALIDATION_ERROR", "Each box must lie inside the image, with x1 < x2 and y1 < y2.")
            rows.append((box["region"], box["finding"], box["state"], x1, y1, x2, y2))
        return rows

    def _guarded(self, db, task_id: int, user_id: int, revision: object) -> dict:
        task = self._row(db, task_id)
        if not self._can_see(task, user_id):
            raise LabelError(404, "RESOURCE_NOT_FOUND", "This resource is unavailable.")
        if revision != task["revision"]:
            raise LabelError(409, "REVISION_CONFLICT", "This task changed since you opened it. Reload it.")
        return task

    def save_boxes(self, task_id: int, user_id: int, boxes: object, revision: object) -> dict:
        with connect(self.path) as db:
            task = self._guarded(db, task_id, user_id, revision)
            labeler = task["labeler_id"] == user_id
            if not ((labeler and task["status"] in ("in_progress", "returned")) or (not labeler and task["status"] == "submitted")):
                raise LabelError(409, "TASK_LOCKED", "The labeler edits in-progress or returned tasks; a reviewer edits submitted ones.")
            rows = self._validated(task, boxes)
            db.execute("DELETE FROM label_boxes WHERE task_id = ?", (task_id,))
            db.executemany("INSERT INTO label_boxes (task_id, region, finding, state, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           [(task_id, *row) for row in rows])
            db.execute("UPDATE label_tasks SET revision = revision + 1, updated_at = ?, reviewer_edited = reviewer_edited OR ? WHERE id = ?",
                       (utc_now(), int(not labeler), task_id))
            self._event(db, task_id, user_id, "saved" if labeler else "reviewer_edited")
        return self.task(task_id, user_id)

    def submit(self, task_id: int, user_id: int, revision: object) -> dict:
        with connect(self.path) as db:
            task = self._guarded(db, task_id, user_id, revision)
            if task["labeler_id"] != user_id or task["status"] not in ("in_progress", "returned"):
                raise LabelError(409, "TASK_LOCKED", "Only the labeler can submit this task.")
            # An image with no finding is a valid label ("nothing to box"), so zero boxes may be submitted.
            db.execute("""UPDATE label_tasks SET status = 'submitted', revision = revision + 1, updated_at = ? WHERE id = ?""", (utc_now(), task_id))
            self._event(db, task_id, user_id, "submitted")
        return self.task(task_id, user_id)

    def review(self, task_id: int, user_id: int, decision: object, comment: object, revision: object) -> dict:
        if decision not in ("approve", "return"):
            raise LabelError(422, "VALIDATION_ERROR", "Decision must be approve or return.")
        if comment is not None and (not isinstance(comment, str) or len(comment) > 1000):
            raise LabelError(422, "VALIDATION_ERROR", "Comment must be text of at most 1000 characters.")
        if decision == "return" and not (comment or "").strip():
            raise LabelError(422, "VALIDATION_ERROR", "Say what to fix when returning a task.")
        with connect(self.path) as db:
            task = self._guarded(db, task_id, user_id, revision)
            if task["labeler_id"] == user_id:
                raise LabelError(403, "SELF_REVIEW", "Someone else must review your labels.")
            if task["status"] != "submitted":
                raise LabelError(409, "TASK_LOCKED", "Only submitted tasks can be reviewed.")
            now = utc_now()
            db.execute("""UPDATE label_tasks SET status = ?, reviewer_id = ?, review_comment = ?, revision = revision + 1,
                          updated_at = ?, approved_at = ? WHERE id = ?""",
                       ("approved" if decision == "approve" else "returned", user_id, (comment or "").strip() or None, now,
                        now if decision == "approve" else None, task_id))
            self._event(db, task_id, user_id, "approved" if decision == "approve" else "returned")
        return self.task(task_id, user_id)

    def oversight(self) -> dict:
        """Admin view: progress per annotator and how often review changes or returns labels."""
        with connect(self.path) as db:
            counts = dict(db.execute("SELECT status, COUNT(*) FROM label_tasks GROUP BY status").fetchall())
            people = db.execute(
                """SELECT u.id, u.name, u.is_active,
                          (SELECT COUNT(*) FROM label_tasks t WHERE t.labeler_id = u.id AND t.status = 'in_progress') AS in_progress,
                          (SELECT COUNT(*) FROM label_tasks t WHERE t.labeler_id = u.id AND t.status IN ('submitted', 'returned', 'approved')) AS labeled,
                          (SELECT COUNT(*) FROM label_tasks t WHERE t.labeler_id = u.id AND t.status = 'approved') AS approved,
                          (SELECT COUNT(*) FROM label_events e JOIN label_tasks t ON t.id = e.task_id
                            WHERE t.labeler_id = u.id AND e.action = 'returned') AS times_returned,
                          (SELECT COUNT(*) FROM label_events e WHERE e.user_id = u.id AND e.action IN ('approved', 'returned')) AS reviews,
                          (SELECT COUNT(*) FROM label_events e WHERE e.user_id = u.id AND e.action = 'reviewer_edited') AS review_edits
                   FROM users u WHERE u.role = 'annotator' ORDER BY u.id""").fetchall()
            edited = db.execute("SELECT COUNT(*) FROM label_tasks WHERE status = 'approved' AND reviewer_edited = 1").fetchone()[0]
        return {"counts": {status: counts.get(status, 0) for status in STATUSES},
                "annotators": [{**dict(row), "is_active": bool(row["is_active"])} for row in people],
                "approved_with_reviewer_changes": edited,
                "vocabulary": {"version": self.vocabulary["version"], "verified": not self.vocabulary["status"].startswith("UNVERIFIED")}}

    # --- export ----------------------------------------------------------------------------
    def export(self, out_dir: str | Path) -> dict:
        """Approved labels only -> labels.jsonl + manifest.json (row count, sha256, vocabulary version)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        with connect(self.path) as db:
            tasks = db.execute("""SELECT t.id, t.image_id, t.labeler_id, t.reviewer_id, t.reviewer_edited, t.approved_at, i.sha256, i.view, i.width, i.height
                                  FROM label_tasks t JOIN images i ON i.id = t.image_id WHERE t.status = 'approved' ORDER BY t.id""").fetchall()
            lines = []
            for task in tasks:
                boxes = db.execute("SELECT region, finding, state, x1, y1, x2, y2 FROM label_boxes WHERE task_id = ? ORDER BY id", (task["id"],)).fetchall()
                regions: dict[tuple, dict] = {}
                for b in boxes:  # one region record per box; findings become attributes like the ImaGenome parser's output
                    key = (b["region"], b["x1"], b["y1"], b["x2"], b["y2"])
                    record = regions.setdefault(key, {"region": b["region"], "xyxy": [b["x1"], b["y1"], b["x2"], b["y2"]],
                                                      "box_status": "localized", "attributes": [], "source": "thorax-annotator"})
                    record["attributes"].append({"category": "finding", "label": b["finding"], "state": b["state"]})
                lines.append(json.dumps({
                    "image_id": task["image_id"], "image_sha256": task["sha256"], "view": task["view"],
                    "width": task["width"], "height": task["height"], "labeler_id": task["labeler_id"],
                    "reviewer_id": task["reviewer_id"], "reviewer_edited": bool(task["reviewer_edited"]),
                    "approved_at": task["approved_at"], "regions": list(regions.values()),
                }, sort_keys=True))
        body = ("\n".join(lines) + "\n") if lines else ""
        data = body.encode("utf-8")
        (out / "labels.jsonl").write_bytes(data)  # bytes, not text: Windows text mode would add CR and break the checksum
        manifest = {"schema": "thorax-region-labels-v1", "created_at": utc_now(), "rows": len(lines),
                    "sha256": hashlib.sha256(data).hexdigest(), "vocabulary_version": self.vocabulary["version"],
                    "vocabulary_status": self.vocabulary["status"], "training_use": "requires an approved scope-change record"}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m thorax_api.labeling", description="Manage labeling tasks.")
    parser.add_argument("--db", default=os.environ.get("THORAX_DB_PATH", "thorax.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-tasks", help="Create a labeling task for every catalog image that has none.")
    export = commands.add_parser("export", help="Write approved labels to a folder (labels.jsonl + manifest.json).")
    export.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    store = LabelStore(args.db)
    if args.command == "create-tasks":
        with connect(args.db) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM images ORDER BY id")]
        print(f"created {store.create_tasks(ids)} tasks")
    else:
        manifest = store.export(args.out)
        print(f"exported {manifest['rows']} approved images, sha256 {manifest['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
