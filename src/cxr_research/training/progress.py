"""Private numeric progress and recovery checkpoints; no batch or text logging."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from cxr_research.utils.io import write_json

NUMERIC_LOGS = frozenset({"loss", "eval_loss", "learning_rate", "grad_norm",
    "train_loss", "train_runtime", "train_samples_per_second", "train_steps_per_second",
    "eval_runtime", "eval_samples_per_second", "eval_steps_per_second"})


def finite_numbers(values):
    return {key: float(value) for key, value in values.items()
            if key in NUMERIC_LOGS and type(value) in (int, float) and math.isfinite(value)}


def progress_callback(config):
    """Use the public Trainer callback control without changing epoch evaluation."""
    from transformers import TrainerCallback

    class Progress(TrainerCallback):
        def __init__(self):
            self.started = time.monotonic()
            self.metrics = {}

        def snapshot(self, args, state, phase):
            payload = {"schema_version": 1, "phase": phase,
                "updated_at_unix": time.time(), "elapsed_seconds": time.monotonic() - self.started,
                "global_step": int(state.global_step), "max_steps": int(state.max_steps),
                "epoch": float(state.epoch or 0), **self.metrics}
            if config.device == "cuda":
                import torch
                payload.update(allocated_bytes=torch.cuda.memory_allocated(),
                    reserved_bytes=torch.cuda.memory_reserved(), peak_allocated_bytes=torch.cuda.max_memory_allocated())
            write_json(Path(args.output_dir) / "progress.json", payload)
            return payload

        def on_train_begin(self, args, state, control, **kwargs):
            self.snapshot(args, state, "training")

        def on_step_end(self, args, state, control, **kwargs):
            if state.global_step % config.train.checkpoint_steps == 0:
                control.should_save = True
            # Every optimizer update supplies a heartbeat even between log events.
            self.snapshot(args, state, "training")
            return control

        def on_log(self, args, state, control, logs=None, **kwargs):
            self.metrics.update(finite_numbers(logs or {}))
            payload = self.snapshot(args, state, "training")
            with (Path(args.output_dir) / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, allow_nan=False, sort_keys=True) + "\n")

        def on_save(self, args, state, control, **kwargs):
            from cxr_research.experiments.checkpoints import artifact_inventory
            checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            write_json(checkpoint / "artifact_hashes.json", artifact_inventory(checkpoint))
            # Publish only after the complete Trainer state has an integrity inventory.
            write_json(Path(args.output_dir) / "latest_checkpoint.json",
                       {"checkpoint": checkpoint.name, "global_step": int(state.global_step)})

        def on_train_end(self, args, state, control, **kwargs):
            self.snapshot(args, state, "training_ended")

    return Progress()
