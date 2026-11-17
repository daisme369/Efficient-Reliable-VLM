"""Validate, train or evaluate the private M1 development experiment."""

import argparse
import json
import os
from pathlib import Path
import sys

from bootstrap import ROOT, check_cache
from cxr_research.data.m1 import M1Bundle, read_json
from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import check_protocol, check_storage, load_config
from cxr_research.utils.errors import ContractError
from cxr_research.utils.io import write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "train", "evaluate"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--checkpoint", type=Path, help="M1 adapter directory for evaluation")
    parser.add_argument("--resume", type=Path, help="M1 recovery checkpoint using the identical experiment")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = load_config(args.config)
        check_storage(config, ROOT)
        if (config.task, config.baseline, config.io.backend, config.medgemma.local_files_only,
                config.medgemma.quantization) != ("medgemma", "B1", "files", True, "none"):
            raise ContractError("M1 needs the offline, unquantized MedGemma B1 files configuration as a base")
        if not config.data.synthetic and (config.device, config.train.precision, config.runtime_profile) != (
                "cuda", "bf16", "cu130"):
            raise ContractError("real M1 runs require the standard BF16 CUDA 13 profile")
        if ((args.command == "evaluate") != bool(args.checkpoint)
                or (args.resume is not None and args.command != "train")):
            raise ContractError("evaluation requires an adapter; resume is for training checkpoints")
        for path in (args.checkpoint, args.resume, args.cache):
            if path is not None and (not path.is_absolute() or (
                    not config.data.synthetic and path.resolve().is_relative_to(ROOT))):
                raise ContractError("cache and checkpoint paths must be absolute and private")
        if not config.protocol:
            raise ContractError("M1 needs a protocol bound to its development bundle")

        manifest = load_manifest(config.data)
        check_protocol(config, manifest.sha256, manifest.provenance_sha256)
        settings = read_json(Path(config.protocol)).get("m1")
        if not isinstance(settings, dict) or not isinstance(settings.get("bundle_report_sha256"), str):
            raise ContractError("M1 protocol requires its bundle report SHA-256")
        risk = settings.get("target_risk")
        pilot = settings.get("pilot", False)
        if type(risk) not in (float, int) or not 0 < risk < 1:
            raise ContractError("M1 target_risk must be between zero and one")
        if type(pilot) is not bool:
            raise ContractError("M1 pilot must be a boolean")
        if not config.data.synthetic and any(
                not isinstance(settings.get(key), str) or
                settings[key].strip().casefold() in {"", "unresolved"}
                for key in ("bbox_review", "impression_supervision_decision", "grounding_protocol")):
            raise ContractError("record the actual bbox, impression and grounding decisions for real M1 runs")

        bundle = M1Bundle(manifest, Path(config.data.manifest).resolve().parent,
                          settings["bundle_report_sha256"], synthetic=config.data.synthetic, pilot=pilot)
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        if args.cache:
            cache = args.cache.resolve()
            os.environ.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"))
            check_cache(config, cache)
        elif not config.data.synthetic:
            raise ContractError("real M1 runs need a private offline model cache")

        from cxr_research.experiments.environment import gpu_preflight
        operation = "evaluate" if args.command == "evaluate" else "train"
        evidence = gpu_preflight(config, operation, ROOT)
        summary = {"variant": "M1", **bundle.identity, **bundle.summary()}
        if args.command == "validate":
            print(json.dumps({"status": "m1_preflight_passed", **summary}))
            return 0

        from cxr_research.experiments.runs import run_record
        from cxr_research.utils.reproducibility import seed_everything
        seed_everything(config.seed)
        with run_record(config, manifest, operation, ROOT, evidence) as (directory, record):
            record.update(variant="M1", m1=bundle.identity, target_risk=risk)
            write_json(directory / "run.json", record)
            write_json(directory / "data_summary.json", summary)
            if args.command == "train":
                from cxr_research.training.medgemma import train
                train(config, manifest, directory, resume=args.resume, multitask=bundle)
            else:
                from cxr_research.evaluation.m1 import evaluate
                evaluate(config, manifest, bundle, directory, args.checkpoint.resolve(), risk)
        print(json.dumps({"status": "complete", "variant": "M1", "split": "development"}))
        return 0
    except ContractError as exc:
        print(f"M1 blocked: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("M1 interrupted. Completed checkpoints are retained.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"M1 failed ({type(exc).__name__}); input values withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
