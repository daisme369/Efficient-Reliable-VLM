"""Join mapped sources into a versioned local dataset."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from cxr_research.data.manifest import load_manifest, local_file
from cxr_research.data.mapping import table_rows
from cxr_research.data.splits import SplitAudit
from cxr_research.experiments.config import DataConfig
from cxr_research.preprocessing.masks import decode_rle
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def prepare(spec_path: Path, repository: Path):
    repository = repository.resolve()
    if spec_path.resolve().is_relative_to(repository):
        raise ContractError("real preparation specifications must stay outside the source repository")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root, destination = Path(spec["image_root"]).resolve(), Path(spec["prepared_dir"]).resolve()
    if root.is_relative_to(repository) or not destination.is_relative_to(root):
        raise ContractError("real preparation requires external storage with prepared_dir inside image_root")
    for key in ("patient_namespace", "split_policy", "uncertain_label_policy", "input_policy",
                "template_policy", "access_review", "producer_commit"):
        if not spec.get(key) or spec[key] == "UNRESOLVED":
            raise ContractError("data preparation decisions are unresolved")
    if spec.get("mask_coordinates") != "original_resolution":
        raise ContractError("only original-resolution CheXmask is supported")
    for kind in ("images", "qa", "masks"):
        table = spec.get(kind)
        if not table:
            continue
        source_path = Path(table.get("path", ""))
        if not source_path.is_absolute() or source_path.resolve().is_relative_to(repository):
            raise ContractError("real source tables must use absolute paths outside the source repository")
        for key in ("name", "version", "access", "license"):
            value = table.get(key)
            if not isinstance(value, str) or not value.strip() or value.strip().upper() == "UNRESOLVED":
                raise ContractError("source access and license metadata are unresolved")
    if spec.get("qa"):
        if ("split" not in spec["qa"].get("columns", {})
                or not isinstance(spec.get("qa_split_values"), dict)
                or not spec["qa_split_values"]):
            raise ContractError("derived QA data requires an explicit split mapping")
        derivation = spec["qa"].get("derivation")
        keys = ("producer", "method", "source_report_section", "filters")
        if (not isinstance(derivation, dict)
                or any(not isinstance(derivation.get(k), str) or not derivation[k].strip()
                       or derivation[k].strip().upper() == "UNRESOLVED" for k in keys)):
            raise ContractError("derived QA provenance is unresolved")
    from PIL import Image
    import numpy as np
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "status.json", {"status": "preparing"})
    try:
        images, image_hashes = {}, {}
        split_audit = SplitAudit()
        for row in table_rows(spec["images"]):
            key = str(row["image_id"])
            if key in images:
                raise ContractError("duplicate image join key")
            row["split"] = spec["split_values"][row["split"]]
            image_path = local_file(root, row["image"])
            split_audit.add(spec["patient_namespace"], str(row["patient_id"]), key,
                            image_path, row["split"])
            images[key] = row
        rows, counts = [], {"qa_missing_image": 0, "qa_test_sealed": 0,
                            "mask_missing_image": 0, "mask_quality": 0, "mask_test_sealed": 0}
        def common(image, task, index):
            path = local_file(root, image["image"])
            if path not in image_hashes:
                image_hashes[path] = file_hash(path)
            return {"sample_id": f"{task}-{index}", "patient_id": str(image["patient_id"]),
                    "patient_namespace": spec["patient_namespace"], "image_id": str(image["image_id"]),
                    "image": image["image"], "split": image["split"], "view": image["view"], "task": task,
                    "image_sha256": image_hashes[path]}
        total = 0
        if spec.get("qa"):
            for index, row in enumerate(table_rows(spec["qa"])):
                total += 1
                image = images.get(str(row["image_id"]))
                if image is None:
                    counts["qa_missing_image"] += 1
                    continue
                # Reconcile source splits before applying any filtering.
                try:
                    qa_split = spec["qa_split_values"][row["split"]]
                except KeyError:
                    raise ContractError("VQA source split is missing from the explicit mapping") from None
                if qa_split != image["split"]:
                    raise ContractError("VQA source split conflicts with the image split")
                if image["split"] == "test":
                    counts["qa_test_sealed"] += 1
                    continue
                rows.append({**common(image, "medgemma", index), "question": row["question"],
                             "answer": row["answer"], "answer_type": spec["answer_type_values"][row["answer_type"]],
                             "template_id": str(row["template_id"]),
                             "primary": str(row["template_id"]) in spec["primary_template_ids"]})
        masks_seen = set()
        if spec.get("masks"):
            (destination / "masks").mkdir()
            threshold = spec["minimum_rca"]
            if threshold is not None and not 0 <= threshold <= 1:
                raise ContractError("invalid mask quality threshold")
            for index, row in enumerate(table_rows(spec["masks"])):
                total += 1
                image_id = str(row["image_id"])
                if image_id in masks_seen:
                    raise ContractError("duplicate mask join key")
                masks_seen.add(image_id)
                image = images.get(image_id)
                if image is None:
                    counts["mask_missing_image"] += 1
                    continue
                if image["split"] == "test":
                    counts["mask_test_sealed"] += 1
                    continue
                quality = float(row["rca"])
                if not np.isfinite(quality) or not 0 <= quality <= 1:
                    raise ContractError("invalid RCA value")
                if threshold is not None and quality < threshold:
                    counts["mask_quality"] += 1
                    continue
                height, width = int(row["height"]), int(row["width"])
                with Image.open(local_file(root, image["image"])) as original:
                    if original.size != (width, height):
                        raise ContractError("mask coordinates do not match source image dimensions")
                mask = decode_rle(row["left_lung"], height, width) | decode_rle(row["right_lung"], height, width)
                mask_path = destination / "masks" / f"mask-{index:08d}.png"
                Image.fromarray(mask).save(mask_path)
                rows.append({**common(image, "unet", index), "mask": mask_path.relative_to(root).as_posix(),
                             "mask_sha256": file_hash(mask_path)})
        manifest_path = destination / "manifest.jsonl"
        with manifest_path.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        sources = []
        for key in ("images", "qa", "masks"):
            if spec.get(key):
                table = spec[key]
                source = {"kind": key, "name": table["name"], "version": table["version"],
                          "access": table["access"], "license": table["license"],
                          "sha256": file_hash(Path(table["path"]))}
                if key == "qa":
                    source["derivation"] = table["derivation"]
                sources.append(source)
        reasons = {
            "qa_missing_image": "QA row did not match the selected image index",
            "qa_test_sealed": "Final-test QA payload is excluded from the development manifest",
            "mask_missing_image": "Mask row did not match the selected image index",
            "mask_quality": "Mask row did not meet the approved quality threshold",
            "mask_test_sealed": "Final-test mask payload is excluded from the development manifest",
        }
        remaining, stages = total, []
        for stage, excluded in counts.items():
            stages.append({"stage": stage, "reason": reasons[stage], "rows_before": remaining,
                           "excluded": excluded, "rows_after": remaining - excluded})
            remaining -= excluded
        if remaining != len(rows):
            raise ContractError("preparation exclusion counts do not reconcile")
        provenance = {"schema_version": 3, "synthetic": False, "sources": sources,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "producer_commit": spec["producer_commit"], "exclusions": counts,
                      "exclusion_stages": stages,
                      "rows_before": total, "rows_after": len(rows), "split_policy": spec["split_policy"],
                      "manifest_sha256": file_hash(manifest_path), "preparation_spec": spec,
                      "preparation_spec_sha256": file_hash(spec_path)}
        provenance_path = destination / "provenance.json"
        write_json(provenance_path, provenance)
        audited = load_manifest(DataConfig(str(root), str(manifest_path), str(provenance_path)))
        write_json(destination / "status.json", {"status": "complete", **audited.summary()})
        return audited.summary()
    except BaseException as error:
        write_json(destination / "status.json", {"status": "failed", "error_type": type(error).__name__})
        raise
