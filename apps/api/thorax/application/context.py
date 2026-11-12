import hashlib
import json
import re
import secrets
import unicodedata

from thorax.domain.errors import DomainError
from thorax.infrastructure.db.models import AIRequest, Box, Image, Message, Patient, Study

from .projections import box_dto, box_snapshot


class IdentityGuard:
    @staticmethod
    def check(value, patient):
        text = unicodedata.normalize("NFKC", json.dumps(value, ensure_ascii=False)).casefold()
        for identifier in (patient.name, patient.code):
            normalized = unicodedata.normalize("NFKC", identifier).casefold()
            if re.search(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)", text):
                raise DomainError("PHI_IN_MODEL_INPUT", 422)


class ContextBuilder:
    def __init__(self, settings, reading):
        self.settings, self.reading = settings, reading
        self.limits = settings.policy["limits"]

    def build(self, repo, session, command, *, request_id=None):
        operation = command["operation"]
        latest = repo.latest_findings(session.id)
        patient = repo.get(Patient, session.patient_id)
        context = {"request_id": request_id or secrets.token_urlsafe(), "prompt_version": self.settings.policy["inference"]["prompt_version"]}
        snapshot = {"submitted_input": command, "context": context, "image_id": None, "image_sha256": None, "box_snapshot": None, "message_ids": [], "history_report_ids": [], "source_findings_version": None}
        if operation == "draft_report":
            if not latest or latest.status != "approved" or latest.version != command["findings_version"] or session.status != "findings_approved":
                raise DomainError("GATE1_REQUIRED")
            items = self.reading.finding_domain(latest).included()
            context.update(approved_findings=[{k: item[k] for k in ("id", "text", "location")} for item in items], no_abnormality_confirmed=latest.no_abnormality_confirmed)
            context["prompt_version"] = self.settings.policy["report"]["prompt_version"]
            snapshot["source_findings_version"] = latest.version
        else:
            image = repo.get(Image, command["image_id"])
            if not image or image.study_id != session.study_id:
                raise DomainError("RESOURCE_NOT_FOUND", 404)
            if operation == "analyze" and latest:
                raise DomainError("ALREADY_ANALYZED")
            if operation == "ask" and not latest:
                raise DomainError("ANALYSIS_REQUIRED")
            history = self.reading.history(repo, session)
            context["history"] = [{"source_id": secrets.token_urlsafe(), "date": h.report_date.isoformat(), "text": h.text} for h in history]
            if any(len(h.text) > self.limits["history_chars"] for h in history):
                raise DomainError("AI_CONTEXT_TOO_LARGE", 422)
            snapshot.update(image_id=image.id, image_sha256=image.sha256, history_report_ids=[h.id for h in history], history_hashes=[hashlib.sha256(h.text.encode()).hexdigest() for h in history])
            if operation == "analyze":
                context.update(indication=repo.get(Study, session.study_id).indication, preprocessing_version=image.preprocessing_version)
            else:
                question = command["question"].strip()
                if not question or len(question) > self.limits["question_chars"]:
                    raise DomainError("VALIDATION_ERROR", 422)
                messages = list(reversed(repo.all(Message, Message.session_id == session.id, Message.deleted_at.is_(None), order_by=[Message.id.desc()], limit=self.limits["context_messages"])))
                if sum(len(m.text) for m in messages) > self.limits["context_message_chars"]:
                    raise DomainError("AI_CONTEXT_TOO_LARGE", 422)
                aliases = {image.id: secrets.token_urlsafe()}
                box_aliases = {}
                context.update(question=question, last_messages=[], current_image_ref=aliases[image.id], roi=None)
                for message in messages:
                    previous = repo.get(AIRequest, message.ai_request_id).input_snapshot
                    previous_image = previous["image_id"]
                    aliases.setdefault(previous_image, secrets.token_urlsafe())
                    item = {"role": message.role, "text": message.text, "image_ref": aliases[previous_image]}
                    if message.box_snapshot:
                        snapshot_key = json.dumps(message.box_snapshot, sort_keys=True, default=str)
                        item["box_ref"] = box_aliases.setdefault(snapshot_key, secrets.token_urlsafe())
                    context["last_messages"].append(item)
                snapshot["message_ids"] = [m.id for m in messages]
                if command.get("box_id"):
                    box = self.reading._box(repo, session, command["box_id"])
                    if box.image_id != image.id or box.review_action == "rejected":
                        raise DomainError("RESOURCE_NOT_FOUND", 404)
                    snapshot["box_snapshot"] = box_snapshot(box)
                    context["roi"] = {"original_bbox": box_dto(box)["bbox_xyxy"], "image_width": image.width, "image_height": image.height}
        IdentityGuard.check(context, patient)
        return snapshot

    @staticmethod
    def fingerprint(snapshot):
        fields = ("image_id", "image_sha256", "box_snapshot", "message_ids", "history_report_ids", "history_hashes", "source_findings_version")
        return {k: snapshot.get(k) for k in fields} | {
            "prompt_version": snapshot["context"].get("prompt_version"),
            "preprocessing_version": snapshot["context"].get("preprocessing_version"),
        }
