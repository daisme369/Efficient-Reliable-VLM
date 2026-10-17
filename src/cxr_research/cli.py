"""Research command entry point; importing it never loads model weights."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from cxr_research.experiments.config import check_protocol, check_storage, load_config
from cxr_research.data.manifest import load_manifest
from cxr_research.utils.errors import ContractError
from cxr_research.utils.paths import repository_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, help="source checkout for provenance and GPU policy")
    commands = parser.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("synthetic")
    fixture.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--spec", type=Path, required=True)
    pack = commands.add_parser("pack", help="create private WebDataset shards and token cache")
    pack.add_argument("--config", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--shard-size-mb", type=int, default=512)
    sweep = commands.add_parser("sweep", help="train each YAML variant and seed sequentially")
    sweep.add_argument("--config", type=Path, required=True)
    sweep.add_argument("--dry-run", action="store_true", help="validate and list runs without training or writes")
    for command in ("validate", "train", "evaluate"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", type=Path, required=True)
        if command == "train":
            sub.add_argument("--resume", type=Path)
        if command == "evaluate":
            sub.add_argument("--checkpoint", type=Path)
    args = parser.parse_args(argv)
    try:
        repository = repository_root(args.repository)
        if args.command == "sweep":
            from cxr_research.experiments.sweep import load_sweep, run_sweep
            plan = load_sweep(args.config, repository)
            if args.dry_run:
                print(json.dumps(plan.preview()))
                return 0
            return run_sweep(plan, repository)
        if args.command == "synthetic":
            from cxr_research.data.synthetic import create_fixture
            create_fixture(args.output)
            print("Synthetic fixtures and unet.json created.")
            return 0
        if args.command == "prepare":
            from cxr_research.data.preparation import prepare
            print(json.dumps(prepare(args.spec, repository)))
            return 0
        config = load_config(args.config)
        check_storage(config, repository)
        if args.command == "pack":
            from cxr_research.data.shards import pack_shards
            manifest = load_manifest(config.data)
            processor = None
            if config.task == "medgemma":
                from transformers import AutoProcessor
                processor = AutoProcessor.from_pretrained(config.medgemma.model_id,
                    revision=config.medgemma.revision, local_files_only=True, trust_remote_code=False)
                processor.tokenizer.padding_side = "right"
            print(json.dumps(pack_shards(config, manifest, args.output, repository,
                                        shard_bytes=args.shard_size_mb * 1024**2, processor=processor)))
            return 0
        manifest = load_manifest(config.data, verify_files=config.io.backend == "files")
        store = None
        if config.io.backend == "webdataset":
            from cxr_research.data.shards import ShardStore
            store = ShardStore(config, manifest)
        if args.command == "validate":
            if store is not None:
                store.verify_all()
            print(json.dumps({"manifest": manifest.summary(), "config_sha256": config.fingerprint,
                              "provenance_sha256": manifest.provenance_sha256,
                              "protocol_status": "not_checked", "GPU_status": "not_checked"}))
            return 0
        check_protocol(config, manifest.sha256, manifest.provenance_sha256)
        if args.command == "train" and config.baseline == "B0":
            raise ContractError("B0 is frozen; use evaluate")
        checkpoint = getattr(args, "checkpoint", None) or getattr(args, "resume", None)
        if checkpoint and not config.data.synthetic and checkpoint.resolve().is_relative_to(repository):
            raise ContractError("restricted checkpoints must be outside source repository")
        # No external experiment tracking; model files must already exist in the private cache.
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        from cxr_research.experiments.environment import gpu_preflight
        from cxr_research.experiments.runs import run_record
        from cxr_research.utils.reproducibility import seed_everything
        gpu_evidence = gpu_preflight(config, args.command, repository)
        seed_everything(config.seed)
        with run_record(config, manifest, args.command, repository, gpu_evidence) as (directory, record):
            if config.task == "unet":
                from cxr_research.training import segmentation as segmentation_training
                from cxr_research.evaluation import segmentation as segmentation_evaluation
                if args.command == "train":
                    segmentation_training.train(config, manifest, directory, resume=args.resume, store=store)
                elif args.checkpoint:
                    segmentation_evaluation.evaluate(config, manifest, directory, args.checkpoint, store=store)
                else:
                    raise ContractError("U-Net evaluation needs a checkpoint")
            else:
                from cxr_research.training import medgemma as medgemma_training
                from cxr_research.evaluation import vqa
                if args.command == "train":
                    medgemma_training.train(config, manifest, directory, resume=args.resume, store=store)
                else:
                    vqa.generate(config, manifest, directory, adapter=args.checkpoint, store=store)
        print(json.dumps({"status": "complete", "evidence": record["evidence"]}))
        return 0
    except ContractError as error:
        print(f"Experiment blocked: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Experiment interrupted; completed checkpoints are retained.", file=sys.stderr)
        return 130
    except Exception as error:
        # File paths, IDs, processor input and remote URLs can appear in exception text.
        print(f"Experiment failed ({type(error).__name__}); no input values displayed.", file=sys.stderr)
        return 1
