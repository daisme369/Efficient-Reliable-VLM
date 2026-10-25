"""Catalog-to-lung-mask behavior using generated images and synthetic identities."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "scripts/gpu")]

from cxr_research.data.catalog import build
from cxr_research.data.manifest import load_manifest
from cxr_research.data import segmentation_catalog as adapter
from cxr_research.experiments.config import DataConfig, check_protocol, load_config
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from tests.data.test_build_manifests import csv_file, fixture
import unet as rental


class UNetCatalogTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "checkout"
        (self.repository / "configs").mkdir(parents=True)
        (self.repository / "AGENTS-INSTRUCTIONS.md").write_text("Synthetic checkout")
        (self.repository / "scripts/ci").mkdir(parents=True)
        (self.repository / "scripts/ci/check_gpu_environment.py").write_text("# Synthetic marker")
        for name in ("unet.json", "protocol.template.json"):
            shutil.copyfile(ROOT / "configs" / name, self.repository / "configs" / name)
        _, self.mask_source = fixture(self.root)
        for path in self.root.glob("mimic-cxr-jpg/**/*.jpg"):
            Image.new("L", (2, 2), 100).save(path)
        masks = [dict(dicom_id=f"synthetic-{name}", Height=2, Width=2,
                      **{"Dice RCA (Mean)": 0.8, "Left Lung": "1 1", "Right Lung": "4 1", "Heart": "2 1"})
                 for name in ("a", "b", "c", "d")]
        # Test and source-VQA-test mask payloads are deliberately invalid.
        for row in masks[2:]:
            row["Left Lung"] = "SEALED_TARGET_MUST_NOT_BE_DECODED"
        csv_file(self.mask_source, list(masks[0]), masks)
        self.snapshot = self.root / "catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, self.snapshot, "mimic-official", repository=self.repository)
        self.spec = {"data_root": str(self.root), "catalog_dir": str(self.snapshot),
                     "catalog_report_sha256": file_hash(self.snapshot / "report.json"),
                     "prepared_dir": str(self.root / "prepared/pilot"), "purpose": "smoke_only",
                     "image_limit_per_split": {"train": 1, "validation": 1}, "target": "binary_lung_union",
                     "mask_coordinates": "original_resolution", "split_policy": "mimic-official",
                     "views": ["AP", "PA"], "minimum_rca": 0.7, "quality_policy": "synthetic only",
                     "segmentation_training_decision": "synthetic fixture preparation, not research approval",
                     "access_review": "synthetic fixtures only", "sources": {
                         "images": {"name": "MIMIC-CXR-JPG", "version": "2.1.0", "access": "synthetic", "license": "synthetic"},
                         "masks": {"name": "CheXmask", "version": "1.0.0", "access": "synthetic", "license": "synthetic"}}}
        self.spec_path = self.root / "prepare-unet.json"
        self.git = subprocess.CompletedProcess([], 0, stdout="a" * 40, stderr="")

    def convert(self):
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        with patch.object(adapter.subprocess, "run", return_value=self.git):
            return adapter.prepare_unet_catalog(self.spec_path, self.repository)

    def rewrite_images(self, change):
        path = self.snapshot / "images.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        change(rows)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        report = self.snapshot / "report.json"
        value = json.loads(report.read_text())
        value["output_sha256"][path.name] = file_hash(path)
        report.write_text(json.dumps(value))
        self.spec["catalog_report_sha256"] = file_hash(report)

    def test_canonical_mask_pixels_test_seal_and_no_qa_payload_access(self):
        from PIL import Image
        for name in ("vqa.train.jsonl", "vqa.validation.jsonl"):
            (self.snapshot / name).unlink()
        result = self.convert()
        self.assertEqual(result["splits"], {"train": 1, "validation": 1})
        self.assertEqual(result["exclusions"]["test_sealed"], 2)
        output = Path(self.spec["prepared_dir"])
        manifest = load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))
        self.assertEqual({s.patient_id for s in manifest.samples}, {"1", "2"})
        self.assertEqual({s.task for s in manifest.samples}, {"unet"})
        with Image.open(self.root / manifest.samples[0].mask) as mask:
            self.assertEqual(list(mask.get_flattened_data()), [255, 0, 0, 255])
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertFalse(provenance["segmentation"]["heart_used"])
        self.assertFalse(provenance["test_masks_decoded"])
        self.assertFalse(result["training_ready"])

    def test_deterministic_manifest_and_no_overwrite(self):
        first = self.convert()
        # Output mask paths are intentionally part of identity; same rows/mask bytes in a new directory.
        original = Path(self.spec["prepared_dir"])
        before = (original / "manifest.jsonl").read_bytes()
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.convert()
        self.assertEqual((original / "manifest.jsonl").read_bytes(), before)
        self.spec["prepared_dir"] = str(self.root / "prepared/another")
        second = self.convert()
        self.assertEqual(first["splits"], second["splits"])
        one = [json.loads(x) for x in before.decode().splitlines()]
        two = [json.loads(x) for x in (Path(self.spec["prepared_dir"]) / "manifest.jsonl").read_text().splitlines()]
        self.assertEqual([(r["image_id"], r["image_sha256"], r["mask_sha256"]) for r in one],
                         [(r["image_id"], r["image_sha256"], r["mask_sha256"]) for r in two])

    def test_all_official_patients_audited_before_view_or_budget_filters(self):
        def change(rows):
            rows[-1]["patient_id"] = "2"
            rows[-1]["view"] = "LATERAL"
        self.rewrite_images(change)
        with self.assertRaisesRegex(ContractError, "patient split overlap"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_changed_mask_source_and_catalog_exports_fail_before_writes(self):
        with self.mask_source.open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ContractError, "mask source changed"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())
        with (self.snapshot / "images.jsonl").open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ContractError, "export checksum"):
            self.convert()

    def test_unselected_catalog_path_conflicts_and_escapes_still_fail(self):
        for variant in ("conflict", "escape"):
            original = (self.snapshot / "images.jsonl").read_text()
            def change(rows):
                rows[-1]["path"] = rows[0]["path"] if variant == "conflict" else "../outside.jpg"
            self.rewrite_images(change)
            with self.assertRaisesRegex(ContractError, "path conflicts|escapes"):
                self.convert()
            self.assertFalse(Path(self.spec["prepared_dir"]).exists())
            (self.snapshot / "images.jsonl").write_text(original)

    def test_selected_publisher_checksum_is_verified(self):
        self.rewrite_images(lambda rows: rows[0].update(expected_sha256="0" * 64))
        with self.assertRaisesRegex(ContractError, "publisher checksum"):
            self.convert()

    def test_source_dimensions_and_publisher_hash_are_checked(self):
        self.rewrite_images(lambda rows: rows[0]["anatomical_mask"].update(width=3))
        with self.assertRaisesRegex(ContractError, "metadata differs"):
            self.convert()
        self.assertEqual(json.loads((Path(self.spec["prepared_dir"]) / "status.json").read_text())["status"], "failed")

    def test_empty_or_low_quality_population_is_not_silently_used(self):
        self.spec["minimum_rca"] = 0.9
        with self.assertRaisesRegex(ContractError, "population is empty"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_policies_threshold_and_budgets_fail_before_writes(self):
        for key, value in (("target", "heart"), ("quality_policy", " UNRESOLVED "), ("minimum_rca", float("nan")),
                           ("minimum_rca", True), ("purpose", "training"), ("views", ["LATERAL"]),
                           ("image_limit_per_split", {"train": 0, "validation": 1})):
            with self.subTest(key=key, value=str(value)):
                previous = self.spec[key]
                self.spec[key] = value
                with self.assertRaises(ContractError):
                    self.convert()
                self.assertFalse(Path(self.spec["prepared_dir"]).exists())
                self.spec[key] = previous

    def test_output_cannot_overwrite_catalog_or_source(self):
        self.spec["prepared_dir"] = str(self.snapshot)
        with self.assertRaisesRegex(ContractError, "separate private"):
            self.convert()

    def configure(self):
        self.convert()
        output = self.root / "rental/unet-fixture"
        result = rental.prepare_unet_run(Path(self.spec["prepared_dir"]), output, "FIXTURE-1", self.repository)
        return result, output

    def test_private_configs_transfer_inventory_and_draft_gate(self):
        result, output = self.configure()
        self.assertFalse(result["training_ready"])
        self.assertEqual(result["transfer_files"], 6)
        local = load_config(output / "unet-local.json")
        self.assertEqual(local.task, "unet")
        self.assertEqual(local.jira_issue, "FIXTURE-1")
        self.assertEqual(local.train.epochs, 2)
        manifest = load_manifest(local.data)
        for name, config_name in (("protocol-unet.json", "unet.json"), ("protocol-unet-local.json", "unet-local.json")):
            protocol = json.loads((output / name).read_text())
            self.assertEqual(protocol["status"], "draft")
            self.assertEqual(protocol["config_sha256"], load_config(output / config_name).fingerprint)
            self.assertEqual(protocol["manifest_sha256"], manifest.sha256)
        with self.assertRaisesRegex(ContractError, "unresolved"):
            check_protocol(local, manifest.sha256, manifest.provenance_sha256)
        with self.assertRaisesRegex(ContractError, "new private"):
            rental.prepare_unet_run(Path(self.spec["prepared_dir"]), output, "FIXTURE-1", self.repository)

    def test_draft_stops_preflight_before_gpu_and_training(self):
        _, output = self.configure()
        with patch("cxr_research.experiments.environment.gpu_preflight") as gpu:
            with self.assertRaisesRegex(ContractError, "unresolved"):
                rental.preflight_unet(output / "unet-local.json", self.repository)
        gpu.assert_not_called()

    def test_synthetic_approved_fixture_checks_without_training(self):
        _, output = self.configure()
        protocol = output / "protocol-unet-local.json"
        raw = json.loads(protocol.read_text())
        raw.update(status="approved", decision_record="synthetic fixture only")
        protocol.write_text(json.dumps(raw))
        with patch("cxr_research.experiments.environment.gpu_preflight", return_value={"role": "train"}), \
             patch.object(rental.subprocess, "run", return_value=self.git):
            result = rental.preflight_unet(output / "unet-local.json", self.repository)
        self.assertFalse(result["training_started"])
        self.assertEqual(result["gpu"], {"role": "train"})
        self.assertFalse((self.root / "runs/unet/unet-fixture").exists())

    def test_cli_sanitizes_unexpected_exception_and_never_starts_train(self):
        output = io.StringIO()
        with patch.object(rental, "preflight_unet", side_effect=RuntimeError("PRIVATE_SENTINEL")), \
             patch("cxr_research.cli.main") as train, contextlib.redirect_stderr(output):
            code = rental.main(["train", "--config", "PRIVATE_SENTINEL"])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
        train.assert_not_called()


if __name__ == "__main__":
    unittest.main()
