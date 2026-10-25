"""Generate a Vast SSH transport layer without changing the locked research image."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {"openssh-server": "1:9.2p1-2+deb12u10", "rsync": "3.2.7-1+deb12u6",
            "curl": "7.88.1-10+deb12u15"}


def prepare(output: Path):
    if not output.is_absolute() or output.resolve().is_relative_to(ROOT.resolve()) or output.exists():
        raise ValueError("choose a new absolute recipe path outside the source checkout")
    source = (ROOT / "docker/Dockerfile.gpu").read_bytes()
    extra = ("\n# Vast SSH startup needs root; research commands run as researcher.\n"
             "USER root\n"
             "RUN apt-get update -o APT::Update::Error-Mode=any \\\n"
             "    && apt-get install --yes --no-install-recommends "
             + " ".join(f"{name}={pin}" for name, pin in PACKAGES.items())
             + " \\\n    && rm -f /etc/ssh/ssh_host_* \\\n"
             "    && rm -rf /var/lib/apt/lists/*\n"
             "EXPOSE 22\n"
             'CMD ["sleep", "infinity"]\n').encode()
    payload = source + extra
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    record = {"canonical_recipe_sha256": hashlib.sha256(source).hexdigest(),
              "generated_recipe_sha256": hashlib.sha256(payload).hexdigest(), "packages": PACKAGES,
              "research_locks_changed": False, "default_user": "root for Vast SSH setup only",
              "research_user": "researcher", "contains_keys_or_host_identity": False,
              "scope": "local recipe generation; no registry publish or paid instance creation"}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output)))
    except Exception as error:
        parser.exit(2, f"Recipe preparation failed ({type(error).__name__}).\n")
