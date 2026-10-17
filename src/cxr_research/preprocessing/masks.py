"""Decode original-resolution CheXmask masks."""

from __future__ import annotations

from cxr_research.utils.errors import ContractError


def decode_rle(rle: str, height: int, width: int):
    """CheXmask uses one-based runs and row-major reshape, not COCO encoding."""
    import numpy as np
    if height <= 0 or width <= 0 or height * width > 100_000_000:
        raise ContractError("invalid mask dimensions")
    values = [int(value) for value in rle.split()]
    if len(values) % 2:
        raise ContractError("unpaired RLE run")
    flat = np.zeros(height * width, dtype=np.uint8)
    previous_end = 0
    for offset in range(0, len(values), 2):
        begin, length = values[offset] - 1, values[offset + 1]
        end = begin + length
        if begin < previous_end or length <= 0 or end > len(flat):
            raise ContractError("RLE run overlaps or exceeds image")
        flat[begin:end] = 255
        previous_end = end
    return flat.reshape(height, width)
