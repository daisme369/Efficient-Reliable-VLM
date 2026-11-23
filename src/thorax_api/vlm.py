"""Client for the model, served by vLLM's OpenAI-compatible API (`vllm serve`, see docker/DEPLOY.md, "Model").

Active when THORAX_VLM_URL (the server's base URL, without /v1) and THORAX_VLM_KEY (vLLM's --api-key) are set.
Inference mirrors src/cxr_research/evaluation/m1.py: the training system prompt and chat layout (image, then
question), greedy decoding, confidence = exp(mean token log-probability), boxes from REGION_PROMPT on a 0-1000
grid, and M1's validation-fold logistic calibration over (answer confidence, box confidence or 0 without a box).
Optional: THORAX_VLM_MODEL (vLLM --served-model-name), THORAX_VLM_CALIBRATION (M1 metrics.json),
THORAX_VLM_RISK_METHOD (answer_signal, grounding_signal or joint).
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

# One screening question per finding in configs/labeling_vocabulary.json; a "yes" becomes an AI suggestion.
# ponytail: worded by hand, not taken from the B1/M1 training templates; check the answers on the
# validation set and reword to the training style before relying on them.
SCREENING = {
    "lung opacity": "Is there a lung opacity?",
    "atelectasis": "Is there atelectasis?",
    "consolidation": "Is there consolidation?",
    "pleural effusion": "Is there a pleural effusion?",
    "pneumothorax": "Is there a pneumothorax?",
    "pulmonary edema": "Is there pulmonary edema?",
    "enlarged cardiac silhouette": "Is the cardiac silhouette enlarged?",
    "lung nodule or mass": "Is there a lung nodule or mass?",
    "fracture": "Is there a fracture?",
    "support device": "Is there a support device, tube or line?",
}

# Copied from cxr_research (data/m1.py, experiments/config.py), which the API image does not ship;
# tests/api/test_vlm.py checks they still match.
GRID = 1000
REGION_PROMPT = ("Locate the evidence for this question. Return a JSON list of [x1,y1,x2,y2] "
                 "boxes on a 0 to 1000 grid, or [] if you cannot localize it. Question: ")
SYSTEM_PROMPT = "Answer the question using the chest X-ray. Give a concise answer."
MAX_NEW_TOKENS = 128
AXES = {"answer_signal": (0,), "grounding_signal": (1,), "joint": (0, 1)}
SCOPE = "Calibrated on the MIMIC development validation fold only; not calibrated for other image sources."


class VlmError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message, self.retryable = message, retryable


def load_calibration(path: str | None, method: str) -> dict | None:
    """Weights and threshold that M1's evaluation fitted on the validation calibration fold (metrics.json)."""
    if not path:
        return None
    if method not in AXES:
        raise ValueError(f"THORAX_VLM_RISK_METHOD must be one of {', '.join(AXES)}.")
    selectivity = json.loads(Path(path).read_text(encoding="utf-8"))["selectivity"]
    if selectivity.get("status") != "complete":
        raise ValueError(f"The calibration in {path} is not usable: {selectivity.get('status')}.")
    chosen = selectivity["signal_ablations_same_adapter"][method]
    return {"method": method, "weights": chosen["weights"], "threshold": chosen["threshold"], "target_risk": selectivity["target_risk"]}


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


def parse_boxes(answer: str):
    """Same rules as cxr_research.evaluation.m1.parse_boxes: None for invalid output, else unique grid boxes."""
    try:
        boxes = json.loads(answer)
    except (ValueError, RecursionError):
        return None
    if not isinstance(boxes, list) or len(boxes) > 64:
        return None
    result = []
    for box in boxes:
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int or not 0 <= v <= GRID for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            return None
        result.append(tuple(box))
    return list(dict.fromkeys(result))


