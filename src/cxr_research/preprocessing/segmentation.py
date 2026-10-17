"""Compose deterministic preprocessing with optional train-only augmentation."""

from __future__ import annotations

import numpy as np
import torch
from cxr_research.augmentation.segmentation import augment_pair
from cxr_research.experiments.config import UNetConfig
from cxr_research.preprocessing.images import letterbox


def transform_pair(image, mask, config: UNetConfig, *, training: bool, seed: int):
    image, mask, valid, geometry = letterbox(image, mask, config.size)
    if training:
        image, mask, valid = augment_pair(image, mask, valid, config, seed=seed)
    tensor = torch.from_numpy(np.array(image, dtype=np.float32) / 255).unsqueeze(0)
    target = torch.from_numpy((np.array(mask) > 0).astype(np.float32)).unsqueeze(0)
    valid_tensor = torch.from_numpy((np.array(valid) > 0).astype(np.float32)).unsqueeze(0)
    return tensor, target, valid_tensor, geometry
