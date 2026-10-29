from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cxr_research.experiments.config import (
    DataConfig, ExperimentConfig, MedGemmaConfig, TrainingConfig,
    config_from_dict, validate_config,
)
from cxr_research.experiments.identity import _identity, _verify_parent
from cxr_research.utils.errors import ContractError


ROOT = Path(__file__).resolve().parents[2]
HAS_PEFT = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "peft"))


def profile(quantization="none", baseline="B1", precision="bf16"):
    return ExperimentConfig("synthetic-profile", "medgemma", baseline,
        DataConfig("/synthetic", "/synthetic/manifest.jsonl", "/synthetic/provenance.json", True),
        "/synthetic/runs", device="cuda", train=TrainingConfig(precision=precision),
        medgemma=MedGemmaConfig(revision="a" * 40, quantization=quantization))


class ProfileContractTests(unittest.TestCase):
    def test_templates_choose_matching_unquantized_bf16_profiles(self):
        for baseline in ("b0", "b1"):
            raw = json.loads((ROOT / f"configs/medgemma_{baseline}.json").read_text())
            self.assertEqual(raw["medgemma"]["quantization"], "none")
            self.assertEqual(raw["train"]["precision"], "bf16")
            raw["medgemma"]["revision"] = "a" * 40
            config_from_dict(raw)

    def test_serialized_profile_controls_fingerprint_and_legacy_default(self):
        config = profile()
        self.assertEqual(config_from_dict(config.as_dict()), config)
        self.assertNotEqual(config.fingerprint, profile("nf4").fingerprint)
        self.assertNotEqual(config.fingerprint, profile(precision="fp16").fingerprint)
        raw = config.as_dict()
        del raw["medgemma"]["quantization"]
        self.assertEqual(config_from_dict(raw).medgemma.quantization, "nf4")

    def test_unknown_quantization_is_rejected(self):
        for value in ("int8", "NF4", None, {}, True):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "quantization"):
                validate_config(profile(value))

    def test_identity_rejects_different_precision_quantization_and_legacy_artifacts(self):
        manifest = SimpleNamespace(sha256="synthetic-manifest", provenance_sha256="synthetic-provenance")
        processor = SimpleNamespace(to_dict=lambda: {"processor": "synthetic"}, chat_template="synthetic")
        current = _identity(profile(), manifest, processor)
        self.assertEqual((current["quantization"], current["precision"]), ("none", "bf16"))
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            path = Path(directory) / "model_identity.json"
            path.write_text(json.dumps(current))
            _verify_parent(path, current)
            legacy = dict(current, quantization="nf4_double_quant")
            legacy.pop("precision")
            for other in (_identity(profile("nf4"), manifest, processor),
                          _identity(profile(precision="fp16"), manifest, processor), legacy):
                path.write_text(json.dumps(other))
                with self.assertRaisesRegex(ContractError, "identity differs"):
                    _verify_parent(path, current)


class LoaderContractTests(unittest.TestCase):
    def setUp(self):
        self.torch = SimpleNamespace(bfloat16=object(), float16=object(), float32=object())
        self.model = Mock()
        self.model.named_modules.return_value = [("model.language_model.layers.0.q_proj", Mock())]
        self.model.named_parameters.return_value = [
            ("base_model.model.language_model.layers.0.q_proj.lora_A.default.weight",
             SimpleNamespace(requires_grad=True))]
        self.processor = SimpleNamespace(tokenizer=SimpleNamespace(padding_side=None))
        self.transformers = ModuleType("transformers")
        self.transformers.AutoModelForImageTextToText = SimpleNamespace(from_pretrained=Mock(return_value=self.model))
        self.transformers.AutoProcessor = SimpleNamespace(from_pretrained=Mock(return_value=self.processor))
        self.transformers.BitsAndBytesConfig = Mock(return_value=object())
        utils = ModuleType("transformers.utils")
        utils.logging = SimpleNamespace(set_verbosity_error=Mock())
        self.peft = ModuleType("peft")
        self.peft.LoraConfig = Mock()
        self.peft.get_peft_model = Mock(return_value=self.model)
        self.peft.prepare_model_for_kbit_training = Mock(return_value=self.model)
        self.modules = {"torch": self.torch, "transformers": self.transformers,
                        "transformers.utils": utils, "peft": self.peft}

    def load(self, config, training=True):
        with patch.dict(sys.modules, self.modules):
            from cxr_research.models import medgemma
            with patch.object(medgemma, "torch", self.torch):
                return medgemma.load_model(config, training=training)

    def test_bf16_training_never_quantizes_or_uses_kbit_preparation(self):
        self.load(profile())
        kwargs = self.transformers.AutoModelForImageTextToText.from_pretrained.call_args.kwargs
        self.assertIs(kwargs["dtype"], self.torch.bfloat16)
        self.assertNotIn("quantization_config", kwargs)
        self.transformers.BitsAndBytesConfig.assert_not_called()
        self.peft.prepare_model_for_kbit_training.assert_not_called()
        self.model.requires_grad_.assert_called_once_with(False)
        self.model.gradient_checkpointing_enable.assert_called_once_with(
            gradient_checkpointing_kwargs={"use_reentrant": False})
        self.model.enable_input_require_grads.assert_called_once_with()
        self.assertFalse(self.model.config.use_cache)
        self.assertEqual(self.processor.tokenizer.padding_side, "right")
        self.assertTrue(self.peft.get_peft_model.call_args.kwargs["autocast_adapter_dtype"])

    def test_nf4_remains_explicit_and_uses_kbit_preparation(self):
        self.load(profile("nf4"))
        self.transformers.BitsAndBytesConfig.assert_called_once_with(load_in_4bit=True,
            bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=self.torch.bfloat16)
        kwargs = self.transformers.AutoModelForImageTextToText.from_pretrained.call_args.kwargs
        self.assertIs(kwargs["quantization_config"], self.transformers.BitsAndBytesConfig.return_value)
        self.peft.prepare_model_for_kbit_training.assert_called_once_with(self.model,
            use_gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False})
        self.model.gradient_checkpointing_enable.assert_not_called()

    def test_unquantized_b0_and_b1_evaluation_are_frozen_without_new_adapters(self):
        for baseline in ("B0", "B1"):
            with self.subTest(baseline=baseline):
                self.load(profile(baseline=baseline), training=False)
                self.model.requires_grad_.assert_called_with(False)
                self.model.eval.assert_called_with()
                self.assertEqual(self.processor.tokenizer.padding_side, "left")
        self.peft.get_peft_model.assert_not_called()
        self.peft.prepare_model_for_kbit_training.assert_not_called()
        self.transformers.BitsAndBytesConfig.assert_not_called()

    def test_missing_language_targets_are_rejected(self):
        self.model.named_modules.return_value = [("model.vision_tower.q_proj", Mock())]
        with self.assertRaisesRegex(ContractError, "target modules"):
            self.load(profile())
        self.peft.get_peft_model.assert_not_called()

    def test_trainable_base_vision_or_projector_weights_are_rejected(self):
        for name in ("model.language_model.q_proj.weight", "model.vision_tower.lora_A.weight",
                     "model.multi_modal_projector.weight"):
            self.model.named_parameters.return_value = [(name, SimpleNamespace(requires_grad=True))]
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, "trainable parameter scope"):
                self.load(profile())


