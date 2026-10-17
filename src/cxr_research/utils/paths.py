"""Resolve the checkout used for provenance and its existing GPU preflight."""

from pathlib import Path

from cxr_research.utils.errors import ContractError


def repository_root(explicit: Path | None = None) -> Path:
    candidates = [explicit] if explicit else [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "AGENTS-INSTRUCTIONS.md").is_file() and (root / "scripts/ci/check_gpu_environment.py").is_file():
            return root
    raise ContractError("run from the source checkout or supply --repository")
