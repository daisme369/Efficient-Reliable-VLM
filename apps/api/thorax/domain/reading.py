from dataclasses import dataclass
from enum import StrEnum

from .errors import DomainError


class SessionStatus(StrEnum):
    ACTIVE = "active"
    APPROVED = "findings_approved"
    FINAL = "final"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


LIVE_STATES = (JobStatus.QUEUED, JobStatus.RUNNING)


@dataclass(frozen=True)
class SessionBinding:
    doctor_id: int
    patient_id: int
    study_id: int


@dataclass
class ReadingSession:
    binding: SessionBinding
    status: SessionStatus
    revision: int

    def assert_writable(self, live_job: bool = False) -> None:
        if self.status == SessionStatus.FINAL:
            raise DomainError("FINAL_READ_ONLY")
        if live_job:
            raise DomainError("AI_BUSY")

    def check_revision(self, expected: int | None) -> None:
        if expected is None:
            raise DomainError("PRECONDITION_REQUIRED", 428)
        if expected != self.revision:
            raise DomainError("REVISION_CONFLICT", 412)
