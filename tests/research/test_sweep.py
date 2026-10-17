from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cxr_research.cli import main
from cxr_research.experiments.sweep import load_sweep, run_sweep
from cxr_research.utils.errors import ContractError

HAS_YAML = importlib.util.find_spec("yaml") is not None
HAS_ML = all(importlib.util.find_spec(name) for name in ("torch", "numpy", "PIL"))


@unittest.skipUnless(HAS_YAML, "run YAML sweep tests in the pinned project environment")
class SweepTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {
            "schema_version": 1, "sweep_id": "pilot", "seeds": [42, 17],
            "base": {"task": "unet", "baseline": "S1", "device": "cpu",
                     "output_root": str(self.root / "outputs"),
                     "data": {"root": str(self.root), "manifest": str(self.root / "manifest.jsonl"),
                              "provenance": str(self.root / "provenance.json"), "synthetic": True},
                     "train": {"epochs": 1, "batch_size": 2, "learning_rate": 0.003},
                     "unet": {"size": 32, "base_channels": 8}},
            "variants": [{"name": "plain"}, {"name": "rotate", "overrides": {"unet": {"rotation_degrees": 5}}}],
        }
        self.path = self.root / "sweep.yaml"

    def write_spec(self, spec=None):
        import yaml
        self.path.write_text(yaml.safe_dump(self.spec if spec is None else spec, sort_keys=False), encoding="utf-8")
        return self.path

    def test_cartesian_order_deep_overrides_and_independent_configs(self):
        plan = load_sweep(self.write_spec(), ROOT)
        self.assertEqual([r.config.run_id for r in plan.runs],
                         ["plain-seed-42", "plain-seed-17", "rotate-seed-42", "rotate-seed-17"])
        self.assertEqual([r.config.unet.rotation_degrees for r in plan.runs], [0, 0, 5, 5])
        self.assertTrue(all(r.config.unet.size == 32 and r.config.train.batch_size == 2 for r in plan.runs))
        self.assertEqual(len({r.config.fingerprint for r in plan.runs}), 4)
        self.assertTrue(all(Path(r.config.output_root) == self.root / "outputs/pilot/runs" for r in plan.runs))
        self.assertEqual(load_sweep(self.path, ROOT).preview(), plan.preview())

    def test_cli_dry_run_has_no_writes_or_child_process(self):
        output = io.StringIO()
        self.write_spec()
        with patch("cxr_research.experiments.sweep.subprocess.run") as child, contextlib.redirect_stdout(output):
            self.assertEqual(main(["sweep", "--config", str(self.path), "--dry-run"]), 0)
        child.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["run_count"], 4)
        self.assertFalse((self.root / "outputs").exists())

    def test_reject_bad_matrix_and_unknown_later_override_before_launch(self):
        cases = []
        for seeds in ([], [1, 1], [True], [-1], [2**32]):
            case = copy.deepcopy(self.spec)
            case["seeds"] = seeds
            cases.append(case)
        for overrides in ({"seed": 2}, {"run_id": "other"}, {"output_root": "/other"},
                          {"train": {"learn_rate": .01}}, {"train": {"epochs": 0}}):
            case = copy.deepcopy(self.spec)
            case["variants"][1]["overrides"] = overrides
            cases.append(case)
        case = copy.deepcopy(self.spec)
        case["variants"][1]["name"] = "PLAIN"
        cases.append(case)
        for case in cases:
            with self.subTest(case=case), patch("cxr_research.experiments.sweep.subprocess.run") as child:
                with self.assertRaises(ContractError):
                    load_sweep(self.write_spec(case), ROOT)
                child.assert_not_called()
        self.assertFalse((self.root / "outputs").exists())

    def test_yaml_duplicate_keys_tags_and_aliases_are_sanitized(self):
        for content in ("seeds: [1]\nseeds: [2]\n", "base: !!python/object:private-sentinel {}",
                        "base: [private-sentinel", "base: &shared {}\nvariants: *shared\n"):
            self.path.write_text(content, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(main(["sweep", "--config", str(self.path), "--dry-run"]), 2)
            self.assertNotIn("private-sentinel", output.getvalue())

    def test_protocol_mapping_and_real_storage_checks_are_preserved(self):
        self.spec["protocols"] = {"plain-seed-42": str(self.root / "protocol-42.json")}
        plan = load_sweep(self.write_spec(), ROOT)
        self.assertEqual(plan.runs[0].config.protocol, str(self.root / "protocol-42.json"))
        self.assertIsNone(plan.runs[1].config.protocol)
        self.spec["base"]["data"]["synthetic"] = False
        with self.assertRaisesRegex(ContractError, "outside"):
            load_sweep(self.write_spec(), ROOT)

    def test_frozen_baseline_is_rejected(self):
        self.spec["base"].update(task="medgemma", baseline="B0", medgemma={"revision": "a" * 40})
        with self.assertRaisesRegex(ContractError, "frozen B0"):
            load_sweep(self.write_spec(), ROOT)

    def run_with_fake_children(self, plan, codes):
        seen = []
        def child(command, **kwargs):
            resolved = json.loads(Path(command[-1]).read_text())
            self.assertEqual(command[:3], [sys.executable, "-m", "cxr_research"])
            self.assertEqual(command[-3], "train")
            state = json.loads((plan.directory / "sweep.json").read_text())
            self.assertEqual([e["status"] for e in state["runs"]].count("running"), 1)
            code = codes[len(seen)]
            seen.append(resolved["run_id"])
            if isinstance(code, BaseException):
                raise code
            if code == 0:
                run = next(r for r in plan.runs if r.config.run_id == resolved["run_id"])
                directory = Path(resolved["output_root"]) / resolved["run_id"]
                directory.mkdir(parents=True)
                (directory / "run.json").write_text(json.dumps({"status": "complete", "config_sha256": run.config.fingerprint}))
            return subprocess.CompletedProcess(command, code)
        with patch("cxr_research.experiments.sweep.subprocess.run", side_effect=child), contextlib.redirect_stdout(io.StringIO()):
            code = run_sweep(plan, ROOT)
        return code, seen, json.loads((plan.directory / "sweep.json").read_text())

    def test_default_stop_keeps_failed_and_unstarted_runs_and_blocks_overwrite(self):
        plan = load_sweep(self.write_spec(), ROOT)
        code, seen, result = self.run_with_fake_children(plan, [0, 2])
        self.assertEqual(code, 1)
        self.assertEqual(seen, ["plain-seed-42", "plain-seed-17"])
        self.assertEqual([r["status"] for r in result["runs"]], ["complete", "failed", "not_run", "not_run"])
        self.assertEqual(result["runs"][1]["exit_code"], 2)
        self.assertEqual(len(list((plan.directory / "configs").glob("*.json"))), 4)
        with self.assertRaisesRegex(ContractError, "already exists"):
            run_sweep(plan, ROOT)

    def test_continue_attempts_remaining_runs_and_returns_failure(self):
        self.spec["on_error"] = "continue"
        plan = load_sweep(self.write_spec(), ROOT)
        code, seen, result = self.run_with_fake_children(plan, [1, 0, 0, 0])
        self.assertEqual(code, 1)
        self.assertEqual(len(seen), 4)
        self.assertEqual([r["status"] for r in result["runs"]], ["failed", "complete", "complete", "complete"])

    def test_interrupt_never_starts_later_runs(self):
        plan = load_sweep(self.write_spec(), ROOT)
        with self.assertRaises(KeyboardInterrupt):
            self.run_with_fake_children(plan, [0, KeyboardInterrupt()])
        summary = json.loads((plan.directory / "sweep.json").read_text())
        self.assertEqual(summary["status"], "interrupted")
        self.assertEqual([r["status"] for r in summary["runs"]], ["complete", "interrupted", "not_run", "not_run"])

    def test_zero_exit_without_run_evidence_is_failure(self):
        plan = load_sweep(self.write_spec(), ROOT)
        with patch("cxr_research.experiments.sweep.subprocess.run", return_value=subprocess.CompletedProcess([], 0)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_sweep(plan, ROOT), 1)
        self.assertEqual(json.loads((plan.directory / "sweep.json").read_text())["runs"][0]["status"], "failed")

    @unittest.skipUnless(HAS_ML, "CPU ML dependencies are needed for the real CLI smoke")
    def test_cli_trains_four_synthetic_runs_with_separate_artifacts(self):
        from cxr_research.data.synthetic import create_fixture
        generated = json.loads(create_fixture(self.root / "fixture").read_text())
        self.spec["base"]["data"] = generated["data"]
        self.write_spec()
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        result = subprocess.run([sys.executable, "-m", "cxr_research", "sweep", "--config", str(self.path)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        directory = self.root / "outputs/pilot"
        summary = json.loads((directory / "sweep.json").read_text())
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(len(summary["runs"]), 4)
        for entry in summary["runs"]:
            run_dir = directory / "runs" / entry["run_id"]
            record = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(record["evidence"], "synthetic")
            self.assertEqual(record["config"]["seed"], entry["seed"])
            self.assertTrue((run_dir / "epoch-0001.pt").is_file())
            self.assertTrue((run_dir / "metrics.json").is_file())


if __name__ == "__main__":
    unittest.main()
