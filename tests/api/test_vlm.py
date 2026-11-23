"""Model service (scripts/serve_vlm.py, fake model) and the backend's real-model path, on synthetic data only."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api.dbtarget import fresh_db  # noqa: E402

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "PIL"))
WEB = "http://127.0.0.1:3000"
KEY = "test-key-0123456789"
PASSWORD = "synthetic-pass-123"


def load_service():
    spec = importlib.util.spec_from_file_location("serve_vlm", ROOT / "scripts" / "serve_vlm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(width=200, height=100) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("L", (width, height), 80).save(out, format="PNG")
    return out.getvalue()


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx, pillow)")
class ModelServiceTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.service = load_service()
        self.client = TestClient(self.service.create_app(self.service.FakeModel(), KEY))

    def ask(self, questions, image=None, key=KEY, regions=True):
        body = {"image_png_base64": base64.b64encode(image or png()).decode(), "questions": questions, "regions": regions}
        return self.client.post("/v1/answer", json=body, headers={"X-Api-Key": key})

    def test_answers_with_pixel_boxes_and_no_abstention_without_calibration(self):
        body = self.ask(["Is there a pleural effusion?", "Is there a pneumothorax?"]).json()
        effusion, pneumothorax = body["results"]
        self.assertEqual((effusion["answer"], effusion["region_status"]), ("Yes.", "found"))
        self.assertEqual(effusion["boxes_xyxy"], [[30.0, 55.0, 90.0, 90.0]])  # grid (150,550,450,900) on 200x100
        self.assertEqual(pneumothorax["answer"], "No.")
        self.assertEqual(effusion["risk"]["status"], "unavailable")
        self.assertEqual(body["model"]["mode"], "fake")
        self.assertEqual(self.ask(["Is there a pleural effusion?"], regions=False).json()["results"][0]["region_status"], "not_requested")

    def test_rejects_bad_key_and_input(self):
        self.assertEqual(self.ask(["Is there a pleural effusion?"], key="wrong-key-0123456").status_code, 401)
        self.assertEqual(self.ask([]).status_code, 422)
        self.assertEqual(self.ask(["x" * 2001]).status_code, 422)
        from PIL import Image
        jpeg = io.BytesIO()
        Image.new("L", (10, 10)).save(jpeg, format="JPEG")
        self.assertEqual(self.ask(["Is there a pleural effusion?"], image=jpeg.getvalue()).status_code, 422)

    def test_calibrated_abstention_matches_the_m1_logistic(self):
        decide = self.service.decide
        calibration = {"method": "answer_signal", "weights": [-2.0, 4.0], "threshold": 0.7, "target_risk": 0.1}
        answered = decide(calibration, 0.8, 0.0)  # sigmoid(-2 + 3.2) = 0.769
        self.assertAlmostEqual(answered["probability_correct"], 0.7685, places=3)
        self.assertFalse(answered["abstain"])
        self.assertTrue(decide(calibration, 0.5, 0.0)["abstain"])  # sigmoid(0) = 0.5
        self.assertTrue(decide({**calibration, "threshold": None}, 0.99, 0.0)["abstain"])  # no feasible operating point
        joint = {"method": "joint", "weights": [0.0, 1.0, 1.0], "threshold": 0.5, "target_risk": 0.1}
        self.assertAlmostEqual(decide(joint, 0.5, 0.5)["probability_correct"], 0.7311, places=3)

    def test_calibration_file_must_be_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text(json.dumps({"selectivity": {"status": "complete", "target_risk": 0.1, "signal_ablations_same_adapter": {
                "answer_signal": {"weights": [0.0, 1.0], "threshold": 0.6}}}}))
            self.assertEqual(self.service.load_calibration(str(path), "answer_signal")["threshold"], 0.6)
            path.write_text(json.dumps({"selectivity": {"status": "insufficient_calibration_or_report_population"}}))
            with self.assertRaises(SystemExit):
                self.service.load_calibration(str(path), "answer_signal")
        self.assertIsNone(self.service.load_calibration(None, "answer_signal"))



class InProcessClient:
    """Stands in for VlmClient: runs the service's real answer path on the fake model, without HTTP."""
    def __init__(self, service, calibration=None, error=None):
        self.service, self.calibration, self.error = service, calibration, error

    def answer(self, data: bytes, questions, *, regions):
        from PIL import Image
        if self.error:
            raise self.error
        image = Image.open(io.BytesIO(data)).convert("RGB")
        model = self.service.FakeModel()
        return {"results": [self.service.answer_one(model, self.calibration, image, q, regions) for q in questions],
                "latency_ms": 321, "model": model.info}


