from dataclasses import dataclass

from .errors import DomainError


@dataclass
class Report:
    status: str
    findings_version: int
    content: dict

    def assert_current(self, latest_version: int, findings_status: str) -> None:
        if self.status != "draft" or self.findings_version != latest_version or findings_status != "approved":
            raise DomainError("REPORT_STALE")

    def finalize(self, latest_version: int, findings_status: str) -> None:
        self.assert_current(latest_version, findings_status)
        if not all(self.content.get(k, "").strip() for k in ("findings", "impression")):
            raise DomainError("REPORT_TEXT_REQUIRED", 422)
