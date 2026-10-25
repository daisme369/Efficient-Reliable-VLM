"""Synthetic data-processing checks; no real dataset or patient content."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from cxr_research.cli import main
from cxr_research.data.catalog import build
from cxr_research.data.quality import audit_catalog
from cxr_research.preprocessing.annotations import scene_regions, transform_box, validate_box
from cxr_research.preprocessing.labels import TEXT_VERSION, label_state, normalize_text, process_qa, review_qa
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from tests.data.test_build_manifests import fixture


class TextProcessingTests(unittest.TestCase):
    def test_nfc_and_whitespace_preserve_clinical_words_punctuation_and_case(self):
        text = "  NO\tleft opacity;  possible mild change.\nCafe\u0301 "
        self.assertEqual(normalize_text(text), "NO left opacity; possible mild change. Café")
        self.assertEqual(normalize_text("① ㎜ ½"), "① ㎜ ½")
        self.assertEqual(normalize_text(text, "identity"), text)
        for a, b in [("no opacity", "opacity"), ("left", "right"), ("small", "large"),
                     ("possible opacity", "opacity"), ("not mentioned", "absent")]:
            self.assertNotEqual(normalize_text(a), normalize_text(b))

    def test_review_cues_retain_temporal_and_uncertain_answers(self):
        result = process_qa("  Is the left opacity unchanged? ", ["No; possible mild interval change."])
        self.assertEqual(result.answers, ("No; possible mild interval change.",))
        self.assertEqual(set(result.flags), {"negation_cue", "laterality_cue", "degree_cue", "uncertainty_cue", "prior_study_cue"})
        self.assertTrue(result.changed)
        self.assertEqual(process_qa("Synthetic?", ["yes"]).flags, ())
        for answers in ([], [" "], "yes", [None]):
            with self.subTest(answers=answers), self.assertRaises(ContractError):
                process_qa("Synthetic?", answers)

    def test_label_mapping_keeps_missing_and_uncertain_distinct(self):
        mapping = {"1": "positive", "0": "negative", "-1": "uncertain", "": "missing", "<NULL>": "missing"}
        self.assertEqual([label_state(v, mapping) for v in [1, 0, -1, "", None]],
                         ["positive", "negative", "uncertain", "missing", "missing"])
        with self.assertRaisesRegex(ContractError, "unmapped"):
            label_state("unknown", mapping)

    def test_incomplete_source_answers_are_retained_for_review_but_block_training(self):
        for answers, flag in [([], "empty_answer_array"), ([""], "empty_answer_text"), ([1], "non_string_answer"),
                              (None, "answer_not_array")]:
            with self.subTest(flag=flag):
                reviewed = review_qa("Synthetic?", answers)
                self.assertEqual(reviewed["answer"], answers)
                self.assertFalse(reviewed["supervision_valid"])
                self.assertIn(flag, reviewed["flags"])
                with self.assertRaisesRegex(ContractError, "incomplete supervision"):
                    process_qa("Synthetic?", answers)


class AnnotationTests(unittest.TestCase):
    def test_original_coordinate_box_roundtrip_uses_actual_letterbox_geometry(self):
        from PIL import Image
        from cxr_research.preprocessing.images import letterbox
        _, _, _, geometry = letterbox(Image.new("L", (101, 59)), Image.new("L", (101, 59)), 64)
        original = (3, 4, 99, 58)
        recovered = transform_box(transform_box(original, geometry), geometry, inverse=True)
        for a, b in zip(original, recovered):
            self.assertAlmostEqual(a, b)
        for box in [(-1, 0, 20, 20), (0, 0, 102, 59), (1, 1, 1, 2), (0, 0, float("nan"), 2)]:
            with self.assertRaises(ContractError):
                validate_box(box, 101, 59)

    def test_scene_graph_preserves_explicit_negation_and_omits_report_text(self):
        graph = {"objects": [{"bbox_name": "synthetic lung", "original_x1": 0, "original_y1": 0,
                               "original_x2": 2, "original_y2": 2}],
                 "attributes": [{"bbox_name": "synthetic lung", "phrases": ["private text sentinel"],
                                 "attributes": [["anatomicalfinding|no|opacity", "anatomicalfinding|yes|synthetic"]]}]}
        result = scene_regions(graph, width=2, height=2)
        self.assertEqual([a["state"] for a in result[0]["attributes"]], ["negative", "positive"])
        self.assertNotIn("private text sentinel", json.dumps(result))
        graph["attributes"][0]["attributes"] = [["category|unknown|label"]]
        with self.assertRaises(ContractError):
            scene_regions(graph, width=2, height=2)

    def test_qc_retains_invalid_boxes_and_unlocalized_attributes_without_inventing_geometry(self):
        graph = {"objects": [{"bbox_name": "synthetic lung", "original_x1": -1, "original_y1": 0,
                               "original_x2": 3, "original_y2": 2}],
                 "attributes": [{"bbox_name": "synthetic other region", "attributes": [["category|no|label"]]}]}
        with self.assertRaises(ContractError):
            scene_regions(graph, width=2, height=2)
        regions = scene_regions(graph, width=2, height=2, strict=False)
        self.assertEqual([r["box_status"] for r in regions], ["invalid_source_box", "unlocalized_attribute"])
        self.assertTrue(all(r["xyxy"] is None for r in regions))
        self.assertEqual(regions[1]["attributes"][0]["state"], "negative")


class ProcessingIntegrationTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "checkout"
        (self.repository / "scripts/ci").mkdir(parents=True)
        (self.repository / "AGENTS-INSTRUCTIONS.md").write_text("synthetic")
        (self.repository / "scripts/ci/check_gpu_environment.py").write_text("# synthetic")
        qa, _ = fixture(self.root)
        for path in self.root.rglob("*.jpg"):
            image = Image.new("L", (2, 2))
            image.putdata([0, 60, 120, 240])
            image.save(path)
        path = qa / "train.json"
        rows = json.loads(path.read_text())
        rows[0].update(question="  Is left opacity unchanged? ", answer=["Possible mild change."])
        path.write_text(json.dumps(rows))
        self.snapshot = self.root / "catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, self.snapshot, "mimic-official", repository=self.repository)
        self.output = self.root / "qc" / "fixture-v1"
        self.spec = {"data_root": str(self.root), "catalog_dir": str(self.snapshot), "output_dir": str(self.output),
                     "catalog_report_sha256": file_hash(self.snapshot / "report.json"),
                     "datasets": ["mimic-cxr"], "image_limit_per_dataset": 2, "voi_policy": "none",
                     "normalization": TEXT_VERSION, "uncertain_label_policy": "preserve_source_answers",
                     "review_policy": "retain_and_flag"}
        self.spec_path = self.root / "qc.json"

    def run_audit(self):
        self.spec_path.write_text(json.dumps(self.spec))
        return audit_catalog(self.spec_path, self.repository)

    def test_full_development_qa_processing_and_resume_are_deterministic(self):
        result = self.run_audit()
        self.assertEqual(result["qa"]["counts"]["train.rows"], 2)
        self.assertEqual(result["qa"]["counts"]["validation.rows"], 1)
        self.assertEqual(result["images"]["datasets"]["mimic-cxr"]["checked"], 2)
        self.assertEqual(result["images"]["datasets"]["mimic-cxr"]["masks_decoded"], 1)
        rows = [json.loads(s) for s in (self.output / "vqa.processed.train.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["question"], "Is left opacity unchanged?")
        self.assertEqual(rows[0]["answer"], ["Possible mild change."])
        self.assertIn("prior_study_cue", rows[0]["review_flags"])
        self.assertTrue(all(r["test_sealed"] == 0 and r["patient_id"] == "1" for r in rows))
        self.assertFalse(result["training_ready"])
        self.assertEqual(result["program_review"]["source_program_ids"], 1)
        self.assertEqual(self.run_audit(), result)

    def test_resume_rejects_configuration_and_completed_output_changes(self):
        self.run_audit()
        self.spec["image_limit_per_dataset"] = 3
        with self.assertRaisesRegex(ContractError, "configuration changed"):
            self.run_audit()
        self.spec["image_limit_per_dataset"] = 2
        (self.output / "vqa.processed.train.jsonl").write_text("tampered")
        with self.assertRaisesRegex(ContractError, "output changed"):
            self.run_audit()

    def test_catalog_database_must_match_published_digest(self):
        with (self.snapshot / "catalog.sqlite").open("ab") as stream:
            stream.write(b"synthetic-tamper")
        with self.assertRaisesRegex(ContractError, "database checksum"):
            self.run_audit()
        self.assertFalse(self.output.exists())

    def test_qc_output_cannot_modify_raw_release_storage(self):
        self.spec["output_dir"] = str(self.root / "mimic-cxr-jpg" / "2.1.0" / "qc-typo")
        with self.assertRaisesRegex(ContractError, "separate private"):
            self.run_audit()
        self.assertFalse(Path(self.spec["output_dir"]).exists())

    def test_resume_rechecks_changed_image_bytes(self):
        self.run_audit()
        image = self.root / "mimic-cxr-jpg/2.1.0/files/p1/p1/s11/synthetic-a.jpg"
        image.write_bytes(b"synthetic corrupt bytes")
        result = self.run_audit()
        self.assertEqual(result["images"]["datasets"]["mimic-cxr"]["image_failed"], 1)

    def test_completed_qa_is_reused_after_interrupted_image_phase(self):
        with patch("cxr_research.data.quality.audit_images", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_audit()
        output_hash = file_hash(self.output / "vqa.processed.train.jsonl")
        with patch("cxr_research.data.quality.audit_qa", side_effect=AssertionError("must reuse QA checkpoint")):
            self.run_audit()
        self.assertEqual(file_hash(self.output / "vqa.processed.train.jsonl"), output_hash)

    def test_cli_emits_only_aggregates(self):
        self.spec_path.write_text(json.dumps(self.spec))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--repository", str(self.repository), "audit-catalog", "--spec", str(self.spec_path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotIn("synthetic-a", output.getvalue())
        self.assertNotIn("Possible mild change", output.getvalue())

    def dicom(self, photo="MONOCHROME2", **overrides):
        import numpy as np
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = "1.2.826.0.1.3680043.10.999.1"
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0"*128)
        ds.Rows, ds.Columns = 2, 2
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, photo
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
        ds.PixelData = np.array([[0, 100], [200, 300]], dtype="<u2").tobytes()
        for key, value in overrides.items():
            setattr(ds, key, value)
        path = self.root / "synthetic.dicom"
        ds.save_as(path, enforce_file_format=True)
        return path

    def test_dicom_photometric_padding_and_rescale(self):
        from cxr_research.preprocessing.dicom import decode_dicom
        pixels, meta = decode_dicom(self.dicom(RescaleSlope=2, RescaleIntercept=-10), voi_policy="none")
        self.assertEqual(pixels.tolist(), [[0, 85], [170, 255]])
        self.assertFalse(meta["orientation_changed"])
        pixels, _ = decode_dicom(self.dicom("MONOCHROME1", PixelPaddingValue=0), voi_policy="none")
        self.assertEqual(pixels.tolist(), [[0, 255], [128, 0]])
        pixels, _ = decode_dicom(self.dicom(WindowCenter=100, WindowWidth=100), voi_policy="dicom")
        self.assertEqual(pixels.tolist(), [[0, 129], [255, 255]])
        with self.assertRaisesRegex(ContractError, "VOI policy"):
            decode_dicom(self.root / "unused", voi_policy="guess")
        with self.assertRaisesRegex(ContractError, "single-frame"):
            decode_dicom(self.dicom(NumberOfFrames=2), voi_policy="none")

    def test_qc_can_drop_only_fully_opaque_rgba_alpha(self):
        from PIL import Image
        from cxr_research.data.image_audit import inspect_image
        path = self.root / "synthetic-rgba.png"
        Image.new("RGBA", (2, 2), (50, 50, 50, 255)).save(path)
        self.assertEqual(inspect_image(path, None, voi_policy="none")["alpha_policy"], "discard_fully_opaque_alpha_only")
        Image.new("RGBA", (2, 2), (50, 50, 50, 120)).save(path)
        with self.assertRaises(ContractError):
            inspect_image(path, None, voi_policy="none")


if __name__ == "__main__":
    unittest.main()
