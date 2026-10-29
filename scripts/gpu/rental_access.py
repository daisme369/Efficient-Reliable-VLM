"""Read rental credentials locally; inspect Vast offers or cache pinned model files."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import fnmatch
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
KEYS = frozenset({"HF_TOKEN", "VAST_API_KEY"})
KEY_ALIASES = {"hf_token": "HF_TOKEN", "hugging_face_hub_token": "HF_TOKEN",
               "hf_full_access_token": "HF_TOKEN",
               "huggingface_hub_token": "HF_TOKEN", "huggingface_token": "HF_TOKEN",
               "vast_api_key": "VAST_API_KEY", "vastai_api_key": "VAST_API_KEY"}
MODEL = "google/medgemma-1.5-4b-it"
PATTERNS = ("*.json", "*.safetensors", "tokenizer.model", "*.txt", "*.jinja", "*.tiktoken",
            "README.md", ".gitattributes")


def read_credentials(path: Path | None) -> dict[str, str]:
    """Parse only two literal keys, without shell expansion or global env changes."""
    values = {}
    if path is not None:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*?)\s*$", line)
            if not match or match[1].casefold() not in KEY_ALIASES:
                continue
            source_key, raw = match.groups()
            key = KEY_ALIASES[source_key.casefold()]
            if key in values:
                raise ValueError("duplicate rental credential field")
            if raw.startswith(("'", '"')):
                quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", raw)
                if not quoted:
                    raise ValueError("malformed rental credential field")
                value = quoted[2]
            else:
                value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
            if any(c in value for c in "\r\n\x00"):
                raise ValueError("invalid rental credential field")
            values[key] = value
    for key in KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def vast_status(key: str, storage: int) -> dict:
    if version("vastai") != "1.8.3":
        raise ValueError("install requirements/vastai-cli.txt in the provider CLI environment")
    if not key or storage < 64:
        raise ValueError("VAST_API_KEY and at least 64 GiB storage are required")
    from vastai import VastAI
    client = VastAI(api_key=key, raw=True, quiet=True)
    # No create/start/stop/destroy/payment endpoint is used by this helper.
    client.show_user()
    common = ("verified=true rentable=true datacenter=true num_gpus=1 "
              "cpu_arch=amd64 driver_version>=580.0.0 cuda_vers>=13 reliability>0.99 "
              f"disk_space>={storage}")
    pro_names = [prefix + "RTX_PRO_6000_Blackwell" + suffix
                 for prefix in ("", "NVIDIA_")
                 for suffix in ("", "_Server_Edition", "_Workstation_Edition", "_Max-Q_Workstation_Edition")]
    queries = [common + " compute_cap=900",
               common + " compute_cap=1200 gpu_name in [" + ",".join(pro_names) + "]"]
    # Filter Blackwell model identity at the provider before its result limit;
    # cheaper inference-only cards must not crowd out the approved training GPU.
    offers = [row for query in queries for row in
              client.search_offers(query=query, storage=storage, limit=100, order="dph_total")]
    fields = ("id", "gpu_name", "gpu_ram", "num_gpus", "cpu_arch", "driver_version",
              "datacenter", "reliability", "dph_total", "storage_cost", "inet_up_cost", "inet_down_cost")
    selected = []
    for row in offers:
        name = " ".join(str(row.get("gpu_name", "")).replace("_", " ").split())
        hopper = name.startswith(("H100", "H200")) and row.get("compute_cap") == 900
        pro_blackwell = row.get("compute_cap") == 1200 and re.fullmatch(
            r"(?:NVIDIA )?RTX PRO 6000 Blackwell(?: (?:Server|(?:Max-Q )?Workstation) Edition)?",
            name, re.IGNORECASE,
        ) is not None
        if hopper or pro_blackwell:
            selected.append({k: row[k] for k in fields if k in row})
    selected.sort(key=lambda row: float(row.get("dph_total", "inf")))
    return {"account_authenticated": True, "storage_gib": storage, "query": queries[0],
            "additional_queries": queries[1:], "offers": selected[:10], "instance_created": False,
            "note": "Live offers only; host privacy/access review and final GPU preflight remain required."}


def cache_model(config_path: Path, cache: Path, token: str) -> dict:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    settings = raw.get("medgemma", {})
    revision = settings.get("revision", "")
    if (settings.get("model_id") != MODEL or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or not token):
        raise ValueError("a pinned MedGemma config and HF_TOKEN are required")
    if not cache.is_absolute() or cache.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("model cache must be absolute and outside the source checkout")
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info(MODEL, revision=revision, token=token, files_metadata=True)
    if info.sha != revision:
        raise ValueError("model revision differs from the locked config")
    files = [item for item in info.siblings if any(fnmatch.fnmatch(item.rfilename, p) for p in PATTERNS)]
    if not files or not any(item.rfilename.endswith(".safetensors") for item in files):
        raise ValueError("pinned model weight listing is incomplete")
    snapshot = Path(snapshot_download(MODEL, revision=revision, token=token,
                                     cache_dir=cache / "hub", allow_patterns=list(PATTERNS), max_workers=2))
    checked_bytes = 0
    for item in files:
        name = Path(item.rfilename)
        path = (snapshot / name).resolve()
        if (name.is_absolute() or ".." in name.parts or not path.is_relative_to(cache)
                or not path.is_file() or path.stat().st_size != item.size):
            raise ValueError("cached model file size or location differs")
        checked_bytes += item.size
        if item.lfs:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item.lfs.sha256:
                raise ValueError("cached model LFS checksum differs")
    from transformers import AutoProcessor
    AutoProcessor.from_pretrained(snapshot, local_files_only=True, trust_remote_code=False)
    return {"model_id": MODEL, "revision": revision, "files_verified": len(files),
            "bytes_verified": checked_bytes, "lfs_sha256_verified": True,
            "offline_processor_loaded": True, "full_model_loaded": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("credentials")
    vast = sub.add_parser("vast-status")
    vast.add_argument("--storage-gib", type=int, default=350)
    model = sub.add_parser("cache-model")
    model.add_argument("--config", type=Path, required=True)
    model.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        credentials = read_credentials(args.env_file)
        if args.command == "credentials":
            result = {key: bool(credentials.get(key)) for key in sorted(KEYS)}
        else:
            # Provider errors can echo authenticated URLs or account details.
            with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                if args.command == "vast-status":
                    result = vast_status(credentials.get("VAST_API_KEY", ""), args.storage_gib)
                else:
                    result = cache_model(args.config, args.cache, credentials.get("HF_TOKEN", ""))
        print(json.dumps(result), flush=True)
        return 0
    except KeyboardInterrupt:
        print("Interrupted; cached downloads are retained.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Rental access failed ({type(error).__name__}); no credential or response values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
