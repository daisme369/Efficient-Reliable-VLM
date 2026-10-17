"""U-Net optimization and epoch-boundary resume."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import torch
from torch.utils.data import DataLoader
from cxr_research.data.loaders import SegmentationLoader
from cxr_research.evaluation.segmentation import evaluate_model
from cxr_research.experiments.checkpoints import load_checkpoint, save_checkpoint
from cxr_research.models.unet import UNet
from cxr_research.training.losses import segmentation_loss
from cxr_research.utils.errors import ContractError
from cxr_research.utils.io import write_json
from cxr_research.utils.precision import _autocast


def train(config, manifest, directory: Path, *, resume: Path | None = None, stop_after_epoch=None, store=None):
    training_count = len(manifest.select("unet", "train"))
    model = UNet(config.unet.base_channels).to(config.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate,
                                  weight_decay=config.train.weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.precision == "fp16")
    start, best, best_path = 0, -1.0, None
    if resume:
        state = load_checkpoint(resume, config, manifest)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scaler.load_state_dict(state["scaler"])
        torch.set_rng_state(state["torch_rng"])
        if config.device == "cuda":
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        start, best = state["epoch"], state["best_dice"]
        best_meta = json.loads((resume.parent / "best.json").read_text(encoding="utf-8"))
        previous_best = resume.parent / best_meta["checkpoint"]
        best_state = load_checkpoint(previous_best, config, manifest)
        if best_state["epoch"] > start or best_meta["validation_dice"] != best:
            raise ContractError("resume checkpoint is older than the run's selected best; use its latest checkpoint")
        best_path = "inherited-best.pt"
        shutil.copyfile(previous_best, directory / best_path)
        shutil.copyfile(previous_best.with_suffix(".json"), (directory / best_path).with_suffix(".json"))
        write_json(directory / "best.json", {"checkpoint": best_path, "validation_dice": best})
    if start >= config.train.epochs:
        raise ContractError("checkpoint already reached the configured epoch budget")
    validation_loader = SegmentationLoader(config, manifest, "validation", store=store)
    history = []
    for epoch in range(start, config.train.epochs):
        loader = SegmentationLoader(config, manifest, "train", epoch=epoch, store=store,
                                    decoder=validation_loader.decoder)
        model.train()
        optimizer.zero_grad(set_to_none=True)
        for index, batch in enumerate(loader):
            image, target, valid = [x.to(config.device, non_blocking=config.io.pin_memory) for x in batch]
            window_start = index // config.train.accumulation_steps * config.train.accumulation_steps
            window_end = min(window_start + config.train.accumulation_steps, len(loader))
            window_samples = min(window_end * config.train.batch_size, training_count) - window_start * config.train.batch_size
            with _autocast(config):
                loss = segmentation_loss(model(image), target, valid) * len(image) / window_samples
            if not torch.isfinite(loss):
                raise ContractError("non-finite segmentation loss")
            scaler.scale(loss).backward()
            if index + 1 == window_end:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.max_grad_norm)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
        metrics = evaluate_model(model, validation_loader, config)
        improved = metrics["dice"] > best
        best = max(best, metrics["dice"])
        checkpoint = save_checkpoint(directory, model, optimizer, scaler, epoch+1, config, manifest, best)
        if improved:
            best_path = checkpoint.name
            write_json(directory / "best.json", {"checkpoint": best_path, "validation_dice": best})
        history.append({"epoch": epoch+1, **metrics, "checkpoint": checkpoint.name})
        write_json(directory / "history.json", history)
        if stop_after_epoch is not None and epoch+1 >= stop_after_epoch:
            break
    result = {"epochs_completed": history[-1]["epoch"], "validation": history[-1],
              "best_validation_dice": best, "best_checkpoint_in_this_run": best_path,
              "trainable_parameters": sum(p.numel() for p in model.parameters()),
              "resume_from": str(resume) if resume else None}
    write_json(directory / "metrics.json", result)
    return result
