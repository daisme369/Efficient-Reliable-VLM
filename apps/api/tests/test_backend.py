import hashlib
import io
import secrets
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import func, select

from thorax.domain.annotation import PixelBox
from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AIRequest, Box, Event, Findings, Message, Report, Session
from thorax.presentation.api import create_app


class Journey:
    def __init__(self, backend, actor=2):
        self.backend = backend
        self.client = TestClient(create_app(backend), base_url=backend.settings.app_origin, raise_server_exceptions=False)
        response = self.client.post("/api/v1/auth/login", json={"email": f"actor{actor}@example.test", "password": backend.test_password}, headers={"Origin": backend.settings.app_origin})
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]
        self.session_id = None

    def request(self, method, path, payload=None, *, key=None, revision=None):
        headers = {"Origin": self.backend.settings.app_origin, "X-CSRF-Token": self.csrf}
        if method != "GET":
            headers["Idempotency-Key"] = key or secrets.token_urlsafe()
            if revision is None and self.session_id:
                revision = self.workspace()["session"]["revision"]
            if revision is not None:
                headers["If-Match"] = f'"{revision}"'
        return self.client.request(method, "/api/v1" + path, json=payload, headers=headers)

    def open(self, study=1):
        response = self.request("POST", f"/studies/{study}/sessions", {})
        assert response.status_code in {200, 201}, response.text
        self.session_id = response.json()["session"]["id"]
        return response.json()

    def workspace(self):
        response = self.request("GET", f"/sessions/{self.session_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def job(self, command):
        response = self.request("POST", f"/sessions/{self.session_id}/ai-requests", command)
        assert response.status_code == 202, response.text
        self.backend.worker.process(self.backend.worker.claim())
        return self.request("GET", f"/sessions/{self.session_id}/ai-requests/{response.json()['request_id']}").json()

    def analyze(self):
        assert self.job({"operation": "analyze", "image_id": 1})["status"] == "succeeded"

    def approve(self):
        workspace = self.workspace()
        for box in workspace["boxes"]:
            if box["source"] != "ai":
                continue
            response = self.request("PATCH", f"/sessions/{self.session_id}/boxes/{box['id']}", {"review_action": "accepted"})
            assert response.status_code == 200, response.text
        items = deepcopy(workspace["latest_findings"]["items"])
        for item in items:
            item["action"] = "accepted"
        response = self.request("PATCH", f"/sessions/{self.session_id}/findings", {"items": items, "no_abnormality_confirmed": not items})
        assert response.status_code == 200, response.text
        version = self.workspace()["latest_findings"]["version"]
        response = self.request("POST", f"/sessions/{self.session_id}/findings/approve", {"findings_version": version})
        assert response.status_code == 200, response.text
        return version


def test_full_workflow_and_final_replay(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    assert flow.job({"operation": "ask", "image_id": 1, "question": "Describe this synthetic fixture"})["status"] == "succeeded"
    region = flow.request("POST", f"/sessions/{flow.session_id}/boxes", {"image_id": 1, "bbox_xyxy": [1.2, 2.3, 12.4, 13.5]}).json()
    assert flow.job({"operation": "ask", "image_id": 1, "box_id": region["resource_id"], "question": "Describe the synthetic crop"})["status"] == "succeeded"
    image = PILImage.open(io.BytesIO(backend.provider.calls[-1][2]))
    assert image.size == (12, 12)
    version = flow.approve()
    assert flow.job({"operation": "draft_report", "findings_version": version})["status"] == "succeeded"
    operation, context, image = backend.provider.calls[-1]
    assert image is None and set(context) == {"request_id", "prompt_version", "approved_findings", "no_abnormality_confirmed"}
    report_id = flow.workspace()["reports"][0]["id"]
    key = secrets.token_urlsafe()
    revision = flow.workspace()["session"]["revision"]
    path = f"/sessions/{flow.session_id}/reports/{report_id}/approve"
    result = flow.request("POST", path, {"findings_version": version}, key=key, revision=revision)
    assert result.status_code == 200, result.text
    assert flow.request("POST", path, {"findings_version": version}, key=key, revision=revision).json() == result.json()
    assert flow.workspace()["session"]["status"] == "final"
    denied = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "ask", "image_id": 1, "question": "New question"})
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "FINAL_READ_ONLY"
    assert flow.request("GET", f"/sessions/{flow.session_id}/events").status_code == 200


