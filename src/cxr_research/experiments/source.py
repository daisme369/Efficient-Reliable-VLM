"""Record either the current checkout or a verified immutable source export."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


def source_provenance(config, repository):
    metadata_path = os.environ.get("CXR_SOURCE_SNAPSHOT")
    if not metadata_path:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
        return {"git_commit": git.stdout.strip() if git.returncode == 0 else "unknown",
            "git_dirty": bool(status.stdout) if status.returncode == 0 else None, "source_kind": "git_checkout"}
    metadata = json.loads(Path(metadata_path).read_text())
    archive = Path(os.environ.get("CXR_SOURCE_ARCHIVE", ""))
    protocol = json.loads(Path(config.protocol).read_text()) if config.protocol else {}
    expected = metadata.get("snapshot_sha256")
    if (protocol.get("status") != "approved" or protocol.get("source_snapshot_sha256") != expected
            or not archive.is_file() or file_hash(archive) != expected
            or not re.fullmatch(r"[0-9a-f]{40}", metadata.get("git_commit", ""))
            or type(metadata.get("git_dirty")) is not bool):
        raise ContractError("source snapshot identity differs from the approved protocol")
    files = metadata.get("files", {})
    required = {"AGENTS-INSTRUCTIONS.md", "src/cxr_research/cli.py", "scripts/ci/check_gpu_environment.py"}
    if not isinstance(files, dict) or not required.issubset(files):
        raise ContractError("source snapshot lacks required entry points")
    root = Path(repository).resolve()
    with zipfile.ZipFile(archive) as bundle:
        if set(bundle.namelist()) != set(files):
            raise ContractError("source archive inventory differs from its manifest")
        for relative, checksum in files.items():
            target = (root / relative).resolve()
            if (Path(relative).is_absolute() or ".." in Path(relative).parts or not target.is_relative_to(root)
                    or not target.is_file() or file_hash(target) != checksum
                    or hashlib.sha256(bundle.read(relative)).hexdigest() != checksum):
                raise ContractError("frozen source bytes differ from the recorded export")
    return {"git_commit": metadata["git_commit"], "git_dirty": metadata["git_dirty"],
        "source_kind": "verified_source_export", "source_snapshot_sha256": expected}
