"""Repository safety checks for local development and GitHub Actions.

The checks are deliberately dependency-free so they can run before the medical
ML environment is installed. They reduce common leakage risks; they do not
prove that a repository is free of protected health information or secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence


MAX_FILE_BYTES = 5 * 1024 * 1024

REQUIRED_PATHS = frozenset(
    {
        ".dockerignore",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "AGENTS-INSTRUCTIONS.md",
        "CI-CD.md",
        "DEPENDENCIES.md",
        "LICENSE",
        "README.md",
        "docker/Dockerfile.gpu",
        "docker/README.md",
        "requirements.txt",
        "requirements/gpu-cu130.txt",
        "requirements/gpu-runtime.txt",
        "scripts/ci/build_release_bundle.py",
        "scripts/ci/check_environment.py",
        "scripts/ci/check_gpu_environment.py",
        "scripts/ci/check_repository.py",
        "tests/ci/test_environment.py",
        "tests/ci/test_gpu_environment.py",
        "tests/ci/test_release_bundle.py",
        "tests/ci/test_repository_checks.py",
    }
)

BLOCKED_TOP_LEVEL = frozenset(
    {
        "artifacts",
        "checkpoints",
        "data",
        "datasets",
        "outputs",
        "runs",
        "wandb",
        "weights",
    }
)

BLOCKED_COMPONENTS = frozenset(
    {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "venv",
    }
)

BLOCKED_FILENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)

BLOCKED_SUFFIXES = (
    ".ckpt",
    ".dcm",
    ".h5",
    ".hdf5",
    ".joblib",
    ".nii",
    ".nii.gz",
    ".npy",
    ".npz",
    ".onnx",
    ".parquet",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
)

SECRET_PATTERNS = (
    ("private-key", re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("github-token", re.compile(rb"(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws-access-key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("hugging-face-token", re.compile(rb"hf_[A-Za-z0-9]{20,}")),
    (
        "signed-url-credential",
        re.compile(rb"(?:X-Amz-Signature|X-Goog-Signature|[?&]sig)=", re.IGNORECASE),
    ),
)

PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^;\s]+(?:\s*;\s*.+)?$"
)
WRITE_PERMISSION = re.compile(
    r"\b(?:actions|attestations|checks|contents|deployments|discussions|id-token|issues|models|packages|pages|pull-requests|security-events|statuses)\s*:\s*write\b",
    re.IGNORECASE,
)
PINNED_CONTAINER_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
DOCKER_FROM_LINE = re.compile(r"^\s*FROM\s+([^\s]+)", re.MULTILINE | re.IGNORECASE)
WHOLE_CONTEXT_COPY = re.compile(
    r"^\s*(?:ADD|COPY)\s+(?:--[^\s]+\s+)*\.\s+[^\s]+",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    rule: str
    message: str


def _run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {error}")
    return result.stdout


def _decode_paths(payload: bytes) -> list[str]:
    return [item.decode("utf-8", errors="surrogateescape") for item in payload.split(b"\0") if item]


def repository_paths(root: Path, *, include_untracked: bool = True) -> list[str]:
    args = ["ls-files", "--cached"]
    if include_untracked:
        args.extend(["--others", "--exclude-standard"])
    args.append("-z")
    return sorted(set(_decode_paths(_run_git(root, *args))))


def staged_paths(root: Path) -> list[str]:
    return sorted(
        set(
            _decode_paths(
                _run_git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
            )
        )
    )


def index_modes(root: Path) -> dict[str, str]:
    modes: dict[str, str] = {}
    for record in _run_git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode = metadata.split(maxsplit=1)[0].decode("ascii")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        modes[path] = mode
    return modes


def _normalise_path(raw_path: str) -> PurePosixPath:
    path = PurePosixPath(raw_path.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("path must be repository-relative and cannot contain '..'")
    return path


def _read_worktree_file(root: Path, raw_path: str) -> bytes:
    return (root / Path(*_normalise_path(raw_path).parts)).read_bytes()


def _read_index_file(root: Path, raw_path: str) -> bytes:
    return _run_git(root, "show", f":{raw_path}")


def _path_violations(path: PurePosixPath, *, mode: str | None) -> list[Violation]:
    display = path.as_posix()
    lowered_parts = tuple(part.lower() for part in path.parts)
    filename = lowered_parts[-1]
    violations: list[Violation] = []

    if lowered_parts[0] in BLOCKED_TOP_LEVEL or lowered_parts[0].startswith("mimic"):
        violations.append(
            Violation(display, "restricted-path", "tracked research data or generated artifacts are not allowed")
        )
    if lowered_parts[:2] == ("tests", "fixtures") and lowered_parts[:3] != (
        "tests",
        "fixtures",
        "synthetic",
    ):
        violations.append(
            Violation(
                display,
                "non-synthetic-fixture",
                "test fixtures must be stored under tests/fixtures/synthetic",
            )
        )
    if any(part in BLOCKED_COMPONENTS for part in lowered_parts):
        violations.append(Violation(display, "generated-path", "environment or cache content is not allowed"))
    if filename in BLOCKED_FILENAMES or filename.endswith((".key", ".pem")):
        violations.append(Violation(display, "credential-file", "credential-bearing filenames are not allowed"))
    if filename.endswith(BLOCKED_SUFFIXES):
        violations.append(
            Violation(display, "restricted-format", "medical images, model weights, and binary datasets are not allowed")
        )
    if mode == "120000":
        violations.append(Violation(display, "symlink", "tracked symbolic links are not allowed in public artifacts"))
    return violations


def _content_violations(path: PurePosixPath, content: bytes) -> list[Violation]:
    display = path.as_posix()
    violations: list[Violation] = []

    if len(content) > MAX_FILE_BYTES:
        violations.append(
            Violation(display, "file-size", f"file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB tracked-file limit")
        )
        return violations

    if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
        violations.append(Violation(display, "git-lfs", "Git LFS pointers require an explicit privacy and license review"))

    for rule, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            violations.append(Violation(display, rule, "credential-like content detected; matched content is suppressed"))

    if path.suffix.lower() == ".ipynb":
        try:
            notebook = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            violations.append(Violation(display, "notebook-json", "notebook is not valid UTF-8 JSON"))
        else:
            for cell in notebook.get("cells", []):
                if cell.get("outputs") or cell.get("execution_count") is not None:
                    violations.append(
                        Violation(display, "notebook-output", "clear execution counts and outputs before tracking a notebook")
                    )
                    break

    if path.name.lower().startswith("requirements") and path.suffix.lower() == ".txt":
        violations.extend(_requirements_violations(path, content))

    if path.name.lower() == "dockerfile" or path.name.lower().startswith("dockerfile."):
        violations.extend(_dockerfile_violations(path, content))

    if path.as_posix() == ".dockerignore":
        violations.extend(_dockerignore_violations(path, content))

    if path.parts[:2] == (".github", "workflows") and path.suffix.lower() in {".yml", ".yaml"}:
        violations.extend(_workflow_violations(path, content))

    return violations


def _dockerfile_violations(path: PurePosixPath, content: bytes) -> list[Violation]:
    display = path.as_posix()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [Violation(display, "dockerfile-encoding", "Dockerfile must be UTF-8 text")]

    violations: list[Violation] = []
    images = DOCKER_FROM_LINE.findall(text)
    if not images:
        violations.append(Violation(display, "dockerfile-base", "Dockerfile has no FROM instruction"))
    for image in images:
        if not PINNED_CONTAINER_IMAGE.fullmatch(image):
            violations.append(
                Violation(display, "dockerfile-base", f"base image must use a sha256 digest: {image.split('@', 1)[0]}")
            )
    if WHOLE_CONTEXT_COPY.search(text):
        violations.append(
            Violation(display, "dockerfile-context", "COPY or ADD of the whole build context is prohibited")
        )
    return violations


def _dockerignore_violations(path: PurePosixPath, content: bytes) -> list[Violation]:
    display = path.as_posix()
    try:
        lines = [
            line.strip()
            for line in content.decode("utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except UnicodeDecodeError:
        return [Violation(display, "dockerignore-encoding", ".dockerignore must be UTF-8 text")]

    if not lines or lines[0] != "**":
        return [
            Violation(
                display,
                "dockerignore-default",
                ".dockerignore must deny the full context before explicit allowlist entries",
            )
        ]
    return []


def _requirements_violations(path: PurePosixPath, content: bytes) -> list[Violation]:
    display = path.as_posix()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [Violation(display, "requirements-encoding", "requirements files must be UTF-8 text")]

    violations: list[Violation] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "http://", "https://", "git+")):
            violations.append(
                Violation(
                    display,
                    "requirements-source",
                    f"line {line_number} uses an external option or URL instead of a pinned package",
                )
            )
            continue
        if not PINNED_REQUIREMENT.fullmatch(line):
            violations.append(
                Violation(
                    display,
                    "requirements-pin",
                    f"line {line_number} must pin one direct dependency with ==",
                )
            )
    return violations


def _workflow_violations(path: PurePosixPath, content: bytes) -> list[Violation]:
    display = path.as_posix()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [Violation(display, "workflow-encoding", "workflow must be UTF-8 text")]

    violations: list[Violation] = []
    lowered = text.lower()
    if re.search(r"\bpull_request_target\b", text):
        violations.append(Violation(display, "privileged-trigger", "pull_request_target is prohibited"))
    if re.search(r"(?m)^\s*runs-on\s*:\s*.*self-hosted", text, re.IGNORECASE):
        violations.append(Violation(display, "self-hosted-runner", "public CI cannot execute on research machines"))
    if "secrets." in lowered:
        violations.append(Violation(display, "workflow-secret", "public CI/CD must not reference repository secrets"))
    if not re.search(r"(?m)^permissions:\s*\r?\n  contents:\s*read\s*$", text):
        violations.append(
            Violation(display, "workflow-permissions", "top-level permissions must explicitly set contents to read")
        )
    write_permissions = WRITE_PERMISSION.findall(text)
    if re.search(r"\bpermissions\s*:\s*write-all\b", text, re.IGNORECASE):
        violations.append(Violation(display, "workflow-write", "permissions: write-all is prohibited"))
    if write_permissions:
        publish_block_match = re.search(
            r"(?ms)^  publish-draft:\s*$.*?(?=^  [A-Za-z0-9_-]+:\s*$|\Z)",
            text,
        )
        publish_block = publish_block_match.group(0) if publish_block_match else ""
        allowed_release_write = (
            path.name == "release.yml"
            and len(write_permissions) == 1
            and re.search(r"\bcontents\s*:\s*write\b", publish_block, re.IGNORECASE)
        )
        if not allowed_release_write:
            violations.append(
                Violation(
                    display,
                    "workflow-write",
                    "write permission is allowed only for contents in release.yml publish-draft",
                )
            )
    if re.search(r"(?:curl|wget)[^\n|]*\|\s*(?:ba)?sh", text, re.IGNORECASE):
        violations.append(Violation(display, "piped-installer", "piping network content into a shell is prohibited"))

    for action in USES_LINE.findall(text):
        if action.startswith("./"):
            continue
        if not PINNED_ACTION.fullmatch(action):
            violations.append(
                Violation(display, "unpinned-action", f"action reference must use a full commit SHA: {action.split('@', 1)[0]}")
            )
    return violations


def inspect_files(
    root: Path,
    paths: Sequence[str],
    *,
    reader: Callable[[Path, str], bytes] | None = None,
    modes: dict[str, str] | None = None,
    require_control_files: bool = False,
) -> list[Violation]:
    read = reader or _read_worktree_file
    known_modes = modes or {}
    violations: list[Violation] = []
    normalised_paths: set[str] = set()

    for raw_path in paths:
        try:
            path = _normalise_path(raw_path)
        except ValueError as error:
            violations.append(Violation(raw_path, "unsafe-path", str(error)))
            continue
        display = path.as_posix()
        normalised_paths.add(display)
        violations.extend(_path_violations(path, mode=known_modes.get(raw_path)))
        try:
            content = read(root, raw_path)
        except (OSError, RuntimeError) as error:
            violations.append(Violation(display, "unreadable", str(error)))
            continue
        violations.extend(_content_violations(path, content))

    if require_control_files:
        for missing in sorted(REQUIRED_PATHS - normalised_paths):
            violations.append(Violation(missing, "required-control", "required CI/CD control file is missing"))

    return sorted(set(violations))


def run_checks(root: Path, scope: str) -> list[Violation]:
    modes = index_modes(root)
    if scope == "staged":
        paths = staged_paths(root)
        return inspect_files(root, paths, reader=_read_index_file, modes=modes)
    paths = repository_paths(root, include_untracked=True)
    return inspect_files(root, paths, modes=modes, require_control_files=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("repository", "staged"),
        default="repository",
        help="scan the repository worktree or only content currently staged in Git",
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    try:
        violations = run_checks(root, args.scope)
    except RuntimeError as error:
        print(f"repository check could not run: {error}", file=sys.stderr)
        return 2

    if violations:
        print(f"Repository check failed with {len(violations)} violation(s):", file=sys.stderr)
        for item in violations:
            print(f"- {item.path}: [{item.rule}] {item.message}", file=sys.stderr)
        return 1

    print(f"Repository check passed ({args.scope} scope).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
