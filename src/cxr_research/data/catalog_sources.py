"""Dataset-specific release indexing; datasets retain separate namespaces."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import zipfile
from cxr_research.utils.hashing import file_hash

SPLITS = {"train": "train", "validate": "validation", "valid": "validation",
          "val": "validation", "validation": "validation", "test": "test"}
VERSIONS = {"mimic-cxr-jpg": "2.1.0", "mimic-ext-mimic-cxr-vqa": "1.0.0",
            "chest-imagenome": "1.0.0", "chexmask-cxr-segmentation-data": "1.0.0",
            "vindr-cxr": "1.0.0", "ChestX-ray14": "local-release"}


def mimic(catalog):
    catalog.phase("mimic_image_index")
    base = catalog.root / "mimic-cxr-jpg" / "2.1.0"
    split_path = base / "mimic-cxr-2.0.0-split.csv.gz"
    for _, _, _, row in catalog.csv_rows(split_path):
        image, patient, study = row["dicom_id"], row["subject_id"], row["study_id"]
        if not patient.isdigit() or not study.isdigit():
            raise ValueError("Invalid MIMIC source identity")
        relative = f"mimic-cxr-jpg/2.1.0/files/p{patient[:2]}/p{patient}/s{study}/{image}.jpg"
        split = SPLITS[row["split"]]
        catalog.add_image("mimic-cxr", image, patient, study, relative, split)
        catalog.db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)", (patient, "mimic", split))
    path = base / "mimic-cxr-2.0.0-metadata.csv.gz"
    for _, _, _, row in catalog.csv_rows(path):
        im = catalog.db.execute("SELECT patient_id,study_id FROM images WHERE dataset='mimic-cxr' AND image_id=?", (row["dicom_id"],)).fetchone()
        if im != (row["subject_id"], row["study_id"]):
            raise ValueError("MIMIC metadata identity mismatch")
        catalog.db.execute("UPDATE images SET view=?,height=?,width=? WHERE dataset='mimic-cxr' AND image_id=?",
                        (row["ViewPosition"], int(row["Rows"]), int(row["Columns"]), row["dicom_id"]))


def auxiliary(catalog):
    catalog.phase("separate_auxiliary_image_indexes")
    nih = catalog.root / "ChestX-ray14"
    path = nih / "Data_Entry_2017.csv"
    if path.is_file():
        names = {}
        for folder, dirs, files in os.walk(nih, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not Path(folder, d).is_symlink())
            for name in sorted(files):
                if name.lower().endswith(".png"):
                    if name in names:
                        raise ValueError("Duplicate NIH image filename")
                    names[name] = (Path(folder) / name).relative_to(catalog.root).as_posix()
        membership = {}
        for filename, split in [("train_val_list.txt", "train_val"), ("test_list.txt", "test")]:
            p = nih / filename
            if p.is_file():
                catalog.source(p)
                lines = p.read_text().splitlines()
                catalog.finish_source(p, file_hash(p), len(lines))
                for name in lines:
                    if name in membership:
                        raise ValueError("NIH source image split overlap")
                    membership[name] = split
        for _, _, _, row in catalog.csv_rows(path):
            key = row["Image Index"]
            catalog.add_image("nih-chestxray14", key, row["Patient ID"], None,
                           names.get(key, f"ChestX-ray14/images/{key}"), membership.get(key), row["View Position"])
    base = catalog.root / "vindr-cxr" / "1.0.0"
    for split in ["train", "test"]:
        path = base / "annotations" / f"image_labels_{split}.csv"
        if not path.is_file():
            continue
        seen = set()
        for _, _, _, row in catalog.csv_rows(path):
            image = row["image_id"]
            if image not in seen:
                catalog.add_image("vindr-cxr", image, None, None,
                               f"vindr-cxr/1.0.0/{split}/{image}.dicom", split)
                seen.add(image)


def annotations(catalog):
    catalog.phase("chest_imagenome_archive_index")
    base = catalog.root / "chest-imagenome" / "1.0.0"
    path = base / "silver_dataset" / "scene_graph.zip"
    if path.is_file():
        source = catalog.source(path)
        count = 0
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.endswith(".json"):
                    continue
                catalog.db.execute("INSERT INTO scene_graphs VALUES(?,?,?)",
                                (PurePosixPath(entry.filename).stem.removesuffix("_SceneGraph"), source, entry.filename))
                count += 1
        catalog.finish_source(path, file_hash(path), count)
    gold = base / "gold_dataset" / "gold_bbox_coordinate_annotations_1000images.csv"
    if gold.is_file():
        catalog.csv_refs(gold, "mimic-cxr", "image_id", "chest_imagenome_gold_boxes")
    base = catalog.root / "chexmask-cxr-segmentation-data" / "1.0.0" / "OriginalResolution"
    for name, dataset, column in [("MIMIC-CXR-JPG.csv", "mimic-cxr", "dicom_id"),
                                  ("ChestX-Ray8.csv", "nih-chestxray14", "Image Index"),
                                  ("VinDr-CXR.csv", "vindr-cxr", "image_id")]:
        path = base / name
        if path.is_file():
            catalog.phase("index_mask_" + dataset)
            catalog.csv_refs(path, dataset, column, "anatomical_mask_original")
    catalog.phase("auxiliary_annotation_references")
    base = catalog.root / "vindr-cxr" / "1.0.0" / "annotations"
    for split in ["train", "test"]:
        for prefix, kind in [("annotations", "finding_boxes"), ("image_labels", "image_labels")]:
            path = base / f"{prefix}_{split}.csv"
            if path.is_file():
                catalog.csv_refs(path, "vindr-cxr", "image_id", kind)
    path = catalog.root / "ChestX-ray14" / "BBox_List_2017.csv"
    if path.is_file():
        catalog.csv_refs(path, "nih-chestxray14", "Image Index", "finding_boxes")


def questions(catalog):
    catalog.phase("vqa_image_patient_study_mapping")
    base = catalog.root / "mimic-ext-mimic-cxr-vqa" / "1.0.0"
    paths = sorted(base.rglob("*.json"))
    if {p.name for p in paths} != {"train.json", "valid.json", "test.json"} or len(paths) != 3:
        raise ValueError("Expected exactly the three release VQA source tables")
    sources = {}
    for path in paths:
        source = catalog.source(path)
        rows = json.loads(path.read_text(encoding="utf-8"))
        sources[path] = source
        for row in rows:
            split = SPLITS[row["split"]]
            patient = str(row["subject_id"])
            catalog.db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)", (patient, "qa", split))
        catalog.finish_source(path, file_hash(path), len(rows))
    catalog.db.execute("INSERT INTO sealed_patients SELECT DISTINCT patient_id FROM memberships WHERE split='test'")
    sealed = {row[0] for row in catalog.db.execute("SELECT patient_id FROM sealed_patients")}
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        for index, row in enumerate(rows):
            split = SPLITS[row["split"]]
            patient, study = str(row["subject_id"]), str(row["study_id"])
            # Never serialize a payload for a patient sealed by any source row.
            payload = None if patient in sealed else json.dumps({k: row[k] for k in
                ["question", "answer", "semantic_type", "content_type", "template", "template_program", "template_arguments"]}, ensure_ascii=False)
            catalog.db.execute("INSERT INTO qa VALUES(?,?,?,?,?,?,?)",
                            (sources[path], index, str(row["image_id"]), patient, study, split, payload))
