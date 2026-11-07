"""Fast demo choices remain explicit and preserve frozen base/vision scope."""
import json
import sys
import unittest
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from cxr_research.experiments.config import config_from_dict
from cxr_research.utils.errors import ContractError
from test_medgemma_profile import LoaderContractTests, profile


class FastDemoTests(unittest.TestCase):
    def test_sdpa_without_checkpointing_still_freezes_base_and_scope(self):
        fixture = LoaderContractTests()
        fixture.setUp()
        c = profile()
        c = replace(c, train=replace(c.train, gradient_checkpointing=False),
                    medgemma=replace(c.medgemma, attention='sdpa', lora_rank=8, lora_alpha=16, lora_dropout=0.0))
        fixture.load(c)
        fixture.model.requires_grad_.assert_called_once_with(False)
        fixture.model.gradient_checkpointing_enable.assert_not_called()
        fixture.model.enable_input_require_grads.assert_not_called()
        call = fixture.transformers.AutoModelForImageTextToText.from_pretrained.call_args
        self.assertEqual(call.kwargs['attn_implementation'], 'sdpa')
        self.assertEqual(fixture.peft.LoraConfig.call_args.kwargs['r'], 8)

    def test_fast_configuration_roundtrip_and_invalid_choices(self):
        c = profile()
        c = replace(c, train=replace(c.train, gradient_checkpointing=False, tf32=True,
                    optimizer='adamw_torch_fused', lr_scheduler='cosine', warmup_ratio=.03,
                    evaluation_strategy='no', evaluation_after_training=False, full_determinism=False),
                    medgemma=replace(c.medgemma, attention='sdpa'))
        self.assertEqual(config_from_dict(json.loads(json.dumps(c.as_dict()))), c)
        for field, value in [('gradient_checkpointing', 0), ('warmup_ratio', True),
                             ('warmup_ratio', float('nan')), ('optimizer', 'unsupported'),
                             ('evaluation_strategy', 'unsupported')]:
            raw = c.as_dict()
            raw['train'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                config_from_dict(raw)


if __name__ == '__main__':
    unittest.main()
