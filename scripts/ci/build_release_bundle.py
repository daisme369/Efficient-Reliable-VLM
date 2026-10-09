"""Build a deterministic, allowlisted research-source release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence

from check_repository import index_modes, inspect_files, repository_paths


PROJECT_NAME = "Efficient-Reliable-VLM"
VERSION_PATTERN = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")

RELEASE_EXACT_PATHS = frozenset(
    {
        ".dockerignore",
        "AGENTS-INSTRUCTIONS.md",
        "CI-CD.md",
        "CITATION.cff",
        "DEPENDENCIES.md",
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "requirements.txt",
    }
)
RELEASE_PREFIXES = ("configs/", "docker/", "requirements/", "scripts/", "src/", "tests/")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_release_paths(paths: Sequence[str]) -> list[str]:
    selected: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path.replace("\\", "/")).as_posix()
        if path.startswith("tests/fixtures/") and not path.startswith("tests/fixtures/synthetic/"):
            continue
        if path in RELEASE_EXACT_PATHS or path.startswith(RELEASE_PREFIXES):
            selected.append(path)
    return sorted(set(selected))


def git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def worktree_is_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git status failed: {error}")
    return not result.stdout.strip()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    os.replace(temporary, path)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def build_bundle(
    root: Path,
    output_dir: Path,
    version: str,
    *,
    source_paths: Sequence[str] | None = None,
    commit: str | None = None,
    include_untracked: bool = False,
) -> tuple[Path, Path, Path]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("version must use exact SemVer tag syntax: vMAJOR.MINOR.PATCH")

    if source_paths is None and not include_untracked and not worktree_is_clean(root):
        raise ValueError("publishable release builds require a clean worktree and index")

    candidates = list(source_paths) if source_paths is not None else repository_paths(
        root, include_untracked=include_untracked
    )
    violations = inspect_files(root, candidates, modes=index_modes(root))
    if violations:
        summary = "; ".join(f"{item.path} [{item.rule}]" for item in violations)
        raise ValueError(f"repository safety checks failed: {summary}")

    selected = select_release_paths(candidates)
    if not selected:
        raise ValueError("release allowlist selected no files")

    entries: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    for relative in selected:
        content = (root / Path(*PurePosixPath(relative).parts)).read_bytes()
        payloads[relative] = content
        entries.append({"path": relative, "bytes": len(content), "sha256": sha256_bytes(content)})

    if source_paths is not None:
        source_state = "explicit-input"
        source_commit = commit or git_commit(root)
        base_commit = None
    elif include_untracked:
        source_state = "development-worktree"
        source_commit = None
        base_commit = git_commit(root)
    else:
        source_state = "clean-commit"
        source_commit = commit or git_commit(root)
        base_commit = None

    manifest = {
        "schema_version": 1,
        "project": PROJECT_NAME,
        "version": version,
        "source_state": source_state,
        "source_commit": source_commit,
        "contents": entries,
        "excluded_categories": [
            "restricted datasets and derivatives",
            "model weights and adapters",
            "raw predictions and reports",
            "experiment outputs and caches",
            "credentials and local environments",
        ],
    }
    if base_commit is not None:
        manifest["base_commit"] = base_commit
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{PROJECT_NAME}-{version}"
    archive_path = output_dir / f"{stem}.zip"
    manifest_path = output_dir / "release-manifest.json"
    checksums_path = output_dir / "SHA256SUMS"

    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".zip", delete=False) as handle:
        temporary_archive = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            prefix = f"{stem}/"
            for relative in selected:
                archive.writestr(_zip_info(prefix + relative), payloads[relative])
            archive.writestr(_zip_info(prefix + "release-manifest.json"), manifest_bytes)
        os.replace(temporary_archive, archive_path)
    finally:
        temporary_archive.unlink(missing_ok=True)

    _atomic_write(manifest_path, manifest_bytes)
    checksums = (
        f"{sha256_file(archive_path)}  {archive_path.name}\n"
        f"{sha256_file(manifest_path)}  {manifest_path.name}\n"
    ).encode("ascii")
    _atomic_write(checksums_path, checksums)
    return archive_path, manifest_path, checksums_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("release-dist"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="development-only: include eligible untracked files in the safety scan and allowlist",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    outputs = build_bundle(
        args.root.resolve(),
        args.output_dir.resolve(),
        args.version,
        include_untracked=args.include_untracked,
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
