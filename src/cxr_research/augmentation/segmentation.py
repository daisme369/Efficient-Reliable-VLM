"""Sample each geometric transform once for the image, mask and valid pixels."""

from __future__ import annotations

import random
from PIL import Image, ImageEnhance

from cxr_research.experiments.config import UNetConfig


def augment_pair(image, mask, valid, config: UNetConfig, *, seed: int):
    rng = random.Random(seed)
    angle = rng.uniform(-config.rotation_degrees, config.rotation_degrees)
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BILINEAR)
        mask = mask.rotate(angle, resample=Image.Resampling.NEAREST)
        valid = valid.rotate(angle, resample=Image.Resampling.NEAREST)
    if config.brightness:
        image = ImageEnhance.Brightness(image).enhance(
            rng.uniform(1 - config.brightness, 1 + config.brightness))
    if config.contrast:
        image = ImageEnhance.Contrast(image).enhance(
            rng.uniform(1 - config.contrast, 1 + config.contrast))
    return image, mask, valid
