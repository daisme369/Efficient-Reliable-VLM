import math
from dataclasses import dataclass

from .errors import DomainError


@dataclass(frozen=True)
class PixelBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def validate(self, width: int, height: int) -> None:
        if not all(math.isfinite(n) for n in self.as_list()):
            raise DomainError("INVALID_BOX", 422)
        if not (0 <= self.x1 < self.x2 <= width and 0 <= self.y1 < self.y2 <= height):
            raise DomainError("INVALID_BOX", 422)

    def crop_bounds(self) -> tuple[int, int, int, int]:
        return math.floor(self.x1), math.floor(self.y1), math.ceil(self.x2), math.ceil(self.y2)

    def as_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]
