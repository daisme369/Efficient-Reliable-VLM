"""Validate the approved Linux Docker GPU profiles before a research workload."""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Sequence


EXPECTED_TORCH = "2.14.1+cu130"
EXPECTED_TORCHVISION = "0.29.1+cu130"
EXPECTED_BITSANDBYTES = "0.50.2"
EXPECTED_DALI = "2.3.0"
EXPECTED_CUDA = "13.0"
MINIMUM_DRIVER_MAJOR = 580
ROLE_CAPABILITIES = {
    "smoke": frozenset({(8, 6)}),
    "train": frozenset({(9, 0), (12, 0)}),
    "inference": frozenset({(8, 9), (12, 0)}),
}


def parse_driver_version_strings(output: str) -> list[str]:
    """Return exact versions from one-value-per-GPU nvidia-smi output."""
    versions: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", line):
            raise ValueError(f"invalid NVIDIA driver version: {line!r}")
        versions.append(line)
    if not versions:
        raise ValueError("nvidia-smi returned no driver versions")
    return versions


def parse_driver_versions(output: str) -> list[int]:
    """Return driver major versions from one-value-per-GPU nvidia-smi output."""
    return [int(item.partition(".")[0]) for item in parse_driver_version_strings(output)]


def validate_profile(
    *,
    role: str,
    operating_system: str,
    cuda_available: bool,
    cuda_version: str | None,
    torch_version: str,
    torchvision_version: str,
    bitsandbytes_version: str,
    dali_version: str,
    capabilities: Sequence[tuple[int, int]],
    driver_majors: Sequence[int],
    device_names: Sequence[str] = (),
) -> list[str]:
    errors: list[str] = []
    if role not in ROLE_CAPABILITIES:
        errors.append(f"unknown GPU role: {role}")
        return errors
    if operating_system != "Linux":
        errors.append(f"GPU workloads require a Linux container, found {operating_system}")
    if not cuda_available:
        errors.append("PyTorch cannot access CUDA; check --gpus and NVIDIA Container Toolkit")
    if cuda_version != EXPECTED_CUDA:
        errors.append(f"PyTorch CUDA is {cuda_version!r}, expected {EXPECTED_CUDA!r}")
    for package, installed, expected in (
        ("torch", torch_version, EXPECTED_TORCH),
        ("torchvision", torchvision_version, EXPECTED_TORCHVISION),
        ("bitsandbytes", bitsandbytes_version, EXPECTED_BITSANDBYTES),
        ("nvidia-dali-cuda130", dali_version, EXPECTED_DALI),
    ):
        if installed != expected:
            errors.append(f"{package} is {installed!r}, expected {expected!r}")

    allowed = ROLE_CAPABILITIES[role]
    if not capabilities:
        errors.append("no visible CUDA devices")
    for index, capability in enumerate(capabilities):
        if capability not in allowed:
            expected = ", ".join(f"sm{major}{minor}" for major, minor in sorted(allowed))
            errors.append(
                f"GPU {index} has sm{capability[0]}{capability[1]}; "
                f"role {role!r} permits only {expected}"
            )
        if role == "train":
            name = device_names[index] if index < len(device_names) else ""
            name = " ".join(name.replace("_", " ").split())
            pro_blackwell = re.fullmatch(
                r"(?:NVIDIA )?RTX PRO 6000 Blackwell(?: (?:Server|(?:Max-Q )?Workstation) Edition)?",
                name, re.IGNORECASE,
            ) is not None
            if capability == (12, 0) and not pro_blackwell:
                errors.append(f"GPU {index}: Blackwell training requires RTX PRO 6000 Blackwell model identity")
            elif pro_blackwell and capability != (12, 0):
                errors.append(f"GPU {index}: RTX PRO 6000 Blackwell training requires sm120")

    if not driver_majors:
        errors.append("no NVIDIA driver version was detected")
    for index, driver_major in enumerate(driver_majors):
        if driver_major < MINIMUM_DRIVER_MAJOR:
            errors.append(
                f"GPU {index} uses driver release {driver_major}; "
                f"CUDA 13.0 requires release {MINIMUM_DRIVER_MAJOR} or newer"
            )
    return errors


def query_driver_majors() -> tuple[list[int], list[str]]:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"nvidia-smi failed: {detail}")
    versions = parse_driver_version_strings(result.stdout)
    return [int(item.partition(".")[0]) for item in versions], versions


def _installed_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_CAPABILITIES), required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        import bitsandbytes  # noqa: F401 - import is part of the runtime check.
        import nvidia.dali  # noqa: F401 - import is part of the runtime check.
        import torch
        import torchvision  # noqa: F401 - import is part of the runtime check.

        device_count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        capabilities = [torch.cuda.get_device_capability(index) for index in range(device_count)]
        names = [torch.cuda.get_device_name(index) for index in range(device_count)]
        driver_majors, driver_versions = query_driver_majors()
    except Exception as error:  # noqa: BLE001 - report a single preflight failure boundary.
        print(f"GPU preflight could not run: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    errors = validate_profile(
        role=args.role,
        operating_system=platform.system(),
        cuda_available=torch.cuda.is_available(),
        cuda_version=torch.version.cuda,
        torch_version=_installed_version("torch"),
        torchvision_version=_installed_version("torchvision"),
        bitsandbytes_version=_installed_version("bitsandbytes"),
        dali_version=_installed_version("nvidia-dali-cuda130"),
        capabilities=capabilities,
        driver_majors=driver_majors,
        device_names=names,
    )
    if errors:
        print(f"GPU preflight failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    devices = ", ".join(
        f"{name} (sm{capability[0]}{capability[1]})"
        for name, capability in zip(names, capabilities, strict=True)
    )
    print(
        f"GPU preflight passed: role={args.role}, devices={devices}, "
        f"CUDA={torch.version.cuda}, torch={_installed_version('torch')}, "
        f"driver-versions={','.join(driver_versions)}, "
        f"driver-release-min={min(driver_majors)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
