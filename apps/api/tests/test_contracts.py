import ast
import hashlib
import io
import json
import os
import secrets
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AIRequest, Event, Message
from thorax.inference.api import MockInference
from thorax.inference.contract import InferenceBudget, InferenceContract
from test_backend import Journey


def test_auth_expiry_has_timezone_after_database_roundtrip(backend):
    flow = Journey(backend)
    result = flow.request("GET", "/auth/me")
    expires = datetime.fromisoformat(result.json()["expires_at"].replace("Z", "+00:00"))
    assert expires.tzinfo is not None
    assert (expires - backend.clock.now()).total_seconds() == backend.settings.policy["security"]["idle_minutes"] * 60


def test_workspace_keeps_initial_provenance_and_roi_chat_contract(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    initial = flow.workspace()["initial_analysis"]
    assert initial["provenance"]["vlm_artifact"] == backend.provider.artifact
    assert initial["provenance"]["cv_artifact"] == backend.provider.artifact
    assert initial["provenance"]["started_at"] and initial["provenance"]["completed_at"]
    box = flow.workspace()["boxes"][0]
    flow.job({"operation": "ask", "image_id": 1, "box_id": box["id"], "question": "Describe the synthetic ROI"})
    workspace = flow.workspace()
    assert workspace["initial_analysis"]["request_id"] == initial["request_id"]
    messages = workspace["messages"]["items"]
    assert [item["role"] for item in messages] == ["doctor", "ai"]
    assert all(item["box_snapshot"]["box_id"] == box["id"] and "id" not in item["box_snapshot"] for item in messages)


def test_gate1_fresh_key_and_repeated_box_delete_are_noops(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    version = flow.approve()
    revision = flow.workspace()["session"]["revision"]
    result = flow.request("POST", f"/sessions/{flow.session_id}/findings/approve", {"findings_version": version})
    assert result.status_code == 200 and result.json()["session_revision"] == revision
    with backend.database.unit_of_work() as uow:
        events = uow.repository.all(Event, Event.session_id == flow.session_id, Event.type == "gate1_approved")
        assert len(events) == 1
    box_id = flow.workspace()["boxes"][0]["id"]
    assert flow.request("DELETE", f"/sessions/{flow.session_id}/boxes/{box_id}").status_code == 200
    revision = flow.workspace()["session"]["revision"]
    assert flow.request("DELETE", f"/sessions/{flow.session_id}/boxes/{box_id}").json()["session_revision"] == revision


def test_failed_chat_restores_submission_but_never_enters_context(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    backend.provider.failure = "ask"
    failed = flow.job({"operation": "ask", "image_id": 1, "question": "Unsuccessful synthetic question"})
    assert failed["status"] == "failed"
    assert flow.workspace()["latest_ai_request"]["submitted_input"]["question"] == "Unsuccessful synthetic question"
    assert flow.workspace()["messages"]["items"] == []
    backend.provider.failure = None
    assert flow.job({"operation": "ask", "image_id": 1, "question": "Fresh synthetic question"})["status"] == "succeeded"
    assert backend.provider.calls[-1][1]["last_messages"] == []


def test_chat_context_is_ten_messages_not_ten_turns(backend):
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    for index in range(7):
        assert flow.job({"operation": "ask", "image_id": 1, "question": f"Synthetic question {index}"})["status"] == "succeeded"
    messages = backend.provider.calls[-1][1]["last_messages"]
    assert len(messages) == 10
    assert messages[0]["text"] == "Synthetic question 1"
    assert all("image_ref" in item for item in messages)


def test_job_replay_survives_provider_outage(backend):
    flow = Journey(backend)
    flow.open()
    key = secrets.token_urlsafe()
    path = f"/sessions/{flow.session_id}/ai-requests"
    body = {"operation": "analyze", "image_id": 1}
    first = flow.request("POST", path, body, key=key)
    assert first.status_code == 202
    def unavailable():
        raise DomainError("AI_UNAVAILABLE", 503)
    backend.provider.capabilities = unavailable
    replay = flow.request("POST", path, body, key=key, revision=1)
    assert replay.status_code == 202 and replay.json()["request_id"] == first.json()["request_id"]


def test_worker_loss_and_queued_deadline_are_reaped(backend):
    flow = Journey(backend)
    flow.open()
    result = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": 1}).json()
    backend.worker.claim()
    backend.clock.advance(backend.settings.policy["jobs"]["lease_seconds"] + 1)
    backend.worker.reap()
    status = flow.request("GET", result["poll_url"].removeprefix("/api/v1")).json()
    assert status["status"] == "failed" and status["error"]["code"] == "WORKER_LOST"
    retry = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": 1, "retry_of": status["request_id"]}).json()
    backend.clock.advance(backend.settings.policy["jobs"]["deadline_seconds"] + 1)
    backend.worker.reap()
    assert flow.request("GET", retry["poll_url"].removeprefix("/api/v1")).json()["status"] == "timed_out"


def test_canonical_import_strips_exif(backend, tmp_path):
    source = tmp_path / "with-metadata.jpg"
    image = PILImage.new("RGB", (16, 16))
    exif = image.getexif()
    exif[270] = "Synthetic sensitive metadata"
    image.save(source, exif=exif)
    prepared = backend.images.prepare(source, "test-version")
    saved = PILImage.open(backend.settings.image_root / prepared["file_path"])
    assert not saved.getexif() and "exif" not in saved.info


def test_domain_has_no_framework_or_infrastructure_imports():
    for source in (Path(__file__).parents[1] / "thorax/domain").glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.split(".")[0] in {"sqlalchemy", "fastapi", "pydantic", "httpx", "PIL"} or "infrastructure" in name for name in names)


def test_mock_service_requires_private_token_and_uses_external_fixtures(backend):
    provider = backend.provider
    image = io.BytesIO()
    PILImage.new("RGB", (16, 16)).save(image, format="PNG")
    payload = image.getvalue()
    response = provider.invoke("analyze", {}, payload, 1)
    manifest = {"capabilities": provider.capabilities(), "operations": {"analyze": {hashlib.sha256(payload).hexdigest(): response}}}
    client = TestClient(MockInference(backend.settings, manifest).app)
    assert client.get("/internal/v1/capabilities").status_code == 401
    headers = {"Authorization": f"Bearer {backend.settings.inference_token}",
               InferenceContract.DEADLINE_HEADER: InferenceBudget(10).wire_deadline}
    result = client.post("/internal/v1/analyze", headers=headers, files={"image": ("image.png", payload)}, data={"context": json.dumps({"request_id": "synthetic", "indication": "Synthetic test", "history": [], "prompt_version": "test", "preprocessing_version": "test"})})
    assert result.status_code == 200 and result.json() == response
    with pytest.raises(ValueError):
        MockInference(replace(backend.settings, provider_mode="real"), manifest)


def test_private_http_deadline_and_capability_contract(backend, monkeypatch):
    import httpx
    from thorax.infrastructure.inference_http import HTTPInferenceProvider

    fake = backend.provider
    image = io.BytesIO()
    PILImage.new("RGB", (16, 16)).save(image, format="PNG")
    pixels = image.getvalue()
    image_hash = hashlib.sha256(pixels).hexdigest()
    empty = fake.invoke("analyze", {}, pixels, 1) | {"findings": []}
    responses = {
        "capabilities": fake.capabilities(),
        "operations": {"analyze": {"default": empty, image_hash: empty | {"delay_seconds": 0.5}}},
    }
    client = TestClient(MockInference(backend.settings, responses).app)
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: client.get(url, headers=kwargs["headers"]))
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kwargs: client.stream(method, url, **kwargs))
    provider = HTTPInferenceProvider(backend.settings)
    capabilities = provider.capabilities()
    assert set(capabilities["operations"]) == {"analyze", "detect", "ask", "draft_report"}
    assert capabilities["coordinate_system"] == "original_pixels_xyxy"
    assert capabilities["context_limits"]["context_messages"] == 10
    with pytest.raises(DomainError, match="AI_DEADLINE_EXCEEDED"):
        provider.invoke("analyze", {"request_id": "synthetic", "indication": "Synthetic test", "history": [], "prompt_version": "test", "preprocessing_version": "test"}, pixels, 0.1)
    assert provider.invoke("analyze", {"request_id": "synthetic", "indication": "Synthetic test", "history": [], "prompt_version": "test", "preprocessing_version": "test"}, pixels, 5) == empty
    with pytest.raises(DomainError, match="AI_CONTEXT_TOO_LARGE"):
        context = {"request_id": "synthetic", "indication": "Synthetic test", "history": [
            {"source_id": "source", "date": "2000-01-01", "text": "x" * (capabilities["context_limits"]["history_chars"] + 1)},
        ], "prompt_version": "test", "preprocessing_version": "test"}
        provider.invoke("analyze", context, pixels, 5)
    broken = capabilities | {"operations": ["detect"]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=broken, request=httpx.Request("GET", url)))
    with pytest.raises(DomainError, match="AI_UNAVAILABLE"):
        provider.capabilities()


