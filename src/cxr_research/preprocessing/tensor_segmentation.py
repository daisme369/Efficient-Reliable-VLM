"""Device-resident paired geometry for DALI decoded U-Net inputs."""

import random
import torch
from torchvision.transforms import InterpolationMode
from torchvision.transforms.v2 import functional as F

from cxr_research.utils.errors import ContractError


def transform_tensor_pair(image, mask, config, *, training, seed):
    if image.shape != mask.shape or image.shape[0] != 1:
        raise ContractError("segmentation expects matched grayscale image and mask")
    if not bool(((mask == 0) | (mask == 1) | (mask == 255)).all()):
        raise ContractError("expected a binary lung mask")
    height, width = image.shape[-2:]
    factor = config.size / max(height, width)
    rh, rw = max(1, round(height * factor)), max(1, round(width * factor))
    top, left = (config.size - rh) // 2, (config.size - rw) // 2
    padding = [left, top, config.size - rw - left, config.size - rh - top]
    image = F.pad(F.resize(image, [rh, rw], interpolation=InterpolationMode.BILINEAR, antialias=True), padding)
    mask = F.pad(F.resize(mask, [rh, rw], interpolation=InterpolationMode.NEAREST_EXACT), padding)
    valid = F.pad(torch.ones((1, rh, rw), dtype=torch.uint8, device=image.device), padding)
    if training:
        rng = random.Random(seed)
        angle = rng.uniform(-config.rotation_degrees, config.rotation_degrees)
        if angle:
            image = F.rotate(image, angle, interpolation=InterpolationMode.BILINEAR)
            mask = F.rotate(mask, angle, interpolation=InterpolationMode.NEAREST)
            valid = F.rotate(valid, angle, interpolation=InterpolationMode.NEAREST)
        if config.brightness:
            image = F.adjust_brightness(image, rng.uniform(1-config.brightness, 1+config.brightness))
        if config.contrast:
            image = F.adjust_contrast(image, rng.uniform(1-config.contrast, 1+config.contrast))
    return image.float() / 255, (mask > 0).float(), (valid > 0).float()
