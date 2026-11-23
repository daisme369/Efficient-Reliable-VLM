import hashlib
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from thorax.domain.errors import DomainError
from thorax.inference.medgemma import AdapterManifest, MedGemma, RealInference
from thorax.inference.contract import InferenceBudget, InferenceContract


def manifest_fixture(tmp_path):
    files = {
        "adapter_config.json": json.dumps({"base_model_name_or_path": "synthetic/base"}),
        "model_identity.json": json.dumps({"model_revision": "a" * 40, "precision": "bf16", "quantization": "none", "adapter_scope": "language_projections"}),
        **{name: "synthetic" for name in ("adapter_model.safetensors", "processor_config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja")},
    }
    hashes = {}
    for name, value in files.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
        hashes[name] = hashlib.sha256(value.encode()).hexdigest()
    value = {"adapter_path": ".", "base_model_id": "synthetic/base", "base_revision": "a" * 40, "file_hashes": hashes,
             "artifact": {"id": "synthetic", "version": "test", "sha256": hashes["adapter_model.safetensors"]}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def test_adapter_tampering_and_manifest_escape_are_rejected(tmp_path):
    path, value = manifest_fixture(tmp_path)
    assert AdapterManifest(path).base_model_id == "synthetic/base"
    (tmp_path / "adapter_model.safetensors").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        AdapterManifest(path)
    value["file_hashes"] = {"../escape": "a" * 64} | value["file_hashes"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        AdapterManifest(path)


def test_base_revision_mismatch_and_native_windows_are_rejected(tmp_path, monkeypatch):
    path, value = manifest_fixture(tmp_path)
    manifest = AdapterManifest(path)
    monkeypatch.setattr("thorax.inference.medgemma.platform.system", lambda: "Windows")
    with pytest.raises(RuntimeError, match="Linux"):
        MedGemma(manifest, {})
    value["base_revision"] = "b" * 40
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        AdapterManifest(path)


class Generator:
    def __init__(self):
        self.output = '{"answer":"Synthetic only"}'
        self.calls = []

    def generate(self, prompt, image, budget):
        assert 0 < budget.remaining() <= 10
        self.calls.append((prompt, image.size if image else None))
        return self.output


class Detector:
    def capabilities(self, **kwargs):
        return {"provider_mode": "real", "ready": True, "can_detect": True, "can_ask_roi": False, "labels": ["synthetic"], "model_artifacts": [{"id": "test-detector", "version": "test", "sha256": "c" * 64}]}


def test_real_service_auth_roi_json_and_no_mock_fallback(backend, tmp_path):
    path, _ = manifest_fixture(tmp_path)
    manifest, generator, detector = AdapterManifest(path), Generator(), Detector()
    settings = replace(backend.settings, provider_mode="real")
    service = RealInference(settings, manifest, {"prompts": {"ask": "Synthetic test", "draft_report": "Approved-only"}}, generator, detector)
    client = TestClient(service.app)
    headers = {"Authorization": f"Bearer {settings.inference_token}",
               InferenceContract.DEADLINE_HEADER: InferenceBudget(10).wire_deadline}
    assert client.get("/internal/v1/capabilities").status_code == 401
    capabilities = client.get("/internal/v1/capabilities", headers=headers).json()
    assert capabilities["provider_mode"] == "real" and capabilities["can_ask_roi"]
    assert manifest.artifact in capabilities["model_artifacts"]
    image = io.BytesIO()
    Image.new("RGB", (12, 14)).save(image, format="PNG")
    crop = {"image": ("crop.png", image.getvalue())}
    context = {"request_id": "synthetic", "question": "synthetic crop", "history": [], "prompt_version": "test",
               "current_image_ref": "current", "last_messages": [],
               "roi": {"original_bbox": [1, 2, 13, 16], "image_width": 20, "image_height": 20}}
    invalid = context | {"patient_name": "sensitive synthetic"}
    rejected = client.post("/internal/v1/ask", headers=headers, files=crop, data={"context": json.dumps(invalid)})
    assert rejected.status_code == 422 and "sensitive" not in rejected.text and not generator.calls
    response = client.post("/internal/v1/ask", headers=headers, files=crop, data={"context": json.dumps(context)})
    assert response.status_code == 200 and response.json()["artifact"] == manifest.artifact
    assert generator.calls[-1][1] == (12, 14)
    assert '"original_bbox": [1, 2, 13, 16]' in generator.calls[-1][0]
    generator.output = "not JSON: sensitive error text"
    response = client.post("/internal/v1/draft-report", headers=headers, json={"request_id": "synthetic", "approved_findings": [], "no_abnormality_confirmed": True, "prompt_version": "test"})
    assert response.status_code == 422 and "sensitive" not in response.text
    generator.output = '{"artifact":{},"answer":"fake identity"}'
    with pytest.raises(DomainError, match="AI_INVALID_OUTPUT"):
        service.provider_invoke("ask", {}, None, InferenceBudget(10))
    with pytest.raises(ValueError):
        RealInference(backend.settings, manifest, {}, generator, detector)
    detector.capabilities = lambda **kwargs: {"provider_mode": "mock", "ready": True, "can_detect": True}
    assert client.get("/internal/v1/capabilities", headers=headers).status_code == 503
