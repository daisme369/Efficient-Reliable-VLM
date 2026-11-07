"""An explicit per-run Jira exception never waives research protocol checks."""
import json
import tempfile
import unittest
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cxr_research.experiments.config import DataConfig, ExperimentConfig, check_protocol
from cxr_research.utils.errors import ContractError


class JiraWaiverTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "protocol.json"
        data = DataConfig(str(self.path.parent), str(self.path.parent / "manifest.jsonl"),
                          str(self.path.parent / "provenance.json"), synthetic=False)
        self.config = ExperimentConfig("waived-b1", "medgemma", "B1", data,
                                       str(self.path.parent / "runs"), protocol=str(self.path))
        self.document = {
            "status": "approved", "decision_record": "review-record",
            "split_policy": "patient-isolated", "input_policy": "frontal",
            "uncertain_label_policy": "preserve", "template_policy": "secondary",
            "metric_policy": "validation loss", "training_data_overlap": "not excluded",
            "access_review": "host-access-record", "config_sha256": self.config.fingerprint,
            "manifest_sha256": "manifest", "provenance_sha256": "provenance",
            "jira_waiver": {"approved_by": "user", "run_id": "waived-b1",
                            "decision_record": "user-request-2026-10-30",
                            "reason": "User explicitly requested skipping Jira for this run."},
        }

    def check(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        check_protocol(self.config, "manifest", "provenance")

    def test_explicit_per_run_waiver_is_accepted(self):
        self.check()

    def test_missing_malformed_or_other_run_waiver_fails(self):
        good = self.document["jira_waiver"]
        for waiver in (None, True, {}, {**good, "run_id": "different"},
                       {**good, "approved_by": "agent"}, {**good, "reason": " "},
                       {**good, "decision_record": "UNRESOLVED"}):
            with self.subTest(waiver=waiver):
                self.document["jira_waiver"] = waiver
                with self.assertRaisesRegex(ContractError, "Jira"):
                    self.check()

    def test_draft_and_unresolved_protocols_still_fail(self):
        for field, value in (("status", "draft"), ("metric_policy", "UNRESOLVED"),
                             ("access_review", "")):
            with self.subTest(field=field):
                old = self.document[field]
                self.document[field] = value
                with self.assertRaisesRegex(ContractError, "unresolved"):
                    self.check()
                self.document[field] = old

    def test_waiver_does_not_waive_fingerprint_binding(self):
        for field in ("config_sha256", "manifest_sha256", "provenance_sha256"):
            with self.subTest(field=field):
                old = self.document[field]
                self.document[field] = "changed"
                with self.assertRaisesRegex(ContractError, "fingerprints"):
                    self.check()
                self.document[field] = old

    def test_malformed_jira_is_not_treated_as_missing(self):
        self.config = replace(self.config, jira_issue="invalid")
        with self.assertRaisesRegex(ContractError, "Jira"):
            self.check()

    def test_regular_jira_path_still_works_without_waiver(self):
        self.config = replace(self.config, jira_issue="CXR-1")
        self.document["config_sha256"] = self.config.fingerprint
        del self.document["jira_waiver"]
        self.check()

    def test_synthetic_path_does_not_require_waiver(self):
        self.config = replace(self.config, data=replace(self.config.data, synthetic=True))
        check_protocol(self.config, "manifest", "provenance")


if __name__ == "__main__":
    unittest.main()
