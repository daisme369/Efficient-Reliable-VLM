"""Prepare checksum-bound binary lung targets from private catalog references."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path, PurePosixPath
import subprocess

from cxr_research.data.manifest import load_manifest, local_file
from cxr_research.data.source_refs import read_csv_reference
from cxr_research.experiments.config import DataConfig
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def _resolved(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() != "UNRESOLVED"


def _rows(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def prepare_unet_catalog(spec_path: Path, repository: Path):
    """Audit the full image population and seal before choosing any lung target."""
    repository, spec_path = repository.resolve(), spec_path.resolve()
    if spec_path.is_relative_to(repository):
        raise ContractError("segmentation specifications must stay outside the source checkout")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root, snapshot, output = (Path(spec[k]).resolve() for k in ("data_root", "catalog_dir", "prepared_dir"))
    if (any(p.is_relative_to(repository) for p in (root, snapshot, output))
            or not snapshot.is_relative_to(root) or not output.is_relative_to(root / "prepared")
            or output == root / "prepared" or output.is_relative_to(snapshot) or snapshot.is_relative_to(output)):
        raise ContractError("segmentation requires separate private catalog and prepared storage")
    if (spec.get("target") != "binary_lung_union" or spec.get("mask_coordinates") != "original_resolution"
            or spec.get("split_policy") != "mimic-official"
            or spec.get("purpose") not in {"smoke_only", "training"}
            or any(not _resolved(spec.get(k)) for k in ("access_review", "segmentation_training_decision", "quality_policy"))):
        raise ContractError("explicit binary-lung segmentation policies are required")
    threshold, views = spec.get("minimum_rca"), spec.get("views")
    if (type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1
            or not isinstance(views, list) or not views or len(set(views)) != len(views)
            or any(v not in {"AP", "PA"} for v in views)):
        raise ContractError("explicit RCA threshold and frontal AP/PA views are required")
    limits = spec.get("image_limit_per_split")
    if ((spec["purpose"] == "smoke_only" and (not isinstance(limits, dict) or set(limits) != {"train", "validation"}
            or any(type(n) is not int or n <= 0 for n in limits.values())))
            or (spec["purpose"] == "training" and limits is not None)):
        raise ContractError("image limits require smoke-only purpose and positive train/validation budgets")
    metadata = spec.get("sources", {})
    for kind, name, version in (("images", "MIMIC-CXR-JPG", "2.1.0"), ("masks", "CheXmask", "1.0.0")):
        source = metadata.get(kind, {})
        if (source.get("name") != name or source.get("version") != version
                or any(not _resolved(source.get(k)) for k in ("access", "license"))):
            raise ContractError("source versions, access and licenses are unresolved")
    report_hash = file_hash(snapshot / "report.json")
    report = json.loads((snapshot / "report.json").read_text())
    if (report_hash != spec.get("catalog_report_sha256") or report.get("schema") != "cxr-local-links-v1"
            or report.get("status") != "complete" or report.get("split_policy") != "mimic-official"
            or report.get("official_patient_overlaps") != 0 or report.get("identity_conflicts") != 0):
        raise ContractError("a pinned, audited official-split catalog is required")
    fingerprints = {}
    for name in ("images.jsonl", "sources.jsonl", "vqa_links.jsonl"):
        fingerprints[name] = file_hash(snapshot / name)
        if fingerprints[name] != report.get("output_sha256", {}).get(name):
            raise ContractError("catalog export checksum differs from report")
    sources = {s["path"]: s for s in _rows(snapshot / "sources.jsonl")}
    images, patients, paths, sealed = {}, {}, {}, set()
    for row in _rows(snapshot / "images.jsonl"):
        if row["dataset"] != "mimic-cxr":
            continue
        key = row["image_id"]
        if key in images:
            raise ContractError("duplicate catalog image identity")
        patient, split, relative = row["patient_id"], row["source_split"], row["path"]
        if (not all(_resolved(v) for v in (key, patient, row["study_id"], relative))
                or split not in {"train", "validation", "test"}):
            raise ContractError("invalid catalog image identity")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            raise ContractError("catalog image path escapes private storage")
        if patients.setdefault(patient, split) != split:
            raise ContractError("patient split overlap detected")
        if paths.setdefault(path.as_posix(), (patient, split)) != (patient, split):
            raise ContractError("catalog image path conflicts across patients or splits")
        images[key] = row
        if row["source_split"] == "test":
            sealed.add(row["patient_id"])
    if len(images) != report["datasets"]["mimic-cxr"]["indexed_images"]:
        raise ContractError("catalog image counts do not reconcile")
    links = 0
    # Crosswalk contains identity/split metadata only, never QA payload or answers.
    for row in _rows(snapshot / "vqa_links.jsonl"):
        image = images.get(row["image_id"])
        if image is None or (row["patient_id"], row["study_id"], row["path"], row["resolved_split"]) != (
                image["patient_id"], image["study_id"], image["path"], image["source_split"]):
            raise ContractError("crosswalk identity differs from official images")
        if row["test_sealed"] or row["resolved_split"] == "test" or row["qa_source_split"] == "test":
            sealed.add(row["patient_id"])
        links += 1
    if links != report["qa_rows"] or len(sealed) != report["sealed_test_patients"]:
        raise ContractError("catalog union test seal does not reconcile")
    counts = Counter({key: 0 for key in ("test_sealed", "missing_image", "input_view", "missing_mask", "mask_quality", "pilot_image_limit")})
    candidates = {"train": [], "validation": []}
    for key in sorted(images):
        row = images[key]
        if row["patient_id"] in sealed or row["source_split"] == "test":
            counts["test_sealed"] += 1
        elif row["source_split"] not in candidates:
            raise ContractError("unsupported official development split")
        elif row["present"] != 1:
            counts["missing_image"] += 1
        elif row["view"] not in views:
            counts["input_view"] += 1
        elif not row["anatomical_mask"]:
            counts["missing_mask"] += 1
        else:
            quality = row["anatomical_mask"]["quality"]
            if type(quality) not in (int, float) or not math.isfinite(quality) or not 0 <= quality <= 1:
                raise ContractError("invalid catalog mask quality")
            if quality < threshold:
                counts["mask_quality"] += 1
            else:
                candidates[row["source_split"]].append(row)
    selected = []
    for split, rows in candidates.items():
        chosen = rows[:limits[split]] if limits else rows
        if not chosen:
            raise ContractError("selected segmentation train/validation population is empty")
        counts["pilot_image_limit"] += len(rows) - len(chosen)
        selected.extend(chosen)
    if output.exists():
        raise ContractError("prepared directory already exists; choose a new output")
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    if git.returncode or dirty.returncode:
        raise ContractError("source Git provenance is unavailable")
    used_sources = {}
    for relative in sorted({r["anatomical_mask"]["path"] for r in selected}):
        source = sources.get(relative)
        if source is None:
            raise ContractError("mask source reference is absent from catalog")
        path = local_file(root, relative)
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (source["bytes"], source["mtime_ns"]) or file_hash(path) != source["sha256"]:
            raise ContractError("mask source changed; rebuild catalog references")
        if source.get("license_path") and file_hash(local_file(root, source["license_path"])) != source["license_sha256"]:
            raise ContractError("source license fingerprint differs")
        used_sources[relative] = source
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "status.json", {"status": "preparing", "phase": "decode_masks"})
    try:
        import numpy as np
        from PIL import Image
        from cxr_research.preprocessing.masks import decode_rle
        (output / "masks").mkdir()
        mask_stats = Counter()
        with (output / "manifest.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for index, row in enumerate(selected):
                ref = row["anatomical_mask"]
                record = read_csv_reference(root, ref)
                if record["dicom_id"] != row["image_id"]:
                    raise ContractError("mask reference image identity mismatch")
                height, width, quality = int(record["Height"]), int(record["Width"]), float(record["Dice RCA (Mean)"])
                if (height, width, quality) != (ref["height"], ref["width"], ref["quality"]):
                    raise ContractError("mask reference metadata differs from catalog")
                path = local_file(root, row["path"])
                if path.stat().st_size != row["actual_bytes"]:
                    raise ContractError("selected image size differs from catalog")
                image_hash = file_hash(path)
                if row["expected_sha256"] and image_hash != row["expected_sha256"]:
                    raise ContractError("selected image publisher checksum differs")
                with Image.open(path) as image:
                    image.load()
                    if image.mode not in {"L", "RGB"} or image.size != (width, height):
                        raise ContractError("source image mode or mask dimensions differ")
                left = decode_rle(record["Left Lung"], height, width)
                right = decode_rle(record["Right Lung"], height, width)
                if not np.any(left) or not np.any(right):
                    raise ContractError("empty lung mask requires source review")
                mask_stats["images_with_lung_overlap"] += bool(np.any((left > 0) & (right > 0)))
                mask = output / "masks" / f"mask-{index:08d}.png"
                Image.fromarray(left | right).save(mask)
                canonical = {"sample_id": f"unet-{row['image_id']}", "patient_id": row["patient_id"],
                             "patient_namespace": "mimic-cxr", "image_id": row["image_id"], "image": row["path"],
                             "split": row["source_split"], "task": "unet", "view": row["view"], "image_sha256": image_hash,
                             "mask": mask.relative_to(root).as_posix(), "mask_sha256": file_hash(mask), "primary": False}
                stream.write(json.dumps(canonical, sort_keys=True) + "\n")
                if (index + 1) % 100 == 0:
                    write_json(output / "status.json", {"status": "preparing", "masks_written": index + 1})
        for relative, source in used_sources.items():
            stat = local_file(root, relative).stat()
            if (stat.st_size, stat.st_mtime_ns) != (source["bytes"], source["mtime_ns"]):
                raise ContractError("mask source changed during preparation")
        remaining, stages = len(images), []
        reasons = {"test_sealed": "official or source-VQA test patient; no mask payload decoded",
                   "missing_image": "catalog image absent or inventory size mismatch", "input_view": "outside explicit AP/PA views",
                   "missing_mask": "no original-resolution CheXmask reference", "mask_quality": "below explicit Dice RCA Mean threshold",
                   "pilot_image_limit": "smoke-only lexical image-ID budget after source metadata eligibility"}
        for stage, count in counts.items():
            stages.append({"stage": stage, "reason": reasons[stage], "rows_before": remaining, "excluded": count,
                           "rows_after": remaining - count})
            remaining -= count
        provenance = {"schema_version": 3, "synthetic": False,
            "sources": [{"kind": "images", **metadata["images"], "sha256": fingerprints["images.jsonl"]},
                        *[{"kind": "masks", **metadata["masks"], "sha256": s["sha256"], "path": p,
                           "license_sha256": s.get("license_sha256")} for p, s in used_sources.items()]],
            "created_at": datetime.now(timezone.utc).isoformat(), "producer_commit": git.stdout.strip(),
            "producer_dirty": bool(dirty.stdout.strip()), "producer_modules_sha256": {
                p.name: file_hash(p) for p in (Path(__file__), Path(__file__).with_name("source_refs.py"),
                                             Path(__file__).parent.parent / "preprocessing/masks.py")},
            "split_policy": "mimic-official; union of official and source-VQA test patients sealed",
            "rows_before": len(images), "rows_after": remaining, "exclusions": dict(counts), "exclusion_stages": stages,
            "manifest_sha256": file_hash(output / "manifest.jsonl"), "catalog_report_sha256": report_hash,
            "catalog_exports_sha256": fingerprints, "preparation_spec": spec, "preparation_spec_sha256": file_hash(spec_path),
            "segmentation": {"target": "binary_lung_union", "labels": "anatomical pseudo-labels; not lesion ground truth",
                             "heart_used": False, "selection": "lexical image IDs, independent of model outputs",
                             "quality_summary": dict(mask_stats)}, "test_qa_payload_read": False, "test_masks_decoded": False}
        write_json(output / "provenance.json", provenance)
        manifest = load_manifest(DataConfig(str(root), str(output / "manifest.jsonl"), str(output / "provenance.json")))
        manifest.select("unet", "train")
        manifest.select("unet", "validation")
        result = {**manifest.summary(), "purpose": spec["purpose"], "exclusions": dict(counts),
                  "test_masks_decoded": False, "training_ready": False}
        write_json(output / "status.json", {"status": "complete", **result})
        return result
    except BaseException as error:
        write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__})
        raise
