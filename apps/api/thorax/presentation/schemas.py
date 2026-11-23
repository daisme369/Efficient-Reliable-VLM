from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, StrictInt, field_validator, model_validator


Identifier = Annotated[StrictInt, Field(gt=0)]
Geometry = Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
Text = Annotated[str, Field(min_length=1)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Schema):
    email: Text
    password: Text


class DoctorCreate(Login):
    name: Text


class DoctorState(Schema):
    is_active: StrictBool


class AssignmentCreate(Schema):
    doctor_id: Identifier
    patient_id: Identifier


class FixtureImport(Schema):
    fixture_key: Text


class BoxCreate(Schema):
    image_id: Identifier
    bbox_xyxy: Geometry
    label: str | None = None
    note: str | None = None


class BoxEdit(Schema):
    bbox_xyxy: Geometry | None = None
    label: str | None = None
    note: str | None = None
    review_action: Literal["accepted", "edited", "rejected", "added"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or ("bbox_xyxy" in self.model_fields_set and self.bbox_xyxy is None) or ("review_action" in self.model_fields_set and self.review_action is None):
            raise ValueError("At least one valid edit is required")
        return self


class FindingItem(Schema):
    id: Text
    text: Text
    location: str | None
    image_id: Identifier
    source: Literal["ai", "doctor"]
    action: Literal["pending", "accepted", "edited", "rejected", "added"]
    box_ids: list[Identifier]

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Text must not be blank")
        return value


class FindingsEdit(Schema):
    items: list[FindingItem]
    no_abnormality_confirmed: StrictBool


class Approval(Schema):
    findings_version: Identifier


class ReportEdit(Schema):
    comparison: str | None = None
    findings: str | None = None
    impression: str | None = None
    recommendation: str | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("At least one narrative string is required")
        return self


class Analyze(Schema):
    operation: Literal["analyze"]
    image_id: Identifier
    retry_of: Identifier | None = None


class Ask(Schema):
    operation: Literal["ask"]
    image_id: Identifier
    question: Text
    box_id: Identifier | None = None
    retry_of: Identifier | None = None


class DraftReport(Schema):
    operation: Literal["draft_report"]
    findings_version: Identifier
    retry_of: Identifier | None = None


SubmitRequest = Annotated[Analyze | Ask | DraftReport, Field(discriminator="operation")]
