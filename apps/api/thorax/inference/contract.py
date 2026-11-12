import math
import re
import time

from thorax.domain.errors import DomainError


class InferenceContract:
    PATHS = {"analyze": "analyze", "ask": "ask", "draft_report": "draft-report", "detect": "detect"}
    DEADLINE_HEADER = "X-Thorax-Deadline-Ms"
    COORDINATES = "original_pixels_xyxy"
    MEDIA = "image/png"
    CONTEXT_KEYS = ("history_reports", "history_chars", "context_messages", "context_message_chars",
                    "question_chars", "finding_count", "finding_chars", "report_chars",
                    "image_bytes", "image_pixels", "payload_bytes")

    @classmethod
    def capabilities(cls, settings, provider):
        if not isinstance(provider, dict):
            raise ValueError("Invalid provider metadata")
        return provider | {
            "operations": list(cls.PATHS),
            "accepted_media": [cls.MEDIA],
            "coordinate_system": cls.COORDINATES,
            "context_limits": {key: settings.policy["limits"][key] for key in cls.CONTEXT_KEYS},
        }

    @classmethod
    def validate_capabilities(cls, result, settings, *, required, operations):
        if not isinstance(result, dict) or result.get("provider_mode") != settings.provider_mode:
            raise ValueError("Provider mode mismatch")
        if any(result.get(key) is not True for key in required):
            raise ValueError("Required capability unavailable")
        offered, media, limits = (result.get(key) for key in ("operations", "accepted_media", "context_limits"))
        if not isinstance(offered, list) or not all(op in offered for op in operations):
            raise ValueError("Required operation unavailable")
        if not isinstance(media, list) or cls.MEDIA not in media:
            raise ValueError("Canonical image media unavailable")
        if "detect" in operations and result.get("coordinate_system") != cls.COORDINATES:
            raise ValueError("Detector must return canonical coordinates")
        keys = cls.CONTEXT_KEYS if len(operations) > 1 else ("image_bytes", "image_pixels", "payload_bytes")
        if not isinstance(limits, dict) or any(type(limits.get(key)) is not int or limits[key] <= 0 for key in keys):
            raise ValueError("Invalid context limits")
        artifacts = result.get("model_artifacts")
        if not isinstance(artifacts, list) or not artifacts or any(not cls.valid_artifact(item) for item in artifacts):
            raise ValueError("Invalid artifact manifest")
        labels = result.get("labels")
        if "detect" in operations and (not isinstance(labels, list) or not labels
                                      or any(not isinstance(label, str) or not label.strip()
                                             or len(label) > settings.policy["limits"]["label_chars"] for label in labels)):
            raise ValueError("Invalid detector labels")
        return result

    @staticmethod
    def valid_artifact(item):
        return (isinstance(item, dict) and set(item) == {"id", "version", "sha256"}
                and all(isinstance(value, str) and value.strip() for value in item.values())
                and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None)


class InferenceBudget:
    def __init__(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0:
            raise DomainError("AI_DEADLINE_EXCEEDED", 504)
        self.ends_at = time.monotonic() + seconds
        self.wire_deadline = str(int((time.time() + seconds) * 1000))

    @classmethod
    def from_header(cls, header, maximum):
        if not re.fullmatch(r"[0-9]{1,16}", header or ""):
            raise DomainError("AI_INVALID_INPUT", 422)
        return cls(min(int(header) / 1000 - time.time(), maximum))

    def remaining(self):
        remaining = self.ends_at - time.monotonic()
        if remaining <= 0:
            raise DomainError("AI_DEADLINE_EXCEEDED", 504)
        return remaining
