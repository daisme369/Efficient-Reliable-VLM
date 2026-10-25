"""Check a private B1 run in the locked GPU container; train only with --train."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import check_protocol, check_storage, load_config
from cxr_research.experiments.environment import gpu_preflight
from cxr_research.utils.errors import ContractError


def check_cache(config, cache: Path):
    from huggingface_hub import snapshot_download
    snapshot = Path(snapshot_download(config.medgemma.model_id, revision=config.medgemma.revision,
                                     cache_dir=cache / "hub", local_files_only=True))
    index = snapshot / "model.safetensors.index.json"
    if index.is_file():
        names = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
    else:
        names = {"model.safetensors"}
    if not names or any(not isinstance(name, str) or Path(name).is_absolute()
                        or ".." in Path(name).parts for name in names):
        raise ContractError("invalid cached model weight index")
    for name in ("config.json", *names):
        path = (snapshot / name).resolve()
        if not path.is_relative_to(cache) or not path.is_file() or path.stat().st_size == 0:
            raise ContractError("pinned model cache is incomplete")
    from transformers import AutoProcessor
    AutoProcessor.from_pretrained(config.medgemma.model_id, revision=config.medgemma.revision,
                                  cache_dir=cache / "hub", local_files_only=True, trust_remote_code=False)


def bootstrap(config_path: Path, cache: Path, repository: Path = ROOT, resume: Path | None = None):
    config = load_config(config_path)
    check_storage(config, repository)
    if (config.task, config.baseline, config.device, config.data.synthetic) != ("medgemma", "B1", "cuda", False):
        raise ContractError("rental bootstrap requires a real CUDA B1 configuration")
    if not config.medgemma.local_files_only:
        raise ContractError("rental bootstrap requires offline model loading")
    if not cache.is_absolute() or cache.resolve().is_relative_to(repository.resolve()):
        raise ContractError("model cache must be absolute and outside the source checkout")
    cache = cache.resolve()
    if not cache.is_dir():
        raise ContractError("persistent model cache is missing")
    output = Path(config.output_root).resolve()
    if (output / config.run_id).exists():
        raise ContractError("run ID already exists; use a new run ID for recovery")
    output.mkdir(parents=True, exist_ok=True)
    for directory in (output, cache):
        with tempfile.TemporaryFile(dir=directory):
            pass
    os.environ.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      TOKENIZERS_PARALLELISM="false")
    manifest = load_manifest(config.data, verify_files=config.io.backend == "files")
    manifest.select("medgemma", "train")
    manifest.select("medgemma", "validation")
    check_protocol(config, manifest.sha256, manifest.provenance_sha256)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    if git.returncode or dirty.returncode:
        raise ContractError("source checkout must expose Git commit and dirty-state provenance")
    if config.io.backend == "webdataset":
        from cxr_research.data.shards import ShardStore
        ShardStore(config, manifest).verify_all()
    evidence = gpu_preflight(config, "train", repository)
    check_cache(config, cache)
    if resume is not None:
        if not resume.is_dir() or resume.resolve().is_relative_to(repository.resolve()):
            raise ContractError("resume requires a private training checkpoint directory")
        from cxr_research.experiments.checkpoints import verify_artifacts
        verify_artifacts(resume)
    return {"status": "preflight_passed", "training_started": False,
            "config_sha256": config.fingerprint, "manifest_sha256": manifest.sha256,
            "provenance_sha256": manifest.provenance_sha256, "gpu": evidence,
            "source_commit": git.stdout.strip(), "source_dirty": bool(dirty.stdout.strip()),
            "model_cache": "files present and processor loaded offline; model load checked during training",
            "checkpoint_policy": "epoch boundaries only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--train", action="store_true", help="start training after all preflight checks pass")
    args = parser.parse_args(argv)
    try:
        result = bootstrap(args.config, args.cache, resume=args.resume)
        print(json.dumps(result), flush=True)
        if not args.train:
            return 0
        from cxr_research.cli import main as run
        command = ["--repository", str(ROOT), "train", "--config", str(args.config)]
        if args.resume:
            command += ["--resume", str(args.resume)]
        return run(command)
    except KeyboardInterrupt:
        print("Bootstrap interrupted; existing checkpoints are retained.", file=sys.stderr)
        return 130
    except ContractError as error:
        print(f"Bootstrap blocked: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Bootstrap blocked ({type(error).__name__}); no input values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
