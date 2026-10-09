"""Validate the approved Linux Docker GPU profiles before a research workload."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Sequence


EXPECTED_TORCH = "2.14.1+cu130"
EXPECTED_TORCHVISION = "0.29.1+cu130"
EXPECTED_BITSANDBYTES = "0.50.2"
EXPECTED_CUDA = "13.0"
MINIMUM_DRIVER_MAJOR = 580
ROLE_CAPABILITIES = {
    "train": frozenset({(9, 0)}),
    "inference": frozenset({(8, 9), (12, 0)}),
}


def parse_driver_versions(output: str) -> list[int]:
    """Return driver major versions from one-value-per-GPU nvidia-smi output."""
    majors: list[int] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        major, separator, _ = line.partition(".")
        if not separator or not major.isdigit():
            raise ValueError(f"invalid NVIDIA driver version: {line!r}")
        majors.append(int(major))
    if not majors:
        raise ValueError("nvidia-smi returned no driver versions")
    return majors


def validate_profile(
    *,
    role: str,
    operating_system: str,
    cuda_available: bool,
    cuda_version: str | None,
    torch_version: str,
    torchvision_version: str,
    bitsandbytes_version: str,
    capabilities: Sequence[tuple[int, int]],
    driver_majors: Sequence[int],
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

    if not driver_majors:
        errors.append("no NVIDIA driver version was detected")
    for index, driver_major in enumerate(driver_majors):
        if driver_major < MINIMUM_DRIVER_MAJOR:
            errors.append(
                f"GPU {index} uses driver release {driver_major}; "
                f"CUDA 13.0 requires release {MINIMUM_DRIVER_MAJOR} or newer"
            )
    return errors


def query_driver_majors() -> list[int]:
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
    return parse_driver_versions(result.stdout)


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
        import torch
        import torchvision  # noqa: F401 - import is part of the runtime check.

        device_count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        capabilities = [torch.cuda.get_device_capability(index) for index in range(device_count)]
        names = [torch.cuda.get_device_name(index) for index in range(device_count)]
        driver_majors = query_driver_majors()
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
        capabilities=capabilities,
        driver_majors=driver_majors,
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
        f"driver-release-min={min(driver_majors)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
