"""Independently reconcile the frozen development bundle and its sources."""
from pathlib import Path
import re
import sqlite3

from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import DataConfig

from .export_review import read, sha, write


def verify(root, out, parent):
    report = read(out / "report.json")
    for name, expected in report["outputs_sha256"].items():
        assert sha(out / name) == expected
    assert sha(parent) == report["inputs_sha256"]["parent_mapping"]
    status = read(out / "status.json")
    assert status["status"] == "complete" and status["sha256"] == report["outputs_sha256"]["manifest.jsonl"]
    print("Bundle hashes verified.", flush=True)
    with sqlite3.connect((out / "mapping.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.execute("ATTACH DATABASE ? AS audit", (parent.as_uri() + "?mode=ro",))
        assert not db.execute("PRAGMA main.foreign_key_check").fetchall()
        assert db.execute("""SELECT count(*) FROM main.images i LEFT JOIN audit.images parent USING(image_id)
            WHERE parent.image_id IS NULL OR i.patient_id<>parent.patient_id OR i.study_id<>parent.study_id OR
                i.split<>parent.mimic_split OR i.relative_path<>parent.relative_path""").fetchone()[0] == 0
        assert not db.execute("""SELECT 1 FROM main.images i WHERE EXISTS(
            SELECT 1 FROM audit.holds h WHERE h.patient_id=i.patient_id) OR split NOT IN ('train','validation')""").fetchone()
        assert not db.execute("""SELECT patient_id FROM main.images GROUP BY patient_id
            HAVING count(DISTINCT split)>1""").fetchone()
        qa_ids = dict(db.execute("SELECT sample_id,image_id FROM vqa"))
        context_checks = 0
        for split in ("train", "validation"):
            refs = db.execute("""SELECT c.source_ref,c.byte_offset,c.byte_length,c.method,
                i.image_id,i.patient_id,i.study_id FROM context_study s JOIN context_image c USING(image_id)
                JOIN main.images i USING(image_id) WHERE i.split=? ORDER BY c.image_id LIMIT 16""", (split,))
            for source, offset, length, method, image, patient, study in refs:
                with (root / source).open("rb") as f:
                    f.seek(offset)
                    record = f.read(length)
                path = re.search(rb'^    "image": "([^"]+)"', record, re.M)
                identity = re.search(rb'^    "id": "([^"]+)"', record, re.M)
                variant = re.search(rb'^    "generate_method": "([^"]+)"', record, re.M)
                assert record.lstrip().startswith(b"{") and record.endswith(b"}")
                assert path[1].decode() == f"mimic/p{patient[:2]}/p{patient}/s{study}/{image}.jpg"
                assert identity[1].decode() == f"{patient}_{study}" and variant[1].decode() == method
                context_checks += 1
    print("Independent split, mapping and contextual source checks passed.", flush=True)

    dataset = load_manifest(DataConfig(str(root), str(out/"manifest.jsonl"), str(out/"provenance.json")),
                            verify_files=False)
    assert len(dataset.samples) == len(qa_ids)
    for sample in dataset.samples:
        assert qa_ids.pop(sample.sample_id) == sample.image_id
    assert not qa_ids
    counts = {split: len(dataset.select("medgemma", split)) for split in ("train", "validation")}
    assert counts == {split: row["rows"] for split, row in report["counts"]["vqa"].items()}
    write(out / "verification.json", {
        "status": "passed", "canonical_manifest_loader": True, "manifest_hash": dataset.sha256,
        "rows": counts, "foreign_key_errors": 0, "held_or_cross_split_patients": 0,
        "llava_sample_byte_ranges_checked": context_checks, "jpg_bytes_rehashed": False,
        "multitask_training_smoke_run": False,
    })
    print("Canonical manifest loader verified.", flush=True)

