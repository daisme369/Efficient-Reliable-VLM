"""Synthetic source fixtures only; no restricted datasets are read by these tests."""
import csv
from contextlib import closing
import gzip
import sys
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
from cxr_research.data import catalog as module
from cxr_research.data.source_refs import csv_record, read_csv_reference, read_scene_graph



def csv_file(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fixture(root):
    mimic = root / "mimic-cxr-jpg/2.1.0"
    identities = [("synthetic-a", "1", "11", "train"), ("synthetic-b", "2", "22", "validate"),
                  ("synthetic-c", "3", "33", "test"), ("synthetic-d", "4", "44", "train")]
    split_rows, metadata = [], []
    for image, patient, study, split in identities:
        p = mimic / f"files/p{patient[:2]}/p{patient}/s{study}/{image}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"synthetic placeholder")
        common = dict(dicom_id=image, subject_id=patient, study_id=study)
        split_rows.append(dict(common, split=split))
        metadata.append(dict(common, ViewPosition="AP", Rows=2, Columns=2))
    csv_file(mimic / "mimic-cxr-2.0.0-split.csv.gz", list(split_rows[0]), split_rows)
    csv_file(mimic / "mimic-cxr-2.0.0-metadata.csv.gz", list(metadata[0]), metadata)
    base = root / "mimic-ext-mimic-cxr-vqa/1.0.0/release"
    base.mkdir(parents=True)
    def qa(image, patient, study, split):
        return dict(image_id=image, subject_id=patient, study_id=study, split=split,
                    question="Synthetic question", answer=["synthetic answer"], semantic_type="synthetic",
                    content_type="synthetic", template="Synthetic template", template_program="synthetic", template_arguments={})
    tables = {"train": [qa("synthetic-a", "1", "11", "train"), qa("synthetic-b", "2", "22", "train"), qa("synthetic-d", "4", "44", "train")],
              "valid": [qa("synthetic-a", "1", "11", "valid")],
              "test": [qa("synthetic-c", "3", "33", "test"), qa("synthetic-d", "4", "44", "test")]}
    for name, rows in tables.items():
        (base / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    scene = root / "chest-imagenome/1.0.0/silver_dataset/scene_graph.zip"
    scene.parent.mkdir(parents=True)
    with zipfile.ZipFile(scene, "w") as archive:
        archive.writestr("scene_graph/synthetic-a_SceneGraph.json", json.dumps({"image_id": "synthetic-a", "objects": []}))
    csv_file(root / "chest-imagenome/1.0.0/gold_dataset/gold_bbox_coordinate_annotations_1000images.csv",
             ["image_id", "original_x1", "original_y1", "original_x2", "original_y2"],
             [{"image_id":"synthetic-a.dcm","original_x1":0,"original_y1":0,"original_x2":1,"original_y2":1}])
    mask = root / "chexmask-cxr-segmentation-data/1.0.0/OriginalResolution/MIMIC-CXR-JPG.csv"
    values = dict(dicom_id="synthetic-a", Height=2, Width=2, **{"Dice RCA (Mean)":0.8,"Left Lung":"1 1\n3 1","Right Lung":"2 1", "Heart":"4 1"})
    csv_file(mask, list(values), [values])
    return base, mask


class CatalogTests(unittest.TestCase):
    def setUp(self):
        scratch = REPOSITORY / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        self.repository = self.root / "checkout"
        (self.repository / "scripts/ci").mkdir(parents=True)
        (self.repository / "AGENTS-INSTRUCTIONS.md").write_text("Synthetic checkout")
        (self.repository / "scripts/ci/check_gpu_environment.py").write_text("# Synthetic marker")
        self.qa, self.mask = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, root, output, split_policy="audit-only"):
        return module.build(root, output, split_policy, repository=self.repository)

    def test_official_split_reconciles_source_overlap_and_seals_test_patients(self):
        out = self.root / "prepared"
        report = self.build(self.root, out, "mimic-official")
        self.assertEqual(report["qa_source_patient_overlaps"], 2)
        self.assertEqual(report["official_patient_overlaps"], 0)
        self.assertEqual(report["qa_source_split_conflicts"], 3)
        self.assertEqual(report["development_rows"], {"train":2, "validation":1})
        train = [json.loads(x) for x in (out / "vqa.train.jsonl").read_text().splitlines()]
        validation = [json.loads(x) for x in (out / "vqa.validation.jsonl").read_text().splitlines()]
        self.assertEqual({r["patient_id"] for r in train}, {"1"})
        self.assertEqual({r["patient_id"] for r in validation}, {"2"})
        self.assertFalse((out / "vqa.test.jsonl").exists())
        self.assertTrue(all(r["split"]=="train" and r["test_sealed"]==0 for r in train))
        links = [json.loads(x) for x in (out / "vqa_links.jsonl").read_text().splitlines()]
        self.assertEqual(sum(r["test_sealed"] for r in links), 3)
        self.assertTrue(all("answer" not in r and "payload" not in r for r in links))
        self.assertFalse(report["training_ready"])
        self.assertEqual(report["qa_with_scene_graph"], 2)
        self.assertEqual(report["qa_with_gold_boxes"], 2)
        self.assertTrue(all(row["scene_graph"] is not None for row in train))

    def test_sealed_patient_payload_bytes_never_enter_catalog(self):
        sentinel = "SEALED_SECRET_SENTINEL_9f2c"
        path = self.qa / "train.json"
        rows = json.loads(path.read_text())
        rows[-1]["answer"] = [sentinel]
        path.write_text(json.dumps(rows), encoding="utf-8")
        out = self.root / "prepared"
        self.build(self.root, out, "mimic-official")
        self.assertNotIn(sentinel.encode(), (out / "catalog.sqlite").read_bytes())

    def test_csv_offsets_and_zip_identity_roundtrip(self):
        out = self.root / "prepared"
        self.build(self.root, out)
        import sqlite3
        with closing(sqlite3.connect(out / "catalog.sqlite")) as db:
            path, offset, length = db.execute("SELECT s.path,r.byte_offset,r.byte_length FROM refs r JOIN sources s ON s.id=r.source_id WHERE r.kind='anatomical_mask_original'").fetchone()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM refs r JOIN images i ON i.dataset=r.dataset AND i.image_id=r.image_id WHERE r.kind='chest_imagenome_gold_boxes'").fetchone()[0],1)
        row = read_csv_reference(self.root, {"path":path,"byte_offset":offset,"byte_length":length})
        self.assertEqual(row["Left Lung"], "1 1\n3 1")
        graph = {"archive":"chest-imagenome/1.0.0/silver_dataset/scene_graph.zip", "entry":"scene_graph/synthetic-a_SceneGraph.json"}
        self.assertEqual(read_scene_graph(self.root, graph, "synthetic-a"), {"image_id":"synthetic-a", "objects":[]})
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            read_scene_graph(self.root, graph, "synthetic-other")
        with self.assertRaises(ValueError):
            read_csv_reference(self.root, {"path":"../outside.csv","byte_offset":0,"byte_length":1})

    def test_determinism_audit_only_and_no_overwrite(self):
        first, second = self.root / "first", self.root / "second"
        a = self.build(self.root, first)
        b = self.build(self.root, second)
        self.assertEqual(a["output_sha256"], b["output_sha256"])
        self.assertFalse((first / "vqa.train.jsonl").exists())
        with self.assertRaises(FileExistsError):
            self.build(self.root, first)

    def test_missing_image_is_counted_and_not_exported_for_development(self):
        p = self.root / "mimic-cxr-jpg/2.1.0/files/p2/p2/s22/synthetic-b.jpg"
        p.unlink()
        result = self.build(self.root, self.root / "prepared", "mimic-official")
        self.assertEqual(result["qa_missing_local_image"], 1)
        self.assertEqual(result["development_rows"]["validation"], 0)

    def test_identity_conflict_fails_without_exporting_train_manifest(self):
        path = self.qa / "train.json"
        rows = json.loads(path.read_text())
        rows[0]["subject_id"] = "synthetic-wrong-patient"
        path.write_text(json.dumps(rows))
        out = self.root / "prepared"
        with self.assertRaisesRegex(ValueError, "identity audit failed"):
            self.build(self.root, out, "mimic-official")
        self.assertEqual(json.loads((out / "status.json").read_text())["status"], "failed")
        self.assertFalse((out / "vqa.train.jsonl").exists())

    def test_publisher_checksum_failure_is_not_accepted(self):
        base = self.root / "mimic-cxr-jpg/2.1.0"
        (base / "SHA256SUMS.txt").write_text("0" * 64 + "  mimic-cxr-2.0.0-metadata.csv.gz\n")
        out = self.root / "prepared"
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.build(self.root, out, "mimic-official")
        self.assertEqual(json.loads((out / "status.json").read_text())["status"], "failed")

    def test_nih_release_trailing_empty_header(self):
        base = self.root / "ChestX-ray14"
        base.mkdir()
        (base / "Data_Entry_2017.csv").write_text('Image Index,Patient ID,View Position,\nsynthetic-nih.png,9,PA\n')
        (base / "train_val_list.txt").write_text('synthetic-nih.png\n')
        images = base / "images"
        images.mkdir()
        (images / "synthetic-nih.png").write_bytes(b"synthetic placeholder")
        result = self.build(self.root, self.root / "prepared")
        self.assertEqual(result["datasets"]["nih-chestxray14"]["local_images"], 1)
        self.assertEqual(csv_record(["name",""], ["synthetic",""]), {"name":"synthetic"})
        with self.assertRaises(ValueError):
            csv_record(["name",""], ["synthetic","unexpected"])


if __name__ == "__main__":
    unittest.main()
