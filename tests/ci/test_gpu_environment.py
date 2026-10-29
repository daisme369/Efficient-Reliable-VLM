from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "ci"))

from check_gpu_environment import parse_driver_version_strings, parse_driver_versions, validate_profile  # noqa: E402


class GpuEnvironmentTests(unittest.TestCase):
    def valid_profile(self, role: str, capability: tuple[int, int], name: str = "") -> list[str]:
        return validate_profile(
            role=role,
            operating_system="Linux",
            cuda_available=True,
            cuda_version="13.0",
            torch_version="2.14.1+cu130",
            torchvision_version="0.29.1+cu130",
            bitsandbytes_version="0.50.2",
            dali_version="2.3.0",
            capabilities=[capability],
            driver_majors=[580],
            device_names=[name],
        )

    def test_accepts_hopper_for_training(self) -> None:
        self.assertEqual([], self.valid_profile("train", (9, 0)))

    def test_accepts_rtx_pro_6000_blackwell_for_training(self) -> None:
        for name in ("NVIDIA RTX PRO 6000 Blackwell", "RTX PRO 6000 Blackwell Server Edition",
                     "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
                     "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
                     "rtx_pro_6000_blackwell"):
            with self.subTest(name=name):
                self.assertEqual([], self.valid_profile("train", (12, 0), name))

    def test_blackwell_training_rejects_other_or_missing_model_identity(self) -> None:
        for name in ("", "NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 5000 Blackwell",
                     "NVIDIA RTX 6000 Ada Generation", "RTX A6000", "RTX PRO 6000",
                     "RTX PRO 6000 Blackwell Unknown Edition"):
            with self.subTest(name=name):
                self.assertTrue(any("model identity" in error
                                    for error in self.valid_profile("train", (12, 0), name)))

    def test_rtx_pro_6000_blackwell_requires_matching_capability(self) -> None:
        for capability in ((8, 6), (8, 9), (9, 0)):
            with self.subTest(capability=capability):
                self.assertTrue(any("requires sm120" in error for error in
                    self.valid_profile("train", capability, "NVIDIA RTX PRO 6000 Blackwell")))

    def test_each_blackwell_training_device_needs_its_own_identity(self) -> None:
        errors = validate_profile(
            role="train", operating_system="Linux", cuda_available=True, cuda_version="13.0",
            torch_version="2.14.1+cu130", torchvision_version="0.29.1+cu130",
            bitsandbytes_version="0.50.2", dali_version="2.3.0",
            capabilities=[(12, 0), (12, 0)], driver_majors=[580, 580],
            device_names=["NVIDIA RTX PRO 6000 Blackwell"],
        )
        self.assertTrue(any("GPU 1" in error and "model identity" in error for error in errors))

    def test_ampere_is_only_allowed_for_synthetic_smoke(self) -> None:
        self.assertEqual([], self.valid_profile("smoke", (8, 6)))
        for role in ("train", "inference"):
            self.assertTrue(any("sm86" in error for error in self.valid_profile(role, (8, 6))))

    def test_rejects_missing_or_mismatched_dali(self) -> None:
        for installed in ("not-installed", "2.2.0"):
            errors = validate_profile(
                role="smoke",
                operating_system="Linux",
                cuda_available=True,
                cuda_version="13.0",
                torch_version="2.14.1+cu130",
                torchvision_version="0.29.1+cu130",
                bitsandbytes_version="0.50.2",
                dali_version=installed,
                capabilities=[(8, 6)],
                driver_majors=[580],
            )
            self.assertTrue(any("nvidia-dali-cuda130" in error for error in errors))

    def test_accepts_ada_and_blackwell_for_inference(self) -> None:
        self.assertEqual([], self.valid_profile("inference", (8, 9)))
        self.assertEqual([], self.valid_profile("inference", (12, 0)))

    def test_rejects_role_mismatch_and_old_driver(self) -> None:
        errors = validate_profile(
            role="train",
            operating_system="Linux",
            cuda_available=True,
            cuda_version="13.0",
            torch_version="2.14.1+cu130",
            torchvision_version="0.29.1+cu130",
            bitsandbytes_version="0.50.2",
            dali_version="2.3.0",
            capabilities=[(8, 9)],
            driver_majors=[575],
        )

        self.assertTrue(any("sm89" in error for error in errors))
        self.assertTrue(any("release 575" in error for error in errors))

    def test_parses_one_driver_version_per_gpu(self) -> None:
        self.assertEqual([580, 590], parse_driver_versions("580.95.05\n590.12\n"))
        self.assertEqual(["580.95.05", "590.12"],
                         parse_driver_version_strings("580.95.05\n590.12\n"))

    def test_rejects_malformed_driver_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid NVIDIA driver"):
            parse_driver_versions("unknown\n")


if __name__ == "__main__":
    unittest.main()
