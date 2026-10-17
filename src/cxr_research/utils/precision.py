"""Shared tensor precision context; no model or experiment ownership."""

from __future__ import annotations

import torch


def _autocast(config):
    dtype = torch.float16 if config.train.precision == "fp16" else torch.bfloat16
    return torch.autocast(device_type=config.device, dtype=dtype, enabled=config.train.precision != "fp32")
