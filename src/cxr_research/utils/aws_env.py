"""Load project AWS settings into a child process without changing the shell."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence

from cxr_research.utils.errors import ContractError


AWS_KEYS = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE",
)
CREDENTIAL_KEYS = AWS_KEYS[:3]


def _read_settings(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ContractError("Cannot read AWS env file.") from None
    settings: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip().upper()
        if key not in AWS_KEYS:
            continue
        error = f"Invalid AWS env setting at line {number}."
        if not separator or key in settings:
            raise ContractError(error)
        value = value.strip()
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            tail = value[end + 1:].strip() if end >= 0 else ""
            if end < 0 or (tail and not tail.startswith("#")):
                raise ContractError(error)
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ContractError(error)
        settings[key] = value
    return settings


def _validate_credentials(settings: Mapping[str, str]) -> None:
    present = [bool(settings.get(key)) for key in CREDENTIAL_KEYS]
    if any(present) and not all(present[:2]):
        raise ContractError("AWS credentials require both access key and secret key from one source.")


def load_aws_environment(
    env_file: str | Path = ".env", *, base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment; existing AWS settings win over the file.

    Only the six allowlisted AWS keys are imported, case-insensitively. Existing
    credentials or an existing profile suppress the entire file credential group.
    No expansion, shell evaluation, profile writes or network access occurs.
    """
    result = dict(os.environ if base_env is None else base_env)
    settings = _read_settings(Path(env_file))
    # Normalize supported aliases also on Linux, where environment keys are case-sensitive.
    for key in list(result):
        canonical = key.upper()
        if canonical in AWS_KEYS and key != canonical:
            result.setdefault(canonical, result[key])
            del result[key]
    _validate_credentials(result)
    use_file_credentials = not (
        any(result.get(key) for key in CREDENTIAL_KEYS) or result.get("AWS_PROFILE")
    )
    if use_file_credentials:
        _validate_credentials(settings)
    for key, value in settings.items():
        if key in CREDENTIAL_KEYS and not use_file_credentials:
            continue
        result.setdefault(key, value)
    _validate_credentials(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--check", action="store_true", help="Check local settings without calling AWS.")
    parser.add_argument("aws_args", nargs=argparse.REMAINDER, help="AWS CLI arguments after --")
    args = parser.parse_args(argv)
    command = args.aws_args
    if command[:1] == ["--"]:
        command = command[1:]
    if (args.check and command) or (not args.check and not command):
        print("Choose --check or supply AWS CLI arguments after --.", file=sys.stderr)
        return 2
    try:
        environment = load_aws_environment(args.env_file)
        if args.check:
            print("AWS env file parsed; authentication has not been checked.")
            for key in AWS_KEYS:
                print(f"{key}: {'set' if environment.get(key) else 'not set'}")
            return 0
        executable = shutil.which("aws", path=environment.get("PATH", os.defpath))
        if executable is None:
            raise ContractError("AWS CLI is not installed or is not on PATH.")
        return subprocess.run([executable, *command], env=environment, shell=False, check=False).returncode
    except ContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("Could not start AWS CLI.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
