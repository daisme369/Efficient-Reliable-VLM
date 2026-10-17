"""Per-image Dice and IoU on valid image pixels."""

from __future__ import annotations




def segmentation_scores(logits, target, valid, threshold: float):
    import torch
    pred = (logits.sigmoid() >= threshold) & valid.bool()
    truth = target.bool() & valid.bool()
    axes = tuple(range(1, pred.ndim))
    intersection = (pred & truth).sum(axes).float()
    total = pred.sum(axes) + truth.sum(axes)
    union = (pred | truth).sum(axes)
    dice = torch.where(total > 0, 2 * intersection / total.clamp_min(1), 1.0)
    iou = torch.where(union > 0, intersection / union.clamp_min(1), 1.0)
    return dice, iou
