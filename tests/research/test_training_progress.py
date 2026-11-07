"""Synthetic numeric logging and interrupted Trainer recovery."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
HAS_TORCH = importlib.util.find_spec("torch") is not None
HAS_TRAINER = HAS_TORCH and importlib.util.find_spec("transformers") is not None
from cxr_research.experiments.config import DataConfig, ExperimentConfig, TrainingConfig, validate_config


def config():
    return ExperimentConfig("synthetic", "unet", "S1", DataConfig("/synthetic", "/synthetic/m", "/synthetic/p", True),
                            "/synthetic/runs", train=TrainingConfig(logging_steps=1, checkpoint_steps=1))


class ProgressTests(unittest.TestCase):
    def test_invalid_operational_cadence_is_rejected(self):
        for field in ("logging_steps", "checkpoint_steps"):
            for value in (True, 0, -1, 1.5, "1"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_config(replace(config(), train=replace(config().train, **{field: value})))

    @unittest.skipUnless(HAS_TORCH, "checkpoint inventory module uses torch")
    def test_callback_saves_without_evaluation_and_logs_only_finite_numbers(self):
        from cxr_research.training.progress import progress_callback
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp") as directory:
            root = Path(directory)
            with patch.dict(sys.modules, {"transformers": SimpleNamespace(TrainerCallback=object)}):
                callback = progress_callback(config())
            args = SimpleNamespace(output_dir=directory)
            state = SimpleNamespace(global_step=1, max_steps=4, epoch=.25)
            control = SimpleNamespace(should_save=False, should_evaluate=False)
            callback.on_step_end(args, state, control)
            self.assertTrue(control.should_save)
            self.assertFalse(control.should_evaluate)
            callback.on_log(args, state, control, logs={"loss": 1.25, "grad_norm": float("nan"),
                "question": "PRIVATE_SENTINEL", "eval_loss": "PRIVATE_SENTINEL", "learning_rate": .001})
            text = (root / "progress.jsonl").read_text()
            self.assertNotIn("PRIVATE_SENTINEL", text)
            self.assertNotIn("grad_norm", text)
            self.assertEqual(json.loads(text)["global_step"], 1)
            checkpoint = root / "checkpoint-1"
            checkpoint.mkdir()
            (checkpoint / "state.bin").write_bytes(b"synthetic")
            callback.on_save(args, state, control)
            from cxr_research.experiments.checkpoints import verify_artifacts
            verify_artifacts(checkpoint)
            self.assertEqual(json.loads((root / "latest_checkpoint.json").read_text())["checkpoint"], "checkpoint-1")
            (checkpoint / "state.bin").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_artifacts(checkpoint)


@unittest.skipUnless(HAS_TRAINER, "tiny CPU Trainer integration requires the ML environment")
class TrainerRecoveryTests(unittest.TestCase):
    def test_step_checkpoint_resume_matches_uninterrupted_epoch_selection(self):
        import torch
        from transformers import Trainer, TrainerCallback, TrainingArguments
        from cxr_research.training.progress import progress_callback
        from cxr_research.experiments.checkpoints import verify_artifacts
        torch.set_num_threads(1)

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.embedding = torch.nn.Embedding(8, 4)
                self.head = torch.nn.Linear(4, 8)
            def forward(self, input_ids, labels=None):
                logits = self.head(self.embedding(input_ids))
                loss = torch.nn.functional.cross_entropy(logits.reshape(-1, 8), labels.reshape(-1))
                return {"loss": loss, "logits": logits}

        rows = [{"input_ids": torch.tensor([1, i, 2]), "labels": torch.tensor([1, i, 2])} for i in range(3, 7)]
        class Interrupt(TrainerCallback):
            def on_save(self, args, state, control, **kwargs):
                if state.global_step == 1:
                    raise KeyboardInterrupt()

        def trainer(path, interrupt=False):
            torch.manual_seed(42)
            args = TrainingArguments(output_dir=str(path), use_cpu=True, num_train_epochs=2,
                per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=2,
                eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
                metric_for_best_model="eval_loss", greater_is_better=False, save_total_limit=2,
                logging_strategy="steps", logging_steps=1, report_to=[], disable_tqdm=True,
                dataloader_pin_memory=False, seed=42, data_seed=42, full_determinism=True,
                optim="adamw_torch")
            return Trainer(model=Tiny(), args=args, train_dataset=rows, eval_dataset=rows,
                callbacks=[progress_callback(config()), *([Interrupt()] if interrupt else [])])

        import contextlib, io
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp") as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            complete = trainer(root / "complete")
            complete.train()
            interrupted = trainer(root / "interrupted", True)
            with self.assertRaises(KeyboardInterrupt):
                interrupted.train()
            checkpoint = root / "interrupted/checkpoint-1"
            verify_artifacts(checkpoint)
            self.assertIsNone(interrupted.state.best_model_checkpoint)  # no epoch evaluation yet
            resumed = trainer(root / "resumed")
            resumed.train(resume_from_checkpoint=str(checkpoint))
            self.assertEqual(complete.state.global_step, resumed.state.global_step)
            self.assertEqual(resumed.state.global_step, 4)
            for name, tensor in complete.model.state_dict().items():
                torch.testing.assert_close(tensor, resumed.model.state_dict()[name], rtol=0, atol=0)
            evals = [r for r in resumed.state.log_history if "eval_loss" in r]
            self.assertEqual([r["step"] for r in evals], [2, 4])


if __name__ == "__main__":
    unittest.main()
