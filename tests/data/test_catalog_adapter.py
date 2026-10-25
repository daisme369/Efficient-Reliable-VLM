"""Synthetic end-to-end catalog, CLI and canonical-loader checks."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from cxr_research.cli import main
from cxr_research.data.catalog import build
from cxr_research.data.catalog_adapter import prepare_catalog
from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import DataConfig
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from tests.data.test_build_manifests import fixture


class CatalogAdapterTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "checkout"
        (self.repository / "scripts/ci").mkdir(parents=True)
        (self.repository / "AGENTS-INSTRUCTIONS.md").write_text("Synthetic checkout")
        (self.repository / "scripts/ci/check_gpu_environment.py").write_text("# Synthetic marker")
        fixture(self.root)
        self.snapshot = self.root / "catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, self.snapshot, "mimic-official", repository=self.repository)
        self.commit = "a" * 40
        identity = patch("cxr_research.data.catalog_adapter._git_identity",
                         return_value=(self.commit, True))
        identity.start()
        self.addCleanup(identity.stop)
        self.spec = {"data_root": str(self.root), "catalog_dir": str(self.snapshot),
                     "prepared_dir": str(self.root / "prepared"), "patient_namespace": "mimic-cxr",
                     "split_policy": "mimic-official", "catalog_report_sha256": file_hash(self.snapshot / "report.json"),
                     "views": ["AP"], "answer_type_field": "semantic_type", "answer_type_values": {"synthetic": "open"},
                     "answer_rendering": "single", "primary_template_ids": ["synthetic"], "template_id_field": "template_program",
                     "sources": {"images": {"name": "MIMIC-CXR-JPG", "version": "2.1.0", "access": "synthetic",
                                            "license": "synthetic"},
                                 "qa": {"name": "MIMIC-Ext-MIMIC-CXR-VQA", "version": "1.0.0", "access": "synthetic",
                                        "license": "synthetic", "derivation": {"producer": "synthetic", "method": "synthetic",
                                                "source_report_section": "synthetic", "filters": "synthetic"}}}}
        for key in ("input_policy", "template_policy", "uncertain_label_policy", "access_review"):
            self.spec[key] = "synthetic-fixture-policy"
        self.spec["producer_commit"] = self.commit
        self.spec.update(uncertain_label_policy="preserve_source_answers", normalization="cxr-text-nfc-space-v1",
                         review_policy="retain_and_flag")
        self.spec_path = self.root / "adapter.json"

    def convert(self):
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        return prepare_catalog(self.spec_path, self.repository)

    def rewrite_export(self, name, rows):
        path = self.snapshot / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        report_path = self.snapshot / "report.json"
        report = json.loads(report_path.read_text())
        report["output_sha256"][name] = file_hash(path)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        self.spec["catalog_report_sha256"] = file_hash(report_path)

    def rows(self, name):
        return [json.loads(line) for line in (self.snapshot / name).read_text().splitlines()]

    def test_conversion_is_loadable_deterministic_and_keeps_test_sealed(self):
        result = self.convert()
        output = Path(self.spec["prepared_dir"])
        config = DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json"))
        manifest = load_manifest(config)
        self.assertEqual(result["splits"], {"train": 2, "validation": 1})
        self.assertEqual({s.patient_id for s in manifest.samples}, {"1", "2"})
        self.assertEqual({s.answer for s in manifest.samples}, {"synthetic answer"})
        self.assertTrue(all(s.primary and s.answer_type == "open" and not s.mask for s in manifest.samples))
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["schema_version"], 3)
        self.assertEqual(provenance["producer_commit"], self.commit)
        self.assertTrue(provenance["producer_dirty"])
        self.assertIn("catalog_adapter.py", provenance["producer_modules_sha256"])
        self.assertEqual(provenance["catalog_audit"]["qa_source_split_conflicts"], 3)
        self.assertEqual(provenance["catalog_report_sha256"], self.spec["catalog_report_sha256"])
        self.spec["prepared_dir"] = str(self.root / "second")
        self.assertEqual(self.convert()["sha256"], result["sha256"])
        with self.assertRaises(FileExistsError):
            self.convert()

    def test_unresolved_policy_blocks_before_writes(self):
        self.spec["uncertain_label_policy"] = " UNRESOLVED "
        with self.assertRaisesRegex(ContractError, "unresolved"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_producer_commit_must_match_current_checkout(self):
        self.spec["producer_commit"] = "b" * 40
        with self.assertRaisesRegex(ContractError, "current checkout"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_normalized_text_and_review_flags_are_written_without_relabeling(self):
        rows = self.rows("vqa.train.jsonl")
        rows[0].update(question="  Is left opacity unchanged? ", answer=[" Possible mild change. "])
        self.rewrite_export("vqa.train.jsonl", rows)
        self.convert()
        output = Path(self.spec["prepared_dir"])
        canonical = [json.loads(s) for s in (output / "manifest.jsonl").read_text().splitlines()]
        review = [json.loads(s) for s in (output / "qa-review.jsonl").read_text().splitlines()]
        self.assertEqual(canonical[0]["question"], "Is left opacity unchanged?")
        self.assertEqual(canonical[0]["answer"], "Possible mild change.")
        self.assertIn("uncertainty_cue", review[0]["flags"])
        self.assertIn("prior_study_cue", review[0]["flags"])
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["text_processing"]["review_sha256"], file_hash(output / "qa-review.jsonl"))

    def test_export_and_report_fingerprints_are_enforced(self):
        path = self.snapshot / "vqa.train.jsonl"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ContractError, "export checksum"):
            self.convert()
        self.spec["catalog_report_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "report checksum"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_source_test_patient_cannot_enter_development_even_if_reexported(self):
        rows = self.rows("vqa.train.jsonl")
        injected = dict(rows[0], image_id="synthetic-d", patient_id="4", study_id="44", row_index=99,
                        path="mimic-cxr-jpg/2.1.0/files/p4/p4/s44/synthetic-d.jpg")
        self.rewrite_export("vqa.train.jsonl", [*rows, injected])
        with self.assertRaisesRegex(ContractError, "test-seal violation"):
            self.convert()
        self.assertEqual(json.loads((Path(self.spec["prepared_dir"]) / "status.json").read_text())["status"], "failed")

    def test_full_official_population_is_audited_before_view_filter(self):
        images = self.rows("images.jsonl")
        images[-1]["patient_id"] = "2"
        self.rewrite_export("images.jsonl", images)
        with self.assertRaisesRegex(ContractError, "patient split overlap"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_answer_array_requires_explicit_rendering_and_taxonomy(self):
        rows = self.rows("vqa.train.jsonl")
        rows[0]["answer"] = ["synthetic one", "synthetic two"]
        self.rewrite_export("vqa.train.jsonl", rows)
        with self.assertRaisesRegex(ContractError, "multiple source answers"):
            self.convert()
        self.spec.update(answer_rendering="join", answer_separator="; ", prepared_dir=str(self.root / "joined"))
        self.convert()
        rows = [json.loads(line) for line in (self.root / "joined/manifest.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["answer"], "synthetic one; synthetic two")
        self.spec.update(answer_type_values={"other": "closed"}, prepared_dir=str(self.root / "unknown"))
        with self.assertRaisesRegex(ContractError, "approved taxonomy"):
            self.convert()

    def test_input_view_filter_counts_reconcile(self):
        images = self.rows("images.jsonl")
        images[1]["view"] = "PA"
        self.rewrite_export("images.jsonl", images)
        result = self.convert()
        self.assertEqual(result["rows"], 2)
        provenance = json.loads((Path(self.spec["prepared_dir"]) / "provenance.json").read_text())
        self.assertEqual(provenance["exclusions"], {"input_view": 1})
        self.assertEqual((provenance["rows_before"], provenance["rows_after"]), (3, 2))

    def test_empty_answers_require_explicit_policy_and_remain_in_review(self):
        rows = self.rows("vqa.train.jsonl")
        rows[0]["answer"] = []
        self.rewrite_export("vqa.train.jsonl", rows)
        with self.assertRaisesRegex(ContractError, "empty source answers"):
            self.convert()
        self.spec.update(empty_answer_policy="exclude_from_supervision", prepared_dir=str(self.root / "empty-policy"))
        result = self.convert()
        self.assertEqual(result["splits"], {"train": 1, "validation": 1})
        output = Path(self.spec["prepared_dir"])
        canonical = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
        review = [json.loads(line) for line in (output / "qa-review.jsonl").read_text().splitlines()]
        self.assertEqual(len(review), 3)
        self.assertTrue(review[0]["excluded_from_supervision"])
        self.assertIn("excluded_empty_supervision", review[0]["flags"])
        self.assertNotIn(review[0]["sample_id"], {row["sample_id"] for row in canonical})
        provenance = json.loads((output / "provenance.json").read_text())
        self.assertEqual(provenance["exclusions"], {"input_view": 0, "empty_answers": 1})
        self.assertEqual((provenance["rows_before"], provenance["rows_after"]), (3, 2))
        self.assertEqual(provenance["exclusion_stages"][1]["rows_after"], 2)

    def test_view_then_empty_answer_exclusions_reconcile(self):
        images = self.rows("images.jsonl")
        images[1]["view"] = "PA"
        self.rewrite_export("images.jsonl", images)
        rows = self.rows("vqa.train.jsonl")
        rows[0]["answer"] = []
        self.rewrite_export("vqa.train.jsonl", rows)
        self.spec["empty_answer_policy"] = "exclude_from_supervision"
        result = self.convert()
        self.assertEqual(result["rows"], 1)
        provenance = json.loads((Path(self.spec["prepared_dir"]) / "provenance.json").read_text())
        self.assertEqual(provenance["exclusions"], {"input_view": 1, "empty_answers": 1})
        self.assertEqual([stage["rows_after"] for stage in provenance["exclusion_stages"]], [2, 1])

    def test_smoke_image_budget_is_deterministic_and_explicit(self):
        self.spec.update(preparation_purpose="smoke_only", image_limit_per_split={"train": 1, "validation": 1})
        result = self.convert()
        self.assertEqual(result["splits"], {"train": 2, "validation": 1})
        rows = [json.loads(line) for line in (Path(self.spec["prepared_dir"]) / "manifest.jsonl").read_text().splitlines()]
        self.assertEqual({r["image_id"] for r in rows if r["split"] == "train"}, {"synthetic-a"})
        provenance = json.loads((Path(self.spec["prepared_dir"]) / "provenance.json").read_text())
        self.assertEqual(provenance["exclusions"], {"input_view": 0, "pilot_image_limit": 0})
        self.assertEqual(provenance["exclusion_stages"][1]["reason"],
                         "smoke-only image budget; lexical image-ID selection independent of answers")
        self.spec["prepared_dir"] = str(self.root / "second-pilot")
        self.assertEqual(self.convert()["sha256"], result["sha256"])

    def test_image_budget_requires_smoke_purpose_before_writes(self):
        self.spec["image_limit_per_split"] = {"train": 1, "validation": 1}
        with self.assertRaisesRegex(ContractError, "smoke-only"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    def test_publisher_image_checksum_is_verified(self):
        images = self.rows("images.jsonl")
        images[0]["expected_sha256"] = "0" * 64
        self.rewrite_export("images.jsonl", images)
        with self.assertRaisesRegex(ContractError, "publisher checksum"):
            self.convert()

    def test_cli_builds_catalog_and_prepares_it_without_row_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--repository", str(self.repository), "catalog", "--data-root", str(self.root),
                         "--output", str(self.root / "cli-catalog"), "--split-policy", "mimic-official"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertTrue((self.root / "cli-catalog/catalog.sqlite").is_file())
        self.spec_path.write_text(json.dumps(self.spec))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--repository", str(self.repository), "prepare-catalog", "--spec", str(self.spec_path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotIn("synthetic-a", output.getvalue())
        self.assertNotIn("Synthetic question", output.getvalue())
        self.assertNotIn("synthetic answer", output.getvalue())

    def test_catalog_import_and_help_do_not_load_ml_libraries(self):
        code = ("import sys; sys.path.insert(0, 'src'); "
                "import cxr_research.data.catalog, cxr_research.data.catalog_adapter; "
                "assert not {'torch', 'transformers', 'numpy', 'PIL'} & set(sys.modules); "
                "from cxr_research.cli import main; main(['catalog', '--help'])")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--data-root", result.stdout)


if __name__ == "__main__":
    unittest.main()
