from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.cli import main
from cxr_research.experiments.registry import check_registry, load_registry, mark_complete, record_problems, render_charts
from cxr_research.utils.errors import ContractError

TEMPLATE = ROOT / "configs/experiment_registry.template.json"
HAS_MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = load_registry(TEMPLATE)

    def write(self, registry):
        path = self.root / "registry.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def run_directory(self, name, metrics, status="complete"):
        directory = self.root / "runs" / name
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        (directory / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        return directory

    def test_template_samples_validate(self):
        self.assertEqual(len(self.template["experiments"]), 2)
        self.assertEqual(check_registry(self.template), [])

    def test_missing_and_malformed_fields_are_reported(self):
        record = copy.deepcopy(self.template["experiments"][0])
        del record["hardware"]
        record.update(commit="HEAD", jira_key="mvd9", seed=True, extra=1)
        problems = record_problems(record)
        for expected in ("missing hardware", "commit must", "jira_key must", "seed must", "unknown field: extra"):
            self.assertTrue(any(expected in problem for problem in problems), expected)

    def test_only_planned_runs_may_omit_artifact_path(self):
        record = copy.deepcopy(self.template["experiments"][1])
        self.assertEqual(record_problems(record), [])
        self.assertIn("missing artifact_path", record_problems(record, status="complete"))

    def test_duplicate_ids_are_rejected(self):
        registry = copy.deepcopy(self.template)
        registry["experiments"][1]["experiment_id"] = registry["experiments"][0]["experiment_id"]
        self.assertIn("experiments[1]: duplicate experiment_id", check_registry(registry))

    def test_complete_refuses_missing_fields_and_outputs(self):
        registry = copy.deepcopy(self.template)
        record = registry["experiments"][1]
        path = self.write(registry)
        with self.assertRaisesRegex(ContractError, "missing artifact_path"):
            mark_complete(path, record["experiment_id"])
        record["artifact_path"] = "runs/b1"
        path = self.write(registry)
        with self.assertRaisesRegex(ContractError, "lacks run.json, metrics.json"):
            mark_complete(path, record["experiment_id"])
        self.run_directory("b1", {}, status="failed")
        with self.assertRaisesRegex(ContractError, "run.json status"):
            mark_complete(path, record["experiment_id"])
        self.assertEqual(load_registry(path)["experiments"][1]["status"], "planned")

    def test_complete_updates_status_when_outputs_exist(self):
        registry = copy.deepcopy(self.template)
        registry["experiments"][1]["artifact_path"] = "runs/b1"
        path = self.write(registry)
        self.run_directory("b1", {})
        mark_complete(path, "example-b1-pilot-seed-42")
        self.assertEqual(load_registry(path)["experiments"][1]["status"], "complete")

    def test_check_command_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["registry", "check", "--registry", str(TEMPLATE)]), 0)
        registry = copy.deepcopy(self.template)
        registry["experiments"][0]["seed"] = -1
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["registry", "check", "--registry", str(self.write(registry))]), 1)

    @unittest.skipUnless(HAS_MATPLOTLIB, "chart tests need matplotlib from requirements.txt")
    def test_charts_regenerate_identically_from_metrics(self):
        registry = copy.deepcopy(self.template)
        registry["experiments"][1].update(status="complete", artifact_path="runs/b1")
        path = self.write(registry)
        self.run_directory("example-unet-synthetic-seed-42", {"dice": 0.81})
        self.run_directory("b1", {"primary": {"closed": {"closed_accuracy": 0.62}}})
        first = render_charts(path, self.root / "charts")
        self.assertEqual([Path(name).name for name in first],
                         ["dice.png", "primary.closed.closed_accuracy.png"])
        before = [Path(name).read_bytes() for name in first]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["registry", "charts", "--registry", str(path),
                                   "--output", str(self.root / "charts")]), 0)
        self.assertEqual([Path(name).read_bytes() for name in first], before)


if __name__ == "__main__":
    unittest.main()
