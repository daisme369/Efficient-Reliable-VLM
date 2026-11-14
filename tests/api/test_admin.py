"""Admin API tests for thorax_api, using synthetic accounts and the synthetic demo seed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"
PASSWORD = "synthetic-pass-123"
BOX = {"region": "left lower lung zone", "finding": "lung opacity", "state": "positive", "bbox_xyxy": [500, 540, 660, 685]}


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class AdminTests(unittest.TestCase):
    def setUp(self):
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db = Path(self.directory.name) / "thorax.sqlite3"
        accounts = AccountStore(db)
        for user_id, email, role in ((1, "doctor@example.test", "doctor"), (2, "doctor2@example.test", "doctor"), (3, "admin@example.test", "admin"),
                                     (4, "ana@example.test", "annotator"), (5, "ben@example.test", "annotator")):
            accounts.create(email, f"User {user_id}", role, PASSWORD, user_id=user_id)
        self.app = create_app(db, [WEB], image_root=str(Path(self.directory.name) / "images"))
        seed_demo(self.app.state.catalog)
        self.app.state.labels.create_tasks([24110])
        self.admin, self.doctor = self.client("admin@example.test"), self.client("doctor@example.test")

    def client(self, email, password=PASSWORD):
        from fastapi.testclient import TestClient
        c = TestClient(self.app, headers={"Origin": WEB})
        response = c.post("/api/v1/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, email)
        c.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return c

    def actions(self):
        return [event["action"] for event in self.admin.get("/api/v1/admin/events").json()["items"]]

    def test_every_admin_route_is_admin_only(self):
        for method, path in (("get", "overview"), ("get", "users"), ("get", "ai"), ("get", "labeling"), ("get", "events"), ("get", "doctors")):
            self.assertEqual(getattr(self.doctor, method)(f"/api/v1/admin/{path}").json()["error"]["code"], "ROLE_FORBIDDEN", path)
        self.assertEqual(self.doctor.put("/api/v1/admin/ai/pause", json={"paused": True}).status_code, 403)
        self.assertEqual(self.doctor.post("/api/v1/admin/labeling/export").status_code, 403)

    def test_overview(self):
        body = self.admin.get("/api/v1/admin/overview").json()
        self.assertEqual(body["users"], {"doctor": {"active": 2, "total": 2}, "admin": {"active": 1, "total": 1}, "annotator": {"active": 2, "total": 2}})
        self.assertEqual(body["signed_in"], 2)
        self.assertEqual(body["catalog"], {"patients": 12, "studies": 23, "images": 27})
        self.assertEqual(body["labeling"]["open"], 1)
        self.assertEqual((body["ai"]["paused"], body["ai"]["model_service"]["status"]), (False, "not_configured"))

    def test_user_lifecycle_and_forced_sign_out(self):
        created = self.admin.post("/api/v1/admin/users", json={"name": "Cara Ito", "email": "Cara@Example.test", "role": "annotator", "password": PASSWORD})
        self.assertEqual((created.status_code, created.json()["email"], created.json()["role"]), (201, "cara@example.test", "annotator"))
        self.assertEqual(self.admin.post("/api/v1/admin/users", json={"name": "Dup", "email": "cara@example.test", "role": "doctor", "password": PASSWORD}).json()["error"]["code"], "EMAIL_TAKEN")
        self.assertEqual(self.admin.post("/api/v1/admin/users", json={"name": "Weak", "email": "weak@example.test", "role": "doctor", "password": "short"}).status_code, 422)
        self.assertEqual(self.admin.post("/api/v1/admin/users", json={"name": "Nurse", "email": "n@example.test", "role": "nurse", "password": PASSWORD}).status_code, 422)

        from fastapi.testclient import TestClient
        TestClient(self.app, headers={"Origin": WEB}).post("/api/v1/auth/login", json={"email": "doctor@example.test", "password": "wrong-password-1"})
        rows = {row["email"]: row for row in self.admin.get("/api/v1/admin/users").json()["items"]}
        self.assertNotIn("password_hash", rows["doctor@example.test"])
        self.assertEqual((rows["doctor@example.test"]["signed_in"], rows["doctor@example.test"]["failed_sign_ins_24h"]), (True, 1))
        self.assertIsNotNone(rows["doctor@example.test"]["last_sign_in_at"])
        self.assertEqual((rows["cara@example.test"]["signed_in"], rows["cara@example.test"]["last_sign_in_at"]), (False, None))

        self.assertEqual(self.admin.post("/api/v1/admin/users/1/sign-out").status_code, 204)
        self.assertEqual(self.doctor.get("/api/v1/auth/me").status_code, 401)

        doctor = self.client("doctor@example.test")
        self.assertEqual(self.admin.patch("/api/v1/admin/users/1", json={"password": "a-new-password-456"}).status_code, 200)
        self.assertEqual(doctor.get("/api/v1/auth/me").status_code, 401)  # old sessions end with the old password
        doctor = self.client("doctor@example.test", "a-new-password-456")
        self.assertEqual(self.admin.patch("/api/v1/admin/users/1", json={"is_active": False}).json()["is_active"], False)
        self.assertEqual(doctor.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(self.admin.patch("/api/v1/admin/users/3", json={"is_active": False}).json()["error"]["code"], "SELF_LOCKOUT")
        for action in ("user_created", "forced_sign_out", "password_reset", "user_disabled"):
            self.assertIn(action, self.actions())

    def test_assignments_change_what_a_doctor_sees(self):
        self.assertEqual(self.doctor.get("/api/v1/studies/2511").status_code, 404)  # PX-1052 belongs to another doctor
        granted = self.admin.post("/api/v1/admin/assignments", json={"doctor_id": 1, "patient_id": 11})
        self.assertEqual((granted.status_code, granted.json()["resource_type"]), (201, "assignment"))
        self.assertEqual(self.doctor.get("/api/v1/studies/2511").status_code, 200)
        doctors = self.admin.get("/api/v1/admin/doctors").json()["items"]
        self.assertIn(11, doctors[0]["assigned_patient_ids"])
        catalog = self.admin.get("/api/v1/admin/patient-catalog", params={"search": "px-1052"}).json()["items"]
        self.assertEqual(catalog, [{"id": 11, "code": "PX-1052", "assigned_doctor_ids": [1, 2]}])
        self.assertEqual(self.admin.delete("/api/v1/admin/assignments/1/11").status_code, 204)
        self.assertEqual(self.doctor.get("/api/v1/studies/2511").status_code, 404)
        self.assertEqual(self.admin.post("/api/v1/admin/assignments", json={"doctor_id": 4, "patient_id": 11}).status_code, 404)  # not a doctor
        self.assertEqual(self.actions()[:2], ["access_revoked", "access_granted"])

    def test_ai_pause_switch_and_request_log(self):
        store = self.app.state.admin
        self.assertFalse(store.ai_paused())
        paused = self.admin.put("/api/v1/admin/ai/pause", json={"paused": True}).json()
        self.assertEqual((paused["paused"], paused["changed_by"]), (True, "User 3"))
        self.assertTrue(store.ai_paused())
        self.assertEqual(self.admin.put("/api/v1/admin/ai/pause", json={"paused": "yes"}).status_code, 422)
        store.record_ai_request(1, "ask", "succeeded", model_version="b1", latency_ms=400)
        store.record_ai_request(1, "ask", "succeeded", model_version="b1", latency_ms=1000)
        store.record_ai_request(1, "analyze", "timed_out", error_code="AI_TIMEOUT", latency_ms=60000)
        body = self.admin.get("/api/v1/admin/ai").json()
        self.assertEqual(body["stats"], {"total": 3, "shown": 3, "failed": 0, "timed_out": 1, "average_ms": 700, "slowest_ms": 1000})
        self.assertEqual((body["items"][0]["operation"], body["items"][0]["user_name"]), ("analyze", "User 1"))
        self.admin.put("/api/v1/admin/ai/pause", json={"paused": False})
        self.assertEqual(self.actions()[:2], ["ai_resumed", "ai_paused"])

    def test_labeling_oversight_and_export(self):
        ana, ben = self.client("ana@example.test"), self.client("ben@example.test")
        task = ana.post("/api/v1/labeling/tasks/next").json()
        task = ana.put(f"/api/v1/labeling/tasks/{task['id']}/boxes", json={"boxes": [BOX], "revision": task["revision"]}).json()
        task = ana.post(f"/api/v1/labeling/tasks/{task['id']}/submit", json={"revision": task["revision"]}).json()
        task = ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "return", "comment": "Tighter.", "revision": task["revision"]}).json()
        task = ana.post(f"/api/v1/labeling/tasks/{task['id']}/submit", json={"revision": task["revision"]}).json()
        task = ben.put(f"/api/v1/labeling/tasks/{task['id']}/boxes", json={"boxes": [{**BOX, "state": "uncertain"}], "revision": task["revision"]}).json()
        ben.post(f"/api/v1/labeling/tasks/{task['id']}/review", json={"decision": "approve", "revision": task["revision"]})

        body = self.admin.get("/api/v1/admin/labeling").json()
        self.assertEqual(body["counts"]["approved"], 1)
        self.assertEqual(body["approved_with_reviewer_changes"], 1)
        people = {row["name"]: row for row in body["annotators"]}
        self.assertEqual({k: people["User 4"][k] for k in ("labeled", "approved", "times_returned", "reviews")}, {"labeled": 1, "approved": 1, "times_returned": 1, "reviews": 0})
        self.assertEqual({k: people["User 5"][k] for k in ("labeled", "reviews", "review_edits")}, {"labeled": 0, "reviews": 2, "review_edits": 1})
        self.assertFalse(body["vocabulary"]["verified"])

        exported = self.admin.post("/api/v1/admin/labeling/export").json()
        row = json.loads(exported["labels_jsonl"])
        self.assertEqual((exported["manifest"]["rows"], row["image_id"], row["reviewer_edited"]), (1, 24110, True))
        self.assertEqual(self.actions()[0], "labels_exported")


if __name__ == "__main__":
    unittest.main()
