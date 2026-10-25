"""Rental preparation with synthetic inventories and simulated GPU/cache boundaries."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/gpu"))
sys.path.insert(0, str(ROOT / "src"))

import bootstrap as rental_bootstrap
import prepare_rental as rental_plan
from cxr_research.experiments.config import load_config
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


class RentalPlanTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repository = self.base / "source"
        (self.repository / "configs").mkdir(parents=True)
        for name in ("catalog-preparation.template.json", "medgemma_b1.json", "protocol.template.json"):
            shutil.copyfile(ROOT / "configs" / name, self.repository / "configs" / name)
        self.data = self.base / "data"
        self.snapshot = self.data / "manifests/fixture-v1"
        self.snapshot.mkdir(parents=True)
        names = ("images.jsonl", "sources.jsonl", "vqa_links.jsonl", "vqa.train.jsonl", "vqa.validation.jsonl")
        for name in names:
            (self.snapshot / name).write_text("", encoding="utf-8")
        self.catalog = {"schema": "cxr-local-links-v1", "status": "complete", "split_policy": "mimic-official",
                        "official_patient_overlaps": 0, "identity_conflicts": 0,
                        "development_rows": {"train": 2, "validation": 1},
                        "output_sha256": {name: file_hash(self.snapshot / name) for name in names}}
        self.write(self.snapshot / "report.json", self.catalog)
        self.write(self.data / "manifests/latest.json", {"snapshot": "fixture-v1", "status": "complete",
                   "report_sha256": file_hash(self.snapshot / "report.json")})
        self.qc_path = self.data / "qc/report.json"
        self.qc_path.parent.mkdir()
        self.qc = {"status": "complete", "identity": {"inputs_sha256": {
            "report.json": file_hash(self.snapshot / "report.json")}}, "qa": {
            "counts": {"train.rows": 2, "validation.rows": 1},
            "source_sha256": {name: self.catalog["output_sha256"][name]
                              for name in ("vqa.train.jsonl", "vqa.validation.jsonl")}}}
        self.write(self.qc_path, self.qc)
        self.output = self.data / "rental/fixture-v1"

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def prepare(self):
        git = subprocess.CompletedProcess([], 0, stdout="fixture-commit", stderr="")
        with patch.object(rental_plan.subprocess, "run", return_value=git):
            return rental_plan.prepare_rental(self.data, self.qc_path, self.output, self.repository)

    def test_plan_pins_inputs_preserves_policies_and_remains_blocked(self):
        result = self.prepare()
        self.assertFalse(result["training_ready"])
        self.assertFalse(result["manifest_created"])
        prep = json.loads((self.output / "catalog-preparation.json").read_text())
        self.assertEqual(prep["catalog_report_sha256"], file_hash(self.snapshot / "report.json"))
        self.assertEqual(prep["uncertain_label_policy"], "preserve_source_answers")
        self.assertEqual(prep["review_policy"], "retain_and_flag")
        self.assertEqual(prep["primary_template_ids"], [])
        self.assertIn("answer_rendering", result["pending"]["preparation"])
        self.assertIn("medgemma.revision", result["pending"]["training"])
        self.assertIn("sources.qa.derivation.method", result["pending"]["preparation"])
        self.assertEqual(result["development_rows"], {"train": 2, "validation": 1})
        self.assertEqual(json.loads((self.output / "protocol-b1.json").read_text())["status"], "draft")
        for name, sha256 in result["draft_sha256"].items():
            self.assertEqual(sha256, file_hash(self.output / name))

    def test_existing_drafts_are_never_overwritten(self):
        self.prepare()
        sentinel = self.output / "b1.json"
        sentinel.write_text("user edits")
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertEqual(sentinel.read_text(), "user edits")

    def test_catalog_tamper_produces_no_drafts(self):
        (self.snapshot / "vqa.train.jsonl").write_text("synthetic tamper")
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_qc_from_another_catalog_is_rejected(self):
        self.qc["identity"]["inputs_sha256"]["report.json"] = "other"
        self.write(self.qc_path, self.qc)
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_qc_from_different_development_inputs_is_rejected(self):
        self.qc["qa"]["source_sha256"]["vqa.train.jsonl"] = "other"
        self.write(self.qc_path, self.qc)
        with self.assertRaises(ContractError):
            self.prepare()

    def test_output_inside_catalog_or_source_is_rejected(self):
        for output in (self.snapshot / "drafts", self.repository / "drafts", self.data,
                       self.data / "mimic-cxr-jpg/2.1.0/rental-draft",
                       self.data / "prepared/rental-draft"):
            self.output = output
            with self.assertRaises(ContractError):
                self.prepare()

    def test_raw_release_output_is_rejected_before_directory_creation(self):
        self.output = self.data / "mimic-cxr-jpg/2.1.0/rental-draft"
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_traversal_in_latest_pointer_is_rejected(self):
        self.write(self.data / "manifests/latest.json", {"snapshot": "../outside"})
        with self.assertRaises(ContractError):
            self.prepare()


class RentalBootstrapTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        image = self.data / "fixture.bin"
        image.write_bytes(b"synthetic image bytes for checksum verification")
        rows = [{"sample_id": f"fixture-{split}", "patient_id": f"generated-{split}",
                 "patient_namespace": "synthetic", "image_id": f"fixture-{split}",
                 "image": f"{split}.bin", "split": split, "task": "medgemma", "view": "synthetic",
                 "image_sha256": file_hash(image), "question": "Synthetic fixture?", "answer": "yes",
                 "answer_type": "closed", "template_id": "fixture", "primary": False}
                for split in ("train", "validation")]
        for split in ("train", "validation"):
            shutil.copyfile(image, self.data / f"{split}.bin")
        self.manifest = self.data / "manifest.jsonl"
        self.manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.provenance = self.data / "provenance.json"
        self.provenance.write_text(json.dumps({"schema_version": 3, "synthetic": False,
            "sources": [{"kind": "generated", "name": "synthetic", "version": "fixture",
                         "access": "synthetic", "license": "synthetic", "sha256": "fixture"}],
            "created_at": "fixture", "producer_commit": "fixture", "split_policy": "synthetic-disjoint",
            "exclusions": {}, "exclusion_stages": [], "rows_before": 2, "rows_after": 2,
            "manifest_sha256": file_hash(self.manifest)}))
        self.config_path = self.root / "b1.json"
        self.config = json.loads((ROOT / "configs/medgemma_b1.json").read_text())
        self.config["data"].update(root=str(self.data), manifest=str(self.manifest), provenance=str(self.provenance))
        self.config.update(output_root=str(self.root / "runs"), protocol=str(self.root / "protocol.json"),
                           jira_issue="FIXTURE-1")
        self.config["medgemma"]["revision"] = "a" * 40
        self.config_path.write_text(json.dumps(self.config))
        protocol = json.loads((ROOT / "configs/protocol.template.json").read_text())
        protocol.update({key: "synthetic-fixture-only" for key in protocol})
        protocol.update(status="approved", config_sha256=load_config(self.config_path).fingerprint,
                        manifest_sha256=file_hash(self.manifest), provenance_sha256=file_hash(self.provenance))
        Path(self.config["protocol"]).write_text(json.dumps(protocol))

    def check(self):
        return rental_bootstrap.bootstrap(self.config_path, self.cache, self.source)

    def test_preflight_validates_manifest_and_protocol_without_starting_training(self):
        with patch.object(rental_bootstrap, "gpu_preflight", return_value={"role": "train"}), \
             patch.object(rental_bootstrap, "check_cache"):
            result = self.check()
        self.assertEqual(result["status"], "preflight_passed")
        self.assertFalse(result["training_started"])
        self.assertEqual(result["manifest_sha256"], file_hash(self.manifest))
        self.assertFalse((self.root / "runs" / self.config["run_id"]).exists())

    def test_draft_protocol_stops_before_gpu_or_model_loading(self):
        path = Path(self.config["protocol"])
        protocol = json.loads(path.read_text())
        protocol["status"] = "draft"
        path.write_text(json.dumps(protocol))
        with patch.object(rental_bootstrap, "gpu_preflight") as gpu, \
             patch.object(rental_bootstrap, "check_cache") as cache:
            with self.assertRaises(ContractError):
                self.check()
        gpu.assert_not_called()
        cache.assert_not_called()

    def test_corrupt_image_stops_before_gpu(self):
        (self.data / "train.bin").write_bytes(b"changed")
        with patch.object(rental_bootstrap, "gpu_preflight") as gpu:
            with self.assertRaises(ContractError):
                self.check()
        gpu.assert_not_called()

    def test_gpu_failure_stops_before_cache_loading(self):
        with patch.object(rental_bootstrap, "gpu_preflight", side_effect=ContractError("fixture")), \
             patch.object(rental_bootstrap, "check_cache") as cache:
            with self.assertRaises(ContractError):
                self.check()
        cache.assert_not_called()

    def test_existing_run_id_is_rejected(self):
        (self.root / "runs" / self.config["run_id"]).mkdir(parents=True)
        with self.assertRaisesRegex(ContractError, "run ID"):
            self.check()

    def test_unavailable_git_provenance_stops_before_gpu(self):
        failed = subprocess.CompletedProcess([], 128, stdout="", stderr="synthetic git failure")
        with patch.object(rental_bootstrap.subprocess, "run", return_value=failed), \
             patch.object(rental_bootstrap, "gpu_preflight") as gpu:
            with self.assertRaisesRegex(ContractError, "Git commit"):
                self.check()
        gpu.assert_not_called()

    def test_online_model_loading_is_rejected(self):
        self.config["medgemma"]["local_files_only"] = False
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ContractError, "offline"):
            self.check()

    def test_cli_sanitizes_missing_private_paths_and_does_not_train(self):
        sentinel = "PRIVATE_SENTINEL"
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), patch("cxr_research.cli.main") as train:
            code = rental_bootstrap.main(["--config", str(self.root / sentinel), "--cache", str(self.cache), "--train"])
        self.assertEqual(code, 2)
        self.assertNotIn(sentinel, stream.getvalue())
        train.assert_not_called()

    def test_cached_sharded_weights_must_all_exist(self):
        snapshot = self.cache / "hub/snapshot"
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "part-1.safetensors"}}))
        (snapshot / "config.json").write_text("{}")
        with patch("huggingface_hub.snapshot_download", return_value=str(snapshot)):
            with self.assertRaisesRegex(ContractError, "incomplete"):
                rental_bootstrap.check_cache(load_config(self.config_path), self.cache)

    def test_cache_index_cannot_escape_snapshot(self):
        snapshot = self.cache / "hub/snapshot"
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "../outside"}}))
        with patch("huggingface_hub.snapshot_download", return_value=str(snapshot)):
            with self.assertRaisesRegex(ContractError, "invalid"):
                rental_bootstrap.check_cache(load_config(self.config_path), self.cache)


if __name__ == "__main__":
    unittest.main()
