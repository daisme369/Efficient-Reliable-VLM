import re

from thorax.domain.annotation import PixelBox
from thorax.domain.errors import DomainError


class OutputValidator:
    def __init__(self, limits, capabilities):
        self.limits, self.capabilities = limits, capabilities

    def text(self, value, maximum, *, empty=False):
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
            raise ValueError("Invalid model text")

    def validate(self, operation, output, snapshot, image=None):
        try:
            if not isinstance(output, dict):
                raise ValueError()
            artifact = output["artifact"]
            if set(artifact) != {"id", "version", "sha256"} or not all(isinstance(v, str) and v for v in artifact.values()) or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
                raise ValueError()
            if artifact not in self.capabilities["model_artifacts"]:
                raise ValueError("Unexpected artifact")
            if type(output["latency_ms"]) is not int or output["latency_ms"] < 0:
                raise ValueError()
            common = {"artifact", "latency_ms"}
            if operation == "analyze":
                if not {"findings"} | common <= output.keys() or output.keys() - (common | {"findings", "evidence_links"}):
                    raise ValueError()
                findings = output["findings"]
                if not isinstance(findings, list) or len(findings) > self.limits["finding_count"]:
                    raise ValueError()
                ids = set()
                for item in findings:
                    if set(item) != {"local_id", "text", "location"} or item["local_id"] in ids:
                        raise ValueError()
                    self.text(item["local_id"], self.limits["finding_chars"])
                    self.text(item["text"], self.limits["finding_chars"])
                    if item["location"] is not None:
                        self.text(item["location"], self.limits["location_chars"], empty=True)
                    ids.add(item["local_id"])
            elif operation == "detect":
                if set(output) != common | {"detections", "coordinate_system"} or output["coordinate_system"] != "original_pixels_xyxy":
                    raise ValueError()
                detections = output["detections"]
                if not isinstance(detections, list) or len(detections) > self.limits["finding_count"]:
                    raise ValueError()
                ids = set()
                for item in detections:
                    if set(item) != {"local_id", "label", "bbox_xyxy"} or item["local_id"] in ids or item["label"] not in self.capabilities["labels"]:
                        raise ValueError()
                    self.text(item["local_id"], self.limits["finding_chars"])
                    self.text(item["label"], self.limits["label_chars"])
                    if len(item["bbox_xyxy"]) != 4 or any(type(n) not in {int, float} for n in item["bbox_xyxy"]):
                        raise ValueError()
                    PixelBox(*item["bbox_xyxy"]).validate(image.width, image.height)
                    ids.add(item["local_id"])
            elif operation == "ask":
                if set(output) != common | {"answer"}:
                    raise ValueError()
                self.text(output["answer"], self.limits["question_chars"])
            else:
                if set(output) != common | {"comparison", "impression", "recommendation", "source_item_ids"}:
                    raise ValueError()
                for field in ("comparison", "impression", "recommendation"):
                    self.text(output[field], self.limits["report_chars"], empty=field != "impression")
                allowed = {i["id"] for i in snapshot["context"]["approved_findings"]}
                ids = output["source_item_ids"]
                if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or not set(ids) <= allowed:
                    raise ValueError()
            return output
        except (KeyError, ValueError, TypeError, DomainError) as error:
            raise DomainError("AI_INVALID_OUTPUT") from error