def test_patient_and_role_isolation(backend):
    flow = Journey(backend)
    flow.open()
    assert len(flow.request("GET", "/patients").json()["items"]) == 1
    for path in ("/studies/2", "/images/2/content"):
        assert flow.request("GET", path).status_code == 404
    assert flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": 2}).status_code == 404
    admin = Journey(backend, 1)
    catalogue = admin.request("GET", "/admin/patient-catalog").json()["items"]
    assert set(catalogue[0]) == {"id", "code", "assigned_doctor_ids"}
    assert admin.request("GET", "/studies/1").status_code == 403
    other = Journey(backend, 3)
    other.open()
    assert other.session_id != flow.session_id
    assert other.request("GET", f"/sessions/{flow.session_id}").status_code == 404
    assert backend.provider.calls == []


def test_approval_guards_and_stale_box_correction(backend):
    flow = Journey(backend)
    flow.open()
    assert flow.request("POST", f"/sessions/{flow.session_id}/boxes", {"image_id": 1, "bbox_xyxy": [1, 1, 2, 2]}).json()["error"]["code"] == "ANALYSIS_REQUIRED"
    flow.analyze()
    assert flow.request("POST", f"/sessions/{flow.session_id}/findings/approve", {"findings_version": 1}).status_code == 409
    version = flow.approve()
    flow.job({"operation": "draft_report", "findings_version": version})
    original = flow.workspace()["latest_findings"]
    report = flow.workspace()["reports"][0]
    box = flow.workspace()["boxes"][0]
    assert flow.request("PATCH", f"/sessions/{flow.session_id}/boxes/{box['id']}", {"note": "Synthetic correction"}).status_code == 200
    current = flow.workspace()
    assert current["latest_findings"]["version"] == 2 and current["reports"][0]["status"] == "stale"
    with backend.database.unit_of_work() as uow:
        frozen = uow.repository.get(Findings, original["id"])
        assert frozen.status == "approved" and frozen.items == original["items"]
    assert flow.request("POST", f"/sessions/{flow.session_id}/reports/{report['id']}/approve", {"findings_version": version}).status_code == 409


def test_detector_failure_is_atomic_and_retry_is_explicit(backend):
    flow = Journey(backend)
    flow.open()
    backend.provider.failure = "detect"
    result = flow.job({"operation": "analyze", "image_id": 1})
    assert result["status"] == "failed" and result["error"]["retryable"]
    workspace = flow.workspace()
    assert workspace["latest_findings"] is None and workspace["boxes"] == []
    backend.provider.failure = None
    result = flow.job({"operation": "analyze", "image_id": 1, "retry_of": result["request_id"]})
    assert result["status"] == "succeeded"


def test_idempotency_and_revision_conflict(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    revision = flow.workspace()["session"]["revision"]
    path = f"/sessions/{flow.session_id}/boxes"
    body = {"image_id": 1, "bbox_xyxy": [1, 1, 2, 2]}
    key = secrets.token_urlsafe()
    first = flow.request("POST", path, body, key=key, revision=revision)
    assert first.status_code == 201
    assert flow.request("POST", path, body, key=key, revision=revision).json() == first.json()
    assert flow.request("POST", path, body, revision=revision).status_code == 412
    assert flow.request("POST", path, body | {"note": "changed"}, key=key).status_code == 409


def test_history_context_and_known_identifier_filter(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    context = backend.provider.calls[0][1]
    assert len(context["history"]) == 5
    assert [h["text"] for h in context["history"]] == [f"Synthetic earlier report {i}" for i in range(7, 2, -1)]
    rejected = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "ask", "image_id": 1, "question": "About ＳＹＮＴＨＥＴＩＣ－１"})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "PHI_IN_MODEL_INPUT"


