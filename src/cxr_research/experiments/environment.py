"""Software and hardware preflight for recorded experiments."""

from __future__ import annotations

import platform
import json
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
    result = subprocess.run([sys.executable, str(checker), "--profile", config.runtime_profile, "--role", role],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ContractError("GPU environment preflight failed")
    driver = re.search(
        r"driver-versions=([0-9.]+(?:,[0-9.]+)*), driver-release-min=([0-9]+)\.",
        result.stdout,
    )
    image_id = os.environ.get("CXR_CONTAINER_IMAGE_ID", "")
    exception = None
    if not image_id and config.protocol:
        protocol = json.loads(Path(config.protocol).read_text(encoding="utf-8"))
        candidate = protocol.get("image_provenance_exception")
        if (isinstance(candidate, dict) and candidate.get("approved_by") == "user"
                and candidate.get("instance_id") == 58351663
                and os.environ.get("CONTAINER_ID") == "58351663"
                and candidate.get("run_id") == config.run_id
                and candidate.get("decision_record") == "USER-IMAGE-EXCEPTION-2026-10-30-VAST58351663"
                and config.runtime_profile == "medgemma-bf16-cu128"
                and operation == "train" and config.task == "medgemma" and config.baseline == "B1"
                and protocol.get("status") == "approved"
                and protocol.get("config_sha256") == config.fingerprint):
            exception = candidate
    if driver is None or (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) and exception is None):
        raise ContractError("GPU provenance requires preflight output and an immutable container image ID")
    import torch
    if torch.cuda.device_count() != 1:
        raise ContractError("this runner requires exactly one visible GPU")
    return {
        "preflight": "passed",
        "role": role,
        "runtime_profile": config.runtime_profile,
        "driver_versions": driver.group(1).split(","),
        "driver_release_min": int(driver.group(2)),
        "container_image_id": image_id or None,
        "image_provenance": "user_approved_exception" if exception else "image_id_supplied",
        "image_provenance_exception": exception,
        "cuda": torch.version.cuda,
        "torch": version("torch"),
        "devices": [{"index": 0, "name": torch.cuda.get_device_name(0),
                     "capability": list(torch.cuda.get_device_capability(0))}],
    }
