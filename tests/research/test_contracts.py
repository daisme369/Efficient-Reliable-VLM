from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cxr_research.cli import main
from cxr_research.utils.errors import ContractError
from cxr_research.experiments.config import DataConfig
from cxr_research.experiments.config import ExperimentConfig
from cxr_research.experiments.config import check_protocol
from cxr_research.experiments.config import check_storage
from cxr_research.experiments.config import load_config
from cxr_research.experiments.config import validate_config
from cxr_research.utils.hashing import file_hash
from cxr_research.data.manifest import load_manifest
from cxr_research.metrics.vqa import normalize_answer
from cxr_research.metrics.vqa import vqa_metrics


class ContractTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "image.png").write_bytes(b"not-read-by-metadata-audit")
        (self.root / "mask.png").write_bytes(b"not-read-by-metadata-audit")
        self.data = DataConfig(str(self.root), str(self.root / "manifest.jsonl"),
                               str(self.root / "provenance.json"), True)
        self.config = ExperimentConfig("check", "unet", "S1", self.data, str(self.root / "runs"))

    def row(self, **overrides):
        return {"sample_id": "example-one", "patient_id": "private-sentinel", "patient_namespace": "synthetic",
                "image_id": "image-one", "image": "image.png", "split": "train", "task": "unet",
                "view": "synthetic", "mask": "mask.png",
                "image_sha256": file_hash(self.root / "image.png"),
                "mask_sha256": file_hash(self.root / "mask.png"), **overrides}

    def write_manifest(self, rows):
        path = Path(self.data.manifest)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        Path(self.data.provenance).write_text(json.dumps({"schema_version": 3, "synthetic": True,
            "sources": [{"kind": "generated", "name": "generated", "version": "1",
                         "access": "synthetic", "license": "synthetic", "sha256": "fixture"}],
            "created_at": "fixture", "producer_commit": "fixture", "exclusions": {}, "exclusion_stages": [],
            "split_policy": "fixture", "rows_before": len(rows), "rows_after": len(rows),
            "manifest_sha256": file_hash(path)}), encoding="utf-8")

    def preparation_spec(self, images_path: Path) -> dict:
        spec = {"image_root": str(self.root), "prepared_dir": str(self.root / "prepared"),
                "patient_namespace": "synthetic", "mask_coordinates": "original_resolution",
                "split_values": {"train": "train"}, "primary_template_ids": [],
                "answer_type_values": {"closed": "closed"},
                "images": {"path": str(images_path), "format": "jsonl", "name": "synthetic-images",
                           "version": "1", "access": "synthetic", "license": "synthetic",
                           "columns": {"image_id": "image_id", "patient_id": "patient_id",
                                       "image": "image", "view": "view", "split": "split"}}}
        for key in ("split_policy", "uncertain_label_policy", "input_policy", "template_policy",
                    "access_review", "producer_commit"):
            spec[key] = "synthetic-fixture"
        return spec

    def test_cross_task_patient_overlap_is_rejected_before_selection(self):
        self.write_manifest([self.row(), self.row(sample_id="qa", task="medgemma", split="test",
            image_id="second", question="Synthetic question?", answer="yes", answer_type="closed", template_id="s")])
        with self.assertRaisesRegex(ContractError, "patient split overlap"):
            load_manifest(self.data)

    def test_duplicate_image_with_different_patient_rejected(self):
        self.write_manifest([self.row(), self.row(sample_id="second", patient_id="another")])
        with self.assertRaisesRegex(ContractError, "image identity"):
            load_manifest(self.data)

    def test_test_split_cannot_be_selected(self):
        self.write_manifest([self.row()])
        with self.assertRaisesRegex(ContractError, "development splits"):
            load_manifest(self.data).select("unet", "test")

    def test_test_payload_is_rejected_from_runtime_manifest(self):
        self.write_manifest([self.row(split="test")])
        with self.assertRaisesRegex(ContractError, "final-test payload"):
            load_manifest(self.data)

    def test_unresolved_source_access_is_rejected(self):
        self.write_manifest([self.row()])
        path = Path(self.data.provenance)
        provenance = json.loads(path.read_text())
        provenance["sources"][0]["access"] = "UNRESOLVED"
        path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ContractError, "resolved source"):
            load_manifest(self.data)

    def test_provenance_rejects_unknown_source_kind_without_derivation(self):
        self.write_manifest([self.row()])
        path = Path(self.data.provenance)
        provenance = json.loads(path.read_text())
        provenance["sources"][0]["kind"] = "vqa"
        path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ContractError, "source kind"):
            load_manifest(self.data)

    def test_provenance_exclusions_must_match_stages(self):
        self.write_manifest([self.row()])
        path = Path(self.data.provenance)
        provenance = json.loads(path.read_text())
        provenance["exclusions"] = {"invented": 99}
        path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ContractError, "do not reconcile"):
            load_manifest(self.data)

    def test_top_level_provenance_decisions_must_be_resolved(self):
        for key in ("created_at", "producer_commit", "split_policy"):
            self.write_manifest([self.row()])
            path = Path(self.data.provenance)
            provenance = json.loads(path.read_text())
            provenance[key] = " UNRESOLVED "
            path.write_text(json.dumps(provenance))
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, "top-level"):
                load_manifest(self.data)

    def test_preparation_rejects_source_tables_inside_repository(self):
        from cxr_research.data.preparation import prepare
        repository = self.root / "checkout"
        repository.mkdir()
        source = repository / "images.jsonl"
        source.write_text("{}\n")
        spec = self.preparation_spec(source)
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ContractError, "outside the source repository"):
            prepare(path, repository)

    def test_preparation_requires_derived_qa_split_mapping(self):
        from cxr_research.data.preparation import prepare
        images = self.root / "images.jsonl"
        qa = self.root / "qa.jsonl"
        images.write_text("{}\n")
        qa.write_text("{}\n")
        spec = self.preparation_spec(images)
        spec["qa"] = {"path": str(qa), "format": "jsonl", "name": "synthetic-qa",
                      "version": "1", "access": "synthetic", "license": "synthetic",
                      "derivation": {"producer": "fixture", "method": "fixture",
                                     "source_report_section": "fixture", "filters": "fixture"},
                      "columns": {"image_id": "image_id", "question": "question",
                                  "answer": "answer", "answer_type": "answer_type",
                                  "template_id": "template_id"}}
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ContractError, "explicit split mapping"):
            prepare(path, self.root / "separate-checkout")

    def test_traversal_rejected(self):
        self.write_manifest([self.row(image="../private.png")])
        with self.assertRaises(ContractError):
            load_manifest(self.data)

    def test_manifest_checksum_is_enforced(self):
        self.write_manifest([self.row()])
        with Path(self.data.manifest).open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ContractError, "checksum"):
            load_manifest(self.data)

    def test_unknown_configuration_field_fails(self):
        raw = self.config.as_dict()
        raw["unet"]["horizontal_flip"] = True
        path = self.root / "config.json"
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ContractError, "unknown"):
            load_config(path)

    def test_image_or_mask_replacement_invalidates_snapshot(self):
        for filename in ("image.png", "mask.png"):
            self.write_manifest([self.row()])
            (self.root / filename).write_bytes(b"changed-asset")
            with self.assertRaisesRegex(ContractError, "asset checksum"):
                load_manifest(self.data)

    def test_unpinned_medgemma_is_blocked(self):
        with self.assertRaisesRegex(ContractError, "immutable"):
            validate_config(replace(self.config, task="medgemma", baseline="B0"))

    def test_boolean_numeric_settings_are_rejected(self):
        for config in (
            replace(self.config, train=replace(self.config.train, learning_rate=True)),
            replace(self.config, train=replace(self.config.train, weight_decay=True)),
            replace(self.config, unet=replace(self.config.unet, rotation_degrees=True)),
        ):
            with self.assertRaisesRegex(ContractError, "numeric settings"):
                validate_config(config)

    def test_real_output_in_repository_rejected(self):
        config = replace(self.config, data=replace(self.data, synthetic=False))
        with self.assertRaisesRegex(ContractError, "outside"):
            check_storage(config, self.root)

    def test_missing_real_protocol_fails(self):
        with self.assertRaisesRegex(ContractError, "Jira"):
            check_protocol(replace(self.config, data=replace(self.data, synthetic=False)), "a", "b")

    def test_real_protocol_requires_resolved_segmentation_decision(self):
        protocol = self.root / "protocol.json"
        config = replace(self.config, data=replace(self.data, synthetic=False), jira_issue="CXR-1",
                         protocol=str(protocol))
        document = {"status": "approved", "decision_record": "approved-record",
                    "split_policy": "approved", "input_policy": "approved",
                    "uncertain_label_policy": "approved", "template_policy": "approved",
                    "metric_policy": "approved", "training_data_overlap": "approved",
                    "access_review": "approved", "segmentation_training_decision": "UNRESOLVED",
                    "config_sha256": config.fingerprint, "manifest_sha256": "a",
                    "provenance_sha256": "b"}
        protocol.write_text(json.dumps(document))
        with self.assertRaisesRegex(ContractError, "CheXmask"):
            check_protocol(config, "a", "b")
        document["segmentation_training_decision"] = " unresolved "
        protocol.write_text(json.dumps(document))
        with self.assertRaisesRegex(ContractError, "CheXmask"):
            check_protocol(config, "a", "b")
        document["segmentation_training_decision"] = "approved-record"
        protocol.write_text(json.dumps(document))
        check_protocol(config, "a", "b")

    def test_evaluation_uses_inference_gpu_role_and_records_evidence(self):
        from cxr_research.experiments.environment import gpu_preflight
        completed = SimpleNamespace(returncode=0,
            stdout="GPU preflight passed: driver-versions=580.95.05, driver-release-min=580.", stderr="")
        fake_torch = SimpleNamespace(version=SimpleNamespace(cuda="13.0"),
            cuda=SimpleNamespace(device_count=lambda: 1, get_device_name=lambda _: "synthetic-gpu",
                                 get_device_capability=lambda _: (8, 9)))
        for baseline in ("B1", "S1"):
            config = replace(self.config, baseline=baseline,
                task="medgemma" if baseline == "B1" else "unet", device="cuda",
                data=replace(self.data, synthetic=False))
            with patch("cxr_research.experiments.environment.Path.exists", return_value=True), \
                    patch("cxr_research.experiments.environment.subprocess.run", return_value=completed) as run, \
                    patch("cxr_research.experiments.environment.version", return_value="2.14.1+cu130"), \
                    patch.dict(sys.modules, {"torch": fake_torch}), \
                    patch.dict("os.environ", {"CXR_CONTAINER_IMAGE_ID": "sha256:" + "a" * 64}):
                evidence = gpu_preflight(config, "evaluate", Path(__file__).resolve().parents[2])
            self.assertEqual(run.call_args.args[0][-2:], ["--role", "inference"])
            self.assertEqual(evidence["role"], "inference")
            self.assertEqual(evidence["driver_release_min"], 580)
            self.assertEqual(evidence["driver_versions"], ["580.95.05"])

    def test_cli_never_echoes_row_values(self):
        self.write_manifest([self.row(), self.row(sample_id="second", split="validation")])
        path = self.root / "config.json"
        path.write_text(json.dumps(self.config.as_dict()))
        output = io.StringIO()
        with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            code = main(["validate", "--config", str(path)])
        self.assertEqual(code, 2)
        self.assertNotIn("private-sentinel", output.getvalue())

    def test_meaning_is_preserved_in_normalization(self):
        self.assertEqual(normalize_answer("  NO  Left opacity "), "no left opacity")
        self.assertNotEqual(normalize_answer("no opacity"), normalize_answer("opacity"))

    def test_primary_and_secondary_are_separate_and_empty_counts_wrong(self):
        result = vqa_metrics([("YES", "yes", "closed", True), ("", "no", "closed", True),
                              ("yes", "yes", "closed", False)])
        self.assertEqual(result["primary"]["closed"]["closed_accuracy"], .5)
        self.assertEqual(result["primary"]["closed"]["empty_output_count"], 1)
        self.assertAlmostEqual(result["secondary"]["closed"]["closed_accuracy"], 2/3)


if __name__ == "__main__":
    unittest.main()
