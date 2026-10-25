"""Run private, resumable catalog QC and preserve development QA for review."""
from __future__ import annotations

from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from cxr_research.data.image_audit import audit_images
from cxr_research.data.manifest import local_file
from cxr_research.data.qa_audit import audit_qa
from cxr_research.data.template_review import review_programs
from cxr_research.preprocessing.labels import TEXT_VERSION
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


@contextmanager
def _exclusive_run(path):
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ContractError("another process owns this QC run") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def audit_catalog(spec_path: Path, repository: Path):
    repository = repository.resolve()
    if spec_path.resolve().is_relative_to(repository):
        raise ContractError("real QC specifications must stay outside the source repository")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root, snapshot, output = (Path(spec[key]).resolve() for key in ("data_root", "catalog_dir", "output_dir"))
    qc_root = (root / "qc").resolve()
    if (any(p.is_relative_to(repository) for p in (root, snapshot, output))
            or not snapshot.is_relative_to(root) or not output.is_relative_to(root) or output == root
            or not output.is_relative_to(qc_root) or output == qc_root
            or snapshot.is_relative_to(output) or output.is_relative_to(snapshot)):
        raise ContractError("QC requires separate private snapshot and output directories")
    if (spec.get("normalization") != TEXT_VERSION or spec.get("uncertain_label_policy") != "preserve_source_answers"
            or spec.get("review_policy") != "retain_and_flag"):
        raise ContractError("QC requires explicit preserve-and-review text policies")
    datasets = spec.get("datasets")
    if (not isinstance(datasets, list) or not datasets or len(set(datasets)) != len(datasets)
            or any(d not in {"mimic-cxr", "nih-chestxray14", "vindr-cxr"} for d in datasets)):
        raise ContractError("unsupported QC dataset selection")
    limit = spec.get("image_limit_per_dataset")
    if type(limit) is not int or limit == 0 or limit < -1 or spec.get("voi_policy") not in {"none", "dicom"}:
        raise ContractError("explicit image limit and DICOM VOI policy are required")
    report_path = snapshot / "report.json"
    report_hash = file_hash(report_path)
    if report_hash != spec.get("catalog_report_sha256"):
        raise ContractError("catalog report checksum differs from QC specification")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("status") != "complete" or report.get("split_policy") != "mimic-official"
            or report.get("identity_conflicts") != 0 or report.get("official_patient_overlaps") != 0):
        raise ContractError("QC requires an audited official-split catalog")
    inputs = {"report.json": report_hash, "catalog.sqlite": file_hash(snapshot / "catalog.sqlite")}
    if inputs["catalog.sqlite"] != report.get("catalog_sqlite_sha256"):
        raise ContractError("catalog database checksum differs from report")
    for name in ("images.jsonl", "sources.jsonl", "vqa.train.jsonl", "vqa.validation.jsonl"):
        inputs[name] = file_hash(snapshot / name)
        if inputs[name] != report.get("output_sha256", {}).get(name):
            raise ContractError("catalog export checksum differs from report")
    output.mkdir(parents=True, exist_ok=True)
    qa_processors = {"data/qa_audit.py": file_hash(Path(__file__).with_name("qa_audit.py")),
                     "preprocessing/labels.py": file_hash(Path(__file__).parent.parent / "preprocessing/labels.py")}
    with _exclusive_run(output / "run.lock"):
        checkpoint_path = output / "checkpoint.json"
        identity = {"spec_sha256": file_hash(spec_path), "inputs_sha256": inputs}
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text())
            if checkpoint["identity"] != identity:
                raise ContractError("QC inputs or configuration changed; use a new output directory")
        else:
            if any(p.name != "run.lock" for p in output.iterdir()):
                raise ContractError("QC output directory is not empty")
            checkpoint = {"identity": identity}
            write_json(checkpoint_path, checkpoint)
        write_json(output / "status.json", {"status": "running", "training_ready": False})
        try:
            with closing(sqlite3.connect((snapshot / "catalog.sqlite").as_uri()+"?mode=ro", uri=True)) as catalog:
                for relative, size, mtime in catalog.execute("SELECT path,bytes,mtime_ns FROM sources"):
                    stat = local_file(root, relative).stat()
                    if (stat.st_size, stat.st_mtime_ns) != (size, mtime):
                        raise ContractError("indexed source changed; CSV offsets and ZIP references must be rebuilt")
                official_overlap = catalog.execute("SELECT COUNT(*) FROM (SELECT patient_id FROM memberships WHERE source='mimic' GROUP BY patient_id HAVING COUNT(DISTINCT split)>1)").fetchone()[0]
                if official_overlap:
                    raise ContractError("official patient overlap detected")
                if "qa" not in checkpoint:
                    checkpoint["qa"] = audit_qa(snapshot, output, catalog)
                    checkpoint["qa_processors_sha256"] = qa_processors
                    for split in ("train", "validation"):
                        if checkpoint["qa"]["counts"].get(f"{split}.rows", 0) != report["development_rows"][split]:
                            raise ContractError("development QA counts differ from catalog report")
                    write_json(checkpoint_path, checkpoint)
                else:
                    if checkpoint.get("qa_processors_sha256") != qa_processors:
                        raise ContractError("QA processing code changed; use a new output directory")
                    for name, expected in checkpoint["qa"]["output_sha256"].items():
                        if file_hash(output / name) != expected:
                            raise ContractError("completed QC output changed")
                program_code = file_hash(Path(__file__).with_name("template_review.py"))
                if checkpoint.get("program_review_code") != program_code:
                    checkpoint["program_review"] = review_programs(output)
                    checkpoint["program_review_code"] = program_code
                    write_json(checkpoint_path, checkpoint)
                elif file_hash(output / "program-review.jsonl") != checkpoint["program_review"]["output_sha256"]:
                    raise ContractError("completed program review output changed")
                image_result = audit_images(root, catalog, output, datasets=datasets, image_limit=limit,
                                            voi_policy=spec["voi_policy"])
                expected_duplicates = catalog.execute("SELECT COUNT(*) FROM (SELECT expected_sha256 FROM images WHERE expected_sha256 IS NOT NULL GROUP BY expected_sha256 HAVING COUNT(*)>1)").fetchone()[0]
                duplicate_split_groups = catalog.execute("SELECT COUNT(*) FROM (SELECT expected_sha256 FROM images WHERE dataset='mimic-cxr' AND expected_sha256 IS NOT NULL GROUP BY expected_sha256 HAVING COUNT(DISTINCT source_split)>1)").fetchone()[0]
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
            modules = {**qa_processors, **image_result["processor_sha256"], "data/quality.py": file_hash(Path(__file__)),
                       "data/template_review.py": program_code,
                       "utils/io.py": file_hash(Path(__file__).parent.parent / "utils/io.py")}
            result = {"status": "complete", "training_ready": False, "identity": identity,
                      "producer_commit": commit.stdout.strip() if commit.returncode == 0 else None,
                      "producer_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
                      "producer_modules_sha256": modules,
                      "qa": checkpoint["qa"], "images": image_result,
                      "program_review": checkpoint["program_review"],
                      "publisher_digest_duplicate_groups": expected_duplicates,
                      "publisher_digest_groups_across_official_mimic_splits": duplicate_split_groups,
                      "duplicate_scope": "publisher hashes are inventory evidence; checked image bytes are in private QC records",
                      "clinical_validation": "not performed", "test_qa_payload_read": False,
                      "remaining_decisions": ["primary template eligibility", "closed/open taxonomy mapping",
                                              "empty and multi-answer rendering",
                                              "input-view and multi-image policy", "source access/derivation review",
                                              "anatomical RCA selection threshold", "finding-linked QA grounding protocol"]}
            write_json(output / "report.json", result)
            write_json(output / "status.json", {"status": "complete", "training_ready": False})
            return result
        except BaseException as error:
            write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__})
            raise