def test_idle_poll_does_not_extend_login(backend):
    flow = Journey(backend)
    flow.open()
    backend.clock.advance(29 * 60)
    assert flow.request("GET", f"/sessions/{flow.session_id}").status_code == 200
    backend.clock.advance(61)
    response = flow.request("GET", "/auth/me")
    assert response.status_code == 401


def test_revoke_during_inference_prevents_publication(backend):
    flow = Journey(backend)
    flow.open()
    def revoke(operation):
        if operation == "analyze":
            backend.admin.revoke(1, 2, 1)
    backend.provider.on_call = revoke
    response = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": 1})
    job_id = response.json()["request_id"]
    backend.worker.process(backend.worker.claim())
    with backend.database.unit_of_work() as uow:
        job = uow.repository.get(AIRequest, job_id)
        assert job.status == "cancelled" and job.error_code == "AUTHORIZATION_REVOKED"
        assert uow.repository.latest_findings(flow.session_id) is None
    assert flow.request("GET", f"/sessions/{flow.session_id}").status_code == 404


def test_deadline_discards_late_result(backend):
    flow = Journey(backend)
    flow.open()
    backend.provider.on_call = lambda _: backend.clock.advance(61)
    result = flow.job({"operation": "analyze", "image_id": 1})
    assert result["status"] == "timed_out"
    assert flow.workspace()["latest_findings"] is None


def test_empty_findings_require_saved_normal_confirmation(backend):
    backend.provider.empty = True
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    assert flow.request("POST", f"/sessions/{flow.session_id}/findings/approve", {"findings_version": 1}).status_code == 409
    flow.approve()
    assert flow.job({"operation": "draft_report", "findings_version": 1})["status"] == "succeeded"
    assert backend.provider.calls[-1][1]["approved_findings"] == []


def test_validation_csrf_and_errors_do_not_echo_input(backend):
    flow = Journey(backend)
    flow.open()
    secret = "untrusted-sensitive-test-text"
    response = flow.request("POST", f"/studies/1/sessions", {"patient_id": secret})
    assert response.status_code == 422 and secret not in response.text
    assert flow.client.post("/api/v1/studies/1/sessions", json={}).status_code == 403
    response = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": True})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, private"


def test_image_bytes_and_fractional_crop_are_immutable(backend):
    flow = Journey(backend)
    flow.open()
    before = flow.request("GET", "/images/1/content").content
    flow.analyze()
    after = flow.request("GET", "/images/1/content").content
    assert hashlib.sha256(before).digest() == hashlib.sha256(after).digest()
    assert PixelBox(1.2, 2.3, 12.4, 13.5).crop_bounds() == (1, 2, 13, 14)
    with pytest.raises(DomainError):
        PixelBox(float("nan"), 0, 1, 1).validate(32, 32)


def test_audit_failure_rolls_back_clinical_mutation(backend, monkeypatch):
    from thorax.infrastructure.db.repositories import Repository
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    before = flow.workspace()
    original = Repository.add
    def add(repo, row):
        if isinstance(row, Event) and row.type == "box_created":
            raise RuntimeError("Synthetic database failure")
        return original(repo, row)
    monkeypatch.setattr(Repository, "add", add)
    response = flow.request("POST", f"/sessions/{flow.session_id}/boxes", {"image_id": 1, "bbox_xyxy": [2, 2, 3, 3]})
    assert response.status_code == 500
    assert flow.workspace()["session"]["revision"] == before["session"]["revision"]
    assert len(flow.workspace()["boxes"]) == len(before["boxes"])
