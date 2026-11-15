"""Private reference-only audit. This is not a training manifest or loader."""
from collections import Counter
import csv
import json
from itertools import count
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess

from cxr_research.data.source_refs import BinaryLines, bounded, csv_record
from cxr_research.preprocessing.annotations import validate_box
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json

SPLIT = {"train": "train", "val": "validation", "validation": "validation", "test": "test"}


def jsonl(path):
    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            yield json.loads(line)


def csv_refs(path, delimiter=","):
    with path.open("rb") as stream:
        lines = BinaryLines(stream)
        reader = csv.reader(lines, delimiter=delimiter, strict=True)
        header = next(reader)
        for index in count(1):
            start = lines.offset
            row = next(reader, None)
            if row is None:
                return
            yield index, start, lines.offset - start, csv_record(header, row)


def llava_refs(path):
    """Read only three top-level metadata values from the pinned release format."""
    with path.open("rb") as f:
        if f.readline().strip() != b"[":
            raise ContractError("unsupported LLaVA-Rad array framing")
        start, metadata, index, closed = None, None, 0, False
        for raw in f:
            line = raw.rstrip(b"\r\n")
            if start is None:
                if line == b"  {" and not closed:
                    start, metadata = f.tell() - len(raw), {}
                elif line == b"]" and (closed or not index):
                    if f.read().strip():
                        raise ContractError("unexpected trailing LLaVA-Rad data")
                    return
                elif line.strip():
                    raise ContractError("unexpected LLaVA-Rad record start")
            elif line in (b"  },", b"  }"):
                if set(metadata) != {"image", "id", "generate_method"}:
                    raise ContractError("incomplete LLaVA-Rad identity metadata")
                index += 1
                yield index, start, f.tell() - start - len(raw) + len(line.rstrip(b",")), metadata
                start, closed = None, line == b"  }"
            elif match := re.fullmatch(rb'    "(image|id|generate_method)": ("(?:[^"\\]|\\.)*"),?', line):
                key = match[1].decode("ascii")
                if key in metadata:
                    raise ContractError("duplicate LLaVA-Rad identity field")
                metadata[key] = json.loads(match[2])
        raise ContractError("incomplete LLaVA-Rad array")


