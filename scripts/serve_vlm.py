"""Thorax AI model service: MedGemma plus a trained adapter (M1, or B1 as fallback) behind a small HTTP API.

Run on the rented GPU (Linux CUDA environment from requirements/medgemma-cu128.txt plus requirements/api.txt):

    THORAX_VLM_KEY=<random secret, 16+ characters> \
    THORAX_VLM_ADAPTER=/private/runs/<run>/checkpoint \
    THORAX_VLM_CONFIG=/private/runs/<run>/config.json \
    THORAX_VLM_CALIBRATION=/private/runs/<evaluation run>/metrics.json \
        python scripts/serve_vlm.py      # binds 127.0.0.1:8500; reach it through an SSH tunnel

THORAX_VLM_CONFIG must be the exact config the adapter was trained with (its fingerprint is checked).
THORAX_VLM_CALIBRATION is optional: without it, answers carry no abstention decision.
THORAX_VLM_RISK_METHOD picks the calibrated method: answer_signal (default, the plan's B2 fallback),
grounding_signal or joint (M1; use only after the team's gate).
THORAX_VLM_FAKE=1 serves fixed outputs without a GPU, for local and CI testing of the whole chain.

Inference mirrors src/cxr_research/evaluation/m1.py: same chat template and processor, greedy decoding,
confidence = exp(mean token log-probability), boxes from REGION_PROMPT on a 0-1000 grid, and the
validation-fold logistic calibration over (answer confidence, box confidence or 0 without a box).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_QUESTIONS = 12
GRID = 1000  # cxr_research.data.m1.GRID
AXES = {"answer_signal": (0,), "grounding_signal": (1,), "joint": (0, 1)}
SCOPE = "Calibrated on the MIMIC development validation fold only; not calibrated for other image sources."


def load_calibration(path: str | None, method: str) -> dict | None:
    """Weights and threshold that M1's evaluation fitted on the validation calibration fold (metrics.json)."""
    if not path:
        return None
    if method not in AXES:
        raise SystemExit(f"THORAX_VLM_RISK_METHOD must be one of {', '.join(AXES)}.")
    selectivity = json.loads(Path(path).read_text(encoding="utf-8"))["selectivity"]
    if selectivity.get("status") != "complete":
        raise SystemExit(f"The calibration in {path} is not usable: {selectivity.get('status')}.")
    chosen = selectivity["signal_ablations_same_adapter"][method]
    return {"method": method, "weights": chosen["weights"], "threshold": chosen["threshold"],
            "target_risk": selectivity["target_risk"]}


def decide(calibration: dict | None, confidence: float, box_confidence: float) -> dict:
    if calibration is None:
        return {"status": "unavailable", "reason": "No calibration is loaded, so the model cannot decide when to abstain."}
    signals = (confidence, box_confidence)
    weights = calibration["weights"]
    logit = weights[0] + sum(w * signals[axis] for w, axis in zip(weights[1:], AXES[calibration["method"]], strict=True))
    probability = 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))
    threshold = calibration["threshold"]
    # No threshold means no operating point met the target risk on calibration data: abstain on everything.
    return {"status": "available", "method": calibration["method"], "probability_correct": probability,
            "threshold": threshold, "target_risk": calibration["target_risk"],
            "abstain": threshold is None or probability < threshold, "scope": SCOPE}


class FakeModel:
    """Fixed outputs for testing the chain without a GPU. Never use it to show model quality."""
    info = {"id": "fake-vlm", "revision": "fake", "adapter_sha256": None, "mode": "fake"}

    def run(self, image, question: str, regions: bool):
        answer = "No." if question.lower().startswith(("is ", "are ", "does ")) else "Fake model answer for testing; no model was run."
        if "effusion" in question.lower():
            answer = "Yes."
        return answer, 0.8, ([(150, 550, 450, 900)] if regions else None), 0.6


