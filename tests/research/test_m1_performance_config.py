"""m1_performance settings: parsed for serving the M1 v2 adapter without changing older fingerprints."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.experiments.config import config_from_dict
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest

PERFORMANCE = {"brightness": 0.03, "compact_logits": True, "contrast": 0.03, "reuse_image_processing": True}


def raw(**extra) -> dict:
    return {"run_id": "m1-test", "task": "medgemma", "baseline": "B1", "output_root": "/private/out", "device": "cuda",
            "data": {"root": "/private/data", "manifest": "/private/data/m.jsonl", "provenance": "/private/data/p.json"},
            "train": {"precision": "bf16"}, "medgemma": {"quantization": "none", "revision": "a" * 40}, **extra}


def recorded_digest(config) -> str:
    """How the training run computed config_sha256: every field except the run-specific ones."""
    payload = config.as_dict()
    for key in ("run_id", "output_root", "device", "jira_issue", "protocol"):
        payload.pop(key)
    return payload, digest(payload)


class M1PerformanceConfigTests(unittest.TestCase):
    def test_configs_without_the_field_keep_their_fingerprint(self):
        config = config_from_dict(raw())
        payload, _ = recorded_digest(config)
        payload.pop("m1_performance")
        self.assertEqual(config.fingerprint, digest(payload))

    def test_recorded_settings_reproduce_the_training_fingerprint(self):
        config = config_from_dict(raw(m1_performance=PERFORMANCE))
        self.assertEqual(config.fingerprint, recorded_digest(config)[1])
        self.assertNotEqual(config.fingerprint, config_from_dict(raw()).fingerprint)

    def test_unknown_performance_field_is_rejected(self):
        with self.assertRaises(ContractError):
            config_from_dict(raw(m1_performance={**PERFORMANCE, "turbo": True}))

    def test_training_with_recorded_settings_is_refused(self):
        try:
            from cxr_research.training.medgemma import train
        except ImportError as error:  # the lightweight CI environment has no ML stack
            self.skipTest(str(error))
        with self.assertRaisesRegex(ContractError, "serving only"):
            train(config_from_dict(raw(m1_performance=PERFORMANCE)), manifest=None, directory=Path("unused"))


if __name__ == "__main__":
    unittest.main()
