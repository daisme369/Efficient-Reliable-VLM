"""Offline launcher integration: frozen synthetic plans, no network or secrets."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/gpu"))
import download_mimic_jpeg as launcher
from s3_download_backends import Boto3Backend, TransferMetrics

spec = importlib.util.spec_from_file_location("cxr_launcher_fixtures", Path(__file__).with_name("test_bulk_dataset_sync.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.BulkSyncTests(methodName="runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        for name in ("socket.socket.connect", "socket.create_connection"):
            blocker = patch(name, side_effect=AssertionError("offline tests prohibit network"))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.bucket = "arn:aws:s3:us-east-1:000000000000:accesspoint/synthetic"
        self.fx.prefix = "mimic-cxr-jpg/2.1.0/"
        self.fx.client = fixtures.FakeS3(self.fx.prefix, self.fx.payloads)
        self.plan = fixtures.bulk.make_plan(
            self.fx.client, self.bucket, self.fx.prefix, self.fx.publisher,
            byte_budget=10000, free_bytes=20000, reserve_bytes=1000)
        self.plan_path = self.fx.base / "frozen-plan.json"
        self.plan_path.write_text(json.dumps(self.plan))
        self.source = self.fx.base / "sources.env"
        self.source.write_text(
            "MIMIC_CXR_JPG_S3_URI=s3://" + self.bucket + "/mimic-cxr-jpg/2.1.0\n"
            "MIMIC_CXR_VQA_S3_URI=s3://synthetic-bucket/mimic-ext-mimic-cxr-vqa/1.0.0\n")
        self.review = self.fx.base / "review.json"
        self.review.write_text('{"synthetic_access_review":true}')
        self.args = argparse.Namespace(
            plan=self.plan_path, publisher=self.fx.publisher, data_root=self.fx.data,
            receipt=self.fx.receipt, status=self.fx.base / "status.json",
            completion=self.fx.base / "complete.json", source_env=self.source,
            access_review=self.review, plan_sha256=self.plan["plan_sha256"],
            access_review_sha256=hashlib.sha256(self.review.read_bytes()).hexdigest(),
            max_bytes=10000, reserve_bytes=1000, expected_images=3,
            backend="boto3", workers=1, stop_after_seconds=0,
        )
        self.fx.client.metrics = TransferMetrics()
        self.closed = []
        self.fx.client.close = lambda: self.closed.append(True)
        self.constructed = []

    def factory(self, backend, workers):
        self.constructed.append((backend, workers))
        return self.fx.client

    def run_download(self, **kwargs):
        return launcher.run(self.args, backend_factory=self.factory, **kwargs)

    def test_complete_certificate_is_published_while_lock_is_held(self):
        original = launcher._write_certificate
        def check(path, value):
            self.assertTrue((self.fx.data / ".bulk-dataset-sync.lock").exists())
            self.assertIn("receipt_sha256", value)
            return original(path, value)
        with patch.object(launcher, "_write_certificate", side_effect=check):
            result = self.run_download()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(json.loads(self.args.completion.read_text()), result)
        self.assertEqual(json.loads(self.args.status.read_text())["status"], "complete")
        self.assertFalse((self.fx.data / ".bulk-dataset-sync.lock").exists())
        self.assertEqual(self.closed, [True])

    def test_preexisting_lock_cannot_modify_active_status_receipt_or_get(self):
        marker = b"synthetic active status must remain unchanged"
        self.args.status.write_bytes(marker)
        with fixtures.bulk._exclusive_lock(self.fx.data, self.plan["plan_sha256"]):
            with self.assertRaises(launcher.BulkSyncError):
                self.run_download()
        self.assertEqual(self.args.status.read_bytes(), marker)
        self.assertFalse(self.args.receipt.exists())
        self.assertFalse(self.args.completion.exists())
        self.assertEqual(self.fx.client.get_calls, [])

    def test_completion_appearing_before_lock_is_rechecked_before_receipt(self):
        def race(backend, workers):
            self.args.completion.write_bytes(b"synthetic other manager certificate")
            return self.factory(backend, workers)
        with self.assertRaises(launcher.BulkSyncError):
            launcher.run(self.args, backend_factory=race)
        self.assertFalse(self.args.receipt.exists())
        self.assertFalse(self.args.status.exists())
        self.assertEqual(self.fx.client.get_calls, [])
        self.assertEqual(self.args.completion.read_bytes(), b"synthetic other manager certificate")

    def test_signal_event_drains_without_false_certificate_and_can_resume(self):
        event = threading.Event()
        original = self.fx.client.get_object
        def request(**kwargs):
            event.set()
            return original(**kwargs)
        self.fx.client.get_object = request
        result = self.run_download(shutdown_event=event)
        self.assertEqual(result["status"], "interrupted")
        self.assertFalse(self.args.completion.exists())
        self.assertFalse((self.fx.data / ".bulk-dataset-sync.lock").exists())
        with closing(sqlite3.connect(self.args.receipt)) as db:
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE name='status'").fetchone()[0], "interrupted")
        count = len(self.fx.client.get_calls)
        self.fx.client.get_object = original
        resumed = self.run_download()
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["reused_objects"], count)
        self.assertEqual(len(self.fx.client.get_calls), 3)

    def test_mismatched_review_stops_before_backend_or_data_writes(self):
        self.args.access_review_sha256 = "0" * 64
        with self.assertRaises(launcher.BulkSyncError):
            self.run_download()
        self.assertEqual(self.constructed, [])
        self.assertFalse(self.fx.receipt.exists())

    def test_timed_stop_starts_after_preflight_and_prevents_new_requests(self):
        events = []
        self.args.stop_after_seconds = 210
        class Deadline:
            def __init__(inner, seconds, callback):
                self.assertTrue((self.fx.data / ".bulk-dataset-sync.lock").exists())
                self.assertTrue(self.fx.receipt.exists())
                self.assertEqual(self.fx.client.get_calls, [])
                events.append(seconds)
                inner.callback = callback
            def start(inner):
                inner.callback()
            def cancel(inner):
                events.append("cancelled")
        with patch.object(launcher.threading, "Timer", Deadline):
            result = self.run_download()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.fx.client.get_calls, [])
        self.assertFalse(self.args.completion.exists())
        self.assertEqual(events, [210, "cancelled"])

    def test_private_temp_evidence_is_preserved_on_resume(self):
        stale = self.fx.data / ".bulk-partial-synthetic-old-evidence"
        stale.write_bytes(b"uncommitted synthetic bytes")
        self.run_download()
        self.assertEqual(stale.read_bytes(), b"uncommitted synthetic bytes")

    def test_cli_fallback_creates_private_scratch_without_sdk_or_real_cli(self):
        self.args.backend = "cli"
        def call(operation, arguments, outfile):
            response = self.fx.client.get_object(**arguments)
            body = response.pop("Body")
            try:
                outfile.write_bytes(body.read())
            finally:
                body.close()
            return response
        stub = argparse.Namespace(call=call)
        with patch.object(launcher, "S3", return_value=stub), patch.object(launcher, "Boto3Backend", side_effect=AssertionError("SDK forbidden")):
            result = launcher.run(self.args)
        self.assertEqual(result["status"], "complete")
        scratch = self.args.status.parent / "cli-object-temporary"
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertFalse(json.loads(self.args.status.read_text())["backend_metrics"]["http_telemetry_available"])

    def test_real_backend_adapter_stream_retry_uses_fresh_hash_and_temp(self):
        relative, payload = next(iter(self.fx.payloads.items()))
        calls = []
        original = self.fx.client.get_object
        class Broken(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise TimeoutError("synthetic sensitive provider body")
                return super().read(1)
        def get_object(**arguments):
            calls.append(arguments["Key"])
            response = original(**arguments)
            if len(calls) == 1:
                response["Body"].close()
                response["Body"] = Broken(payload)
            return response
        fake = argparse.Namespace(get_object=get_object, close=lambda: None)
        sdk = Boto3Backend(1, session_factory=lambda: argparse.Namespace(client=lambda *a, **k: fake), config_factory=lambda **k: k)
        result = launcher.run(self.args, backend_factory=lambda *a: sdk)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(calls), 4)
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)
        self.assertEqual(sdk.metrics.snapshot()["counters"]["body_retries"], 1)
        self.assertEqual(list(self.fx.data.rglob(".bulk-partial-*")), [])

    def test_credential_stdin_rejects_unapproved_keys_without_echo(self):
        for value in ({"credentials": {"HF_TOKEN": "synthetic-not-a-token"}}, {"unexpected": "synthetic"}):
            with self.subTest(shape=list(value)):
                with patch.object(sys, "stdin", io.StringIO(json.dumps(value))):
                    with self.assertRaises(launcher.BulkSyncError) as caught:
                        launcher._credentials_from_stdin()
                    self.assertEqual(str(caught.exception), "invalid_credential_envelope")


if __name__ == "__main__":
    unittest.main()
