"""Build a local development-only manifest and grounding/context mappings."""
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from zipfile import ZipFile

from cxr_research.preprocessing.annotations import scene_regions, validate_box


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rows(path):
    with path.open(encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def context_rank(record):
    return (record["method"] != "gpt4", record["view"] != "PA",
            record["image_id"], record["source_ref"], record["row_number"])


def export_review(root, base, audit, gate, out):
    assert context_rank(dict(method="gpt4", view="PA", image_id="b", source_ref="f", row_number=2)) < context_rank(
        dict(method="gpt4", view="AP", image_id="a", source_ref="f", row_number=1))
    old = read(base / "provenance.json")
    prior = read(audit / "report.json")
    eligibility = read(gate / "report.json")
    source_db_path = audit / "mapping.sqlite"
    candidate_path = gate / "candidate_links.jsonl"
    assert sha(base / "manifest.jsonl") == old["manifest_sha256"]
    assert sha(source_db_path) == prior["mapping_sha256"] == eligibility["inputs_sha256"]["mapping.sqlite"]
    assert sha(candidate_path) == eligibility["outputs_sha256"]["candidate_links.jsonl"]
    for name, meta in prior["source_metadata"].items():
        if (name.startswith("ms-cxr/") or name.endswith("scene_graph.zip")
                or ("llava-rad" in name and "chat_test_" not in name)):
            assert sha(root / name) == meta["sha256"]
    print("Inputs and development source hashes verified.", flush=True)

    db = sqlite3.connect(source_db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    held = {p for p, in db.execute("SELECT DISTINCT patient_id FROM holds")}
    image_info = {r["image_id"]: tuple(r)[1:] for r in db.execute(
        "SELECT image_id, patient_id, study_id, mimic_split, relative_path, view FROM images")}

    @lru_cache(maxsize=None)
    def local(relative):
        path = Path(relative)
        assert not path.is_absolute() and ".." not in path.parts
        return (root / path).is_file()

    mapping = out / "mapping.sqlite"
    manifest = out / "manifest.jsonl"
    # A completed report is the publication marker. Retries use a new directory.
    index = sqlite3.connect(mapping)
    index.execute("PRAGMA foreign_keys=ON")
    index.executescript("""
      CREATE TABLE images(image_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
        study_id TEXT NOT NULL, split TEXT CHECK(split IN ('train','validation')),
        relative_path TEXT NOT NULL, view TEXT NOT NULL);
      CREATE TABLE vqa(sample_id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images);
      CREATE TABLE chest_imagenome_bbox(sample_id TEXT PRIMARY KEY REFERENCES vqa,
        archive TEXT NOT NULL, entry TEXT NOT NULL, region TEXT NOT NULL, xyxy TEXT NOT NULL);
      CREATE TABLE ms_cxr_phrase(phrase_id INTEGER PRIMARY KEY,
        image_id TEXT NOT NULL REFERENCES images, source_ref TEXT NOT NULL,
        category_id INTEGER NOT NULL, annotation_ids TEXT NOT NULL, boxes_xyxy TEXT NOT NULL);
      CREATE TABLE context_image(image_id TEXT PRIMARY KEY REFERENCES images,
        source_ref TEXT NOT NULL, row_number INTEGER NOT NULL,
        byte_offset INTEGER NOT NULL CHECK(byte_offset > 0),
        byte_length INTEGER NOT NULL CHECK(byte_length > 0),
        method TEXT NOT NULL CHECK(method IN ('gpt4','rule-based')));
      CREATE TABLE context_study(study_id TEXT PRIMARY KEY,
        image_id TEXT NOT NULL REFERENCES context_image);
      CREATE INDEX vqa_image ON vqa(image_id);
      CREATE VIEW vqa_context AS SELECT v.sample_id, c.* FROM vqa v JOIN context_image c USING(image_id);
    """)
    omitted = defaultdict(Counter)
    seen_patients = defaultdict(set)

    def image(image_id, patient, study, split, relative, view):
        assert image_info[image_id] == (patient, study, split, relative, view)
        assert patient not in held and split in {"train", "validation"} and local(relative)
        seen_patients[split].add(patient)
        index.execute("INSERT OR IGNORE INTO images VALUES(?,?,?,?,?,?)",
                      (image_id, patient, study, split, relative, view))

    with manifest.open("x", encoding="utf-8", newline="\n") as output:
        for row in rows(base / "manifest.jsonl"):
            split, iid = row["split"], row["image_id"]
            p, study, official, relative, view = image_info[iid]
            assert (row["patient_id"], split, row["image"], row["view"]) == (p, official, relative, view)
            assert row["patient_namespace"] == "mimic-cxr" and row["task"] == "medgemma"
            if p in held:
                omitted["vqa_holdout"][split] += 1
                continue
            if not local(relative):
                omitted["vqa_missing_jpg"][split] += 1
                continue
            image(iid, p, study, split, relative, view)
            index.execute("INSERT INTO vqa VALUES(?,?)", (row["sample_id"], iid))
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print("VQA manifest filtered by patient and live image availability.", flush=True)

    linked = {r["image_id"]: r for r in db.execute("""SELECT * FROM development_references
        WHERE source='imagenome-silver-scene'""")}
    by_image = defaultdict(list)
    for row in rows(candidate_path):
        if row["source"] == "imagenome-silver-scene":
            sample = f"qa-{row['vqa_source_id']}-{row['vqa_row_index']}"
            match = index.execute("SELECT image_id FROM vqa WHERE sample_id=?", (sample,)).fetchone()
            if match is None:
                omitted["chest_qa_outside_filtered_b1"][row["split"]] += 1
                continue
            assert match == (row["image_id"],)
            by_image[row["image_id"]].append((sample, row))
    archive_path = "chest-imagenome/1.0.0/silver_dataset/scene_graph.zip"
    with ZipFile(root / archive_path) as z:
        for iid, matches in sorted(by_image.items()):
            link = linked[iid]
            assert link["source_ref"] == archive_path
            g = json.loads(z.read(link["entry"]))
            assert (g["image_id"], str(g["patient_id"]), str(g["study_id"])) == (
                iid, link["patient_id"], link["study_id"])
            regions = {r["region"].casefold(): r for r in scene_regions(
                g, width=link["width"], height=link["height"], strict=False)}
            for sample, row in matches:
                assert row["split"] == link["mimic_split"]
                region, = row["references"]
                box = validate_box(regions[region]["xyxy"], link["width"], link["height"])
                index.execute("INSERT INTO chest_imagenome_bbox VALUES(?,?,?,?,?)",
                              (sample, archive_path, link["entry"], region, json.dumps(box)))

    ms_path = "ms-cxr/MS_CXR_Local_Alignment_v1.1.0.json"
    ms = read(root / ms_path)
    annotations = {a["id"]: a for a in ms["annotations"]}
    ms_images = {a["id"]: a for a in ms["images"]}
    phrase_boxes = defaultdict(list)
    for link in db.execute("""SELECT * FROM development_references
            WHERE source='ms-cxr-finding-phrase' ORDER BY image_id,row_number"""):
        iid, ref, split = link["image_id"], link["row_number"], link["mimic_split"]
        if not local(link["relative_path"]):
            omitted["ms_missing_jpg_boxes"][split] += 1
            continue
        ann = annotations[ref]
        assert link["source_ref"] == ms_path and {"train": "train", "val": "validation",
                                                 "validation": "validation"}[ann["split"]] == split
        assert Path(ms_images[ann["image_id"]]["file_name"]).stem == iid
        image(iid, link["patient_id"], link["study_id"], split, link["relative_path"], link["view"])
        box = validate_box(json.loads(link["xyxy"]), link["width"], link["height"])
        phrase_boxes[iid, ann["category_id"], ann["label_text"]].append((ref, box))
    for (iid, category, _), examples in sorted(phrase_boxes.items()):
        refs, boxes = zip(*examples, strict=True)
        index.execute("INSERT INTO ms_cxr_phrase VALUES(?,?,?,?,?,?)",
                      (min(refs), iid, ms_path, category, json.dumps(refs),
                       json.dumps(sorted(set(boxes)))))
    print("Chest ImaGenome and MS-CXR bbox mappings exported.", flush=True)

    candidates = db.execute("""SELECT image_id, patient_id, study_id, mimic_split, relative_path,
        view, source_ref, row_number, byte_offset, byte_length, method
        FROM development_references WHERE source='llava-rad-report-context' AND view IN ('PA','AP')
        ORDER BY image_id, method, source_ref, row_number""")
    per_study = {}
    previous = None
    for link in candidates:
        iid, p, study, split, relative, view, ref, line, offset, length, method = tuple(link)
        if not local(relative):
            omitted["llava_missing_jpg_references"][split] += 1
            continue
        # Release lexical order puts gpt4 before rule-based for the same image.
        if iid == previous:
            continue
        previous = iid
        image(iid, p, study, split, relative, view)
        assert offset + length <= prior["source_metadata"][ref]["bytes"]
        index.execute("INSERT INTO context_image VALUES(?,?,?,?,?,?)",
                      (iid, ref, line, offset, length, method))
        rank = context_rank(link)
        if study not in per_study or rank < per_study[study][0]:
            per_study[study] = rank, iid
    index.executemany("INSERT INTO context_study VALUES(?,?)",
                      ((study, rank_iid[1]) for study, rank_iid in sorted(per_study.items())))
    assert not (seen_patients["train"] & seen_patients["validation"])
    assert not (held & (seen_patients["train"] | seen_patients["validation"]))
    assert index.execute("PRAGMA foreign_key_check").fetchone() is None
    assert index.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert index.execute("""SELECT count(*) FROM context_study c JOIN images i USING(image_id)
                             WHERE c.study_id<>i.study_id""").fetchone()[0] == 0
    index.commit()

    counts = {}
    for name in ("vqa", "ms_cxr_phrase", "context_image", "context_study", "vqa_context"):
        counts[name] = {split: {"rows": n, "images": images, "patients": patients}
                        for split, n, images, patients in index.execute(f"""SELECT split, count(*),
                        count(DISTINCT image_id),count(DISTINCT patient_id) FROM {name}
                        JOIN images USING(image_id) GROUP BY split""")}
    counts["chest_imagenome_bbox"] = {split: {"rows": n, "images": images, "patients": patients}
           for split, n, images, patients in index.execute("""SELECT split,count(*),
           count(DISTINCT image_id),count(DISTINCT patient_id)
           FROM chest_imagenome_bbox JOIN vqa USING(sample_id) JOIN images USING(image_id) GROUP BY split""")}
    index.close()
    db.close()

    exclusions = old["exclusions"]
    stages = old["exclusion_stages"]
    for stage, reason in (
        ("proposed_grounding_patient_holdout", "Provisional union of release holdouts and gold patients"),
        ("missing_live_image", "Local image did not exist at materialization time"),
    ):
        excluded = sum(omitted[("vqa_holdout" if stage.startswith("proposed") else "vqa_missing_jpg")].values())
        before = old["rows_after"]
        stages.append(dict(stage=stage, reason=reason, rows_before=before, excluded=excluded,
                           rows_after=before-excluded))
        exclusions[stage] = excluded
        old["rows_after"] -= excluded
    assert old["rows_after"] == sum(v["rows"] for v in counts["vqa"].values())
    old.update(created_at=date.today().isoformat(), producer_commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(), parent_manifest_sha256=old["manifest_sha256"],
        manifest_sha256=sha(manifest), split_policy="mimic-official with proposed grounding holdout exclusions",
        development_review_only=True)
    write(out / "provenance.json", old)
    write(out / "status.json", {
        "status": "complete",
        "sha256": old["manifest_sha256"],
        "rows": old["rows_after"],
        "splits": {split: stats["rows"] for split, stats in counts["vqa"].items()},
        "development_review_only": True,
    })
    write(out / "report.json", {
        "status": "development_review_only",
        "multitask_trainer_ready": False, "human_review_done": False, "training_started": False,
        "context_role": "review reference; future optional separate report target subject to approval",
        "context_selection": "AP/PA; GPT4 before rule-based, PA before AP across equal methods, then lexical image ID",
        "counts": counts, "exclusions": dict(omitted),
        "patient_checks": {"holdout_patients": len(held), "held_patient_leaks": 0, "train_validation_overlap": 0},
        "image_check": "local path exists; full JPG hashes not recomputed",
        "source_metadata": prior["source_metadata"],
        "inputs_sha256": {"parent_b1_manifest": old["parent_manifest_sha256"],
                          "parent_mapping": prior["mapping_sha256"], "qa_candidates": sha(candidate_path),
                          "eligibility_report": sha(gate / "report.json")},
        "producer_sha256": {
            p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
        "outputs_sha256": {
            p.name: sha(p) for p in (manifest, mapping, out/"provenance.json", out/"status.json")
        },
    })
    print(json.dumps(counts), flush=True)

