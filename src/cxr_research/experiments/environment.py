"""Software and hardware preflight for recorded experiments."""

from __future__ import annotations

import platform
import os
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from cxr_research.experiments.config import ExperimentConfig
from cxr_research.utils.errors import ContractError


def environment() -> dict:
    packages = {}
    for name in ("torch", "transformers", "peft", "numpy", "pillow", "bitsandbytes", "nvidia-dali-cuda130"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "platform": platform.system(), "packages": packages}


def gpu_preflight(config: ExperimentConfig, operation: str, repository: Path) -> dict | None:
    if config.device == "cpu":
        if not config.data.synthetic:
            raise ContractError("CPU execution is reserved for synthetic development")
        return None
    if config.data.synthetic:
        raise ContractError("synthetic smoke command is CPU-only")
    if not Path("/.dockerenv").exists():
        raise ContractError("GPU execution requires the locked Linux Docker environment")
    if operation not in {"train", "evaluate"}:
        raise ContractError("GPU preflight requires a train or evaluate operation")
    role = "train" if operation == "train" else "inference"
    checker = repository / "scripts/ci/check_gpu_environment.py"
    result = subprocess.run([sys.executable, str(checker), "--role", role],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ContractError("GPU environment preflight failed")
    driver = re.search(
        r"driver-versions=([0-9.]+(?:,[0-9.]+)*), driver-release-min=([0-9]+)\.",
        result.stdout,
    )
    image_id = os.environ.get("CXR_CONTAINER_IMAGE_ID", "")
    if driver is None or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ContractError("GPU provenance requires preflight output and an immutable container image ID")
    import torch
    if torch.cuda.device_count() != 1:
        raise ContractError("this runner requires exactly one visible GPU")
    return {
        "preflight": "passed",
        "role": role,
        "driver_versions": driver.group(1).split(","),
        "driver_release_min": int(driver.group(2)),
        "container_image_id": image_id,
        "cuda": torch.version.cuda,
        "torch": version("torch"),
        "devices": [{"index": 0, "name": torch.cuda.get_device_name(0),
                     "capability": list(torch.cuda.get_device_capability(0))}],
    }
