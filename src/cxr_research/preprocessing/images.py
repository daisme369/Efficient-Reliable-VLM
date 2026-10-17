"""Deterministic paired image and mask geometry."""

from __future__ import annotations

from dataclasses import dataclass
from PIL import Image
from cxr_research.utils.errors import ContractError


@dataclass(frozen=True)
class Geometry:
    original_width: int
    original_height: int
    width: int
    height: int
    left: int
    top: int
    size: int


def letterbox(image: Image.Image, mask: Image.Image, size: int):
    if image.size != mask.size:
        raise ContractError("image and mask dimensions differ")
    w, h = image.size
    factor = size / max(w, h)
    rw, rh = max(1, round(w * factor)), max(1, round(h * factor))
    left, top = (size - rw) // 2, (size - rh) // 2
    geometry = Geometry(w, h, rw, rh, left, top, size)
    output = Image.new("L", (size, size))
    target = Image.new("L", (size, size))
    valid = Image.new("L", (size, size))
    output.paste(image.resize((rw, rh), Image.Resampling.BILINEAR), (left, top))
    target.paste(mask.resize((rw, rh), Image.Resampling.NEAREST), (left, top))
    valid.paste(Image.new("L", (rw, rh), 255), (left, top))
    return output, target, valid, geometry


def restore_mask(mask: Image.Image, geometry: Geometry) -> Image.Image:
    g = geometry
    return mask.crop((g.left, g.top, g.left + g.width, g.top + g.height)).resize(
        (g.original_width, g.original_height), Image.Resampling.NEAREST)
