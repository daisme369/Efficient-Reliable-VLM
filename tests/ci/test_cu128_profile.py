"""Keep the explicit BF16/cu128 exception isolated from the default cu130 gate."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import contextlib
import io
import os

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("gpu_profile_checker", ROOT / "scripts/ci/check_gpu_environment.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
from cxr_research.experiments.config import DataConfig, ExperimentConfig, TrainingConfig, MedGemmaConfig, IOConfig, config_from_dict, validate_config
from cxr_research.utils.errors import ContractError


class CUDA128ProfileTests(unittest.TestCase):
    def arguments(self):
        return dict(role="train", operating_system="Linux", cuda_available=True, cuda_version="12.8",
            torch_version="2.11.0+cu128", torchvision_version="0.26.0+cu128",
            bitsandbytes_version="not-installed", dali_version="not-installed", capabilities=[(12, 0)],
            driver_majors=[570], driver_versions=["570.195.03"],
            device_names=["NVIDIA RTX PRO 6000 Blackwell Workstation Edition"], profile="medgemma-bf16-cu128")

    def config(self):
        return ExperimentConfig("fixture", "medgemma", "B1", DataConfig("/private/data", "/private/m", "/private/p"),
            "/private/runs", device="cuda", train=TrainingConfig(precision="bf16"),
            medgemma=MedGemmaConfig(revision="a" * 40, quantization="none"), runtime_profile="medgemma-bf16-cu128")

    def test_approved_exact_pair_and_driver_pass_without_unused_optional_cuda_packages(self):
        self.assertEqual(checker.validate_profile(**self.arguments()), [])

    def test_default_gate_still_rejects_the_same_cuda128_environment(self):
        args = self.arguments(); args["profile"] = "cu130"
        errors = checker.validate_profile(**args)
        self.assertTrue(any("CUDA" in s for s in errors))
        self.assertTrue(any("driver release" in s for s in errors))
        self.assertTrue(any("dali" in s for s in errors))

    def test_wrong_driver_precision_hardware_or_package_cannot_use_the_exception(self):
        for changes in ({"driver_versions": []}, {"driver_versions": ["570.124.05"]},
                        {"driver_versions": ["invalid"]}, {"torch_version": "2.14.1+cu130"},
                        {"cuda_version": "13.0"}, {"device_names": ["NVIDIA RTX 5090"]},
                        {"role": "smoke"}):
            with self.subTest(changes=changes):
                self.assertTrue(checker.validate_profile(**{**self.arguments(), **changes}))

    def test_profile_is_explicit_fingerprinted_and_rejects_nf4_dali_or_other_tasks(self):
        cfg = self.config()
        self.assertEqual(config_from_dict(cfg.as_dict()), cfg)
        self.assertNotEqual(cfg.fingerprint, replace(cfg, runtime_profile="cu130").fingerprint)
        for other in (replace(cfg, medgemma=replace(cfg.medgemma, quantization="nf4")),
                      replace(cfg, train=replace(cfg.train, precision="fp16")), replace(cfg, task="unet", baseline="S1"),
                      replace(cfg, runtime_profile="automatic"), replace(cfg, runtime_profile={}),
                      replace(cfg, runtime_profile=None), replace(cfg, io=IOConfig(backend="webdataset"))):
            with self.assertRaises(ContractError):
                validate_config(other)

    def fake_torch(self):
        return SimpleNamespace(version=SimpleNamespace(cuda="12.8"), cuda=SimpleNamespace(
            is_available=lambda: True, device_count=lambda: 1,
            get_device_name=lambda index=0: "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
            get_device_capability=lambda index=0: (12, 0)))

    def test_runtime_passes_explicit_profile_and_keeps_immutable_image_gate(self):
        from cxr_research.experiments import environment
        output = SimpleNamespace(returncode=0, stdout="driver-versions=570.195.03, driver-release-min=570.")
        with patch.object(environment.Path, "exists", return_value=True), \
             patch.object(environment.subprocess, "run", return_value=output) as run, \
             patch.object(environment, "version", return_value="2.11.0+cu128"), \
             patch.dict(sys.modules, {"torch": self.fake_torch()}), \
             patch.dict(os.environ, {"CXR_CONTAINER_IMAGE_ID": "sha256:" + "a" * 64}):
            evidence = environment.gpu_preflight(self.config(), "train", ROOT)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--profile") + 1], "medgemma-bf16-cu128")
            self.assertEqual(evidence["runtime_profile"], "medgemma-bf16-cu128")
            os.environ["CXR_CONTAINER_IMAGE_ID"] = ""
            with self.assertRaisesRegex(ContractError, "immutable"):
                environment.gpu_preflight(self.config(), "train", ROOT)

    def test_cli_cu128_does_not_import_unused_cuda_optional_packages(self):
        import builtins
        original = builtins.__import__
        optional_imports = []
        def importing(name, *args, **kwargs):
            if name in {"bitsandbytes", "nvidia.dali"}:
                optional_imports.append(name)
                raise ImportError("synthetic optional dependency unavailable")
            return original(name, *args, **kwargs)
        versions = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128"}
        with patch.dict(sys.modules, {"torch": self.fake_torch(), "torchvision": SimpleNamespace()}), \
             patch.object(builtins, "__import__", side_effect=importing), \
             patch.object(checker, "query_driver_majors", return_value=([570], ["570.195.03"])), \
             patch.object(checker, "_installed_version", side_effect=lambda name: versions.get(name, "not-installed")), \
             patch.object(checker.platform, "system", return_value="Linux"), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(["--role", "train", "--profile", "medgemma-bf16-cu128"]), 0)
            self.assertEqual(optional_imports, [])
            self.assertEqual(checker.main(["--role", "train"]), 2)
            self.assertIn("bitsandbytes", optional_imports)


if __name__ == "__main__":
    unittest.main()