def test_request_local_roi_and_operational_log_correlation(backend, caplog):
    from thorax.telemetry import OperationalLog

    caplog.set_level("INFO", logger=OperationalLog.logger.name)
    flow = Journey(backend)
    flow.open()
    flow.analyze()
    box = flow.request("POST", f"/sessions/{flow.session_id}/boxes", {"image_id": 1, "bbox_xyxy": [2, 3, 12, 15]})
    assert box.status_code == 201
    region = box.json()["resource_id"]
    assert flow.job({"operation": "ask", "image_id": 1, "box_id": region, "question": "Synthetic ROI question"})["status"] == "succeeded"
    refs = []
    for _ in range(2):
        response = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests",
                                {"operation": "ask", "image_id": 1, "question": "Synthetic follow-up text"})
        assert response.status_code == 202
        job_id = response.json()["request_id"]
        backend.worker.process(backend.worker.claim())
        context = backend.provider.calls[-1][1]
        assert context["request_id"] == response.headers["X-Request-ID"]
        assert context["last_messages"][0]["box_ref"] == context["last_messages"][1]["box_ref"]
        refs.append(context["last_messages"][0]["box_ref"])
        records = [json.loads(item.getMessage()) for item in caplog.records if item.name == OperationalLog.logger.name]
        matched = [item for item in records if item.get("request_id") == context["request_id"]]
        assert {item["event"] for item in matched} == {"api", "worker_invocation"}
        assert all(item["job_id"] == job_id for item in matched)
    assert refs[0] != refs[1]
    assert "Synthetic follow-up text" not in caplog.text
    assert backend.test_password not in caplog.text


