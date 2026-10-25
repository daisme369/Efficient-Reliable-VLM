"""Process development QA locally and emit aggregate, versioned review reports."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

from cxr_research.preprocessing.labels import CUE_VERSION, TEXT_VERSION, review_qa
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


def audit_qa(snapshot, output, catalog):
    sealed = {row[0] for row in catalog.execute("SELECT patient_id FROM sealed_patients")}
    counts, templates = Counter(), {}
    source_fingerprints, products = {}, {}
    # A private SQLite table bounds memory for exact QA duplication checks.
    import sqlite3
    db = sqlite3.connect(output / "qa-audit.sqlite")
    db.execute("DROP TABLE IF EXISTS pairs")
    db.execute("CREATE TABLE pairs(pair_hash TEXT,patient_id TEXT,split TEXT)")
    db.execute("CREATE INDEX pair_hash ON pairs(pair_hash)")
    try:
        for split in ("train", "validation"):
            source = snapshot / f"vqa.{split}.jsonl"
            source_fingerprints[source.name] = file_hash(source)
            target = output / f"vqa.processed.{split}.jsonl"
            split_counts = Counter()
            with source.open(encoding="utf-8") as stream, target.open("w", encoding="utf-8", newline="\n") as dest:
                for line in stream:
                    row = json.loads(line)
                    if (row["split"] != split or row["test_sealed"] != 0
                            or row["patient_id"] in sealed or row["qa_source_split"] == "test"):
                        raise ContractError("QA audit encountered a test-seal violation")
                    image = catalog.execute("SELECT patient_id,study_id,path,source_split FROM images WHERE dataset='mimic-cxr' AND image_id=?",
                                            (row["image_id"],)).fetchone()
                    if image != (row["patient_id"], row["study_id"], row["path"], split):
                        raise ContractError("QA audit encountered an image identity mismatch")
                    processed = review_qa(row["question"], row["answer"])
                    flags = [*processed["flags"], "template_eligibility_unreviewed"]
                    split_counts.update(flags)
                    split_counts["rows"] += 1
                    split_counts["normalization_changed"] += processed["changed"]
                    split_counts["invalid_supervision"] += not processed["supervision_valid"]
                    split_counts["multiple_answers"] += isinstance(processed["answer"], list) and len(processed["answer"]) > 1
                    pair_hash = hashlib.sha256(json.dumps([processed["question"], processed["answer"]], ensure_ascii=False).encode()).hexdigest()
                    db.execute("INSERT INTO pairs VALUES(?,?,?)", (pair_hash, row["patient_id"], split))
                    template = row["template"]
                    if not isinstance(template, str) or not template:
                        raise ContractError("source template is not a string")
                    item = templates.setdefault(template, {"template": template, "counts": Counter(),
                                                           "semantic_types": Counter(), "content_types": Counter(),
                                                           "eligibility": "unreviewed"})
                    item["counts"][split] += 1
                    item["semantic_types"][str(row["semantic_type"])] += 1
                    item["content_types"][str(row["content_type"])] += 1
                    export = {**row, "schema": "cxr-processed-qa-v1", "question": processed["question"],
                              "answer": processed["answer"], "review_flags": flags,
                              "supervision_valid": processed["supervision_valid"],
                              "normalization_version": TEXT_VERSION, "cue_version": CUE_VERSION}
                    dest.write(json.dumps(export, ensure_ascii=False, sort_keys=True) + "\n")
            counts.update({f"{split}.{key}": value for key, value in split_counts.items()})
            products[target.name] = file_hash(target)
        db.commit()
        # Repeated language across patients is reported, never automatically removed.
        repeated = db.execute("SELECT COUNT(*) FROM (SELECT pair_hash FROM pairs GROUP BY pair_hash HAVING COUNT(*)>1)").fetchone()[0]
        cross_split = db.execute("SELECT COUNT(*) FROM (SELECT pair_hash FROM pairs GROUP BY pair_hash HAVING COUNT(DISTINCT split)>1)").fetchone()[0]
        target = output / "template-review.jsonl"
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            for key in sorted(templates):
                stream.write(json.dumps(templates[key], ensure_ascii=False, sort_keys=True) + "\n")
        products[target.name] = file_hash(target)
        return {"counts": dict(counts), "template_count": len(templates), "exact_text_pair_groups": repeated,
                "text_pair_groups_across_development_splits": cross_split,
                "duplicate_policy": "report only; recurring QA language is not image/patient leakage evidence",
                "normalization_version": TEXT_VERSION, "cue_version": CUE_VERSION,
                "source_sha256": source_fingerprints, "output_sha256": products,
                "training_ready": False, "clinical_labels_inferred": False}
    finally:
        db.close()
