"""Doctor reading workflow against the SQLite backend: the same contract as the web demo's mock API."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
WEB = "http://127.0.0.1:3000"
PASSWORD = "synthetic-pass-123"


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx)")
class ReadingTests(unittest.TestCase):
    def setUp(self):
        from thorax_api.accounts import AccountStore
        from thorax_api.catalog import seed_demo

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "thorax.sqlite3"
        accounts = AccountStore(self.db)
        for user_id, email, role in ((1, "doctor@example.test", "doctor"), (2, "doctor2@example.test", "doctor"), (3, "admin@example.test", "admin")):
            accounts.create(email, f"User {user_id}", role, PASSWORD, user_id=user_id)
        self.now = 1_800_000_000.0
        self.app = self.make_app()
        seed_demo(self.app.state.catalog)
        self.doctor = self.client("doctor@example.test")

    def make_app(self):
        from thorax_api.app import create_app
        return create_app(self.db, [WEB], clock=lambda: self.now, image_root=str(Path(self.directory.name) / "images"))

    def client(self, email, app=None):
        from fastapi.testclient import TestClient
        c = TestClient(app or self.app, headers={"Origin": WEB})
        response = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        c.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return c

    def write(self, method, path, body=None, *, revision, key=True, who=None):
        headers = {"If-Match": f'"{revision}"'}
        if key:
            headers["Idempotency-Key"] = uuid.uuid4().hex
        return getattr(who or self.doctor, method)(path, headers=headers, **({"json": body} if body is not None else {}))

    def open(self, study_id=2411):
        response = self.doctor.post(f"/api/v1/studies/{study_id}/sessions")
        self.assertIn(response.status_code, (200, 201))
        return response.json()

    def settle(self, workspace):
        self.now += 2
        return self.doctor.get(f"/api/v1/sessions/{workspace['session']['id']}").json()

    def analyze(self, workspace):
        sid = workspace["session"]["id"]
        image = next(i for i in workspace["images"] if i["view"] == "PA")
        job = self.write("post", f"/api/v1/sessions/{sid}/ai-requests", {"operation": "analyze", "image_id": image["id"]}, revision=workspace["session"]["revision"])
        self.assertEqual(job.status_code, 202, job.text)
        return self.settle(workspace)

    def test_full_reading_to_signed_report_and_it_survives_a_restart(self):
        first = self.doctor.post("/api/v1/studies/2411/sessions")
        self.assertEqual((first.status_code, first.headers["etag"]), (201, '"1"'))
        self.assertEqual(self.doctor.post("/api/v1/studies/2411/sessions").status_code, 200)  # reopening returns the same reading
        ws = first.json()
        sid = ws["session"]["id"]
        self.assertEqual([b["code"] for b in ws["blockers"]], ["ANALYSIS_REQUIRED", "GATE1_REQUIRED"])
        self.assertEqual(len(ws["history"]), 5)

        ws = self.analyze(ws)
        findings, boxes = ws["latest_findings"], ws["boxes"]
        self.assertEqual((findings["version"], findings["status"], len(findings["items"])), (1, "ai_proposed", 2))
        self.assertEqual(findings["items"][0]["box_ids"], [boxes[0]["id"]])
        self.assertEqual(ws["initial_analysis"]["status"], "succeeded")

        rev = ws["session"]["revision"]
        accepted = self.write("patch", f"/api/v1/sessions/{sid}/boxes/{boxes[0]['id']}", {"review_action": "accepted"}, revision=rev, key=False)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        rev = accepted.json()["session_revision"]
        items = [{**item, "action": "accepted"} for item in findings["items"]]
        saved = self.write("patch", f"/api/v1/sessions/{sid}/findings", {"items": items, "no_abnormality_confirmed": False}, revision=rev, key=False)
        self.assertEqual(saved.status_code, 200, saved.text)
        approved = self.write("post", f"/api/v1/sessions/{sid}/findings/approve", {"findings_version": 1}, revision=saved.json()["session_revision"])
        self.assertEqual(approved.status_code, 200, approved.text)

        draft = self.write("post", f"/api/v1/sessions/{sid}/ai-requests", {"operation": "draft_report", "findings_version": 1}, revision=approved.json()["session_revision"])
        self.assertEqual(draft.status_code, 202, draft.text)
        ws = self.settle(ws)
        report = ws["reports"][0]
        self.assertEqual((report["status"], report["content"]["findings"].splitlines()[0]), ("draft", "Possible mild left basilar opacity."))
        edited = self.write("patch", f"/api/v1/sessions/{sid}/reports/{report['id']}", {"comparison": "No prior comparison."}, revision=ws["session"]["revision"], key=False)
        signed = self.write("post", f"/api/v1/sessions/{sid}/reports/{report['id']}/approve", {"findings_version": 1}, revision=edited.json()["session_revision"])
        self.assertEqual(signed.status_code, 200, signed.text)

        # A fresh backend process on the same database file sees everything.
        restarted = self.make_app()
        doctor = self.client("doctor@example.test", restarted)
        ws = doctor.get(f"/api/v1/sessions/{sid}").json()
        self.assertEqual((ws["session"]["status"], ws["reports"][0]["status"], ws["reports"][0]["content"]["comparison"]), ("final", "final", "No prior comparison."))
        studies = doctor.get("/api/v1/patients/1/studies").json()["items"]
        self.assertEqual((studies[0]["status"], studies[0]["own_session_status"]), ("final", "final"))
        events = [e["type"] for e in doctor.get(f"/api/v1/sessions/{sid}/events").json()["items"]]
        self.assertEqual(events[0], "gate2_approved")
        self.assertIn("ai_request_succeeded", events)
        late = self.write("patch", f"/api/v1/sessions/{sid}/reports/{report['id']}", {"comparison": "x"}, revision=ws["session"]["revision"], key=False, who=doctor)
        self.assertEqual(late.json()["error"]["code"], "FINAL_READ_ONLY")

    def test_revision_and_idempotency_rules(self):
        ws = self.open()
        sid, rev = ws["session"]["id"], ws["session"]["revision"]
        url = f"/api/v1/sessions/{sid}/ai-requests"
        body = {"operation": "analyze", "image_id": 24110}
        self.assertEqual(self.doctor.post(url, json=body, headers={"Idempotency-Key": uuid.uuid4().hex}).status_code, 428)
        self.assertEqual(self.write("post", url, body, revision=rev + 5).json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.write("post", url, body, revision=rev, key=False).status_code, 422)  # AI needs an action key
        key = uuid.uuid4().hex
        headers = {"If-Match": f'"{rev}"', "Idempotency-Key": key}
        once = self.doctor.post(url, json=body, headers=headers)
        again = self.doctor.post(url, json=body, headers=headers)  # a retried click replays, it doesn't queue twice
        self.assertEqual((once.status_code, again.status_code, once.json()["request_id"]), (202, 202, again.json()["request_id"]))
        self.assertEqual(self.doctor.post(url, json={**body, "image_id": 24111}, headers=headers).json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        busy = self.write("post", f"/api/v1/sessions/{sid}/boxes", {"image_id": 24110, "bbox_xyxy": [1, 1, 50, 50]}, revision=once.json()["session_revision"])
        self.assertEqual(busy.json()["error"]["code"], "AI_BUSY")

    def test_ai_guards_pause_phi_and_monitoring(self):
        ws = self.analyze(self.open())
        sid, rev = ws["session"]["id"], ws["session"]["revision"]
        url = f"/api/v1/sessions/{sid}/ai-requests"
        phi = self.write("post", url, {"operation": "ask", "image_id": 24110, "question": "Compare PX-1042 with prior"}, revision=rev)
        self.assertEqual(phi.json()["error"]["code"], "PHI_IN_MODEL_INPUT")
        self.app.state.admin.set_ai_paused(True, 3)
        paused = self.write("post", url, {"operation": "ask", "image_id": 24110, "question": "Any effusion?"}, revision=rev)
        self.assertEqual((paused.status_code, paused.json()["error"]["code"]), (503, "AI_PAUSED"))
        self.app.state.admin.set_ai_paused(False, 3)
        asked = self.write("post", url, {"operation": "ask", "image_id": 24110, "question": "Any effusion?"}, revision=rev)
        self.assertEqual(asked.status_code, 202)
        ws = self.settle(ws)
        self.assertEqual([m["role"] for m in ws["messages"]["items"]], ["user", "assistant"])
        logged = self.app.state.admin.ai_requests()["items"]
        self.assertEqual([(r["operation"], r["status"]) for r in logged], [("ask", "succeeded"), ("analyze", "succeeded")])

    def test_access_rules(self):
        ws = self.open()
        sid = ws["session"]["id"]
        self.assertEqual(self.doctor.post("/api/v1/studies/2521/sessions").status_code, 404)  # PX-1052 isn't assigned to this doctor
        other = self.client("doctor2@example.test")
        self.assertEqual(other.get(f"/api/v1/sessions/{sid}").status_code, 404)  # someone else's reading
        self.assertEqual(self.client("admin@example.test").get(f"/api/v1/sessions/{sid}").json()["error"]["code"], "ROLE_FORBIDDEN")
        # Revoking access cancels a running AI job and hides the reading.
        self.write("post", f"/api/v1/sessions/{sid}/ai-requests", {"operation": "analyze", "image_id": 24110}, revision=ws["session"]["revision"])
        self.app.state.catalog.set_assignment(1, 1, False)
        self.assertEqual(self.doctor.get(f"/api/v1/sessions/{sid}").status_code, 404)
        self.app.state.catalog.set_assignment(1, 1, True)
        self.now += 2
        ws = self.doctor.get(f"/api/v1/sessions/{sid}").json()
        self.assertEqual(ws["latest_ai_request"]["status"], "succeeded")  # access was back before the job settled


if __name__ == "__main__":
    unittest.main()