def test_configuration_rejects_nonlocal_insecure_cookie_and_short_secrets(backend):
    with pytest.raises(ValueError):
        replace(backend.settings, app_origin="http://example.test").validate()
    with pytest.raises(ValueError):
        replace(backend.settings, session_secret="short").validate()


def test_postgresql_migration_compiles_offline(monkeypatch):
    from alembic.config import Config
    from alembic import command
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://placeholder:placeholder@localhost/thorax_test")
    output = io.StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 15
    assert "CREATE TRIGGER events_append_only" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql


def test_request_size_limit_is_configured(backend):
    from thorax.presentation.api import create_app
    original = backend.settings.policy["limits"]["payload_bytes"]
    backend.settings.policy["limits"]["payload_bytes"] = 8
    try:
        client = TestClient(create_app(backend), base_url=backend.settings.app_origin)
        response = client.post("/api/v1/auth/login", content=b"x" * 9)
        assert response.status_code == 413
        assert "no-store" in response.headers["cache-control"]
    finally:
        backend.settings.policy["limits"]["payload_bytes"] = original


def test_missing_required_provider_capability_never_queues_job(backend):
    flow = Journey(backend)
    flow.open()
    capabilities = backend.provider.capabilities()
    backend.provider.capabilities = lambda: capabilities | {"can_detect": False}
    response = flow.request("POST", f"/sessions/{flow.session_id}/ai-requests", {"operation": "analyze", "image_id": 1})
    assert response.status_code == 503
    with backend.database.unit_of_work() as uow:
        assert uow.repository.all(AIRequest) == []
