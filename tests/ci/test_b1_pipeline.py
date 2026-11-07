"""Synthetic S3 → split seal → image mapping → BF16 LoRA configuration."""
from __future__ import annotations

import contextlib
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts/gpu"))
import dataset_sync as sync
import setup_b1
import monitor_b1
from cxr_research.data.catalog import Catalog, build
from cxr_research.data.catalog_adapter import prepare_catalog
from cxr_research.experiments.config import check_protocol, load_config
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash
from tests.data.test_build_manifests import fixture


class S3CLIContractTests(unittest.TestCase):
    def test_get_parser_required_arguments_preserve_conditional_request(self):
        client = sync.S3.__new__(sync.S3)
        client.executable = "synthetic-aws"
        client.env = {"AWS_ACCESS_KEY_ID": "synthetic-key",
                      "AWS_SECRET_ACCESS_KEY": "synthetic-secret"}
        params = {"Bucket": "synthetic-bucket", "Key": "synthetic-image.jpg",
                  "IfMatch": '"synthetic-etag"'}

        def cli(command, **kwargs):
            # Reproduce the required-argument parser used by the actual CLI.
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("--bucket", required=True)
            parser.add_argument("--key", required=True)
            parser.add_argument("--if-match", required=True)
            parser.add_argument("--version-id")
            parsed, _ = parser.parse_known_args(command[3:])
            self.assertEqual((parsed.bucket, parsed.key), (params["Bucket"], params["Key"]))
            self.assertEqual(parsed.if_match, params["IfMatch"])
            self.assertEqual(parsed.version_id, params.get("VersionId"))
            self.assertNotIn("--cli-input-json", command)
            self.assertNotIn("--cli-input-yaml", command)
            self.assertEqual(command[-1], "synthetic-output.part")
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["env"]["AWS_SECRET_ACCESS_KEY"], "synthetic-secret")
            self.assertNotIn("synthetic-secret", " ".join(command))
            return sync.subprocess.CompletedProcess(command, 0, stdout='{"ETag":"synthetic-etag"}')

        with patch.object(sync.subprocess, "run", side_effect=cli):
            for version in (None, "synthetic-version"):
                with self.subTest(version=version):
                    if version is not None:
                        params["VersionId"] = version
                    self.assertEqual(client.call("get-object", params, "synthetic-output.part"),
                                     {"ETag": "synthetic-etag"})

    def test_get_rejects_missing_or_unknown_identity_arguments_before_launch(self):
        client = sync.S3.__new__(sync.S3)
        client.executable, client.env = "synthetic-aws", {}
        params = {"Bucket": "synthetic-bucket", "Key": "synthetic-image.jpg", "IfMatch": '"etag"'}
        cases = [(dict(params, Range="bytes=0-0"), "synthetic.part"),
                 ({k: v for k, v in params.items() if k != "IfMatch"}, "synthetic.part"),
                 (params, None)]
        with patch.object(sync.subprocess, "run") as launched:
            for request, outfile in cases:
                with self.subTest(request=request, outfile=outfile), self.assertRaises(ContractError):
                    client.call("get-object", request, outfile)
            launched.assert_not_called()


