"""Verify that the active Python environment matches requirements.txt."""

from __future__ import annotations

import argparse
import importlib
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Sequence


IMPORT_NAMES = {
    "accelerate": "accelerate",
    "bert-score": "bert_score",
    "datasets": "datasets",
    "evaluate": "evaluate",
    "huggingface-hub": "huggingface_hub",
    "matplotlib": "matplotlib",
    "numpy": "numpy",
    "pandas": "pandas",
    "peft": "peft",
    "pillow": "PIL",
    "pydicom": "pydicom",
    "pyyaml": "yaml",
    "rouge-score": "rouge_score",
    "sacrebleu": "sacrebleu",
    "safetensors": "safetensors",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "sentencepiece": "sentencepiece",
    "torch": "torch",
    "torchvision": "torchvision",
    "tqdm": "tqdm",
    "transformers": "transformers",
}

LOCAL_BUILD_ALLOWED = frozenset({"torch", "torchvision"})


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, pinned_version = line.partition("==")
        if not separator or not name or not pinned_version:
            raise ValueError(f"invalid exact pin in {path}: {line}")
        pins[name.lower()] = pinned_version
    return pins


def version_matches(package: str, installed: str, expected: str) -> bool:
    """Allow the locked CUDA local suffix for PyTorch packages only."""
    return installed == expected or (
        package in LOCAL_BUILD_ALLOWED and installed.startswith(f"{expected}+")
    )


def verify_environment(requirements: Path) -> list[str]:
    errors: list[str] = []
    pins = read_pins(requirements)
    for package, expected in sorted(pins.items()):
        try:
            installed = version(package)
        except PackageNotFoundError:
            errors.append(f"{package}: not installed")
            continue
        if not version_matches(package, installed, expected):
            errors.append(f"{package}: installed {installed}, expected {expected}")

        import_name = IMPORT_NAMES.get(package)
        if import_name:
            try:
                importlib.import_module(import_name)
            except Exception as error:  # noqa: BLE001 - report runtime import failures uniformly.
                errors.append(f"{package}: import failed with {type(error).__name__}: {error}")

    try:
        from peft import LoraConfig  # noqa: F401
        from transformers import AutoModelForImageTextToText, AutoProcessor  # noqa: F401
    except Exception as error:  # noqa: BLE001 - smoke-check the planned public APIs.
        errors.append(f"VLM/PEFT API smoke check failed with {type(error).__name__}: {error}")
    return errors


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "requirements.txt",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    errors = verify_environment(args.requirements.resolve())
    if errors:
        print(f"Environment check failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    import torch

    print(
        "Environment check passed: "
        f"Python {sys.version.split()[0]}, torch {torch.__version__}, "
        f"CUDA available={torch.cuda.is_available()}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