@unittest.skipUnless(HAS_API_DEPS, "requires requirements/api.txt (fastapi, httpx, pillow)")
class RealModelReadingTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from thorax_api.accounts import AccountStore
        from thorax_api.app import create_app
        from thorax_api.catalog import seed_demo

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db = fresh_db(self.directory.name)
        accounts = AccountStore(db)
        for user_id in (1, 2):  # the demo seed assigns patients to doctors 1 and 2
            accounts.create(f"doctor{user_id}@example.test", f"Doctor {user_id}", "doctor", PASSWORD, user_id=user_id)
        self.app = create_app(db, [WEB], image_root=str(Path(self.directory.name) / "images"))
        seed_demo(self.app.state.catalog)
        self.service = load_service()
        self.app.state.readings.vlm = InProcessClient(self.service)
        self.doctor = TestClient(self.app, headers={"Origin": WEB})
        response = self.doctor.post("/api/v1/auth/login", json={"email": "doctor1@example.test", "password": PASSWORD})
        self.doctor.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        self.workspace = self.doctor.post("/api/v1/studies/2411/sessions").json()
        self.sid = self.workspace["session"]["id"]
        self.image = next(i for i in self.workspace["images"] if i["view"] == "PA")

    def submit(self, body):
        revision = self.doctor.get(f"/api/v1/sessions/{self.sid}").json()["session"]["revision"]
        response = self.doctor.post(f"/api/v1/sessions/{self.sid}/ai-requests", json=body,
                                    headers={"If-Match": f'"{revision}"', "Idempotency-Key": uuid.uuid4().hex})
        self.assertEqual(response.status_code, 202, response.text)
        for _ in range(100):  # the model call runs in a background thread; each read settles it
            workspace = self.doctor.get(f"/api/v1/sessions/{self.sid}").json()
            if workspace["latest_ai_request"]["status"] not in ("queued", "running"):
                return workspace
            time.sleep(0.05)
        self.fail("the AI request did not finish")

    def test_analysis_turns_yes_answers_into_findings_with_model_regions(self):
        workspace = self.submit({"operation": "analyze", "image_id": self.image["id"]})
        request = workspace["latest_ai_request"]
        self.assertEqual((request["status"], request["provenance"]["provider_mode"]), ("succeeded", "real"))
        self.assertEqual(request["provenance"]["vlm_artifact"]["version"], "fake")
        self.assertEqual(workspace["capabilities"]["provider_mode"], "real")
        items = workspace["latest_findings"]["items"]
        self.assertEqual([i["text"] for i in items], ["Possible pleural effusion."])  # the fake model says yes only to effusion
        box = next(b for b in workspace["boxes"] if b["id"] in items[0]["box_ids"])
        width, height = self.image["width"], self.image["height"]
        self.assertEqual(box["bbox_xyxy"], [round(150 * width / 1000, 2), round(550 * height / 1000, 2),
                                            round(450 * width / 1000, 2), round(900 * height / 1000, 2)])
        self.assertEqual((box["source"], box["label"]), ("ai", "Pleural effusion"))
        self.assertEqual(len(request["output"]["screening"]), 10)

    def test_abstained_answers_are_withheld(self):
        # Calibrated so that confidence 0.8 falls below the threshold: everything is withheld.
        self.app.state.readings.vlm = InProcessClient(self.service, {"method": "answer_signal", "weights": [-2.0, 1.0], "threshold": 0.9, "target_risk": 0.1})
        workspace = self.submit({"operation": "analyze", "image_id": self.image["id"]})
        self.assertEqual(workspace["latest_findings"]["items"], [])
        screening = workspace["latest_ai_request"]["output"]["screening"]
        effusion = next(entry for entry in screening if entry["finding"] == "pleural effusion")
        self.assertEqual((effusion["withheld"], effusion["answer"]), (True, None))  # the candidate answer is not sent
        self.assertIn("abstained", workspace["preliminary_report"]["text"])
        answer = self.submit({"operation": "ask", "image_id": self.image["id"], "question": "Is there a pleural effusion?"})
        messages = self.doctor.get(f"/api/v1/sessions/{self.sid}/messages").json()["items"]
        self.assertTrue(messages[-1]["text"].startswith("The model abstained"))
        self.assertTrue(answer["latest_ai_request"]["output"]["answer"]["withheld"])

    def test_ask_returns_the_model_answer_and_errors_fail_the_request(self):
        self.submit({"operation": "analyze", "image_id": self.image["id"]})
        self.submit({"operation": "ask", "image_id": self.image["id"], "question": "Is there a pleural effusion?"})
        last = self.doctor.get(f"/api/v1/sessions/{self.sid}/messages").json()["items"][-1]
        self.assertEqual((last["role"], last["text"], last["latency_ms"]), ("assistant", "Yes.", 321))
        from thorax_api.vlm import VlmError
        self.app.state.readings.vlm = InProcessClient(self.service, error=VlmError("The model service did not respond in time."))
        failed = self.submit({"operation": "ask", "image_id": self.image["id"], "question": "Is there a pneumothorax?"})["latest_ai_request"]
        self.assertEqual((failed["status"], failed["error"]["code"], failed["error"]["retryable"]), ("failed", "AI_UNAVAILABLE", True))

    def test_client_reports_an_unreachable_service_as_retryable(self):
        from thorax_api.vlm import VlmClient, VlmError
        with self.assertRaises(VlmError) as caught:
            VlmClient("http://127.0.0.1:9", KEY, timeout=2).answer(png(), ["Is there a pleural effusion?"], regions=True)
        self.assertTrue(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
