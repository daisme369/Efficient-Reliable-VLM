"""Validation inference and aggregate segmentation results."""

from __future__ import annotations

from pathlib import Path
import torch
from torch.utils.data import DataLoader
from cxr_research.data.loaders import SegmentationLoader
from cxr_research.experiments.checkpoints import load_checkpoint
from cxr_research.metrics import METRIC_VERSION
from cxr_research.metrics.segmentation import segmentation_scores
from cxr_research.models.unet import UNet
from cxr_research.training.losses import segmentation_loss
from cxr_research.utils.io import write_json
from cxr_research.utils.precision import _autocast


@torch.inference_mode()
def evaluate_model(model, loader, config):
    model.eval()
    dice_total, iou_total, count, loss_sum = 0.0, 0.0, 0, 0.0
    for batch in loader:
        image, target, valid = [x.to(config.device) for x in batch]
        with _autocast(config):
            logits = model(image)
            loss = segmentation_loss(logits, target, valid)
        dice, iou = segmentation_scores(logits, target, valid, config.unet.threshold)
        n = len(image)
        dice_total += dice.sum().item()
        iou_total += iou.sum().item()
        loss_sum += loss.item() * n
        count += n
    return {"count": count, "dice": dice_total/count, "iou": iou_total/count,
            "loss": loss_sum/count, "metric_version": METRIC_VERSION,
            "aggregation": "mean_per_image_valid_pixels", "empty_both_score": 1,
            "label_type": "synthetic" if config.data.synthetic else "anatomical_pseudo_label"}


def evaluate(config, manifest, directory: Path, checkpoint: Path, *, store=None):
    model = UNet(config.unet.base_channels).to(config.device)
    model.load_state_dict(load_checkpoint(checkpoint, config, manifest)["model"])
    result = evaluate_model(model, SegmentationLoader(config, manifest, "validation", store=store), config)
    write_json(directory / "metrics.json", result)
    return result
