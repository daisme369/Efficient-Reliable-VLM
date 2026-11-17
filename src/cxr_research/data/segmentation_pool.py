"""Build a patient-isolated MIMIC/NIH manifest with immutable RLE references."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess

from cxr_research.data.manifest import DataConfig, load_manifest, local_file
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json
from cxr_research.preprocessing.source_images import check_source_mode


def nih_split(patient: str, seed: int, validation_fraction: float) -> str:
    value = int.from_bytes(hashlib.sha256(f"{seed}:nih-chestxray14:{patient}".encode()).digest(), "big")
    return "validation" if value / 2**256 < validation_fraction else "train"


def prepare_pool(spec_path: Path, repository: Path):
    spec_path, repository = spec_path.resolve(), repository.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root, catalog, output = (Path(spec[k]).resolve() for k in ("data_root", "catalog_dir", "prepared_dir"))
    if (any(p.is_relative_to(repository) for p in (spec_path, root, catalog, output))
            or not catalog.is_relative_to(root) or not output.is_relative_to(root / "prepared")
            or output == root / "prepared" or output.is_relative_to(catalog) or catalog.is_relative_to(output)):
        raise ContractError("pooled preparation requires separate private storage")
    if output.exists():
        raise ContractError("pooled preparation output already exists")
    if (spec.get("datasets") != ["mimic-cxr", "nih-chestxray14"]
            or spec.get("purpose") != "training" or spec.get("image_limit_per_split") is not None
            or spec.get("target") != "binary_lung_union" or spec.get("mask_coordinates") != "original_resolution"
            or spec.get("views") != ["AP", "PA"]
            or spec.get("split_policy") != "mimic-official+nih-patient-hash-v1"):
        raise ContractError("explicit two-source full-population lung protocol is required")
    for key in ("access_review", "segmentation_training_decision", "quality_policy"):
        if not isinstance(spec.get(key), str) or not spec[key].strip() or spec[key].strip().upper() == "UNRESOLVED":
            raise ContractError("pooled source decisions are unresolved")
    fraction, seed, rca = (spec.get(k) for k in ("nih_validation_fraction", "nih_split_seed", "minimum_rca"))
    if (type(seed) is not int or seed < 0 or type(fraction) not in (float, int)
            or not 0 < fraction < 1 or not math.isfinite(fraction)
            or type(rca) not in (float, int) or not 0 <= rca <= 1 or not math.isfinite(rca)):
        raise ContractError("invalid split or RCA settings")
    sources_meta = spec.get("sources", {})
    hash_policy = spec.get("publisher_hash_policy", "verify_during_preparation")
    if hash_policy not in {"verify_during_preparation", "verify_at_train_boundary"}:
        raise ContractError("invalid publisher hash verification policy")
    for key, expected in (("images", ("MIMIC-CXR-JPG", "2.1.0")),
                          ("nih_images", ("NIH ChestX-ray14", "local-release")),
                          ("masks", ("CheXmask", "1.0.0"))):
        value = sources_meta.get(key, {})
        if ((value.get("name"), value.get("version")) != expected
                or any(not isinstance(value.get(k), str) or not value[k].strip()
                       or value[k].strip().upper() == "UNRESOLVED" for k in ("access", "license"))):
            raise ContractError("source versions, access and licenses must be recorded")
    report = json.loads((catalog / "report.json").read_text())
    if (file_hash(catalog / "report.json") != spec["catalog_report_sha256"] or report.get("status") != "complete"
            or report.get("split_policy") != "mimic-official" or report.get("official_patient_overlaps") != 0
            or report.get("identity_conflicts") != 0):
        raise ContractError("a pinned patient-audited catalog is required")
    for name in ("images.jsonl", "sources.jsonl", "vqa_links.jsonl"):
        if file_hash(catalog / name) != report["output_sha256"][name]:
            raise ContractError("catalog export checksum mismatch")
    def rows(name):
        with (catalog / name).open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)
    images, patients, paths, sealed = {}, {}, {}, set()
    indexed = Counter()
    for row in rows("images.jsonl"):
        dataset = row["dataset"]
        if dataset not in spec["datasets"]:
            continue
        patient, key, split, relative = row["patient_id"], (dataset, row["image_id"]), row["source_split"], row["path"]
        allowed = {"train", "validation", "test"} if dataset == "mimic-cxr" else {"train_val", "test"}
        if (not isinstance(patient, str) or not patient.strip() or split not in allowed or key in images):
            raise ContractError("missing patient identity or invalid source membership")
        if patients.setdefault((dataset, patient), split) != split:
            raise ContractError("patient split overlap detected")
        if (PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
                or "\\" in relative or ":" in relative):
            raise ContractError("catalog image path escapes private storage")
        if paths.setdefault(relative, (dataset, patient, split)) != (dataset, patient, split):
            raise ContractError("image path conflicts across patients or splits")
        images[key] = row
        indexed[dataset] += 1
        if dataset == "mimic-cxr" and split == "test":
            sealed.add(patient)
    for dataset in spec["datasets"]:
        if indexed[dataset] != report["datasets"][dataset]["indexed_images"]:
            raise ContractError("pooled image population does not reconcile")
    links = 0
    for row in rows("vqa_links.jsonl"):
        image = images.get(("mimic-cxr", row["image_id"]))
        if image is None or (row["patient_id"], row["study_id"], row["path"], row["resolved_split"]) != (
                image["patient_id"], image["study_id"], image["path"], image["source_split"]):
            raise ContractError("crosswalk identity differs from official source")
        if row["test_sealed"] or row["resolved_split"] == "test" or row["qa_source_split"] == "test":
            sealed.add(row["patient_id"])
        links += 1
    if links != report["qa_rows"] or len(sealed) != report["sealed_test_patients"]:
        raise ContractError("union test seal does not reconcile")
    excluded = Counter({k: 0 for k in ("test_sealed", "missing_image", "input_view", "missing_mask", "mask_quality")})
    selected = []
    for key in sorted(images):
        row = images[key]
        if row["source_split"] == "test" or (key[0] == "mimic-cxr" and row["patient_id"] in sealed):
            excluded["test_sealed"] += 1
        elif row["present"] != 1:
            excluded["missing_image"] += 1
        elif row["view"] not in {"AP", "PA"}:
            excluded["input_view"] += 1
        elif not row["anatomical_mask"]:
            excluded["missing_mask"] += 1
        elif not isinstance(row["anatomical_mask"]["quality"], (float, int)) or not math.isfinite(row["anatomical_mask"]["quality"]):
            raise ContractError("invalid source RCA")
        elif row["anatomical_mask"]["quality"] < rca:
            excluded["mask_quality"] += 1
        else:
            selected.append(row)
    source_rows = {r["path"]: r for r in rows("sources.jsonl")}
    used = {}
    for path in sorted({r["anatomical_mask"]["path"] for r in selected}):
        source = source_rows[path]
        asset = local_file(root, path)
        stat = asset.stat()
        if ((stat.st_size, stat.st_mtime_ns) != (source["bytes"], source["mtime_ns"])
                or file_hash(asset) != source["sha256"]):
            raise ContractError("mask source changed; rebuild catalog references")
        if source.get("license_path") and file_hash(local_file(root, source["license_path"])) != source["license_sha256"]:
            raise ContractError("mask license fingerprint differs")
        used[path] = source
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
    if git.returncode or dirty.returncode:
        raise ContractError("source Git provenance unavailable")
    output.mkdir(parents=True)
    write_json(output / "status.json", {"status": "preparing", "phase": "assembling_manifest", "total": len(selected)})
    def canonical(row):
        from PIL import Image
        path = local_file(root, row["path"])
        if path.stat().st_size != row["actual_bytes"]:
            raise ContractError("selected image size differs from catalog")
        deferred = hash_policy == "verify_at_train_boundary" and bool(row["expected_sha256"])
        sha = row["expected_sha256"] if deferred else file_hash(path)
        if not deferred and row["expected_sha256"] and sha != row["expected_sha256"]:
            raise ContractError("selected image publisher checksum differs")
        ref = row["anatomical_mask"]
        if not deferred:
            with Image.open(path) as image:
                check_source_mode(image)
                if image.size != (ref["width"], ref["height"]):
                    raise ContractError("source image and mask dimensions differ")
        dataset = row["dataset"]
        split = row["source_split"] if dataset == "mimic-cxr" else nih_split(row["patient_id"], seed, fraction)
        return {"sample_id": f"unet-{dataset}-{row['image_id']}", "patient_id": row["patient_id"],
            "patient_namespace": dataset, "image_id": row["image_id"], "image": row["path"],
            "split": split, "task": "unet", "view": row["view"], "primary": False,
            "image_sha256": sha, "mask": ref["path"], "mask_sha256": used[ref["path"]]["sha256"],
            "mask_format": "chexmask_rle", "mask_reference": {k: ref[k] for k in ("byte_offset", "byte_length", "height", "width")}}
    try:
        counts, hashes = Counter(), {}
        with ThreadPoolExecutor(max_workers=8) as pool, (output / "manifest.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            converted = (sample for start in range(0, len(selected), 64)
                         for sample in pool.map(canonical, selected[start:start + 64]))
            for index, sample in enumerate(converted):
                if hashes.setdefault(sample["image_sha256"], sample["split"]) != sample["split"]:
                    raise ContractError("duplicate image bytes cross development splits")
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
                counts[f"{sample['patient_namespace']}:{sample['split']}"] += 1
                if (index + 1) % 1000 == 0:
                    write_json(output / "status.json", {"status": "preparing", "phase": "assembling_manifest",
                        "images_recorded": index + 1, "total": len(selected)})
        remaining, stages = len(images), []
        for name, count in excluded.items():
            stages.append({"stage": name, "reason": name.replace("_", " "), "rows_before": remaining,
                "excluded": count, "rows_after": remaining - count})
            remaining -= count
        provenance = {"schema_version": 3, "synthetic": False,
            "sources": [{"kind": "images", **sources_meta[k], "sha256": report["output_sha256"]["images.jsonl"]}
                for k in ("images", "nih_images")] + [{"kind": "masks", **sources_meta["masks"],
                "sha256": source["sha256"], "path": path} for path, source in used.items()],
            "created_at": datetime.now(timezone.utc).isoformat(), "producer_commit": git.stdout.strip(),
            "producer_dirty": bool(dirty.stdout.strip()), "producer_module_sha256": file_hash(Path(__file__)),
            "split_policy": spec["split_policy"], "rows_before": len(images), "rows_after": remaining,
            "exclusions": dict(excluded), "exclusion_stages": stages, "manifest_sha256": file_hash(output / "manifest.jsonl"),
            "preparation_spec": spec, "preparation_spec_sha256": file_hash(spec_path),
            "segmentation": {"target": "binary_lung_union", "labels": "anatomical pseudo-labels; not lesion ground truth",
                "mask_storage": "immutable original-resolution CheXmask CSV references; decoded on demand", "heart_used": False},
            "counts_by_dataset_split": dict(counts), "test_masks_decoded": False, "test_qa_payload_read": False}
        provenance["image_integrity"] = {"publisher_hash_policy": hash_policy,
            "training_gate": "canonical files loader verifies all image and CSV bytes before GPU/model startup",
            "alpha_policy": "discard fully opaque alpha only; non-opaque RGBA stops"}
        write_json(output / "provenance.json", provenance)
        manifest = load_manifest(DataConfig(str(root), str(output / "manifest.jsonl"), str(output / "provenance.json")), verify_files=False)
        manifest.select("unet", "train")
        manifest.select("unet", "validation")
        result = {"status": "complete", **manifest.summary(), "counts_by_dataset_split": dict(counts),
            "test_masks_decoded": False, "training_ready": False}
        write_json(output / "status.json", result)
        return result
    except BaseException as error:
        write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__})
        raise
