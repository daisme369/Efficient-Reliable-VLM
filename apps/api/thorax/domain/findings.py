from dataclasses import dataclass

from .errors import DomainError


INCLUDED_ACTIONS = frozenset({"accepted", "edited", "added"})
REVIEW_ACTIONS = INCLUDED_ACTIONS | {"pending", "rejected"}


@dataclass
class FindingSet:
    items: list[dict]
    ai_items: list[dict]
    no_abnormality_confirmed: bool
    status: str

    def validate(self, boxes: dict[int, dict], image_ids: set[int]) -> None:
        ids = [item["id"] for item in self.items]
        if len(ids) != len(set(ids)):
            raise DomainError("DUPLICATE_FINDING", 422)
        originals = {item["id"]: item for item in self.ai_items}
        if not originals.keys() <= set(ids):
            raise DomainError("MISSING_AI_FINDING", 422)
        for item in self.items:
            original = originals.get(item["id"])
            if item["image_id"] not in image_ids or item["action"] not in REVIEW_ACTIONS:
                raise DomainError("INVALID_FINDING", 422)
            if original:
                if item["source"] != "ai" or item["image_id"] != original["image_id"]:
                    raise DomainError("IMMUTABLE_AI_SOURCE", 422)
                if item["action"] not in REVIEW_ACTIONS - {"added"}:
                    raise DomainError("INVALID_REVIEW_ACTION", 422)
                if item["action"] == "accepted" and any(
                    item[k] != original[k] for k in ("text", "location")
                ):
                    raise DomainError("EDITED_ACTION_REQUIRED", 422)
            elif item["source"] != "doctor" or item["action"] not in {"added", "rejected"}:
                raise DomainError("INVALID_FINDING_SOURCE", 422)
            for box_id in item["box_ids"]:
                box = boxes.get(box_id)
                if not box or box["deleted_at"] or box["review_action"] == "rejected" or box["image_id"] != item["image_id"]:
                    raise DomainError("INVALID_EVIDENCE_LINK", 422)
        if self.no_abnormality_confirmed and self.included():
            raise DomainError("CONTRADICTORY_NORMAL_CONFIRMATION", 422)

    def included(self) -> list[dict]:
        return [item for item in self.items if item["action"] in INCLUDED_ACTIONS]

    def approve(self, boxes: list[dict]) -> None:
        if self.status != "in_review" or any(item["action"] == "pending" for item in self.items):
            raise DomainError("UNRESOLVED_REVIEW")
        if any(not box["deleted_at"] and box["review_action"] == "pending" for box in boxes):
            raise DomainError("UNRESOLVED_REVIEW")
        if not self.included() and not self.no_abnormality_confirmed:
            raise DomainError("UNRESOLVED_REVIEW")
