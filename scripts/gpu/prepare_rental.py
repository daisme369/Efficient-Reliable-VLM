"""Create private, unresolved rental inputs from an existing catalog and QC report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def unresolved(value, prefix=""):
    """Return field names only; never include source values in diagnostics."""
    if isinstance(value, dict):
        return [key for name, child in value.items()
                for key in unresolved(child, f"{prefix}.{name}".lstrip("."))]
    if isinstance(value, list):
        return [key for index, child in enumerate(value)
                for key in unresolved(child, f"{prefix}[{index}]")]
    return [prefix] if value is None or value == "UNRESOLVED" else []


def prepare_rental(data_root: Path, qc_report: Path, output: Path, repository: Path = ROOT):
    data_root, qc_report, output = (p.resolve() for p in (data_root, qc_report, output))
    repository = repository.resolve()
    rental_root = (data_root / "rental").resolve()
    if (data_root.is_relative_to(repository) or not qc_report.is_relative_to(data_root)
            or not output.is_relative_to(rental_root) or output == rental_root
            or output.is_relative_to(repository)):
        raise ContractError("rental drafts require a new private directory under data_root/rental")
    latest = json.loads((data_root / "manifests/latest.json").read_text(encoding="utf-8"))
    name = latest.get("snapshot", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ContractError("invalid catalog snapshot pointer")
    snapshot = (data_root / "manifests" / name).resolve()
    if not snapshot.is_relative_to(data_root):
        raise ContractError("catalog snapshot escapes private storage")
    report_path = snapshot / "report.json"
    report_hash = file_hash(report_path)
    if latest.get("status") != "complete" or report_hash != latest.get("report_sha256"):
        raise ContractError("catalog pointer or report fingerprint differs")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("schema") != "cxr-local-links-v1" or report.get("status") != "complete"
            or report.get("split_policy") != "mimic-official"
            or report.get("official_patient_overlaps") != 0 or report.get("identity_conflicts") != 0):
        raise ContractError("an audited official-split catalog is required")
    exports = ("images.jsonl", "sources.jsonl", "vqa_links.jsonl", "vqa.train.jsonl", "vqa.validation.jsonl")
    for name in exports:
        if file_hash(snapshot / name) != report.get("output_sha256", {}).get(name):
            raise ContractError("catalog export fingerprint differs")
    qc = json.loads(qc_report.read_text(encoding="utf-8"))
    if (qc.get("status") != "complete"
            or qc.get("identity", {}).get("inputs_sha256", {}).get("report.json") != report_hash):
        raise ContractError("QC report is incomplete or belongs to another catalog")
    if any(qc.get("qa", {}).get("source_sha256", {}).get(name) != report["output_sha256"][name]
           for name in ("vqa.train.jsonl", "vqa.validation.jsonl")):
        raise ContractError("QC development inputs differ from the catalog")
    if output.exists():
        raise ContractError("choose a new rental draft directory")
    prep = json.loads((repository / "configs/catalog-preparation.template.json").read_text(encoding="utf-8"))
    prepared = data_root / "prepared" / output.name
    prep.update(data_root=str(data_root), catalog_dir=str(snapshot), prepared_dir=str(prepared),
                catalog_report_sha256=report_hash)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    if commit.returncode or dirty.returncode:
        raise ContractError("source Git provenance is unavailable")
    prep["producer_commit"] = commit.stdout.strip()
    train = json.loads((repository / "configs/medgemma_b1.json").read_text(encoding="utf-8"))
    remote_prepared = f"/private/project/data/prepared/{output.name}"
    train["data"].update(root="/private/project/data", manifest=f"{remote_prepared}/manifest.jsonl",
                         provenance=f"{remote_prepared}/provenance.json")
    train["protocol"] = "/private/project/configs/protocol-b1.json"
    protocol = json.loads((repository / "configs/protocol.template.json").read_text(encoding="utf-8"))
    protocol.update(split_policy="mimic-official; union of official and source-VQA test patients sealed",
                    uncertain_label_policy="preserve_source_answers")
    # Segmentation is not part of this B1 run. Its unrelated null field is not a blocker.
    protocol_fields = {k: v for k, v in protocol.items() if k != "segmentation_training_decision"}
    summary = {
        "schema": "cxr-rental-plan-v1", "status": "draft", "training_ready": False,
        "catalog_report_sha256": report_hash, "qc_report_sha256": file_hash(qc_report),
        "development_rows": report["development_rows"], "qa_review_counts": qc["qa"]["counts"],
        "source_commit": prep["producer_commit"], "source_dirty": bool(dirty.stdout.strip()),
        "pending": {"preparation": [*unresolved(prep), "answer_type_values"], "training": unresolved(train),
                    "protocol": ["status=approved", *unresolved(protocol_fields)],
                    "runtime": ["verified Docker image", "private cached model weights and processor",
                                "successful training-role GPU preflight", "pilot and checkpoint recovery"]},
        "verification_scope": "catalog export checksums and QC linkage; no full image scan or clinical review",
        "test_qa_payload_read": False, "manifest_created": False,
        "checkpoint_policy": "current trainer saves at epoch boundaries only",
    }
    output.mkdir(parents=True, exist_ok=False)
    files = {"catalog-preparation.json": prep, "b1.json": train, "protocol-b1.json": protocol}
    for name, value in files.items():
        write_json(output / name, value)
    summary["draft_sha256"] = {name: file_hash(output / name) for name in files}
    write_json(output / "readiness.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--qc-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_rental(args.data_root, args.qc_report, args.output)
        print(json.dumps(result))
        return 0
    except ContractError as error:
        print(f"Rental preparation blocked: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Rental preparation blocked ({type(error).__name__}); no input values displayed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
