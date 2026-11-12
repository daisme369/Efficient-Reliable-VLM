from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints

from thorax.domain.annotation import PixelBox
from thorax.domain.errors import DomainError


OpaqueRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,128}$")]


class PrivateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HistoryItem(PrivateModel):
    source_id: OpaqueRef
    date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    text: str


class ContextMessage(PrivateModel):
    role: Literal["doctor", "ai"]
    text: str
    image_ref: OpaqueRef
    box_ref: OpaqueRef | None = None


class ROI(PrivateModel):
    original_bbox: Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
    image_width: Annotated[int, Field(gt=0)]
    image_height: Annotated[int, Field(gt=0)]


class ApprovedFinding(PrivateModel):
    id: str
    text: str
    location: str | None


class DetectContext(PrivateModel):
    request_id: OpaqueRef
    preprocessing_version: str

    def validate_limits(self, limits):
        pass


class HistoryContext(PrivateModel):
    request_id: OpaqueRef
    history: list[HistoryItem]
    prompt_version: str

    def validate_limits(self, limits):
        if len(self.history) > limits["history_reports"] or any(len(item.text) > limits["history_chars"] for item in self.history):
            raise DomainError("AI_CONTEXT_TOO_LARGE", 422)


class AnalyzeContext(HistoryContext):
    indication: str
    preprocessing_version: str


class AskContext(HistoryContext):
    question: Annotated[str, Field(min_length=1)]
    current_image_ref: OpaqueRef
    last_messages: list[ContextMessage]
    roi: ROI | None

    def validate_limits(self, limits):
        super().validate_limits(limits)
        if (len(self.question) > limits["question_chars"]
                or len(self.last_messages) > limits["context_messages"]
                or sum(len(item.text) for item in self.last_messages) > limits["context_message_chars"]):
            raise DomainError("AI_CONTEXT_TOO_LARGE", 422)
        if self.roi is not None:
            PixelBox(*self.roi.original_bbox).validate(self.roi.image_width, self.roi.image_height)


class DraftContext(PrivateModel):
    request_id: OpaqueRef
    approved_findings: list[ApprovedFinding]
    no_abnormality_confirmed: bool
    prompt_version: str

    def validate_limits(self, limits):
        if len(self.approved_findings) > limits["finding_count"] or any(
            len(item.text) > limits["finding_chars"] or len(item.location or "") > limits["location_chars"]
            for item in self.approved_findings
        ):
            raise DomainError("AI_CONTEXT_TOO_LARGE", 422)
        if self.no_abnormality_confirmed == bool(self.approved_findings):
            raise ValueError("Inconsistent approved findings")


class InboundContext:
    SCHEMAS = {"analyze": AnalyzeContext, "ask": AskContext, "draft_report": DraftContext, "detect": DetectContext}

    @classmethod
    def validate(cls, operation, context, limits):
        payload = cls.SCHEMAS[operation].model_validate(context)
        payload.validate_limits(limits)
