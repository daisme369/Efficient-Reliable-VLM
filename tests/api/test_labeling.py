"""Annotator role and region-labeling workflow tests, using synthetic demo images only."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"
PASSWORD = "synthetic-pass-123"
BOX = {"region": "left lower lung zone", "finding": "lung opacity", "state": "positive", "bbox_xyxy": [501.25, 542.5, 663.75, 685]}


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class LabelingTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "thorax.sqlite3"
        accounts = AccountStore(self.db)
        for user_id, email, role in ((1, "doctor@example.test", "doctor"), (2, "doctor2@example.test", "doctor"),
                                     (4, "ana@example.test", "annotator"), (5, "ben@example.test", "annotator")):
            accounts.create(email, f"User {user_id}", role, PASSWORD, user_id=user_id)
        self.app = create_app(self.db, [WEB], image_root=str(Path(self.directory.name) / "images"))
        seed_demo(self.app.state.catalog)
        self.app.state.labels.create_tasks([24110, 24111])  # one PA and one LAT image of patient PX-1042

        def client(email):
            c = TestClient(self.app, headers={"Origin": WEB})
            body = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()
            c.headers["X-CSRF-Token"] = body["csrf_token"]
            return c

        self.ana, self.ben, self.doctor = client("ana@example.test"), client("ben@example.test"), client("doctor@example.test")

    def label_and_submit(self, who=None):
        who = who or self.ana
        task = who.post("/api/v1/labeling/tasks/next").json()
        task = who.put(f"/api/v1/labeling/tasks/{task['id']}/boxes", json={"boxes": [BOX], "revision": task["revision"]}).json()
        return who.post(f"/api/v1/labeling/tasks/{task['id']}/submit", json={"revision": task["revision"]}).json()

    def test_full_flow_label_review_export(self):
        task = self.label_and_submit()
        self.assertEqual((task["status"], task["role"], len(task["boxes"])), ("submitted", "labeler", 1))
        queue = self.ben.get("/api/v1/labeling/queue").json()
        self.assertEqual([t["id"] for t in queue["to_review"]], [task["id"]])
        seen = self.ben.get(f"/api/v1/labeling/tasks/{task['id']}").json()
        self.assertTrue(seen["can_review"])
        approved = self.ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "approve", "revision": seen["revision"]}).json()
        self.assertEqual(approved["status"], "approved")

        from thorax_api.labeling import LabelStore
        out = Path(self.directory.name) / "export"
        manifest = LabelStore(self.db).export(out)
        rows = [json.loads(line) for line in (out / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(manifest["rows"], 1)
        self.assertEqual(manifest["sha256"], hashlib.sha256((out / "labels.jsonl").read_bytes()).hexdigest())
        self.assertEqual(rows[0]["image_id"], 24110)
        self.assertEqual(rows[0]["regions"], [{"region": "left lower lung zone", "xyxy": [501.25, 542.5, 663.75, 685.0], "box_status": "localized",
                                               "attributes": [{"category": "finding", "label": "lung opacity", "state": "positive"}],
                                               "source": "thorax-annotator"}])
        self.assertEqual((rows[0]["labeler_id"], rows[0]["reviewer_id"]), (4, 5))

    def test_return_for_changes_then_resubmit(self):
        task = self.label_and_submit()
        bad = self.ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "return", "revision": task["revision"]})
        self.assertEqual(bad.status_code, 422)  # returning needs a reason
        returned = self.ben.post(f"/api/v1/labeling/tasks/{task['id']}/review",
                                 json={"decision": "return", "comment": "Box is too wide.", "revision": task["revision"]}).json()
        self.assertEqual(returned["status"], "returned")
        again = self.ana.post("/api/v1/labeling/tasks/next").json()  # own returned work comes back first
        self.assertEqual((again["id"], again["review_comment"], again["can_edit"]), (task["id"], "Box is too wide.", True))
        resubmitted = self.ana.post(f"/api/v1/labeling/tasks/{task['id']}/submit", json={"revision": again["revision"]}).json()
        self.assertEqual(resubmitted["status"], "submitted")

    def test_no_self_review_and_no_edits_after_submit(self):
        task = self.label_and_submit()
        self.assertEqual(self.ana.post(f"/api/v1/labeling/tasks/{task['id']}/review",
                                       json={"decision": "approve", "revision": task["revision"]}).json()["error"]["code"], "SELF_REVIEW")
        locked = self.ana.put(f"/api/v1/labeling/tasks/{task['id']}/boxes", json={"boxes": [], "revision": task["revision"]})
        self.assertEqual(locked.json()["error"]["code"], "TASK_LOCKED")

    def test_reviewer_can_fix_boxes_before_approving(self):
        task = self.label_and_submit()
        seen = self.ben.get(f"/api/v1/labeling/tasks/{task['id']}").json()
        self.assertTrue(seen["can_edit"] and seen["can_review"])
        fixed = self.ben.put(f"/api/v1/labeling/tasks/{task['id']}/boxes",
                             json={"boxes": [{**BOX, "bbox_xyxy": [510, 550, 640, 680]}], "revision": seen["revision"]}).json()
        self.assertEqual((fixed["status"], fixed["reviewer_edited"], fixed["boxes"][0]["bbox_xyxy"]), ("submitted", True, [510.0, 550.0, 640.0, 680.0]))
        stale = self.ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "approve", "revision": seen["revision"]})
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")  # must decide on the version just saved
        self.ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "approve", "revision": fixed["revision"]})
        from thorax_api.labeling import LabelStore
        out = Path(self.directory.name) / "export"
        LabelStore(self.db).export(out)
        row = json.loads((out / "labels.jsonl").read_text(encoding="utf-8"))
        self.assertTrue(row["reviewer_edited"])
        self.assertEqual(row["regions"][0]["xyxy"], [510.0, 550.0, 640.0, 680.0])

    def test_box_validation_and_stale_revision(self):
        task = self.ana.post("/api/v1/labeling/tasks/next").json()
        url = f"/api/v1/labeling/tasks/{task['id']}/boxes"
        for bad in ({**BOX, "region": "left knee"}, {**BOX, "state": "maybe"}, {**BOX, "bbox_xyxy": [700, 10, 600, 20]},
                    {**BOX, "bbox_xyxy": [0, 0, 801, 10]}, {**BOX, "bbox_xyxy": [0, 0, True, 10]}, {**BOX, "extra": 1}):
            self.assertEqual(self.ana.put(url, json={"boxes": [bad], "revision": task["revision"]}).status_code, 422, bad)
        self.assertEqual(self.ana.put(url, json={"boxes": [BOX], "revision": task["revision"] - 1}).json()["error"]["code"], "REVISION_CONFLICT")

    def test_privacy_boundaries(self):
        # Annotators see only images with a task they may view; doctors keep their assignment rules.
        self.assertEqual(self.ana.get("/api/v1/images/24110/content").status_code, 404)  # not claimed yet
        task = self.ana.post("/api/v1/labeling/tasks/next").json()
        self.assertEqual(self.ana.get(f"/api/v1/images/{task['image_id']}/content").status_code, 200)
        self.assertEqual(self.ben.get(f"/api/v1/images/{task['image_id']}/content").status_code, 404)  # in progress, not his
        self.assertEqual(self.ben.get(f"/api/v1/labeling/tasks/{task['id']}").status_code, 404)
        self.assertEqual(self.ana.get("/api/v1/patients").json()["error"]["code"], "ROLE_FORBIDDEN")
        self.assertEqual(self.ana.get("/api/v1/images/24210/content").status_code, 404)  # no labeling task at all
        self.assertEqual(self.doctor.get("/api/v1/labeling/queue").json()["error"]["code"], "ROLE_FORBIDDEN")

    def test_mutations_need_csrf(self):
        del self.ana.headers["X-CSRF-Token"]
        self.assertEqual(self.ana.post("/api/v1/labeling/tasks/next").json()["error"]["code"], "CSRF_INVALID")

    def test_queue_runs_dry(self):
        self.label_and_submit()
        self.label_and_submit(self.ben)
        self.assertEqual(self.ana.post("/api/v1/labeling/tasks/next").json()["error"]["code"], "NO_TASKS")
        counts = self.ana.get("/api/v1/labeling/queue").json()["counts"]
        self.assertEqual(counts, {"open": 0, "in_progress": 0, "submitted": 2, "returned": 0, "approved": 0})


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class RoleMigrationTests(unittest.TestCase):
    def test_existing_database_gains_the_annotator_role_without_losing_users_or_links(self):
        from thorax_api.accounts import AccountStore
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "old.sqlite3"
            old = sqlite3.connect(db)
            old.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('doctor', 'admin')), password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
                INSERT INTO users VALUES (1, 'doctor@example.test', 'Dr. Old', 'doctor', 'scrypt$1$1$1$00$00', 1, '2026-11-12T00:00:00Z');
                CREATE TABLE assignments (doctor_id INTEGER NOT NULL REFERENCES users (id), patient_id INTEGER NOT NULL, active INTEGER NOT NULL);
                INSERT INTO assignments VALUES (1, 7, 1);""")
            old.commit()
            old.close()
            store = AccountStore(db)
            store.create("ana@example.test", "Ana", "annotator", PASSWORD)
            self.assertEqual(store.by_id(1).name, "Dr. Old")
            check = sqlite3.connect(db)
            try:
                self.assertEqual(check.execute("SELECT doctor_id, patient_id FROM assignments").fetchall(), [(1, 7)])
                self.assertEqual(check.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                check.close()


if __name__ == "__main__":
    unittest.main()
