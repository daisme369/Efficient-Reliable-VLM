"""Convert pinned catalog development QA to canonical VQA records.

Research policies come from an explicit private specification. Anatomical masks
and finding annotations remain catalog references, not VQA grounding targets.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from cxr_research.data.manifest import load_manifest, local_file
from cxr_research.data.splits import SplitAudit
from cxr_research.experiments.config import DataConfig
from cxr_research.preprocessing.labels import TEXT_VERSION, CUE_VERSION, process_qa, review_qa
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def _resolved(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() != "UNRESOLVED"


def _rows(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def _git_identity(repository):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    value = commit.stdout.strip().lower()
    if (commit.returncode or dirty.returncode or len(value) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in value)):
        raise ContractError("source Git provenance is unavailable")
    return value, bool(dirty.stdout.strip())


def _check_spec(spec, producer_commit):
    for key in ("uncertain_label_policy", "input_policy", "template_policy", "access_review", "producer_commit"):
        if not _resolved(spec.get(key)):
            raise ContractError("catalog conversion decisions are unresolved")
    if spec["producer_commit"].strip().lower() != producer_commit:
        raise ContractError("catalog conversion producer commit differs from the current checkout")
    if spec.get("split_policy") != "mimic-official" or spec.get("patient_namespace") != "mimic-cxr":
        raise ContractError("catalog conversion requires the official MIMIC split and namespace")
    if (spec.get("uncertain_label_policy") != "preserve_source_answers"
            or spec.get("normalization") not in {"identity", TEXT_VERSION}
            or spec.get("review_policy") != "retain_and_flag"):
        raise ContractError("explicit preserve-and-review text policies are required")
    views = spec.get("views")
    if not isinstance(views, list) or not views or not all(_resolved(v) for v in views):
        raise ContractError("catalog conversion requires explicit input views")
    if spec.get("answer_type_field") not in {"semantic_type", "content_type"}:
        raise ContractError("an explicit source answer taxonomy field is required")
    taxonomy = spec.get("answer_type_values")
    if (not isinstance(taxonomy, dict) or not taxonomy
            or any(not _resolved(k) or v not in {"closed", "open"} for k, v in taxonomy.items())):
        raise ContractError("an explicit closed/open answer taxonomy is required")
    if spec.get("answer_rendering") not in {"single", "join"}:
        raise ContractError("answer rendering is unresolved")
    if spec.get("empty_answer_policy", "error") not in {"error", "exclude_from_supervision"}:
        raise ContractError("empty-answer supervision policy is unresolved")
    if "image_limit_per_split" in spec:
        limits = spec["image_limit_per_split"]
        if (spec.get("preparation_purpose") != "smoke_only" or not isinstance(limits, dict)
                or set(limits) != {"train", "validation"}
                or any(type(n) is not int or n <= 0 for n in limits.values())):
            raise ContractError("image limits require an explicit smoke-only purpose and positive split limits")
    if spec["answer_rendering"] == "join" and not _resolved(spec.get("answer_separator")):
        raise ContractError("joining source answers requires an explicit separator")
    primary = spec.get("primary_template_ids")
    if spec.get("template_id_field") not in {"template_program", "template"}:
        raise ContractError("an explicit source template ID field is required")
    if not isinstance(primary, list) or not all(_resolved(v) for v in primary):
        raise ContractError("primary template IDs must be explicitly recorded")
    sources = spec.get("sources", {})
    if not isinstance(sources, dict) or set(sources) != {"images", "qa"}:
        raise ContractError("catalog conversion requires image and QA source metadata")
    for kind, name, version in (("images", "MIMIC-CXR-JPG", "2.1.0"),
                                ("qa", "MIMIC-Ext-MIMIC-CXR-VQA", "1.0.0")):
        source = sources.get(kind, {})
        if (source.get("name") != name or source.get("version") != version
                or any(not _resolved(source.get(k)) for k in ("access", "license"))):
            raise ContractError("source versions, access and license review are unresolved")
        if kind == "qa":
            derivation = source.get("derivation", {})
            if any(not _resolved(derivation.get(k)) for k in ("producer", "method", "source_report_section", "filters")):
                raise ContractError("derived QA provenance is unresolved")


def prepare_catalog(spec_path: Path, repository: Path):
    """Write a new private schema-3 VQA manifest; never export test QA."""
    repository = repository.resolve()
    spec_path = spec_path.resolve()
    if spec_path.is_relative_to(repository):
        raise ContractError("real catalog conversion specifications must stay outside the source repository")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    producer_commit, producer_dirty = _git_identity(repository)
    _check_spec(spec, producer_commit)
    root = Path(spec["data_root"]).resolve()
    snapshot = Path(spec["catalog_dir"]).resolve()
    output = Path(spec["prepared_dir"]).resolve()
    if (root.is_relative_to(repository) or snapshot.is_relative_to(repository) or output.is_relative_to(repository)
            or not snapshot.is_relative_to(root)
            or not output.is_relative_to(root) or output == root
            or output.is_relative_to(snapshot) or snapshot.is_relative_to(output)):
        raise ContractError("catalog conversion requires separate private snapshot and output directories")
    report_path = snapshot / "report.json"
    report_hash = file_hash(report_path)
    if report_hash != spec.get("catalog_report_sha256"):
        raise ContractError("catalog report checksum differs from the pinned specification")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("schema") != "cxr-local-links-v1" or report.get("status") != "complete"
            or report.get("split_policy") != "mimic-official"
            or report.get("official_patient_overlaps") != 0 or report.get("identity_conflicts") != 0):
        raise ContractError("a completed, identity-audited official-split catalog is required")
    names = ("images.jsonl", "sources.jsonl", "vqa_links.jsonl", "vqa.train.jsonl", "vqa.validation.jsonl")
    fingerprints = {}
    for name in names:
        fingerprints[name] = file_hash(snapshot / name)
        if fingerprints[name] != report.get("output_sha256", {}).get(name):
            raise ContractError("catalog export checksum differs from its report")
    # Audit all official images before any presence or input-view filtering.
    images, audit, sealed = {}, SplitAudit(), set()
    for row in _rows(snapshot / "images.jsonl"):
        if row["dataset"] != "mimic-cxr":
            continue
        key = row["image_id"]
        if key in images:
            raise ContractError("duplicate catalog image identity")
        audit.add("mimic-cxr", row["patient_id"], key,
                  local_file(root, row["path"], must_exist=False), row["source_split"])
        images[key] = row
        if row["source_split"] == "test":
            sealed.add(row["patient_id"])
    link_count = 0
    limits = spec.get("image_limit_per_split")
    candidates = {"train": set(), "validation": set()}
    for row in _rows(snapshot / "vqa_links.jsonl"):
        image = images.get(row["image_id"])
        if image is None or (row["patient_id"], row["study_id"], row["path"], row["resolved_split"]) != (
                image["patient_id"], image["study_id"], image["path"], image["source_split"]):
            raise ContractError("catalog QA link identity mismatch")
        if row["test_sealed"] or row["resolved_split"] == "test" or row["qa_source_split"] == "test":
            sealed.add(row["patient_id"])
        if limits and row["resolved_split"] in candidates and image["present"] == 1 and image["view"] in spec["views"]:
            candidates[row["resolved_split"]].add(row["image_id"])
        link_count += 1
    if link_count != report["qa_rows"] or len(sealed) != report["sealed_test_patients"]:
        raise ContractError("catalog test-seal counts do not reconcile")
    selected = {split: set(sorted(key for key in keys if images[key]["patient_id"] not in sealed)[:limits[split]])
                for split, keys in candidates.items()} if limits else None
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "status.json", {"status": "preparing"})
    try:
        counts = Counter({"input_view": 0})
        if limits:
            counts["pilot_image_limit"] = 0
        if spec.get("empty_answer_policy") == "exclude_from_supervision":
            counts["empty_answers"] = 0
        before, after, verified, seen = 0, 0, {}, set()
        review_counts = Counter()
        manifest_path = output / "manifest.jsonl"
        review_path = output / "qa-review.jsonl"
        with manifest_path.open("w", encoding="utf-8", newline="\n") as stream, review_path.open("w", encoding="utf-8", newline="\n") as review:
            for split in ("train", "validation"):
                actual = 0
                for row in _rows(snapshot / f"vqa.{split}.jsonl"):
                    actual += 1
                    before += 1
                    image = images.get(row["image_id"])
                    if (row.get("schema") != "cxr-local-links-v1" or row.get("dataset") != "mimic-cxr"
                            or row["test_sealed"] != 0 or row["patient_id"] in sealed
                            or row["qa_source_split"] == "test" or image is None
                            or (row["patient_id"], row["study_id"], row["path"], row["split"]) != (
                                image["patient_id"], image["study_id"], image["path"], split)
                            or image["source_split"] != split or image["present"] != 1):
                        raise ContractError("development QA identity or test-seal violation")
                    if image["view"] not in spec["views"]:
                        counts["input_view"] += 1
                        continue
                    if selected is not None and row["image_id"] not in selected[split]:
                        counts["pilot_image_limit"] += 1
                        continue
                    answers = row["answer"]
                    sample_id = f"qa-{row['source_id']}-{row['row_index']}"
                    if sample_id in seen:
                        raise ContractError("duplicate development QA record")
                    seen.add(sample_id)
                    if answers == []:
                        if spec.get("empty_answer_policy") != "exclude_from_supervision":
                            raise ContractError("empty source answers require an explicit supervision exclusion policy")
                        reviewed = review_qa(row["question"], answers, normalization=spec["normalization"])
                        if not isinstance(reviewed["question"], str) or not reviewed["question"].strip():
                            raise ContractError("source QA question is invalid")
                        counts["empty_answers"] += 1
                        flags = [*reviewed["flags"], "excluded_empty_supervision"]
                        review_counts.update(flags)
                        review.write(json.dumps({"sample_id": sample_id, "flags": flags,
                                                 "excluded_from_supervision": True,
                                                 "normalization_changed": reviewed["changed"]}) + "\n")
                        continue
                    processed = process_qa(row["question"], answers, normalization=spec["normalization"])
                    answers = processed.answers
                    if spec["answer_rendering"] == "single" and len(answers) != 1:
                        raise ContractError("single-answer policy encountered multiple source answers")
                    answer = answers[0] if spec["answer_rendering"] == "single" else spec["answer_separator"].join(answers)
                    answer_type = spec["answer_type_values"].get(row[spec["answer_type_field"]])
                    if answer_type is None:
                        raise ContractError("source answer type is absent from the approved taxonomy")
                    path = local_file(root, image["path"])
                    if path not in verified:
                        verified[path] = file_hash(path)
                    if image["expected_sha256"] and verified[path] != image["expected_sha256"]:
                        raise ContractError("image checksum differs from the publisher checksum")
                    template_id = row[spec["template_id_field"]]
                    if not isinstance(template_id, str) or not template_id:
                        raise ContractError("source template ID is missing")
                    flags = [*processed.flags]
                    if template_id not in spec["primary_template_ids"]:
                        flags.append("template_not_selected_for_primary")
                    review_counts.update(flags)
                    review_counts["normalization_changed"] += processed.changed
                    review.write(json.dumps({"sample_id": sample_id, "flags": flags, "normalization_changed": processed.changed}) + "\n")
                    canonical = {"sample_id": sample_id, "patient_id": image["patient_id"],
                                 "patient_namespace": "mimic-cxr", "image_id": row["image_id"],
                                 "image": image["path"], "split": split, "task": "medgemma",
                                 "view": image["view"], "image_sha256": verified[path],
                                 "question": processed.question, "answer": answer, "answer_type": answer_type,
                                 "template_id": template_id, "primary": template_id in spec["primary_template_ids"]}
                    stream.write(json.dumps(canonical, ensure_ascii=False, sort_keys=True) + "\n")
                    after += 1
                    if after % 1000 == 0:
                        write_json(output / "status.json", {"status": "preparing", "phase": "image_checksums",
                            "rows_read": before, "rows_written": after, "images_hashed": len(verified),
                            "exclusions": dict(counts)})
                if actual != report["development_rows"][split]:
                    raise ContractError("catalog development counts do not reconcile")
        sources = [{"kind": kind, **metadata, "sha256": fingerprints[
            "images.jsonl" if kind == "images" else "vqa_links.jsonl"]} for kind, metadata in spec["sources"].items()]
        reasons = {"input_view": "outside the explicitly selected input views",
                   "pilot_image_limit": "smoke-only image budget; lexical image-ID selection independent of answers",
                   "empty_answers": "source answer set is empty; retained in catalog/QC, excluded from supervised targets"}
        stages, remaining = [], before
        for stage, excluded in counts.items():
            stages.append({"stage": stage, "reason": reasons[stage], "rows_before": remaining,
                           "excluded": excluded, "rows_after": remaining - excluded})
            remaining -= excluded
        modules = [Path(__file__), Path(__file__).with_name("manifest.py"),
                   Path(__file__).parent.parent / "preprocessing/labels.py"]
        provenance = {"schema_version": 3, "synthetic": False, "sources": sources,
                      "created_at": datetime.now(timezone.utc).isoformat(), "producer_commit": producer_commit,
                      "producer_dirty": producer_dirty,
                      "producer_modules_sha256": {p.name: file_hash(p) for p in modules},
                      "split_policy": "mimic-official; union of official and source-VQA test patients sealed",
                      "rows_before": before, "rows_after": after, "exclusions": dict(counts),
                      "exclusion_stages": stages,
                      "manifest_sha256": file_hash(manifest_path), "preparation_spec": spec,
                      "preparation_spec_sha256": file_hash(spec_path), "catalog_report_sha256": report_hash,
                      "catalog_exports_sha256": fingerprints, "catalog_audit": report,
                      "text_processing": {"normalization": spec["normalization"], "cue_version": CUE_VERSION,
                                          "review_policy": "retain_and_flag", "counts": dict(review_counts),
                                          "review_sha256": file_hash(review_path)},
                      "annotation_role": "catalog references only; no finding-grounding targets generated"}
        provenance_path = output / "provenance.json"
        write_json(provenance_path, provenance)
        write_json(output / "status.json", {"status": "preparing", "phase": "canonical_validation",
            "rows_read": before, "rows_written": after, "images_hashed": len(verified), "exclusions": dict(counts)})
        manifest = load_manifest(DataConfig(str(root), str(manifest_path), str(provenance_path)))
        result = manifest.summary()
        write_json(output / "status.json", {"status": "complete", **result})
        return result
    except BaseException as error:
        write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__})
        raise
