from datetime import UTC, date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
ID_TYPE = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint("role IN ('doctor','admin')"), CheckConstraint("email = lower(email)"))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    csrf_hash: Mapped[str] = mapped_column(String)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("expires_at >= last_activity_at"),)


class Patient(Base):
    __tablename__ = "patients"
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    birth_year: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String)
    fixture_key: Mapped[str] = mapped_column(String, index=True)
    __table_args__ = (CheckConstraint("sex IN ('M','F')"), CheckConstraint("birth_year >= 1900"))


class Assignment(Base):
    __tablename__ = "doctor_patients"
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("doctor_id", "patient_id"),)


class Study(Base):
    __tablename__ = "studies"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), index=True)
    study_date: Mapped[date] = mapped_column(Date)
    indication: Mapped[str] = mapped_column(Text)
    fixture_key: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("id", "patient_id"),)


class Image(Base):
    __tablename__ = "images"
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="RESTRICT"), index=True)
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    view: Mapped[str] = mapped_column(String)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String)
    input_sha256: Mapped[str] = mapped_column(String)
    preprocessing_version: Mapped[str] = mapped_column(String)
    byte_size: Mapped[int] = mapped_column(ID_TYPE)
    media_type: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("id", "study_id"), CheckConstraint("width > 0 AND height > 0"), CheckConstraint("view IN ('PA','AP','LAT')"))


class PastReport(Base):
    __tablename__ = "past_reports"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    report_date: Mapped[date] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(String, unique=True)
    __table_args__ = (Index("ix_history_patient_date", "patient_id", "report_date", "id"),)


class Session(Base):
    __tablename__ = "sessions"
    patient_id: Mapped[int] = mapped_column(ID_TYPE)
    study_id: Mapped[int] = mapped_column(ID_TYPE)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String, default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        ForeignKeyConstraint(["study_id", "patient_id"], ["studies.id", "studies.patient_id"], ondelete="RESTRICT"),
        UniqueConstraint("study_id", "doctor_id"), UniqueConstraint("id", "study_id"), UniqueConstraint("id", "doctor_id"),
        CheckConstraint("revision > 0"), CheckConstraint("status IN ('active','findings_approved','final')"),
    )


class AIRequest(Base):
    __tablename__ = "ai_requests"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    actor_id: Mapped[int] = mapped_column(ID_TYPE)
    operation: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String)
    request_hash: Mapped[str] = mapped_column(String)
    input_revision: Mapped[int] = mapped_column(Integer)
    input_snapshot: Mapped[dict] = mapped_column(JSON_TYPE)
    result: Mapped[dict | None] = mapped_column(JSON_TYPE)
    provenance: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    error_code: Mapped[str | None] = mapped_column(String)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_of: Mapped[int | None] = mapped_column(ID_TYPE)
    __table_args__ = (
        ForeignKeyConstraint(["session_id", "actor_id"], ["sessions.id", "sessions.doctor_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["retry_of", "session_id"], ["ai_requests.id", "ai_requests.session_id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "session_id"), UniqueConstraint("session_id", "actor_id", "operation", "idempotency_key"),
        CheckConstraint("operation IN ('analyze','ask','draft_report')"),
        CheckConstraint("status IN ('queued','running','succeeded','failed','timed_out','cancelled')"),
        Index("uq_live_request", "session_id", unique=True, postgresql_where=text("status IN ('queued','running')"), sqlite_where=text("status IN ('queued','running')")),
        Index("ix_job_claim", "status", "created_at", "id"),
    )


class Box(Base):
    __tablename__ = "boxes"
    session_id: Mapped[int] = mapped_column(ID_TYPE)
    study_id: Mapped[int] = mapped_column(ID_TYPE)
    image_id: Mapped[int] = mapped_column(ID_TYPE)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    x2: Mapped[float] = mapped_column(Float)
    y2: Mapped[float] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    ai_request_id: Mapped[int | None] = mapped_column(ID_TYPE)
    ai_original: Mapped[dict | None] = mapped_column(JSON_TYPE)
    review_action: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["session_id", "study_id"], ["sessions.id", "sessions.study_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["image_id", "study_id"], ["images.id", "images.study_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["ai_request_id", "session_id"], ["ai_requests.id", "ai_requests.session_id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "session_id"), CheckConstraint("0 <= x1 AND x1 < x2 AND 0 <= y1 AND y1 < y2"),
        CheckConstraint("source IN ('ai','doctor')"),
        CheckConstraint("review_action IN ('pending','accepted','edited','rejected','added')"),
        CheckConstraint("source != 'ai' OR (ai_request_id IS NOT NULL AND ai_original IS NOT NULL)"),
        Index("ix_boxes_session", "session_id"),
    )


class Message(Base):
    __tablename__ = "messages"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    ai_request_id: Mapped[int] = mapped_column(ID_TYPE)
    role: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    box_id: Mapped[int | None] = mapped_column(ID_TYPE)
    box_snapshot: Mapped[dict | None] = mapped_column(JSON_TYPE)
    model_version: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["ai_request_id", "session_id"], ["ai_requests.id", "ai_requests.session_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["box_id", "session_id"], ["boxes.id", "boxes.session_id"], ondelete="RESTRICT"),
        UniqueConstraint("ai_request_id", "role"), CheckConstraint("role IN ('doctor','ai')"), Index("ix_messages_session", "session_id", "id"),
    )


class Findings(Base):
    __tablename__ = "findings"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    items: Mapped[list] = mapped_column(JSON_TYPE)
    ai_items: Mapped[list] = mapped_column(JSON_TYPE)
    no_abnormality_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    annotation_snapshot: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("session_id", "version"), CheckConstraint("status IN ('ai_proposed','in_review','approved')"), CheckConstraint("(status = 'approved') = (approved_by IS NOT NULL AND approved_at IS NOT NULL)"))


class Report(Base):
    __tablename__ = "reports"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    findings_version: Mapped[int] = mapped_column(Integer)
    ai_request_id: Mapped[int] = mapped_column(ID_TYPE)
    status: Mapped[str] = mapped_column(String)
    content: Mapped[dict] = mapped_column(JSON_TYPE)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["session_id", "findings_version"], ["findings.session_id", "findings.version"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["ai_request_id", "session_id"], ["ai_requests.id", "ai_requests.session_id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('draft','stale','final')"), CheckConstraint("(status = 'final') = (approved_by IS NOT NULL AND approved_at IS NOT NULL)"),
        Index("uq_report_draft", "session_id", unique=True, postgresql_where=text("status = 'draft'"), sqlite_where=text("status = 'draft'")),
        Index("uq_report_final", "session_id", unique=True, postgresql_where=text("status = 'final'"), sqlite_where=text("status = 'final'")),
    )


class Event(Base):
    __tablename__ = "events"
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    idempotency_scope: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str | None] = mapped_column(String)
    request_hash: Mapped[str | None] = mapped_column(String)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict | None] = mapped_column(JSON_TYPE)
    __table_args__ = (UniqueConstraint("user_id", "idempotency_scope", "idempotency_key"), Index("ix_events_session", "session_id", "id"))
