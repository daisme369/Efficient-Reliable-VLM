"""Checkpoint integrity and task-specific state serialization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import torch
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def save_checkpoint(directory, model, optimizer, scaler, epoch, config, manifest, best):
    path = directory / f"epoch-{epoch:04d}.pt"
    temporary = path.with_suffix(".tmp")
    payload = {"model": model.state_dict(), "optimizer": optimizer.state_dict(),
               "scaler": scaler.state_dict(), "epoch": epoch, "best_dice": best,
               "torch_rng": torch.get_rng_state(),
               "cuda_rng": torch.cuda.get_rng_state_all() if config.device == "cuda" else []}
    torch.save(payload, temporary)
    os.replace(temporary, path)
    write_json(path.with_suffix(".json"), {"sha256": file_hash(path),
               "config_sha256": config.fingerprint, "manifest_sha256": manifest.sha256,
               "provenance_sha256": manifest.provenance_sha256, "epoch": epoch})
    return path


def load_checkpoint(path: Path, config, manifest):
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if (meta.get("sha256") != file_hash(path) or meta.get("config_sha256") != config.fingerprint
            or meta.get("manifest_sha256") != manifest.sha256
            or meta.get("provenance_sha256") != manifest.provenance_sha256):
        raise ContractError("checkpoint inputs or content differ from recorded experiment")
    return torch.load(path, map_location="cpu", weights_only=True)


def artifact_inventory(directory: Path) -> dict:
    return {p.relative_to(directory).as_posix(): file_hash(p) for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name != "artifact_hashes.json"}


def verify_artifacts(directory: Path) -> dict:
    expected = json.loads((directory / "artifact_hashes.json").read_text(encoding="utf-8"))
    if artifact_inventory(directory) != expected:
        raise ContractError("adapter or training checkpoint content changed")
    return expected