class MedGemmaModel:
    def __init__(self, config_path: Path, adapter: Path):
        import torch
        from peft import PeftModel
        from cxr_research.data.m1 import GRID as TRAINING_GRID, REGION_PROMPT
        from cxr_research.evaluation.m1 import parse_boxes
        from cxr_research.experiments.checkpoints import verify_artifacts
        from cxr_research.experiments.config import load_config
        from cxr_research.models.medgemma import load_model
        from cxr_research.preprocessing.medgemma import VQACollator
        from cxr_research.utils.hashing import digest

        if not torch.cuda.is_available():
            raise SystemExit("A CUDA GPU is required (set THORAX_VLM_FAKE=1 to test without one).")
        if TRAINING_GRID != GRID:
            raise SystemExit("The box grid changed in cxr_research.data.m1; update GRID here.")
        identity = json.loads((adapter / "model_identity.json").read_text(encoding="utf-8"))
        config = load_config(config_path)
        if config.fingerprint != identity["config_sha256"]:
            raise SystemExit("THORAX_VLM_CONFIG is not the config this adapter was trained with.")
        adapter_sha = digest(verify_artifacts(adapter))  # refuses a changed or incomplete adapter
        model, processor = load_model(config, training=False)
        if digest(processor.to_dict()) != identity["processor_sha256"] or digest(processor.chat_template) != identity["chat_template_sha256"]:
            raise SystemExit("The processor or chat template differs from the one used in training.")
        self.model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False).eval()
        self.collator, self.processor, self.config, self.torch = VQACollator(processor, config.medgemma), processor, config, torch
        self.region_prompt, self.parse_boxes = REGION_PROMPT, parse_boxes
        self.info = {"id": config.medgemma.model_id, "revision": identity["model_revision"],
                     "adapter_sha256": adapter_sha, "mode": identity.get("objective", "adapter")}

    def _predict(self, image, question: str):
        """Mirrors predict() in src/cxr_research/evaluation/m1.py."""
        torch = self.torch
        with torch.inference_mode():
            batch = self.collator.encode([{"image": image, "question": question}], supervised=False).to(self.model.device)
            output = self.model.generate(**batch, do_sample=False, max_new_tokens=self.config.medgemma.max_new_tokens,
                                         return_dict_in_generate=True, output_scores=True)
        tokens = output.sequences[0, batch["input_ids"].shape[-1]:]
        if not output.scores:
            return "", 0.0
        log_p = [torch.log_softmax(s[0].float(), dim=-1)[t] for s, t in zip(output.scores, tokens, strict=True)]
        confidence = float(torch.stack(log_p).mean().exp().item())
        return self.processor.decode(tokens, skip_special_tokens=True).strip(), confidence if math.isfinite(confidence) else 0.0

    def run(self, image, question: str, regions: bool):
        answer, confidence = self._predict(image, question)
        if not regions:
            return answer, confidence, None, 0.0
        box_text, box_confidence = self._predict(image, self.region_prompt + question)
        return answer, confidence, self.parse_boxes(box_text), box_confidence


def answer_one(model, calibration, image, question: str, regions: bool) -> dict:
    answer, confidence, grid_boxes, box_confidence = model.run(image, question, regions)
    # Grid boxes -> pixels of the image that was sent, so the app can draw them directly.
    pixels = [[round(x1 * image.width / GRID, 2), round(y1 * image.height / GRID, 2),
               round(x2 * image.width / GRID, 2), round(y2 * image.height / GRID, 2)] for x1, y1, x2, y2 in grid_boxes or []]
    region_status = "not_requested" if not regions else "invalid" if grid_boxes is None else "found" if pixels else "none"
    return {"question": question, "answer": answer, "confidence": confidence,
            "region_status": region_status, "boxes_xyxy": pixels, "box_confidence": box_confidence if pixels else None,
            "risk": decide(calibration, confidence, box_confidence if pixels else 0.0)}


def create_app(model, key: str, calibration: dict | None = None) -> FastAPI:
    app = FastAPI(title="Thorax AI model service", docs_url=None, redoc_url=None, openapi_url=None)
    gpu = threading.Lock()  # one generation at a time on the single GPU
    info = {**model.info, "calibration": None if calibration is None else {k: calibration[k] for k in ("method", "threshold", "target_risk")}}

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": info}

    @app.post("/v1/answer")
    def answer(request: Request, body: dict):
        if not hmac.compare_digest(request.headers.get("x-api-key", ""), key):
            return error(401, "Invalid API key.")
        questions, encoded, regions = body.get("questions"), body.get("image_png_base64"), body.get("regions", True)
        if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS \
                or not all(isinstance(q, str) and 1 <= len(q) <= 2000 for q in questions) or not isinstance(regions, bool):
            return error(422, f"Send 1-{MAX_QUESTIONS} questions of 1-2000 characters and a boolean 'regions'.")
        if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            return error(422, "Send a PNG image of at most 10 MiB.")
        try:
            from PIL import Image
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as raw:
                if raw.format != "PNG":
                    return error(422, "Send a PNG image.")
                image = raw.convert("RGB")  # training used RGB input
        except (binascii.Error, OSError):
            return error(422, "The image could not be read.")
        started = time.perf_counter()
        with gpu:
            results = [answer_one(model, calibration, image, question, regions) for question in questions]
        return {"results": results, "latency_ms": round((time.perf_counter() - started) * 1000), "model": info}

    return app


def main() -> None:
    import uvicorn

    key = os.environ.get("THORAX_VLM_KEY", "")
    if len(key) < 16:
        raise SystemExit("Set THORAX_VLM_KEY to a random secret of at least 16 characters.")
    if os.environ.get("THORAX_VLM_FAKE") == "1":
        model = FakeModel()
    else:
        adapter, config = os.environ.get("THORAX_VLM_ADAPTER"), os.environ.get("THORAX_VLM_CONFIG")
        if not adapter or not config:
            raise SystemExit("Set THORAX_VLM_ADAPTER (adapter folder) and THORAX_VLM_CONFIG (its training config).")
        model = MedGemmaModel(Path(config), Path(adapter))
    calibration = load_calibration(os.environ.get("THORAX_VLM_CALIBRATION"), os.environ.get("THORAX_VLM_RISK_METHOD", "answer_signal"))
    print(f"Model service ready: {model.info}; calibration: {calibration and calibration['method']}", flush=True)
    uvicorn.run(create_app(model, key, calibration), host=os.environ.get("THORAX_VLM_HOST", "127.0.0.1"),
                port=int(os.environ.get("THORAX_VLM_PORT", "8500")), workers=1)


if __name__ == "__main__":
    main()
