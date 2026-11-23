from datetime import UTC, datetime
from typing import Protocol


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class InferenceProvider(Protocol):
    def capabilities(self) -> dict: ...

    def invoke(self, operation: str, context: dict, image: bytes | None, timeout: float) -> dict: ...