@unittest.skipUnless(HAS_PEFT and sys.platform == "linux", "tiny PEFT integration uses the prepared Linux environment")
class TinyLoRAIntegrationTests(unittest.TestCase):
    def test_bf16_gradient_flow_and_adapter_roundtrip(self):
        import torch
        from peft import PeftModel
        from transformers import Gemma3Config, Gemma3ForConditionalGeneration
        from cxr_research.models.medgemma import load_model

        torch.set_num_threads(1)
        settings = Gemma3Config(text_config={"vocab_size": 64, "hidden_size": 32,
            "intermediate_size": 64, "num_hidden_layers": 2, "num_attention_heads": 4,
            "num_key_value_heads": 2, "head_dim": 8, "max_position_embeddings": 64,
            "sliding_window": 16, "pad_token_id": 0, "bos_token_id": 1, "eos_token_id": 2},
            vision_config={"hidden_size": 32, "intermediate_size": 64, "num_hidden_layers": 1,
                "num_attention_heads": 4, "image_size": 16, "patch_size": 4},
            mm_tokens_per_image=4, image_token_index=63, boi_token_index=61, eoi_token_index=62)
        settings._attn_implementation = "eager"

        def fresh_base():
            torch.manual_seed(7)
            return Gemma3ForConditionalGeneration(settings).to(dtype=torch.bfloat16)

        base = fresh_base()
        processor = SimpleNamespace(tokenizer=SimpleNamespace(padding_side=None))
        with patch("transformers.AutoModelForImageTextToText.from_pretrained", return_value=base), \
                patch("transformers.AutoProcessor.from_pretrained", return_value=processor):
            config = profile()
            config = replace(config, medgemma=replace(config.medgemma, lora_dropout=0.0))
            model, _ = load_model(config, training=True)
        trainable = [(name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad]
        self.assertTrue(trainable)
        self.assertTrue(all("language_model" in name and "lora_" in name for name, _ in trainable))
        self.assertTrue(all(parameter.dtype == torch.float32 for _, parameter in trainable))
        frozen = [parameter for _, parameter in model.named_parameters() if not parameter.requires_grad]
        self.assertTrue(all(parameter.dtype == torch.bfloat16 for parameter in frozen if parameter.is_floating_point()))
        before = [parameter.detach().clone() for parameter in frozen]
        tokens = torch.tensor([[1, 8, 9, 10, 2]])
        labels = torch.tensor([[-100, -100, 9, 10, 2]])
        optimizer = torch.optim.AdamW([parameter for _, parameter in trainable], lr=0.01)
        model.train()
        with torch.autocast("cpu", dtype=torch.bfloat16):
            loss = model(input_ids=tokens, attention_mask=torch.ones_like(tokens), labels=labels).loss
        self.assertTrue(torch.isfinite(loss))
        loss.backward()
        self.assertTrue(any(parameter.grad is not None and parameter.grad.abs().sum() > 0
                            for _, parameter in trainable))
        self.assertTrue(all(parameter.grad is None for parameter in frozen))
        optimizer.step()
        self.assertTrue(all(torch.equal(old, parameter) for old, parameter in zip(before, frozen)))
        model.eval()
        with torch.no_grad(), torch.autocast("cpu", dtype=torch.bfloat16):
            expected = model(input_ids=tokens).logits
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            model.save_pretrained(directory, safe_serialization=True)
            reloaded = PeftModel.from_pretrained(fresh_base(), directory, is_trainable=False).eval()
            with torch.no_grad(), torch.autocast("cpu", dtype=torch.bfloat16):
                actual = reloaded(input_ids=tokens).logits
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
