"""Task-specific PyTorch datasets; manifests own split selection."""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image
from cxr_research.data.manifest import local_file
from cxr_research.experiments.config import UNetConfig
from cxr_research.preprocessing.segmentation import transform_pair
from cxr_research.utils.errors import ContractError


class SegmentationDataset(torch.utils.data.Dataset):
    def __init__(self, manifest, samples, config: UNetConfig, *, training: bool, seed: int):
        self.manifest, self.samples, self.config = manifest, samples, config
        self.training, self.seed, self.epoch = training, seed, 0

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        sample = self.samples[index]
        with Image.open(local_file(self.manifest.root, sample.image)) as raw:
            if raw.mode not in {"L", "RGB"}:
                raise ContractError("segmentation expects prepared 8-bit PNG/JPG images")
            image = raw.convert("L")
        with Image.open(local_file(self.manifest.root, sample.mask)) as raw:
            mask = raw.convert("L")
        if not set(np.unique(np.array(mask))).issubset({0, 1, 255}):
            raise ContractError("expected a binary lung mask")
        values = transform_pair(image, mask, self.config, training=self.training,
                                seed=self.seed + self.epoch * len(self.samples) + index)
        return values[:3]


class VQADataset(torch.utils.data.Dataset):
    def __init__(self, manifest, samples):
        self.manifest, self.samples = manifest, samples

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        s = self.samples[index]
        with Image.open(local_file(self.manifest.root, s.image)) as image:
            rgb = image.convert("RGB")
        return {"image": rgb, "question": s.question, "answer": s.answer}
