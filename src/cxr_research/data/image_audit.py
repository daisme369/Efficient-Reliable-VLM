"""Bounded image, original mask and scene-graph QC with private resume records."""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import zipfile

from cxr_research.data.manifest import local_file
from cxr_research.data.source_refs import read_csv_reference
from cxr_research.preprocessing.annotations import scene_regions
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


def inspect_image(path, expected, *, voi_policy):
    from PIL import Image
    if path.suffix.lower() in {".dcm", ".dicom"}:
        from cxr_research.preprocessing.dicom import decode_dicom
        pixels, metadata = decode_dicom(path, voi_policy=voi_policy)
        image = Image.fromarray(pixels)
    else:
        with Image.open(path) as raw:
            raw.verify()
        with Image.open(path) as raw:
            raw.load()
            metadata = {"width": raw.width, "height": raw.height, "mode": raw.mode, "format": raw.format}
            if raw.mode == "RGBA" and raw.getchannel("A").getextrema() == (255, 255):
                metadata["alpha_policy"] = "discard_fully_opaque_alpha_only"
            elif raw.mode not in {"L", "RGB"}:
                raise ContractError("source image requires an explicit bit-depth conversion")
            image = raw.convert("L")
    sha = file_hash(path)
    if expected and sha.lower() != expected.lower():
        raise ContractError("image publisher checksum mismatch")
    thumb = list(image.resize((9, 8), Image.Resampling.BILINEAR).get_flattened_data())
    difference = sum(int(thumb[y*9+x] > thumb[y*9+x+1]) << (y*8+x) for y in range(8) for x in range(8))
    metadata.update(sha256=sha, dhash=f"{difference:016x}", constant_image=image.getextrema()[0] == image.getextrema()[1])
    return metadata


def inspect_mask(row, width, height):
    import numpy as np
    from cxr_research.preprocessing.masks import decode_rle
    if (int(row["Width"]), int(row["Height"])) != (width, height):
        raise ContractError("mask dimensions differ from decoded image")
    quality = float(row["Dice RCA (Mean)"])
    if not np.isfinite(quality) or not 0 <= quality <= 1:
        raise ContractError("invalid RCA value")
    areas = {}
    masks = {}
    for organ in ("Left Lung", "Right Lung", "Heart"):
        mask = decode_rle(row[organ], height, width)
        areas[organ] = int(np.count_nonzero(mask))
        masks[organ] = mask > 0
    return {"rca": quality, "areas": areas, "empty_organs": sum(area == 0 for area in areas.values()),
            "lung_overlap_pixels": int(np.count_nonzero(masks["Left Lung"] & masks["Right Lung"]))}