def audit_grounding_sources(root, catalog, out, repository, catalog_sha256):
    root, catalog, out, repository = (p.resolve() for p in (root, catalog, out, repository))
    if not out.is_relative_to(root / "manifests") or out.is_relative_to(repository) or out == catalog:
        raise ContractError("output must be a separate private manifest directory")
    meta = {}

    def source(base, name, version):
        path = bounded(root, f"{base}/{name}")
        checksums = dict((n.lstrip("*"), sha.lower()) for sha, n in
                         (line.split(maxsplit=1) for line in bounded(root, f"{base}/SHA256SUMS.txt")
                          .read_text(encoding="utf-8-sig").splitlines() if line.strip()))
        sha = file_hash(path)
        if checksums.get(name) != sha:
            raise ContractError("release source checksum missing or changed")
        license_file = bounded(root, f"{base}/LICENSE.txt")
        meta[path.relative_to(root).as_posix()] = {"version": version, "sha256": sha, "bytes": path.stat().st_size,
            "access": "PhysioNet credentialed local release; audit only", "license_sha256": file_hash(license_file)}
        return path

    if file_hash(catalog / "report.json") != catalog_sha256:
        raise ContractError("expected the pinned MIMIC catalog report")
    report = json.loads((catalog / "report.json").read_text(encoding="utf-8"))
    if report.get("status") != "complete" or report.get("split_policy") != "mimic-official":
        raise ContractError("a complete official-split catalog is required")
    for n in ("images.jsonl", "vqa_links.jsonl", "sources.jsonl"):
        if file_hash(catalog / n) != report["output_sha256"][n]:
            raise ContractError("pinned MIMIC catalog export changed")
    images, official, held = {}, {}, {}

    def hold(patient, reason):
        held.setdefault(patient, set()).add(reason)

    for row in jsonl(catalog / "images.jsonl"):
        if row["dataset"] != "mimic-cxr":
            continue
        image, patient, split = row["image_id"], row["patient_id"], row["source_split"]
        if image in images or official.setdefault(patient, split) != split or split not in SPLIT.values():
            raise ContractError("invalid official MIMIC image or patient split")
        images[image] = row
        if split == "test":
            hold(patient, "mimic-test")
    for row in jsonl(catalog / "vqa_links.jsonl"):
        if row["qa_source_split"] == "test":
            hold(row["patient_id"], "mimic-vqa-test")
    if len(held) != report["sealed_test_patients"]:
        raise ContractError("existing MIMIC test seal does not reconcile")
    print("Pinned catalog and original test seal verified.", flush=True)

    cx = "chest-imagenome/1.0.0"
    ll = "llava-rad-mimic-cxr-annotations-1.0.0"
    coco_path = source("ms-cxr", "MS_CXR_Local_Alignment_v1.1.0.json", "1.1.0")
    coco = json.loads(coco_path.read_text(encoding="utf-8-sig"))
    coco_images = {x["id"]: x for x in coco["images"]}
    if len(coco_images) != len(coco["images"]) or len({a["id"] for a in coco["annotations"]}) != len(coco["annotations"]):
        raise ContractError("MS-CXR duplicate COCO identity")
    archive = source(cx, "silver_dataset/scene_graph.zip", "1.0.0")
    gold = source(cx, "gold_dataset/gold_bbox_coordinate_annotations_1000images.csv", "1.0.0")
    gold_attributes = source(cx, "gold_dataset/gold_attributes_relations_500pts_500studies1st.txt", "1.0.0")

    def key(name):
        return PurePosixPath(name).stem

    silver_split, silver_patients = {}, {}
    for name, split in (("train", "train"), ("valid", "validation"), ("test", "test"), ("images_to_avoid", "holdout")):
        for _, _, _, row in csv_refs(source(cx, f"silver_dataset/splits/{name}.csv", "1.0.0")):
            image, patient = row["dicom_id"], row["subject_id"]
            im = images.get(image)
            if not im or (patient, row["study_id"]) != (im["patient_id"], im["study_id"]):
                raise ContractError("Chest ImaGenome split image identity mismatch")
            if split == "holdout":
                hold(patient, "imagenome-images-to-avoid")
                continue
            if silver_split.setdefault(image, split) != split or silver_patients.setdefault(patient, split) != split:
                raise ContractError("Chest ImaGenome source patient overlap")
            if split == "test":
                hold(patient, "imagenome-test")
    ms_patients = {}
    for ann in coco["annotations"]:
        im = images.get(key(coco_images[ann["image_id"]]["file_name"]))
        if im:
            patient, split = im["patient_id"], SPLIT[ann["split"]]
            if ms_patients.setdefault(patient, split) != split:
                raise ContractError("MS-CXR source patient overlap")
            if split == "test":
                hold(patient, "ms-cxr-test")
    for path, delimiter in ((gold, ","), (gold_attributes, "\t")):
        for _, _, _, row in csv_refs(path, delimiter):
            if im := images.get(key(row["image_id"])):
                if path == gold_attributes and (row["patient_id"], row["study_id"]) != (im["patient_id"], im["study_id"]):
                    raise ContractError("Chest ImaGenome gold attribute identity mismatch")
                hold(im["patient_id"], "imagenome-gold")

    def llava_identity(row):
        match = re.fullmatch(r"mimic/p(\d{2})/p(\d+)/s(\d+)/([^/\\]+)\.jpg", row["image"])
        if not match or match[2][:2] != match[1] or row["id"] != f"{match[2]}_{match[3]}":
            raise ContractError("LLaVA-Rad source path and study identity disagree")
        image = match[4]
        if (im := images.get(image)) and (match[2], match[3]) != (im["patient_id"], im["study_id"]):
            raise ContractError("LLaVA-Rad identity disagrees with MIMIC")
        return image, match[2]

    llava = {split: source(ll, f"chat_{name}_MIMIC_CXR_all_gpt4extract_rulebased_v1.json", "1.0.0")
             for split, name in (("train", "train"), ("validation", "dev"), ("test", "test"))}
    for _, _, _, row in llava_refs(llava["test"]):
        _, patient = llava_identity(row)
        hold(patient, "llava-rad-test")
    print("Release hashes checked; protected patient inventory assembled.", flush=True)

    out.mkdir(exist_ok=True)
    if (out / "mapping.sqlite").exists():
        raise ContractError("mapping already exists; use a new directory")
    db = sqlite3.connect(out / "mapping.sqlite")
    counts, joins = {}, {}
    try:
        db.executescript("""
          CREATE TABLE images(image_id TEXT PRIMARY KEY,patient_id TEXT,study_id TEXT,relative_path TEXT,
             mimic_split TEXT,view TEXT,width INTEGER,height INTEGER,catalog_present INTEGER,expected_sha256 TEXT);
          CREATE TABLE holds(patient_id TEXT,reason TEXT,PRIMARY KEY(patient_id,reason));
          CREATE TABLE links(source TEXT,source_ref TEXT,row_number INTEGER,image_id TEXT,source_split TEXT,
             state TEXT,byte_offset INTEGER,byte_length INTEGER,entry TEXT,method TEXT,xyxy TEXT,
             PRIMARY KEY(source,source_ref,row_number));
          CREATE INDEX links_image ON links(image_id);
          CREATE VIEW development_references AS SELECT l.*,i.patient_id,i.study_id,i.relative_path,i.mimic_split,i.width,i.height,i.view
             FROM links l JOIN images i USING(image_id) WHERE state='matched_development_reference';
        """)
        db.executemany("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?)", (
            (image, r["patient_id"], r["study_id"], r["path"], r["source_split"], r["view"], r["width"],
             r["height"], r["present"], r["expected_sha256"]) for image, r in images.items()))
        db.executemany("INSERT INTO holds VALUES(?,?)", ((p, reason) for p, rs in held.items() for reason in sorted(rs)))

        def add(kind, path, row_number, image, split, offset=None, length=None, entry=None, method=None, box=None, dimensions=None, evaluation_only=False):
            im = images.get(image)
            if not im:
                state = "image_not_in_catalog"
            elif evaluation_only or im["patient_id"] in held:
                state = "protected_reference"
            elif split != im["source_split"]:
                state = "source_split_unassigned" if split is None else "source_split_conflict"
            elif not im["present"]:
                state = "catalog_image_missing"
            else:
                state = "matched_development_reference"
            xyxy = None
            if box is not None and state == "matched_development_reference":
                try:
                    size = im["width"], im["height"]
                    if dimensions != size:
                        raise ContractError("reference dimensions differ")
                    xyxy = json.dumps(validate_box(box, *size), allow_nan=False)
                except (ContractError, TypeError, ValueError):
                    state = "box_requires_review"
            counts.setdefault(kind, Counter())[state] += 1
            joins.setdefault(kind, Counter())[f"{split or 'unassigned'}:{im['source_split'] if im else 'missing'}"] += 1
            db.execute("INSERT INTO links VALUES(?,?,?,?,?,?,?,?,?,?,?)", (kind, path.relative_to(root).as_posix(), row_number,
                image, split, state, offset, length, entry, method, xyxy))

        for ann in coco["annotations"]:
            r = coco_images[ann["image_id"]]
            x, y, w, h = ann["bbox"]
            add("ms-cxr-finding-phrase", coco_path, ann["id"], key(r["file_name"]), SPLIT[ann["split"]],
                box=(x, y, x+w, y+h), dimensions=(r["width"], r["height"]))
        for path, delimiter, kind in ((gold, ",", "imagenome-gold-anatomy"),
                                       (gold_attributes, "\t", "imagenome-gold-attributes")):
            for i, offset, length, row in csv_refs(path, delimiter):
                add(kind, path, i, key(row["image_id"]), "gold", offset, length, evaluation_only=True)
        for i, (image, r) in enumerate(images.items(), 1):
            if ref := r["scene_graph"]:
                if ref["archive"] != archive.relative_to(root).as_posix():
                    raise ContractError("unexpected Chest ImaGenome archive reference")
                add("imagenome-silver-scene", archive, i, image, silver_split.get(image), entry=ref["entry"])
        methods = Counter()
        for split, path in llava.items():
            for i, offset, length, row in llava_refs(path):
                image, _ = llava_identity(row)
                if row["generate_method"] not in {"gpt4", "rule-based"}:
                    raise ContractError("unexpected LLaVA-Rad extraction method")
                methods[f"{split}:{row['generate_method']}"] += 1
                add("llava-rad-report-context", path, i, image, split, offset, length, method=row["generate_method"])
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",) or db.execute("""
          SELECT count(*) FROM development_references d WHERE d.mimic_split != d.source_split OR
          EXISTS(SELECT 1 FROM holds h WHERE h.patient_id=d.patient_id) OR d.mimic_split='test'
        """).fetchone()[0]:
            raise ContractError("sidecar integrity or patient holdout check failed")
        overlaps = dict(db.execute("""SELECT a.source||' + '||b.source,count(DISTINCT a.image_id)
          FROM (SELECT DISTINCT source,image_id FROM development_references) a
          JOIN (SELECT DISTINCT source,image_id FROM development_references) b USING(image_id)
          WHERE a.source<b.source GROUP BY a.source,b.source"""))
        samples = db.execute("SELECT DISTINCT relative_path FROM development_references ORDER BY relative_path LIMIT 32").fetchall()
        for (relative,) in samples:
            bounded(root, relative)
    finally:
        db.close()
    result = {"status": "audit_complete", "training_ready": False, "canonical_split": "official MIMIC; unchanged",
        "review_scope": "private metadata/reference audit only; no approved training integration", "mimic_catalog_sha256": catalog_sha256,
        "mimic_images": len(images), "mimic_catalog_exports_sha256": report["output_sha256"], "holdout_patients": len(held),
        "holdout_patients_by_reason": dict(Counter(reason for rs in held.values() for reason in rs)),
        "states_by_source": counts, "source_vs_mimic_split_counts": joins, "llava_extraction_methods": methods,
        "development_shared_images": overlaps, "development_holdout_leaks": 0,
        "live_image_path_sample_checked": len(samples), "all_image_bytes_rehashed": False,
        "report_text_exported": False, "qa_to_box_targets_created": False, "source_metadata": meta,
        "producer_git_base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
        "producer_script_sha256": file_hash(Path(__file__)), "mapping_sha256": file_hash(out / "mapping.sqlite")}
    write_json(out / "report.json", result)
    print(json.dumps({k: result[k] for k in ("status", "holdout_patients", "states_by_source", "development_holdout_leaks")}))

