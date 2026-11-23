"""Client for the model service (scripts/serve_vlm.py). Active when THORAX_VLM_URL and THORAX_VLM_KEY are set."""

from __future__ import annotations

import base64
import json
import os
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


class VlmError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message, self.retryable = message, retryable


class VlmClient:
    def __init__(self, url: str, key: str, timeout: float = 55.0):  # just under the reading's 60 s deadline
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    @classmethod
    def from_env(cls) -> "VlmClient | None":
        url, key = os.environ.get("THORAX_VLM_URL", ""), os.environ.get("THORAX_VLM_KEY", "")
        return cls(url, key) if url and key else None

    def answer(self, png: bytes, questions: list[str], *, regions: bool) -> dict:
        """{"results": [{"answer", "confidence", "boxes_xyxy", "region_status", "risk", ...}], "latency_ms", "model"}."""
        body = json.dumps({"image_png_base64": base64.b64encode(png).decode(), "questions": questions, "regions": regions}).encode()
        request = urllib.request.Request(f"{self.url}/v1/answer", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Api-Key": self.key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - admin-configured URL
                result = json.loads(response.read())
        except urllib.error.HTTPError as problem:
            raise VlmError(f"The model service refused the request ({problem.code}).", retryable=problem.code >= 500) from None
        except (OSError, ValueError):
            raise VlmError("The model service did not respond in time.") from None
        results = result.get("results") if isinstance(result, dict) else None
        if not isinstance(results, list) or len(results) != len(questions) \
                or not all(isinstance(r, dict) and isinstance(r.get("answer"), str) for r in results):
            raise VlmError("The model service returned an invalid response.", retryable=False)
        return result


def is_yes(answer: str) -> bool:
    return answer.strip().lower().startswith("yes")


def model_version(model: dict) -> str:
    sha = model.get("adapter_sha256")
    return f"{model.get('mode', 'model')}:{sha[:12]}" if sha else str(model.get("revision") or model.get("mode") or "unknown")
