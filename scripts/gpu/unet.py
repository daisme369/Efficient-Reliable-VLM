"""Prepare private U-Net inputs and check or launch an explicitly approved run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import check_protocol, check_storage, config_from_dict, load_config
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def prepare_unet_run(prepared: Path, output: Path, jira_issue: str | None, repository: Path = ROOT):
    """Write local validation and Linux run configs; preserve a draft review gate."""
    prepared, output, repository = prepared.resolve(), output.resolve(), repository.resolve()
    provenance = json.loads((prepared / "provenance.json").read_text())
    spec = provenance.get("preparation_spec", {})
    root = Path(spec["data_root"]).resolve()
    if (any(p.is_relative_to(repository) for p in (root, prepared, output))
            or not prepared.is_relative_to(root / "prepared") or not output.is_relative_to(root / "rental")
            or output == root / "rental" or prepared == output or output.exists()):
        raise ContractError("choose a new private rental directory separate from prepared data")
    if jira_issue is not None and not re.fullmatch(r"[A-Z][A-Z0-9]+-[1-9][0-9]*", jira_issue):
        raise ContractError("invalid Jira issue key")
    if provenance.get("segmentation", {}).get("target") != "binary_lung_union":
        raise ContractError("a prepared binary-lung segmentation manifest is required")
    local = json.loads((repository / "configs/unet.json").read_text())
    local["data"].update(root=str(root), manifest=str(prepared / "manifest.jsonl"),
                          provenance=str(prepared / "provenance.json"), synthetic=False)
    local.update(jira_issue=jira_issue, run_id=output.name, output_root=str(root / "runs/unet"),
                 protocol=str(output / "protocol-unet-local.json"))
    if spec["purpose"] == "smoke_only":
        # A checkpoint after epoch one can resume within the unchanged budget.
        local["train"]["epochs"] = 2
    local_config = config_from_dict(local)
    check_storage(local_config, repository)
    manifest = load_manifest(local_config.data)
    if any(s.task != "unet" for s in manifest.samples):
        raise ContractError("U-Net inputs must not contain VQA supervision")
    manifest.select("unet", "train")
    manifest.select("unet", "validation")
    status = json.loads((prepared / "status.json").read_text())
    if status.get("status") != "complete" or status.get("sha256") != manifest.sha256:
        raise ContractError("preparation status does not match the canonical manifest")
    remote = json.loads(json.dumps(local))
    remote_prepared = PurePosixPath("/private/project/data") / prepared.relative_to(root).as_posix()
    remote["data"].update(root="/private/project/data", manifest=str(remote_prepared / "manifest.jsonl"),
                           provenance=str(remote_prepared / "provenance.json"))
    remote.update(output_root="/private/project/runs/unet", protocol="/private/project/configs/protocol-unet.json")
    files = {"unet.json": remote, "unet-local.json": local}
    protocols = {}
    for name, raw in files.items():
        protocol = json.loads((repository / "configs/protocol.template.json").read_text())
        protocol.update(status="draft", config_sha256=config_from_dict(raw).fingerprint,
            manifest_sha256=manifest.sha256, provenance_sha256=manifest.provenance_sha256,
            split_policy=provenance["split_policy"], input_policy="Original AP/PA MIMIC JPG; paired grayscale letterbox, no flip or crop; default augmentation disabled.",
            uncertain_label_policy="Not a disease-label task; source anatomical pseudo-labels are retained without clinical relabeling.",
            template_policy="Not applicable to anatomical segmentation; no VQA or primary template selection.",
            metric_policy="Engineering pilot: BCE plus soft Dice loss, validation mean per-image Dice/IoU over valid pixels, threshold 0.5. Empty-both score 1. Pseudo-label agreement, not lesion or clinical accuracy.",
            training_data_overlap="CheXmask provides generated anatomical targets on MIMIC images; no independent external-test or clinical ground-truth claim. U-Net initializes from random weights.",
            access_review=spec["access_review"], segmentation_training_decision=spec["segmentation_training_decision"],
            decision_record="User requested U-Net implementation and local preparation; actual run/protocol review and remote access review remain pending.")
        protocols["protocol-unet-local.json" if "local" in name else "protocol-unet.json"] = protocol
    output.mkdir(parents=True, exist_ok=False)
    for name, value in {**files, **protocols}.items():
        write_json(output / name, value)
    inventory = {s.image: s.image_sha256 for s in manifest.samples}
    inventory.update({s.mask: s.mask_sha256 for s in manifest.samples})
    inventory.update({(prepared / name).relative_to(root).as_posix(): file_hash(prepared / name)
                      for name in ("manifest.jsonl", "provenance.json")})
    with (output / "transfer-files.jsonl").open("w", encoding="utf-8") as stream:
        for path, sha in sorted(inventory.items()):
            stream.write(json.dumps({"path": path, "sha256": sha, "bytes": (root / path).stat().st_size}) + "\n")
    result = {"status": "draft", "training_ready": False, "target": "binary_lung_union", "manifest": manifest.summary(),
              "purpose": spec["purpose"], "provenance_sha256": manifest.provenance_sha256,
              "transfer_files": len(inventory), "transfer_bytes": sum((root / p).stat().st_size for p in inventory),
              "transfer_inventory_sha256": file_hash(output / "transfer-files.jsonl"), "jira_issue": jira_issue,
              "pending": ["actual protocol review", "remote data access review if renting", "training-role GPU preflight", "real pilot and checkpoint recovery"],
              "config_sha256": {name: file_hash(output / name) for name in (*files, *protocols)}}
    write_json(output / "readiness.json", result)
    return result


def preflight_unet(config_path: Path, repository: Path = ROOT, resume: Path | None = None):
    config = load_config(config_path)
    check_storage(config, repository)
    if (config.task, config.baseline, config.device, config.data.synthetic) != ("unet", "S1", "cuda", False):
        raise ContractError("U-Net bootstrap requires a real CUDA S1 config")
    if config.io.backend != "files":
        raise ContractError("this U-Net pilot bootstrap requires the verified files backend")
    manifest = load_manifest(config.data)
    if any(s.task != "unet" for s in manifest.samples):
        raise ContractError("U-Net pilot manifest contains another task")
    manifest.select("unet", "train")
    manifest.select("unet", "validation")
    check_protocol(config, manifest.sha256, manifest.provenance_sha256)
    run = Path(config.output_root) / config.run_id
    if run.exists():
        raise ContractError("run ID already exists; use a new run ID for recovery")
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    if git.returncode or dirty.returncode:
        raise ContractError("source checkout must expose Git provenance")
    run.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=run.parent):
        pass
    if resume is not None:
        if not resume.is_file() or resume.resolve().is_relative_to(repository.resolve()):
            raise ContractError("resume requires a private epoch checkpoint file")
        from cxr_research.experiments.checkpoints import load_checkpoint
        load_checkpoint(resume, config, manifest)
    from cxr_research.experiments.environment import gpu_preflight
    evidence = gpu_preflight(config, "train", repository)
    return {"status": "preflight_passed", "training_started": False, "manifest": manifest.summary(),
            "config_sha256": config.fingerprint, "provenance_sha256": manifest.provenance_sha256,
            "source_commit": git.stdout.strip(), "source_dirty": bool(dirty.stdout.strip()),
            "gpu": evidence, "checkpoint_policy": "epoch boundaries only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="decode selected masks from a pinned catalog")
    prepare.add_argument("--spec", type=Path, required=True)
    draft = sub.add_parser("configure", help="generate private draft configs from a completed manifest")
    draft.add_argument("--prepared-dir", type=Path, required=True)
    draft.add_argument("--output", type=Path, required=True)
    draft.add_argument("--jira-issue")
    for name in ("check", "train"):
        run = sub.add_parser(name)
        run.add_argument("--config", type=Path, required=True)
        run.add_argument("--resume", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            from cxr_research.data.segmentation_catalog import prepare_unet_catalog
            result = prepare_unet_catalog(args.spec, ROOT)
        elif args.command == "configure":
            result = prepare_unet_run(args.prepared_dir, args.output, args.jira_issue)
        else:
            result = preflight_unet(args.config, resume=args.resume)
        print(json.dumps(result), flush=True)
        if args.command == "train":
            from cxr_research.cli import main as run
            command = ["--repository", str(ROOT), "train", "--config", str(args.config)]
            if args.resume:
                command += ["--resume", str(args.resume)]
            return run(command)
        return 0
    except KeyboardInterrupt:
        print("U-Net interrupted; completed artifacts are retained.", file=sys.stderr)
        return 130
    except ContractError as error:
        print(f"U-Net blocked: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"U-Net failed ({type(error).__name__}); no private input values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
