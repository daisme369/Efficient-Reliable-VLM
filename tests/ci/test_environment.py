from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "ci"))

from check_environment import version_matches  # noqa: E402


class EnvironmentVersionTests(unittest.TestCase):
    def test_accepts_exact_versions(self) -> None:
        self.assertTrue(version_matches("transformers", "5.19.0", "5.19.0"))

    def test_accepts_cuda_local_version_for_torch_packages(self) -> None:
        self.assertTrue(version_matches("torch", "2.14.1+cu130", "2.14.1"))
        self.assertTrue(version_matches("torchvision", "0.29.1+cu130", "0.29.1"))

    def test_rejects_local_version_for_other_packages(self) -> None:
        self.assertFalse(version_matches("transformers", "5.19.0+local", "5.19.0"))


if __name__ == "__main__":
    unittest.main()
