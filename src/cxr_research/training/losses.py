"""Training objectives, separate from reported evaluation metrics."""

from __future__ import annotations

import torch
from torch.nn import functional as F


def segmentation_loss(logits, target, valid):
    axes = (1, 2, 3)
    bce = (F.binary_cross_entropy_with_logits(logits, target, reduction="none") * valid).sum(axes)
    bce = bce / valid.sum(axes).clamp_min(1)
    probability = logits.sigmoid() * valid
    truth = target * valid
    dice = (2 * (probability * truth).sum(axes) + 1e-6) / (
        probability.sum(axes) + truth.sum(axes) + 1e-6)
    return (bce + 1 - dice).mean()


def answer_loss(outputs, labels, num_items_in_batch=None):
    logits = outputs.logits[..., :-1, :].contiguous().float()
    targets = labels[..., 1:].contiguous().to(logits.device)
    denominator = (targets != -100).sum() if num_items_in_batch is None else num_items_in_batch
    return torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)),
             targets.reshape(-1), ignore_index=-100, reduction="sum") / denominator
