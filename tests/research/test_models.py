from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from types import SimpleNamespace

HAS_ML = all(importlib.util.find_spec(name) for name in ("torch", "numpy", "PIL"))


@unittest.skipUnless(HAS_ML, "CPU ML dependencies are not installed; run in the project CPU environment")
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        torch.set_num_threads(1)

    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_answer_prefix_masks_multimodal_and_padding(self):
        import torch
        from cxr_research.preprocessing.medgemma import answer_labels
        full = torch.tensor([[1, 8, 8, 3, 4, 5], [0, 1, 8, 3, 6, 5]])
        attention = torch.tensor([[1, 1, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1]])
        prompt = torch.tensor([[1, 8, 8, 3], [0, 1, 8, 3]])
        prefix_attention = torch.tensor([[1, 1, 1, 1], [0, 1, 1, 1]])
        result = answer_labels(full, attention, prompt, prefix_attention)
        self.assertEqual(result.tolist(), [[-100, -100, -100, -100, 4, 5], [-100, -100, -100, -100, 6, 5]])

    def test_prefix_mismatch_and_empty_answer_are_rejected(self):
        import torch
        from cxr_research.utils.errors import ContractError
        from cxr_research.preprocessing.medgemma import answer_labels
        with self.assertRaises(ContractError):
            answer_labels(torch.tensor([[1, 2, 3]]), torch.ones(1, 3), torch.tensor([[1, 9]]), torch.ones(1, 2))
        with self.assertRaises(ContractError):
            answer_labels(torch.tensor([[1, 2]]), torch.ones(1, 2), torch.tensor([[1, 2]]), torch.ones(1, 2))

    def test_shifted_answer_loss_uses_token_denominator(self):
        import math
        import torch
        from cxr_research.training.losses import answer_loss
        logits = torch.zeros(2, 4, 8, requires_grad=True)
        labels = torch.tensor([[-100, -100, 2, 3], [-100, -100, -100, 4]])
        loss = answer_loss(SimpleNamespace(logits=logits), labels, 6)
        self.assertAlmostEqual(loss.item(), math.log(8)/2, places=6)
        loss.backward()
        self.assertEqual(logits.grad[:, 0].abs().sum().item(), 0)

    def test_generation_dialogue_cannot_contain_reference(self):
        from cxr_research.preprocessing.medgemma import dialogue
        messages = dialogue("Synthetic?", "Prompt")
        self.assertEqual([m["role"] for m in messages], ["system", "user"])

    def test_chexmask_rle_is_one_based_row_major(self):
        from cxr_research.preprocessing.masks import decode_rle
        self.assertEqual(decode_rle("2 2 6 1", 2, 3).tolist(), [[0, 255, 255], [0, 0, 255]])

    def test_bad_rle_rejected(self):
        from cxr_research.utils.errors import ContractError
        from cxr_research.preprocessing.masks import decode_rle
        for encoding in ("0 1", "1 2 2 2", "6 2", "1"):
            with self.assertRaises(ContractError):
                decode_rle(encoding, 2, 3)

    def test_preparation_audits_source_patients_before_join_filtering(self):
        from cxr_research.data.preparation import prepare
        from cxr_research.utils.errors import ContractError
        first = self.root / "first.png"
        second = self.root / "second.png"
        first.write_bytes(b"synthetic")
        second.write_bytes(b"synthetic")
        rows = [dict(image_id="a", patient_id="same", image="first.png", split="train", view="AP"),
                dict(image_id="b", patient_id="same", image="second.png", split="test", view="AP")]
        table = self.root / "images.jsonl"
        table.write_text("".join(json.dumps(row) + "\n" for row in rows))
        spec = dict(image_root=str(self.root), prepared_dir=str(self.root / "prepared"),
                    patient_namespace="synthetic", mask_coordinates="original_resolution",
                    split_values={"train": "train", "test": "test"},
                    images={"path": str(table), "format": "jsonl", "name": "synthetic-images",
                            "version": "1", "access": "synthetic", "license": "synthetic",
                            "columns": {k: k for k in rows[0]}})
        for key in ("split_policy", "uncertain_label_policy", "input_policy", "template_policy", "access_review", "producer_commit"):
            spec[key] = "synthetic-fixture"
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ContractError, "patient split overlap"):
            prepare(path, self.root / "separate-source-checkout")
        status = json.loads((self.root / "prepared/status.json").read_text())
        self.assertEqual(status["status"], "failed")

    def test_training_augmentation_keeps_mask_geometry_and_is_repeatable(self):
        import numpy as np
        from PIL import Image
        from cxr_research.augmentation.segmentation import augment_pair
        from cxr_research.experiments.config import UNetConfig
        pixels = np.zeros((32, 32), dtype="uint8")
        pixels[5:22, 7:25] = 255
        image = Image.fromarray(pixels)
        valid = Image.new("L", image.size, 255)
        config = UNetConfig(size=32, rotation_degrees=5)
        a, b, c = augment_pair(image, image, valid, config, seed=17)
        aa, bb, cc = augment_pair(image, image, valid, config, seed=17)
        self.assertTrue(np.array_equal(np.array(a), np.array(aa)))
        self.assertTrue(np.array_equal(np.array(b), np.array(bb)))
        self.assertTrue(np.array_equal(np.array(c), np.array(cc)))
        overlap = np.mean((np.array(a) >= 128) == (np.array(b) > 0))
        self.assertGreater(overlap, .98)

    def test_segmentation_transforms_preserve_laterality(self):
        import numpy as np
        import torch
        from PIL import Image
        from cxr_research.experiments.config import UNetConfig
        from cxr_research.preprocessing.segmentation import transform_pair
        from cxr_research.preprocessing.tensor_segmentation import transform_tensor_pair
        pixels = np.zeros((32, 32), dtype="uint8")
        pixels[8:24, 2:9] = 255
        settings = UNetConfig(size=32)
        _, pil_mask, _, _ = transform_pair(Image.fromarray(pixels), Image.fromarray(pixels),
                                            settings, training=True, seed=3)
        tensor = torch.from_numpy(pixels.copy()).unsqueeze(0)
        _, tensor_mask, _ = transform_tensor_pair(tensor, tensor, settings, training=True, seed=3)
        self.assertLess(torch.nonzero(pil_mask[0], as_tuple=False)[:, 1].max().item(), 16)
        self.assertLess(torch.nonzero(tensor_mask[0], as_tuple=False)[:, 1].max().item(), 16)

    def test_geometry_and_validation_disable_augmentation(self):
        import torch
        from PIL import Image, ImageDraw
        from cxr_research.experiments.config import UNetConfig
        from cxr_research.preprocessing.segmentation import transform_pair
        from cxr_research.preprocessing.images import restore_mask
        image = Image.new("L", (48, 32))
        ImageDraw.Draw(image).rectangle((8, 6, 35, 24), fill=255)
        settings = UNetConfig(size=32, rotation_degrees=5, brightness=.1, contrast=.1)
        x, y, valid, geometry = transform_pair(image, image, settings, training=False, seed=0)
        xx, yy, vv, _ = transform_pair(image, image, settings, training=False, seed=999)
        self.assertTrue(torch.equal(x, xx) and torch.equal(y, yy) and torch.equal(valid, vv))
        self.assertEqual(valid.sum().item(), 21*32)
        self.assertLess((x-y).abs().mean().item(), .05)
        restored = restore_mask(Image.fromarray((y[0].numpy()*255).astype("uint8")), geometry)
        self.assertEqual(restored.size, image.size)

    def test_segmentation_metrics_ignore_padding_and_define_empty_masks(self):
        import torch
        from cxr_research.metrics.segmentation import segmentation_scores
        logits = torch.tensor([[[[20., 20.], [-20., 20.]]]])
        target = torch.tensor([[[[1., 0.], [0., 0.]]]])
        valid = torch.tensor([[[[1., 0.], [1., 0.]]]])
        dice, iou = segmentation_scores(logits, target, valid, .5)
        self.assertEqual((dice.item(), iou.item()), (1., 1.))
        dice, iou = segmentation_scores(-torch.ones_like(logits)*20, target*0, valid, .5)
        self.assertEqual((dice.item(), iou.item()), (1., 1.))

    def test_tiny_unet_learns_and_epoch_resume_matches(self):
        import torch
        from cxr_research.experiments.config import load_config
        from cxr_research.data.manifest import load_manifest
        from cxr_research.utils.reproducibility import seed_everything
        from cxr_research.data.synthetic import create_fixture
        from cxr_research.training.segmentation import train
        config = load_config(create_fixture(self.root / "fixture"))
        manifest = load_manifest(config.data)
        complete, partial, resumed = [self.root / name for name in ("complete", "partial", "resumed")]
        for directory in (complete, partial, resumed):
            directory.mkdir()
        seed_everything(config.seed)
        train(config, manifest, complete)
        history = json.loads((complete / "history.json").read_text())
        self.assertLess(history[-1]["loss"], history[0]["loss"])
        seed_everything(config.seed)
        train(config, manifest, partial, stop_after_epoch=1)
        seed_everything(config.seed)
        train(config, manifest, resumed, resume=partial / "epoch-0001.pt")
        a = torch.load(complete / "epoch-0003.pt", weights_only=True)
        b = torch.load(resumed / "epoch-0003.pt", weights_only=True)
        self.assertTrue(all(torch.equal(a["model"][key], b["model"][key]) for key in a["model"]))
        self.assertTrue((resumed / "best.json").exists())

    def test_failed_run_status_and_no_overwrite(self):
        from cxr_research.experiments.config import load_config
        from cxr_research.data.manifest import load_manifest
        from cxr_research.experiments.runs import run_record
        from cxr_research.data.synthetic import create_fixture
        config = load_config(create_fixture(self.root / "fixture"))
        manifest = load_manifest(config.data)
        repository = Path(__file__).resolve().parents[2]
        with self.assertRaises(RuntimeError):
            with run_record(config, manifest, "train", repository):
                raise RuntimeError("private-sentinel")
        record = Path(config.output_root) / config.run_id / "run.json"
        self.assertEqual(json.loads(record.read_text())["status"], "failed")
        self.assertNotIn("private-sentinel", record.read_text())
        with self.assertRaises(FileExistsError):
            with run_record(config, manifest, "train", repository):
                pass


if __name__ == "__main__":
    unittest.main()