def audit_images(root, catalog, output, *, datasets, image_limit, voi_policy):
    data = Path(__file__).parent
    preprocessing = data.parent / "preprocessing"
    processor_hashes = {str(p.relative_to(data.parent)): file_hash(p) for p in
                        (Path(__file__), data / "source_refs.py", preprocessing / "annotations.py",
                         preprocessing / "dicom.py", preprocessing / "masks.py")}
    db = sqlite3.connect(output / "image-audit.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS checks(dataset TEXT,image_id TEXT,result TEXT,PRIMARY KEY(dataset,image_id))")
    try:
        with ExitStack() as stack:
            archives = {}
            for dataset in datasets:
                rows = catalog.execute("SELECT * FROM image_links WHERE dataset=? ORDER BY image_id LIMIT ?", (dataset, image_limit))
                for values in rows:
                    row = dict(zip([d[0] for d in rows.description], values))
                    path = local_file(root, row["path"], must_exist=False)
                    try:
                        stat = path.stat()
                        asset_stat = [stat.st_size, stat.st_mtime_ns]
                    except FileNotFoundError:
                        asset_stat = None
                    previous = db.execute("SELECT result FROM checks WHERE dataset=? AND image_id=?", (dataset, row["image_id"])).fetchone()
                    if (previous and json.loads(previous[0]).get("asset_stat") == asset_stat
                            and json.loads(previous[0]).get("processor_sha256") == processor_hashes):
                        continue
                    result = {"status": "complete", "mask_available": bool(row["mask_source_path"]),
                              "graph_available": bool(row["graph_archive"]), "asset_stat": asset_stat,
                              "processor_sha256": processor_hashes}
                    try:
                        metadata = inspect_image(local_file(root, row["path"]), row["expected_sha256"], voi_policy=voi_policy)
                        result["image"] = metadata
                    except Exception as error:
                        result.update(status="image_failed", error_type=type(error).__name__)
                        if isinstance(error, ContractError):
                            result["image_error_reason"] = str(error)
                    if "image" in result and row["mask_source_path"]:
                        try:
                            reference = {"path": row["mask_source_path"], "byte_offset": row["mask_byte_offset"], "byte_length": row["mask_byte_length"]}
                            mask = read_csv_reference(root, reference)
                            key = {"mimic-cxr": "dicom_id", "nih-chestxray14": "Image Index", "vindr-cxr": "image_id"}[dataset]
                            if mask[key] != row["image_id"]:
                                raise ContractError("mask reference image identity mismatch")
                            result["mask"] = inspect_mask(mask, metadata["width"], metadata["height"])
                        except Exception as error:
                            result.update(mask_failed=True, mask_error_type=type(error).__name__)
                            if isinstance(error, ContractError):
                                result["mask_error_reason"] = str(error)
                    if "image" in result and row["graph_archive"]:
                        try:
                            path = local_file(root, row["graph_archive"])
                            if path not in archives:
                                archives[path] = stack.enter_context(zipfile.ZipFile(path))
                            graph = json.loads(archives[path].read(row["graph_entry"]))
                            if (str(graph["image_id"]), str(graph["patient_id"]), str(graph["study_id"])) != (
                                    row["image_id"], row["patient_id"], row["study_id"]):
                                raise ContractError("scene-graph identity mismatch")
                            regions = scene_regions(graph, width=metadata["width"], height=metadata["height"], strict=False)
                            result["graph"] = {"regions": len(regions), "attributes": sum(len(r["attributes"]) for r in regions),
                                               "invalid_source_boxes": sum(r["box_status"] == "invalid_source_box" for r in regions),
                                               "unlocalized_attribute_regions": sum(r["box_status"] == "unlocalized_attribute" for r in regions)}
                        except Exception as error:
                            result.update(graph_failed=True, graph_error_type=type(error).__name__)
                            if isinstance(error, ContractError):
                                result["graph_error_reason"] = str(error)
                    db.execute("INSERT OR REPLACE INTO checks VALUES(?,?,?)", (dataset, row["image_id"], json.dumps(result)))
                    db.commit()
        counts = {}
        for dataset in datasets:
            counter = Counter()
            organs = Counter()
            hashes, perceptual, error_reasons = Counter(), Counter(), Counter()
            for value, in db.execute("SELECT result FROM checks WHERE dataset=?", (dataset,)):
                result = json.loads(value)
                counter["checked"] += 1
                counter[result["status"]] += 1
                for key in ("mask_available", "graph_available", "mask_failed", "graph_failed"):
                    counter[key] += bool(result.get(key))
                if "image" in result:
                    counter["constant_images"] += result["image"]["constant_image"]
                    hashes[result["image"]["sha256"]] += 1
                    perceptual[result["image"]["dhash"]] += 1
                for key in ("image_error_reason", "mask_error_reason", "graph_error_reason"):
                    if key in result:
                        error_reasons[result[key]] += 1
                if "mask" in result:
                    counter["masks_decoded"] += 1
                    counter["empty_organs"] += result["mask"]["empty_organs"]
                    counter["masks_with_lung_overlap"] += result["mask"]["lung_overlap_pixels"] > 0
                    for organ, area in result["mask"]["areas"].items():
                        organs[organ] += area > 0
                if "graph" in result:
                    counter["graphs_validated"] += 1
                    counter["invalid_source_boxes"] += result["graph"]["invalid_source_boxes"]
                    counter["unlocalized_attribute_regions"] += result["graph"]["unlocalized_attribute_regions"]
            counts[dataset] = {**dict(counter), "nonempty_organ_masks": dict(organs),
                               "checked_exact_digest_duplicate_groups": sum(v > 1 for v in hashes.values()),
                               "equal_dhash_review_groups": sum(v > 1 for v in perceptual.values()),
                               "error_reasons": dict(error_reasons)}
        return {"datasets": counts, "selection": "first image IDs in lexical order per dataset",
                "image_limit_per_dataset": image_limit, "full_dataset_validation": False,
                "all_selected_images_attempted": image_limit == -1, "processor_sha256": processor_hashes,
                "qc_only": True, "no_rows_automatically_excluded": True}
    finally:
        db.close()
