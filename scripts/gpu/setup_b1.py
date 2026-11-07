"""Prepare MIMIC/VQA supervision and hash-bound private BF16 LoRA drafts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.data.manifest import load_manifest, local_file
from cxr_research.experiments.config import check_storage, config_from_dict
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json
from dataset_sync import private_path
from prepare_rental import unresolved


def draft_preparation(root, catalog, output, smoke=False, repository=ROOT):
    root, catalog, output = (private_path(p, repository) for p in (root, catalog, output))
    if (not catalog.is_relative_to(root) or not output.is_relative_to(root / "rental")
            or output == root / "rental" or output.exists()):
        raise ContractError("choose a new private rental draft directory and a catalog under data root")
    report_path = catalog / "report.json"
    report = json.loads(report_path.read_text())
    if (report.get("status") != "complete" or report.get("split_policy") != "mimic-official"
            or report.get("identity_conflicts") != 0 or report.get("official_patient_overlaps") != 0):
        raise ContractError("an audited official-split catalog is required")
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    if git.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", git.stdout.strip()):
        raise ContractError("source Git provenance is unavailable")
    spec = json.loads((repository / "configs/catalog-preparation.template.json").read_text())
    spec.update(data_root=str(root), catalog_dir=str(catalog), prepared_dir=str(root / "prepared" / output.name),
        catalog_report_sha256=file_hash(report_path), producer_commit=git.stdout.strip(),
        input_policy="Original QA-linked frontal AP/PA image; preserve source image geometry for the pinned processor.",
        views=["AP", "PA"], answer_type_field="semantic_type",
        answer_type_values={"verify": "closed", "choose": "closed", "query": "open"},
        answer_rendering="join", answer_separator="; ", empty_answer_policy="exclude_from_supervision",
        template_policy="Secondary-only engineering preparation; primary template eligibility remains unreviewed.")
    if smoke:
        spec.update(preparation_purpose="smoke_only", image_limit_per_split={"train": 32, "validation": 8})
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "catalog-preparation.json", spec)
    result = {"status": "draft", "training_ready": False, "pending": unresolved(spec),
              "purpose": "smoke_only" if smoke else "development", "source_commit": git.stdout.strip()}
    write_json(output / "readiness.json", result)
    return result


def configure(prepared, output, revision, jira_issue=None, repository=ROOT, runtime_profile="cu130"):
    prepared, output = (private_path(p, repository) for p in (prepared, output))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ContractError("an immutable MedGemma revision is required")
    if jira_issue is not None and not re.fullmatch(r"[A-Z][A-Z0-9]+-[1-9][0-9]*", jira_issue):
        raise ContractError("invalid Jira issue key")
    provenance = json.loads((prepared / "provenance.json").read_text())
    spec = provenance.get("preparation_spec", {})
    root = private_path(spec["data_root"], repository)
    if (not prepared.is_relative_to(root) or not output.is_relative_to(root / "rental")
            or output == root / "rental" or output.exists() or output.is_relative_to(prepared)
            or prepared.is_relative_to(output)):
        raise ContractError("choose a new private rental directory separate from prepared data")
    local = json.loads((repository / "configs/medgemma_b1.json").read_text())
    local["data"].update(root=str(root), manifest=str(prepared / "manifest.jsonl"),
                         provenance=str(prepared / "provenance.json"), synthetic=False)
    local.update(run_id=output.name, jira_issue=jira_issue, output_root=str(root / "runs/b1"), runtime_profile=runtime_profile,
                 protocol=str(output / "protocol-b1-local.json"))
    local["medgemma"].update(revision=revision, quantization="none", local_files_only=True)
    local["train"].update(precision="bf16")
    config = config_from_dict(local)
    check_storage(config, repository)
    manifest = load_manifest(config.data)
    if any(s.task != "medgemma" or s.split not in {"train", "validation"} for s in manifest.samples):
        raise ContractError("B1 requires a development-only VQA manifest")
    training, validation = manifest.select("medgemma", "train"), manifest.select("medgemma", "validation")
    status = json.loads((prepared / "status.json").read_text())
    if status.get("status") != "complete" or status.get("sha256") != manifest.sha256:
        raise ContractError("preparation status does not match the manifest")
    remote = json.loads(json.dumps(local))
    remote_prepared = PurePosixPath("/private/project/data") / prepared.relative_to(root).as_posix()
    remote["data"].update(root="/private/project/data", manifest=str(remote_prepared / "manifest.jsonl"),
                          provenance=str(remote_prepared / "provenance.json"))
    remote.update(output_root="/private/project/runs/b1", protocol="/private/project/configs/protocol-b1.json")
    files = {"b1-local.json": local, "b1.json": remote}
    for name, raw in list(files.items()):
        protocol = json.loads((repository / "configs/protocol.template.json").read_text())
        protocol.update(status="draft", config_sha256=config_from_dict(raw).fingerprint,
            manifest_sha256=manifest.sha256, provenance_sha256=manifest.provenance_sha256,
            split_policy=provenance["split_policy"], input_policy=spec["input_policy"],
            uncertain_label_policy=spec["uncertain_label_policy"], template_policy=spec["template_policy"])
        # Access to the source is not permission for a particular rental host.
        files["protocol-b1-local.json" if "local" in name else "protocol-b1.json"] = protocol
    inventory = {s.image: s.image_sha256 for s in manifest.samples}
    inventory.update({(prepared / name).relative_to(root).as_posix(): file_hash(prepared / name)
                      for name in ("manifest.jsonl", "provenance.json")})
    output.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        write_json(output / name, value)
    with (output / "transfer-files.jsonl").open("w", encoding="utf-8") as stream:
        for path, sha in sorted(inventory.items()):
            stream.write(json.dumps({"path": path, "sha256": sha,
                                    "bytes": local_file(root, path).stat().st_size}) + "\n")
    updates = (len(training) + config.train.batch_size * config.train.accumulation_steps - 1) // (config.train.batch_size * config.train.accumulation_steps)
    result = {"status": "draft", "training_ready": False, "manifest": manifest.summary(),
        "purpose": spec.get("preparation_purpose", "development"),
        "train_qa": len(training), "validation_qa": len(validation),
        "estimated_optimizer_updates_per_epoch": updates,
        "transfer_files": len(inventory), "transfer_bytes": sum(local_file(root, p).stat().st_size for p in inventory),
        "transfer_inventory_sha256": file_hash(output / "transfer-files.jsonl"),
        "profile": "BF16 frozen base; FP32 language-only LoRA adapters",
        "pending": [*(["jira_issue"] if jira_issue is None else []), "actual protocol review",
                    "target-host data access review", "model terms and offline cache",
                    "locked Docker/GPU preflight", "real pilot, adapter reload and checkpoint recovery"],
        "files_sha256": {name: file_hash(output / name) for name in files}}
    write_json(output / "readiness.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    draft = sub.add_parser("draft", help="write the documented data policies, leaving access decisions unresolved")
    draft.add_argument("--data-root", type=Path, required=True)
    draft.add_argument("--catalog", type=Path, required=True)
    draft.add_argument("--output", type=Path, required=True)
    draft.add_argument("--smoke", action="store_true")
    prepare = sub.add_parser("prepare", help="convert an explicitly completed preparation specification")
    prepare.add_argument("--spec", type=Path, required=True)
    conf = sub.add_parser("configure", help="write local/Linux configs, draft protocols and selected transfer inventory")
    conf.add_argument("--prepared-dir", type=Path, required=True)
    conf.add_argument("--output", type=Path, required=True)
    conf.add_argument("--revision", required=True)
    conf.add_argument("--jira-issue")
    conf.add_argument("--runtime-profile", choices=["cu130", "medgemma-bf16-cu128"], default="cu130")
    args = parser.parse_args(argv)
    try:
        if args.command == "draft":
            result = draft_preparation(args.data_root, args.catalog, args.output, args.smoke)
        elif args.command == "prepare":
            from cxr_research.data.catalog_adapter import prepare_catalog
            result = prepare_catalog(args.spec, ROOT)
        else:
            result = configure(args.prepared_dir, args.output, args.revision, args.jira_issue,
                               runtime_profile=args.runtime_profile)
        print(json.dumps(result), flush=True)
        return 0
    except KeyboardInterrupt:
        print("Setup interrupted; completed private artifacts retained.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"B1 setup blocked ({type(error).__name__}); no private values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