class B1PipelineTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repository = self.base / "checkout"
        (self.repository / "configs").mkdir(parents=True)
        (self.repository / "scripts/ci").mkdir(parents=True)
        (self.repository / "AGENTS-INSTRUCTIONS.md").write_text("Synthetic checkout")
        (self.repository / "scripts/ci/check_gpu_environment.py").write_text("# Synthetic marker")
        for name in ("medgemma_b1.json", "protocol.template.json", "catalog-preparation.template.json"):
            shutil.copyfile(ROOT / "configs" / name, self.repository / "configs" / name)
        self.upstream, self.root = self.base / "upstream", self.base / "data"
        fixture(self.upstream)
        self.env = self.base / "settings.env"
        self.env.write_text('mimic_cxr_jpg="aws s3 sync s3://synthetic/mimic-cxr-jpg/2.1.0 ignored"\n'
            'mimic_cxr_vqa=s3://synthetic/mimic-ext-mimic-cxr-vqa/1.0.0/\n')
        for slug, version in sync.RELEASES.values():
            release = self.upstream / slug / version
            (release / "LICENSE.txt").write_text("Synthetic fixtures only")
            lines = [f"{file_hash(p)}  {p.relative_to(release).as_posix()}\n"
                     for p in sorted(release.rglob("*")) if p.is_file()]
            (release / "SHA256SUMS.txt").write_text("".join(lines))
        self.calls = []
        owner = self
        class FakeS3:
            def __init__(self, env_file):
                pass
            def listing(self, source):
                return ({"Key": p.relative_to(owner.upstream).as_posix()}
                        for p in owner.upstream.rglob("*") if p.is_file()
                        and p.relative_to(owner.upstream).as_posix().startswith(source["prefix"]))
            def call(self, operation, params, outfile=None):
                owner.calls.append((operation, dict(params)))
                path = owner.upstream / params["Key"]
                raw = path.read_bytes()
                etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
                if operation == "head-object":
                    return {"ContentLength": len(raw), "ETag": etag, "VersionId": "synthetic-v1"}
                if operation == "get-object":
                    if params.get("IfMatch") != etag or params.get("VersionId") != "synthetic-v1":
                        raise ContractError("source changed")
                    Path(outfile).write_bytes(raw)
                    return {"ETag": etag}
                raise AssertionError("unexpected AWS operation")
        mocked = patch.object(sync, "S3", FakeS3)
        mocked.start()
        self.addCleanup(mocked.stop)

    def fetch(self, path):
        return sync.fetch_plan(self.env, path, file_hash(path), 10**8, self.repository)

    def stage_catalog(self):
        meta = self.base / "metadata.json"
        sync.metadata_plan(self.env, self.root, meta, "synthetic private target", self.repository)
        self.fetch(meta)
        initial = self.root / "catalog-initial"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, initial, "mimic-official", repository=self.repository)
        return initial

    def test_synthetic_end_to_end_download_mapping_and_draft_configs(self):
        initial = self.stage_catalog()
        self.assertEqual(json.loads((initial / "report.json").read_text())["development_rows"],
                         {"train": 0, "validation": 0})
        plan = self.base / "images.json"
        result = sync.image_plan(self.env, self.root, initial, plan, "synthetic target", {"train": 32, "validation": 8}, self.repository)
        self.assertEqual(result["objects"], 2)
        self.assertEqual(self.fetch(plan)["objects"], 2)
        gets = len([c for c in self.calls if c[0] == "get-object"])
        self.assertEqual(self.fetch(plan)["downloaded_bytes"], 0)
        self.assertEqual(len([c for c in self.calls if c[0] == "get-object"]), gets)
        snapshot = self.root / "catalog-complete"
        with contextlib.redirect_stdout(io.StringIO()):
            report = build(self.root, snapshot, "mimic-official", repository=self.repository)
        self.assertEqual(report["development_rows"], {"train": 2, "validation": 1})
        output = self.root / "rental/preparation"
        git = type("Git", (), {"returncode": 0, "stdout": "a" * 40})()
        with patch.object(setup_b1.subprocess, "run", return_value=git):
            setup_b1.draft_preparation(self.root, snapshot, output, True, self.repository)
        spec_path = output / "catalog-preparation.json"
        spec = json.loads(spec_path.read_text())
        self.assertEqual(spec["answer_type_values"], {"verify": "closed", "choose": "closed", "query": "open"})
        for key in ("access_review",):
            spec[key] = "synthetic fixture review"
        for source in spec["sources"].values():
            source.update(access="synthetic", license="synthetic")
            if "derivation" in source:
                source["derivation"] = {key: "synthetic" for key in source["derivation"]}
        # Fixture semantic names are deliberately synthetic; use an explicit fixture mapping.
        spec["answer_type_values"] = {"synthetic": "open"}
        spec_path.write_text(json.dumps(spec))
        with patch("cxr_research.data.catalog_adapter._git_identity", return_value=("a" * 40, False)):
            prepare_catalog(spec_path, self.repository)
        configured = self.root / "rental/b1-fixture"
        result = setup_b1.configure(Path(spec["prepared_dir"]), configured, "a" * 40, repository=self.repository)
        self.assertFalse(result["training_ready"])
        self.assertEqual((result["train_qa"], result["validation_qa"]), (2, 1))
        local, remote = (load_config(configured / name) for name in ("b1-local.json", "b1.json"))
        self.assertEqual((remote.train.precision, remote.medgemma.quantization), ("bf16", "none"))
        self.assertTrue(remote.medgemma.local_files_only)
        self.assertNotEqual(local.fingerprint, remote.fingerprint)
        inventory = [json.loads(line) for line in (configured / "transfer-files.jsonl").read_text().splitlines()]
        self.assertEqual(len(inventory), 4)  # two unique images, manifest and provenance
        for name, config in (("protocol-b1-local.json", local), ("protocol-b1.json", remote)):
            protocol = json.loads((configured / name).read_text())
            self.assertEqual(protocol["status"], "draft")
            self.assertEqual(protocol["config_sha256"], config.fingerprint)
            with self.assertRaises(ContractError):
                check_protocol(config, protocol["manifest_sha256"], protocol["provenance_sha256"])
        with self.assertRaises(ContractError):
            setup_b1.configure(Path(spec["prepared_dir"]), configured, "a" * 40, repository=self.repository)

    def test_budget_and_changed_plan_block_before_get(self):
        plan = self.base / "metadata.json"
        sync.metadata_plan(self.env, self.root, plan, "synthetic", self.repository)
        for sha, budget in (("0" * 64, 10**8), (file_hash(plan), 1)):
            with self.assertRaises(ContractError):
                sync.fetch_plan(self.env, plan, sha, budget, self.repository)
        self.assertFalse(any(c[0] == "get-object" for c in self.calls))

    def test_completed_file_tamper_is_never_overwritten(self):
        initial = self.stage_catalog()
        path = self.root / "mimic-cxr-jpg/2.1.0/LICENSE.txt"
        path.write_text("changed synthetic bytes")
        gets = len(self.calls)
        with self.assertRaises(ContractError):
            self.fetch(self.base / "metadata.json")
        self.assertEqual(path.read_text(), "changed synthetic bytes")
        self.assertEqual(len(self.calls), gets)

    def test_source_mutation_between_plan_and_download_is_rejected(self):
        plan = self.base / "metadata.json"
        sync.metadata_plan(self.env, self.root, plan, "synthetic", self.repository)
        (self.upstream / "mimic-cxr-jpg/2.1.0/LICENSE.txt").write_text("new bytes")
        with self.assertRaises(ContractError):
            self.fetch(plan)
        self.assertFalse((self.root / "mimic-cxr-jpg/2.1.0/LICENSE.txt").exists())
        self.assertFalse(plan.with_suffix(".lock").exists())

    def test_legacy_command_and_arn_are_parsed_without_execution(self):
        original = self.env.read_text()
        arn = "arn:aws:s3:us-east-1:000000000000:accesspoint/synthetic"
        self.env.write_text(original.replace("s3://synthetic/", f"s3://{arn}/"))
        with patch.object(sync.subprocess, "run") as run:
            self.assertEqual(sync.source_settings(self.env)["images"]["bucket"], arn)
        run.assert_not_called()
        for injection in ("; echo sentinel", " $(sentinel)", " | echo sentinel", " s3://second/path"):
            self.env.write_text(original.replace(' ignored"', injection + '"'))
            with self.assertRaises(ContractError):
                sync.source_settings(self.env)

    def test_pagination_is_complete_and_repeated_tokens_fail(self):
        client = sync.S3.__new__(sync.S3)
        # Reach the original class because this fixture patches S3 construction.
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("sync_original_fixture", ROOT / "scripts/gpu/dataset_sync.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        client = module.S3.__new__(module.S3)
        pages = [{"Contents": [{"Key": "a"}], "IsTruncated": True, "NextContinuationToken": "next"},
                 {"Contents": [{"Key": "b"}], "IsTruncated": False}]
        with patch.object(client, "call", side_effect=pages):
            self.assertEqual([r["Key"] for r in client.listing({"bucket": "synthetic", "prefix": ""})], ["a", "b"])
        with patch.object(client, "call", return_value=pages[0]):
            with self.assertRaises(ContractError):
                list(client.listing({"bucket": "synthetic", "prefix": ""}))

    def test_monitor_sanitizes_text_and_detects_stale_record(self):
        run = self.root / "runs/fixture"
        run.mkdir(parents=True)
        (run / "run.json").write_text(json.dumps({"status": "running", "config": "PRIVATE_SENTINEL"}))
        (run / "progress.json").write_text(json.dumps({"loss": 1.5, "global_step": 4,
            "updated_at_unix": 1, "question": "PRIVATE_SENTINEL", "eval_loss": float("nan")}))
        result = monitor_b1.snapshot(run, repository=self.repository)
        self.assertEqual(result["loss"], 1.5)
        self.assertTrue(result["heartbeat_stale"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        self.assertNotIn("eval_loss", result)
        (run / "latest_checkpoint.json").write_text(json.dumps({"checkpoint": "checkpoint-4", "global_step": 4}))
        result = monitor_b1.snapshot(run, repository=self.repository)
        self.assertFalse(result["latest_checkpoint_available"])
        self.assertNotIn("latest_checkpoint_step", result)
        checkpoint = run / "checkpoint-4"
        checkpoint.mkdir()
        (checkpoint / "artifact_hashes.json").write_text("{}")
        self.assertTrue(monitor_b1.snapshot(run, repository=self.repository)["latest_checkpoint_available"])

    def test_transfer_path_escape_and_duplicate_aliases_are_rejected(self):
        for relative in ("../escape", "C:/escape", "a\\escape", "/escape"):
            with self.assertRaises(ContractError):
                sync.destination(self.root, relative)
        self.env.write_text(self.env.read_text() + "MIMIC_CXR_JPG_S3_URI=s3://synthetic/mimic-cxr-jpg/2.1.0\n")
        with self.assertRaises(ContractError):
            sync.source_settings(self.env)

    def test_catalog_rejects_source_replacement_between_passes(self):
        target = self.upstream / "mimic-ext-mimic-cxr-vqa/1.0.0/release/test.json"
        original = Catalog.finish_source
        def finish(catalog, path, sha, count):
            original(catalog, path, sha, count)
            if path.name == "valid.json":
                rows = json.loads(target.read_text())
                rows.append(dict(rows[0], image_id="synthetic-a", subject_id="1", study_id="11",
                                 answer=["MUTATED_SYNTHETIC_SENTINEL"]))
                target.write_text(json.dumps(rows))
        output = self.upstream / "failed-catalog"
        with patch.object(Catalog, "finish_source", finish), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "changed during indexing"):
                build(self.upstream, output, "mimic-official", repository=self.repository)
        self.assertEqual(json.loads((output / "status.json").read_text())["status"], "failed")
        self.assertFalse((output / "vqa.train.jsonl").exists())
        self.assertNotIn(b"MUTATED_SYNTHETIC_SENTINEL", (output / "catalog.sqlite").read_bytes())


if __name__ == "__main__":
    unittest.main()