class VlmClient:
    def __init__(self, url: str, key: str, *, model: str = "thorax-m1", calibration: dict | None = None,
                 timeout: float = 55.0):  # just under the reading's 60 s deadline
        self.url, self.key, self.model, self.calibration, self.timeout = url.rstrip("/"), key, model, calibration, timeout

    @classmethod
    def from_env(cls) -> "VlmClient | None":
        url, key = os.environ.get("THORAX_VLM_URL", ""), os.environ.get("THORAX_VLM_KEY", "")
        if not (url and key):
            return None
        calibration = load_calibration(os.environ.get("THORAX_VLM_CALIBRATION"), os.environ.get("THORAX_VLM_RISK_METHOD", "answer_signal"))
        return cls(url, key, model=os.environ.get("THORAX_VLM_MODEL", "thorax-m1"), calibration=calibration)

    def _complete(self, image_url: str, text: str, deadline: float) -> tuple[str, float]:
        """One greedy chat completion: the text and exp(mean token log-probability)."""
        body = json.dumps({"model": self.model, "temperature": 0, "max_tokens": MAX_NEW_TOKENS, "logprobs": True, "messages": [
            {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}, {"type": "text", "text": text}]},
        ]}).encode()
        request = urllib.request.Request(f"{self.url}/v1/chat/completions", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, deadline - time.monotonic())) as response:  # noqa: S310 - admin-configured URL
                result = json.loads(response.read())
        except urllib.error.HTTPError as problem:
            raise VlmError(f"The model service refused the request ({problem.code}).", retryable=problem.code >= 500 or problem.code == 429) from None
        except (OSError, ValueError):
            raise VlmError("The model service did not respond in time.") from None
        try:
            choice = result["choices"][0]
            answer = choice["message"]["content"]
            logprobs = [token["logprob"] for token in (choice.get("logprobs") or {}).get("content") or []]
            if not isinstance(answer, str) or not all(isinstance(p, (int, float)) for p in logprobs):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise VlmError("The model service returned an invalid response.", retryable=False) from None
        confidence = math.exp(sum(logprobs) / len(logprobs)) if logprobs else 0.0
        return answer.strip(), confidence if math.isfinite(confidence) else 0.0

    def _answer_one(self, image_url: str, size: tuple[int, int], question: str, regions: bool, deadline: float) -> dict:
        answer, confidence = self._complete(image_url, question, deadline)
        grid_boxes, box_confidence = None, 0.0
        if regions:
            box_text, box_confidence = self._complete(image_url, REGION_PROMPT + question, deadline)
            grid_boxes = parse_boxes(box_text)
        width, height = size
        # Grid boxes -> pixels of the image that was sent, so the app can draw them directly.
        pixels = [[round(x1 * width / GRID, 2), round(y1 * height / GRID, 2), round(x2 * width / GRID, 2), round(y2 * height / GRID, 2)]
                  for x1, y1, x2, y2 in grid_boxes or []]
        region_status = "not_requested" if not regions else "invalid" if grid_boxes is None else "found" if pixels else "none"
        return {"question": question, "answer": answer, "confidence": confidence,
                "region_status": region_status, "boxes_xyxy": pixels, "box_confidence": box_confidence if pixels else None,
                "risk": decide(self.calibration, confidence, box_confidence if pixels else 0.0)}

    def answer(self, png: bytes, questions: list[str], *, regions: bool) -> dict:
        """{"results": [{"answer", "confidence", "boxes_xyxy", "region_status", "risk", ...}], "latency_ms", "model"}."""
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            size = image.size
        image_url = "data:image/png;base64," + base64.b64encode(png).decode()
        started = time.monotonic()
        deadline = started + self.timeout
        # vLLM batches concurrent requests on the GPU, so the screening questions run in parallel.
        with ThreadPoolExecutor(max_workers=min(8, len(questions))) as pool:
            results = list(pool.map(lambda q: self._answer_one(image_url, size, q, regions, deadline), questions))
        return {"results": results, "latency_ms": round((time.monotonic() - started) * 1000),
                "model": {"id": self.model, "revision": self.model, "mode": "vllm", "adapter_sha256": None}}


def is_yes(answer: str) -> bool:
    return answer.strip().lower().startswith("yes")


def model_version(model: dict) -> str:
    sha = model.get("adapter_sha256")
    return f"{model.get('mode', 'model')}:{sha[:12]}" if sha else str(model.get("revision") or model.get("mode") or "unknown")
