"""Doctor reading workflow against the SQLite backend: the same contract as the web demo's mock API."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
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

    def test_history_is_the_five_latest_reports_before_the_study(self):
        import sqlite3
        from thorax_api.reading import ReadingStore

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE history_reports (id INTEGER PRIMARY KEY, patient_id INTEGER, report_date TEXT, text TEXT)")
        # Saved oldest first, so id order and date order disagree with the demo seed's newest-first ids.
        dates = ["2026-01-02", "2026-02-02", "2026-03-02", "2026-04-02", "2026-05-02", "2026-06-02", "2026-07-02", "2026-09-01"]
        db.executemany("INSERT INTO history_reports VALUES (?, 1, ?, 'Synthetic prior report.')", list(enumerate(dates, start=1)))
        db.execute("INSERT INTO history_reports VALUES (9, 2, '2026-08-02', 'Other patient.')")
        history = ReadingStore._history(db, {"patient_id": 1, "study_date": "2026-09-01"})
        self.assertEqual([h["report_date"] for h in history], ["2026-07-02", "2026-06-02", "2026-05-02", "2026-04-02", "2026-03-02"])

    def test_full_reading_to_signed_report_and_it_survives_a_restart(self):
        first = self.doctor.post("/api/v1/studies/2411/sessions")
        self.assertEqual((first.status_code, first.headers["etag"]), (201, '"1"'))
        self.assertEqual(self.doctor.post("/api/v1/studies/2411/sessions").status_code, 200)  # reopening returns the same reading
        ws = first.json()
        sid = ws["session"]["id"]
        self.assertEqual([b["code"] for b in ws["blockers"]], ["ANALYSIS_REQUIRED", "GATE1_REQUIRED"])
        self.assertEqual(len(ws["history"]), 5)
        self.assertIsNone(ws["preliminary_report"])

        ws = self.analyze(ws)
        findings, boxes = ws["latest_findings"], ws["boxes"]
        self.assertEqual((findings["version"], findings["status"], len(findings["items"])), (1, "ai_proposed", 2))
        self.assertEqual(findings["items"][0]["box_ids"], [boxes[0]["id"]])
        self.assertEqual(ws["initial_analysis"]["status"], "succeeded")
        preliminary = ws["preliminary_report"]
        self.assertEqual(preliminary, {"image_id": 24110, "text": "\n".join(item["text"] for item in findings["items"])})

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
        self.assertEqual(ws["preliminary_report"], preliminary)
        studies = doctor.get("/api/v1/patients/1/studies").json()["items"]
        self.assertEqual((studies[0]["status"], studies[0]["own_session_status"]), ("final", "final"))
        events = [e["type"] for e in doctor.get(f"/api/v1/sessions/{sid}/events").json()["items"]]
        self.assertEqual(events[0], "gate2_approved")
        self.assertIn("ai_request_succeeded", events)
        late = self.write("patch", f"/api/v1/sessions/{sid}/reports/{report['id']}", {"comparison": "x"}, revision=ws["session"]["revision"], key=False, who=doctor)
        self.assertEqual(late.json()["error"]["code"], "FINAL_READ_ONLY")

        # PX-1042 is shared: doctor 1's final reading does not lock doctor 2's independent reading.
        other = self.client("doctor2@example.test", restarted)
        shared = other.get("/api/v1/patients/1/studies").json()["items"][0]
        self.assertEqual((shared["id"], shared["status"], shared["own_session_status"]), (2411, "final", None))
        opened = other.post("/api/v1/studies/2411/sessions")
        self.assertEqual(opened.status_code, 201, opened.text)
        self.assertNotEqual(opened.json()["session"]["id"], sid)
        self.assertNotEqual(opened.json()["session"]["status"], "final")
        self.assertIn("ANALYSIS_REQUIRED", [b["code"] for b in opened.json()["blockers"]])

    def test_analysis_boxes_follow_image_view(self):
        for study_id, view, count in ((2431, "AP", 1), (2411, "LAT", 0)):
            with self.subTest(view=view):
                ws = self.open(study_id)
                sid = ws["session"]["id"]
                image = next(i for i in ws["images"] if i["view"] == view)
                job = self.write("post", f"/api/v1/sessions/{sid}/ai-requests", {"operation": "analyze", "image_id": image["id"]}, revision=ws["session"]["revision"])
                self.assertEqual(job.status_code, 202, job.text)
                ws = self.settle(ws)
                self.assertEqual(ws["initial_analysis"]["status"], "succeeded")
                self.assertEqual(ws["preliminary_report"]["image_id"], image["id"])
                self.assertTrue(ws["preliminary_report"]["text"].strip())
                self.assertEqual(len(ws["boxes"]), count)
                if count:
                    box = ws["boxes"][0]
                    self.assertEqual((box["image_id"], box["source"], box["review_action"]), (image["id"], "ai", "pending"))
                    x1, y1, x2, y2 = box["bbox_xyxy"]
                    self.assertTrue(0 <= x1 < x2 <= image["width"] and 0 <= y1 < y2 <= image["height"])
                    self.assertEqual(ws["latest_findings"]["items"][0]["box_ids"], [box["id"]])

    def test_detector_failure_publishes_no_analysis_artifacts(self):
        with patch.dict(os.environ, {"THORAX_DEMO_AI_FAULT": "detector"}):
            ws = self.analyze(self.open())
        self.assertEqual(ws["latest_ai_request"]["status"], "failed")
        self.assertIsNone(ws["preliminary_report"])
        self.assertIsNone(ws["latest_findings"])
        self.assertEqual(ws["boxes"], [])

    def test_legacy_analysis_without_preliminary_report(self):
        from thorax_api.db import connect

        ws = self.analyze(self.open())
        sid = ws["session"]["id"]
        with connect(self.db) as db:
            db.execute("DROP TABLE preliminary_reports")
        doctor = self.client("doctor@example.test", self.make_app())
        restored = doctor.get(f"/api/v1/sessions/{sid}").json()
        self.assertIsNone(restored["preliminary_report"])
        for field in ("initial_analysis", "latest_findings", "boxes"):
            self.assertEqual(restored[field], ws[field])

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

    # --- SW2 acceptance checks (TAS-18..21); helpers keep the revision chain explicit -------------
    def ask(self, ws, image_id, question, box_id=None, revision=None):
        body = {"operation": "ask", "image_id": image_id, "question": question, **({"box_id": box_id} if box_id else {})}
        return self.write("post", f"/api/v1/sessions/{ws['session']['id']}/ai-requests", body, revision=revision or ws["session"]["revision"])

    def save_findings(self, ws, items, normal):
        return self.write("patch", f"/api/v1/sessions/{ws['session']['id']}/findings",
                          {"items": items, "no_abnormality_confirmed": normal}, revision=ws["session"]["revision"], key=False)

    def test_chat_keeps_last_ten_messages_labels_images_and_excludes_failures(self):  # TAS-18
        ws = self.open()
        self.assertEqual(self.ask(ws, 24110, "Any effusion?").json()["error"]["code"], "ANALYSIS_REQUIRED")
        ws = self.analyze(ws)
        for turn in range(6):  # 12 messages, alternating the PA and LAT images of a multi-image study
            image = (24110, 24111)[turn % 2]
            self.assertEqual(self.ask(ws, image, f"Question {turn}?").status_code, 202)
            ws = self.settle(ws)
        messages = ws["messages"]["items"]
        self.assertEqual(len(messages), 12)
        self.assertEqual([m["image_id"] for m in messages], [24110, 24110, 24111, 24111] * 3)
        with patch.dict(os.environ, {"THORAX_DEMO_AI_FAULT": "timeout"}):
            self.assertEqual(self.ask(ws, 24110, "Will this time out?").status_code, 202)
            self.now += 61
            ws = self.doctor.get(f"/api/v1/sessions/{ws['session']['id']}").json()
        self.assertEqual(ws["latest_ai_request"]["status"], "timed_out")
        self.assertEqual(len(ws["messages"]["items"]), 12)  # a failed request adds nothing to the chat or later context
        request = self.ask(ws, 24111, "Final question?").json()
        self.assertEqual(request["provenance"]["message_ids"], [m["id"] for m in messages[-10:]])  # last 10 messages, not 10 turns
        ws = self.settle(ws)
        restarted = self.client("doctor@example.test", self.make_app())
        reopened = restarted.get(f"/api/v1/sessions/{ws['session']['id']}").json()["messages"]["items"]
        self.assertEqual([(m["id"], m["image_id"], m["text"]) for m in reopened], [(m["id"], m["image_id"], m["text"]) for m in ws["messages"]["items"]])

    def test_roi_bounds_image_binding_and_answer_snapshot_survive_box_changes(self):  # TAS-19
        ws = self.analyze(self.open())
        sid = ws["session"]["id"]
        boxes = f"/api/v1/sessions/{sid}/boxes"
        for bad in ([0, 0, 9999, 10], [50, 50, 10, 10], [1, 2, 3], [-1, 0, 10, 10]):
            self.assertEqual(self.write("post", boxes, {"image_id": 24110, "bbox_xyxy": bad}, revision=ws["session"]["revision"]).status_code, 422, bad)
        # httpx refuses to encode NaN, so send the raw JSON a buggy client could produce.
        nan = self.doctor.post(boxes, content='{"image_id":24110,"bbox_xyxy":[NaN,1,50,50]}', headers={
            "Content-Type": "application/json", "If-Match": f'"{ws["session"]["revision"]}"', "Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(nan.status_code, 422, nan.text)
        self.assertEqual(self.write("post", boxes, {"image_id": 25210, "bbox_xyxy": [1, 1, 5, 5]}, revision=ws["session"]["revision"]).status_code, 404)
        made = self.write("post", boxes, {"image_id": 24110, "bbox_xyxy": [100.5, 120.25, 300, 310], "label": "Doctor ROI"}, revision=ws["session"]["revision"])
        self.assertEqual(made.status_code, 201, made.text)
        box_id, rev = made.json()["resource_id"], made.json()["session_revision"]
        self.assertEqual(self.ask(ws, 24111, "What is here?", box_id, rev).status_code, 404)  # region belongs to another image
        self.assertEqual(self.ask(ws, 24110, "What is here?", box_id, rev).status_code, 202)
        ws = self.settle(ws)
        answer = ws["messages"]["items"][-1]
        self.assertEqual((answer["box_id"], answer["box_snapshot"]["bbox_xyxy"]), (box_id, [100.5, 120.25, 300, 310]))
        moved = self.write("patch", f"{boxes}/{box_id}", {"bbox_xyxy": [10, 10, 60, 60]}, revision=ws["session"]["revision"], key=False)
        deleted = self.write("delete", f"{boxes}/{box_id}", revision=moved.json()["session_revision"], key=False)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        ws = self.doctor.get(f"/api/v1/sessions/{sid}").json()
        self.assertEqual([m["box_snapshot"]["bbox_xyxy"] for m in ws["messages"]["items"]], [[100.5, 120.25, 300, 310]] * 2)
        self.assertTrue(next(b for b in ws["boxes"] if b["id"] == box_id)["deleted_at"])
        image = self.doctor.get("/api/v1/images/24110/content").content
        self.assertEqual(hashlib.sha256(image).hexdigest(), ws["images"][0]["sha256"])  # the original is never altered

    def test_gate1_blocks_pending_bypass_and_contradictions_and_signs_once(self):  # TAS-20
        from thorax_api.db import connect
        ws = self.analyze(self.open())
        sid = ws["session"]["id"]
        ai_items, box = ws["latest_findings"]["items"], ws["boxes"][0]
        approve = lambda revision: self.write("post", f"/api/v1/sessions/{sid}/findings/approve", {"findings_version": 1}, revision=revision)
        self.assertEqual(approve(ws["session"]["revision"]).json()["error"]["code"], "UNRESOLVED_REVIEW")
        rejected = [{**item, "action": "rejected"} for item in ai_items]
        self.assertEqual(self.save_findings(ws, rejected[:1], False).status_code, 422)  # dropping an AI finding is not a bypass
        self.assertEqual(self.save_findings(ws, [{**ai_items[0], "action": "accepted"}, rejected[1]], True).status_code, 422)  # contradictory normal
        linked = {**rejected[1], "action": "edited", "text": "Edited", "box_ids": [box["id"]], "image_id": 24111}
        self.assertEqual(self.save_findings(ws, [rejected[0], linked], False).status_code, 422)  # image changed / cross-image link
        saved = self.save_findings(ws, rejected, False)
        ws = self.doctor.get(f"/api/v1/sessions/{sid}").json()
        codes = [b["code"] for b in ws["blockers"] if b["gate"] == "gate1"]
        self.assertIn("NORMAL_CONFIRMATION_REQUIRED", codes)
        self.assertIn("UNRESOLVED_REVIEW", codes)  # the AI region is still pending
        self.assertEqual(approve(saved.json()["session_revision"]).json()["error"]["code"], "UNRESOLVED_REVIEW")
        rev = self.write("patch", f"/api/v1/sessions/{sid}/boxes/{box['id']}", {"review_action": "rejected"}, revision=ws["session"]["revision"], key=False).json()["session_revision"]
        ws = self.doctor.get(f"/api/v1/sessions/{sid}").json()
        # A rejected region can no longer be linked as evidence, so the final save drops the link.
        rev = self.save_findings(ws, [{**item, "box_ids": []} for item in rejected], True).json()["session_revision"]
        headers = {"If-Match": f'"{rev}"', "Idempotency-Key": uuid.uuid4().hex}
        first = self.doctor.post(f"/api/v1/sessions/{sid}/findings/approve", json={"findings_version": 1}, headers=headers)
        replay = self.doctor.post(f"/api/v1/sessions/{sid}/findings/approve", json={"findings_version": 1}, headers=headers)  # double click
        again = approve(first.json()["session_revision"])  # a second, separate approval of the same version
        self.assertEqual((first.status_code, replay.status_code, again.status_code), (200, 200, 200), first.text)
        self.assertEqual(replay.json(), first.json())
        events = [e["type"] for e in self.doctor.get(f"/api/v1/sessions/{sid}/events").json()["items"]]
        self.assertEqual(events.count("gate1_approved"), 1)
        reopened = self.write("post", f"/api/v1/sessions/{sid}/boxes", {"image_id": 24110, "bbox_xyxy": [1, 1, 9, 9]}, revision=first.json()["session_revision"])
        self.assertEqual(reopened.status_code, 201)
        with connect(self.db) as db:
            rows = [dict(r) for r in db.execute("SELECT version, status, annotation_snapshot FROM findings_versions WHERE session_id = ? ORDER BY version", (sid,))]
        self.assertEqual([(r["version"], r["status"]) for r in rows], [(1, "approved"), (2, "in_review")])
        self.assertTrue(rows[0]["annotation_snapshot"])  # the signed version keeps its evidence snapshot

    def test_gate2_needs_gate1_rejects_stale_and_identity_edits_and_locks_final(self):  # TAS-21
        ws = self.analyze(self.open())
        sid = ws["session"]["id"]
        url = f"/api/v1/sessions/{sid}"
        draft = lambda revision, version: self.write("post", f"{url}/ai-requests", {"operation": "draft_report", "findings_version": version}, revision=revision)
        self.assertEqual(draft(ws["session"]["revision"], 1).json()["error"]["code"], "GATE1_REQUIRED")
        self.assertEqual(self.write("post", f"{url}/reports", {"findings_version": 1}, revision=ws["session"]["revision"]).json()["error"]["code"], "GATE1_REQUIRED")
        rev = self.write("patch", f"{url}/boxes/{ws['boxes'][0]['id']}", {"review_action": "accepted"}, revision=ws["session"]["revision"], key=False).json()["session_revision"]
        ws = self.doctor.get(url).json()
        items = [{**ws["latest_findings"]["items"][0], "action": "accepted"}, {**ws["latest_findings"]["items"][1], "action": "rejected"}]
        rev = self.save_findings(ws, items, False).json()["session_revision"]
        rev = self.write("post", f"{url}/findings/approve", {"findings_version": 1}, revision=rev).json()["session_revision"]
        self.assertEqual(draft(rev, 1).status_code, 202)
        ws = self.settle(ws)
        report = ws["reports"][0]
        self.assertEqual((report["findings_version"], report["content"]["source_item_ids"]), (1, [items[0]["id"]]))
        reports = f"{url}/reports/{report['id']}"
        self.assertEqual(self.write("patch", reports, {"patient": {"name": "Someone else"}}, revision=ws["session"]["revision"], key=False).status_code, 422)
        rev = self.write("patch", reports, {"impression": " "}, revision=ws["session"]["revision"], key=False).json()["session_revision"]
        self.assertEqual(self.write("post", f"{reports}/approve", {"findings_version": 1}, revision=rev).status_code, 422)  # required sections
        rev = self.write("patch", reports, {"impression": "Left basilar opacity."}, revision=rev, key=False).json()["session_revision"]
        rev = self.write("post", f"{url}/boxes", {"image_id": 24110, "bbox_xyxy": [1, 1, 9, 9]}, revision=rev).json()["session_revision"]
        stale = self.write("post", f"{reports}/approve", {"findings_version": 1}, revision=rev)
        self.assertEqual(stale.json()["error"]["code"], "REPORT_STALE")
        self.assertEqual(self.doctor.get(url).json()["reports"][0]["status"], "stale")
        rev = self.write("post", f"{url}/findings/approve", {"findings_version": 2}, revision=rev).json()["session_revision"]
        created = self.write("post", f"{url}/reports", {"findings_version": 2}, revision=rev)
        self.assertEqual(created.status_code, 201, created.text)
        new_report, rev = created.json()["resource_id"], created.json()["session_revision"]
        signed = self.write("post", f"{url}/reports/{new_report}/approve", {"findings_version": 2}, revision=rev)
        self.assertEqual(signed.status_code, 200, signed.text)
        rev = signed.json()["session_revision"]
        events = [e["type"] for e in self.doctor.get(f"{url}/events").json()["items"]]
        self.assertEqual((events.count("gate1_approved"), events.count("gate2_approved")), (2, 1))  # two gates, signed separately
        for path, body in ((f"{url}/ai-requests", {"operation": "ask", "image_id": 24110, "question": "Anything else?"}),
                           (f"{url}/boxes", {"image_id": 24110, "bbox_xyxy": [1, 1, 9, 9]})):
            self.assertEqual(self.write("post", path, body, revision=rev).json()["error"]["code"], "FINAL_READ_ONLY", path)

    def test_concurrent_opens_create_one_session_bound_to_the_url_study(self):
        from concurrent.futures import ThreadPoolExecutor
        store = self.app.state.readings
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.open_reading(2411, 1, (None, None)), range(8)))
        self.assertEqual(sorted(status for _, status in results), [200] * 7 + [201])
        self.assertEqual({ws["session"]["id"] for ws, _ in results}, {results[0][0]["session"]["id"]})
        # The binding comes from the URL and the signed-in doctor only; a body cannot redirect it.
        forged = self.doctor.post("/api/v1/studies/2411/sessions", json={"patient_id": 3, "doctor_id": 2})
        self.assertEqual(forged.status_code, 422)
        ws = self.doctor.post("/api/v1/studies/2411/sessions").json()
        self.assertEqual((ws["session"]["patient_id"], ws["session"]["study_id"], ws["session"]["doctor_id"]), (1, 2411, 1))
        self.assertEqual(ws["session"]["id"], results[0][0]["session"]["id"])

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

    def test_late_poll_keeps_completed_mock_analysis_and_injected_failures(self):
        for study, fault, expected in ((2431, "", "succeeded"), (2411, "timeout", "timed_out"), (2421, "detector", "failed")):
            with self.subTest(fault=fault), patch.dict(os.environ, {"THORAX_DEMO_AI_FAULT": fault}):
                ws = self.open(study)
                sid = ws["session"]["id"]
                response = self.write("post", f"/api/v1/sessions/{sid}/ai-requests",
                                      {"operation": "analyze", "image_id": ws["images"][0]["id"]}, revision=ws["session"]["revision"])
                self.assertEqual(response.status_code, 202, response.text)
                self.now += 61
                resumed = self.open(study)
                self.assertEqual(resumed["latest_ai_request"]["status"], expected)
                self.assertEqual(bool(resumed["latest_findings"]), expected == "succeeded")
                self.assertEqual(bool(resumed["preliminary_report"]), expected == "succeeded")
                again = self.doctor.get(f"/api/v1/sessions/{sid}").json()
                self.assertEqual(again, resumed)

    def test_manual_reading_reaches_final_while_ai_is_paused(self):
        self.app.state.admin.set_ai_paused(True, 3)
        ws = self.open()
        sid, revision = ws["session"]["id"], ws["session"]["revision"]
        url = f"/api/v1/sessions/{sid}"
        early = self.write("post", url + "/reports", {"findings_version": 1}, revision=revision)
        self.assertEqual(early.json()["error"]["code"], "GATE1_REQUIRED")
        started = self.write("post", url + "/findings", {}, revision=revision)
        self.assertEqual(started.status_code, 201, started.text)
        revision = started.json()["session_revision"]
        empty = self.write("post", url + "/findings/approve", {"findings_version": 1}, revision=revision)
        self.assertEqual(empty.json()["error"]["code"], "UNRESOLVED_REVIEW")
        added = self.write("post", url + "/boxes", {"image_id": 24110, "bbox_xyxy": [20, 30, 80, 100]}, revision=revision)
        self.assertEqual(added.status_code, 201, added.text)
        item = {"id": "doctor-1", "text": "Synthetic physician observation.", "location": "Left base", "image_id": 24110,
                "source": "doctor", "action": "added", "box_ids": [added.json()["resource_id"]]}
        saved = self.write("patch", url + "/findings", {"items": [item], "no_abnormality_confirmed": False},
                           revision=added.json()["session_revision"], key=False)
        self.assertEqual(saved.status_code, 200, saved.text)
        approved = self.write("post", url + "/findings/approve", {"findings_version": 1}, revision=saved.json()["session_revision"])
        self.assertEqual(approved.status_code, 200, approved.text)
        created = self.write("post", url + "/reports", {"findings_version": 1}, revision=approved.json()["session_revision"])
        self.assertEqual(created.status_code, 201, created.text)
        report_id = created.json()["resource_id"]
        edited = self.write("patch", f"{url}/reports/{report_id}", {"impression": "Synthetic signed assessment."},
                            revision=created.json()["session_revision"], key=False)
        self.assertEqual(edited.status_code, 200, edited.text)
        final = self.write("post", f"{url}/reports/{report_id}/approve", {"findings_version": 1}, revision=edited.json()["session_revision"])
        self.assertEqual(final.status_code, 200, final.text)
        doctor = self.client("doctor@example.test", self.make_app())
        restored = doctor.get(url).json()
        self.assertEqual(restored["session"]["status"], "final")
        self.assertEqual(restored["reports"][0]["source"], "doctor")
        self.assertIsNone(restored["reports"][0]["content"]["study"]["analyzed_image_id"])
        for field in ("analyzed_image_id", "initial_analysis", "preliminary_report", "latest_ai_request"):
            self.assertIsNone(restored[field])
        self.assertEqual(self.app.state.admin.ai_requests()["items"], [])
        late = self.write("patch", url + "/findings", {"items": [], "no_abnormality_confirmed": True},
                          revision=restored["session"]["revision"], key=False, who=doctor)
        self.assertEqual(late.json()["error"]["code"], "FINAL_READ_ONLY")

    def test_late_job_cancellation_keeps_analysis_artifacts_empty(self):
        ws = self.open()
        url = f"/api/v1/sessions/{ws['session']['id']}"
        submitted = self.write("post", url + "/ai-requests", {"operation": "analyze", "image_id": 24110}, revision=ws["session"]["revision"])
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.now += 61
        with patch.object(self.app.state.readings, "is_allowed", return_value=False):
            resumed = self.doctor.get(url).json()
        self.assertEqual(resumed["latest_ai_request"]["status"], "cancelled")
        self.assertEqual(resumed["latest_ai_request"]["error"]["code"], "AUTHORIZATION_REVOKED")
        self.assertIsNone(resumed["preliminary_report"])
        self.assertIsNone(resumed["latest_findings"])
        self.assertEqual(resumed["boxes"], [])

    def test_manual_start_and_report_creation_keep_action_keys_and_revisions(self):
        ws = self.open()
        url = f"/api/v1/sessions/{ws['session']['id']}"
        revision = ws["session"]["revision"]
        missing_key = self.write("post", url + "/findings", {}, revision=revision, key=False)
        self.assertEqual(missing_key.status_code, 422)
        stale = self.write("post", url + "/findings", {}, revision=revision + 1)
        self.assertEqual(stale.status_code, 412)
        headers = {"If-Match": f'"{revision}"', "Idempotency-Key": uuid.uuid4().hex}
        once = self.doctor.post(url + "/findings", json={}, headers=headers)
        replay = self.doctor.post(url + "/findings", json={}, headers=headers)
        self.assertEqual((once.status_code, replay.json()), (201, once.json()))
        revision = once.json()["session_revision"]
        duplicate = self.write("post", url + "/findings", {}, revision=revision)
        self.assertEqual(duplicate.json()["error"]["code"], "FINDINGS_ALREADY_EXIST")
        analyze = self.write("post", url + "/ai-requests", {"operation": "analyze", "image_id": 24110}, revision=revision)
        self.assertEqual(analyze.json()["error"]["code"], "FINDINGS_ALREADY_EXIST")
        ask = self.write("post", url + "/ai-requests", {"operation": "ask", "image_id": 24110, "question": "Any observation?"}, revision=revision)
        self.assertEqual(ask.json()["error"]["code"], "ANALYSIS_REQUIRED")
        normal = self.write("patch", url + "/findings", {"items": [], "no_abnormality_confirmed": True}, revision=revision, key=False)
        approved = self.write("post", url + "/findings/approve", {"findings_version": 1}, revision=normal.json()["session_revision"])
        self.assertEqual(approved.status_code, 200, approved.text)
        version_mismatch = self.write("post", url + "/reports", {"findings_version": 2}, revision=approved.json()["session_revision"])
        self.assertEqual(version_mismatch.json()["error"]["code"], "GATE1_REQUIRED")
        headers = {"If-Match": f'"{approved.json()["session_revision"]}"', "Idempotency-Key": uuid.uuid4().hex}
        once = self.doctor.post(url + "/reports", json={"findings_version": 1}, headers=headers)
        replay = self.doctor.post(url + "/reports", json={"findings_version": 1}, headers=headers)
        self.assertEqual((once.status_code, replay.json()), (201, once.json()))
        before = self.doctor.get(url).json()
        changed = self.write("patch", url + "/findings", {"items": [], "no_abnormality_confirmed": True},
                             revision=before["session"]["revision"], key=False)
        self.assertEqual(changed.status_code, 200, changed.text)
        after = self.doctor.get(url).json()
        self.assertEqual((after["latest_findings"]["version"], after["latest_findings"]["status"], after["reports"][0]["status"]),
                         (2, "in_review", "stale"))

    def test_analyzed_reading_can_create_a_manual_report_during_pause(self):
        ws = self.analyze(self.open(2411))
        url = f"/api/v1/sessions/{ws['session']['id']}"
        decision = self.write("patch", f"{url}/boxes/{ws['boxes'][0]['id']}", {"review_action": "accepted"}, revision=ws["session"]["revision"], key=False)
        items = [{**item, "action": "accepted"} for item in ws["latest_findings"]["items"]]
        saved = self.write("patch", url + "/findings", {"items": items, "no_abnormality_confirmed": False}, revision=decision.json()["session_revision"], key=False)
        approved = self.write("post", url + "/findings/approve", {"findings_version": 1}, revision=saved.json()["session_revision"])
        ai = self.write("post", url + "/ai-requests", {"operation": "draft_report", "findings_version": 1}, revision=approved.json()["session_revision"])
        self.assertEqual(ai.status_code, 202, ai.text)
        self.now += 61
        ws = self.doctor.get(url).json()
        self.assertEqual(ws["reports"][0]["source"], "ai")
        self.app.state.admin.set_ai_paused(True, 3)
        manual = self.write("post", url + "/reports", {"findings_version": 1}, revision=ws["session"]["revision"])
        self.assertEqual(manual.status_code, 201, manual.text)
        ws = self.doctor.get(url).json()
        self.assertEqual([(r["status"], r["source"]) for r in ws["reports"]], [("stale", "ai"), ("draft", "doctor")])
        self.assertEqual(ws["analyzed_image_id"], 24110)

    def test_transaction_guard_checks_access_before_receipt_replay(self):
        from thorax_api.reading import ReadingError

        ws = self.open()
        sid = ws["session"]["id"]
        key = uuid.uuid4().hex
        options = {"if_match": f'"{ws["session"]["revision"]}"', "idempotency_key": key,
                   "path": f"/api/v1/sessions/{sid}/findings", "method": "POST"}
        store = self.app.state.readings
        receipt = store.mutate(sid, 1, "start_manual", {}, **options)
        self.app.state.catalog.set_assignment(1, 1, False)
        for call in (lambda: store.mutate(sid, 1, "start_manual", {}, **options), lambda: store.open_reading(2411, 1, (None, None)),
                     lambda: store.read(sid, 1, "workspace", None, None)):
            with self.assertRaises(ReadingError) as failure:
                call()
            self.assertEqual(failure.exception.status, 404)
        self.app.state.catalog.set_assignment(1, 1, True)
        self.app.state.accounts.set_active("doctor@example.test", False)
        with self.assertRaises(ReadingError):
            store.mutate(sid, 1, "start_manual", {}, **options)
        self.app.state.accounts.set_active("doctor@example.test", True)
        self.assertEqual(store.mutate(sid, 1, "start_manual", {}, **options), receipt)

    def test_transaction_guard_rechecks_current_login_before_open_or_replay(self):
        from thorax_api.reading import ReadingError

        store = self.app.state.readings
        ws = self.open()
        options = {"if_match": f'"{ws["session"]["revision"]}"', "idempotency_key": uuid.uuid4().hex,
                   "path": f"/api/v1/sessions/{ws['session']['id']}/findings", "method": "POST"}
        store.mutate(ws["session"]["id"], 1, "start_manual", {}, **options)
        guard = store._authorize
        def checked_guard(db, actor_id, patient_id, authorize):
            self.assertTrue(db.in_transaction)
            return guard(db, actor_id, patient_id, authorize)
        def expired():
            raise ReadingError(401, "UNAUTHENTICATED", "The login expired.")
        with patch.object(store, "_authorize", side_effect=checked_guard) as checked:
            with self.assertRaises(ReadingError):
                store.mutate(ws["session"]["id"], 1, "start_manual", {}, authorize=expired, **options)
            with self.assertRaises(ReadingError):
                store.open_reading(2421, 1, (None, None), authorize=expired)
            self.assertEqual(checked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
