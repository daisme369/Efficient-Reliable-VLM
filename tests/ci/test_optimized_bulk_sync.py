"""Offline regression tests for frozen-plan resume and bounded transfer workers.

All objects, paths and errors below are generated synthetic fixtures. These
tests never import boto3 or contact S3 and deny socket connections explicitly.
"""
from __future__ import annotations

import copy
from contextlib import closing
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


_FIXTURE_PATH = Path(__file__).with_name("test_bulk_dataset_sync.py")
_SPEC = importlib.util.spec_from_file_location("cxr_optimized_bulk_fixtures", _FIXTURE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
fixtures = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = fixtures
_SPEC.loader.exec_module(fixtures)
bulk = fixtures.bulk


class SyntheticStreamFailure(OSError):
    """Transport failure recognized only by the synthetic retry policy."""


class PartialFailureBody(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.reads == 1:
            return super().read(max(1, len(self.getvalue()) // 2))
        raise SyntheticStreamFailure("SOURCE_KEY_DO_NOT_LOG: interrupted body")


class OptimizedBulkSyncTests(unittest.TestCase):
    def setUp(self):
        # Composition reuses fixture setup without inheriting/rerunning its tests.
        self.fx = fixtures.BulkSyncTests(methodName="runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        for target in ("socket.socket.connect", "socket.create_connection"):
            blocker = patch(target, side_effect=AssertionError("network forbidden in offline tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def reject(self, operation, *args, **kwargs):
        with self.assertRaises(bulk.BulkSyncError) as captured:
            operation(*args, **kwargs)
        self.assertNotIn("SOURCE_KEY_DO_NOT_LOG", str(captured.exception))
        return captured.exception.code

    def save_plan(self, plan):
        path = self.fx.base / "frozen-plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def load(self, path, plan, **changes):
        options = dict(expected_plan_sha256=plan["plan_sha256"], byte_budget=10000,
                       reserve_bytes=1000, bucket="synthetic-bucket",
                       prefix=self.fx.prefix, publisher_path=self.fx.publisher,
                       expected_images=len(self.fx.payloads))
        options.update(changes)
        return bulk.load_plan(path, **options)

    def seed_completed(self, relative, payload):
        target = self.fx.data / relative
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        target.write_bytes(payload)
        return target

    def assert_no_new_partials(self):
        self.assertEqual(list(self.fx.data.rglob(".bulk-partial-*")), [])

    def test_frozen_plan_resume_needs_only_remaining_space_and_no_listing(self):
        plan = self.fx.make_plan()
        relative, payload = next(iter(self.fx.payloads.items()))
        self.seed_completed(relative, payload)
        remaining = self.fx.total - len(payload)
        free = remaining + 1000
        self.assertLess(free, self.fx.total + 1000)
        path = self.save_plan(plan)
        self.fx.client.list_objects_v2 = lambda **kwargs: self.fail("resume must not list S3")
        loaded = self.load(path, plan)
        self.assertEqual(loaded, plan)
        self.assertEqual(self.fx.client.get_calls, [])
        result = self.fx.fetch(loaded, free_bytes=lambda: free, workers=1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["reused_objects"], 1)
        self.assertEqual(result["verified_bytes"], self.fx.total)
        self.assertEqual({call["Key"] for call in self.fx.client.get_calls},
                         {self.fx.prefix + name for name in self.fx.payloads if name != relative})
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)

    def test_remaining_capacity_shortfall_rejects_before_any_get(self):
        plan = self.fx.make_plan()
        relative, payload = next(iter(self.fx.payloads.items()))
        self.seed_completed(relative, payload)
        free = self.fx.total - len(payload) + 999
        self.assertEqual(self.reject(self.fx.fetch, plan, free_bytes=lambda: free),
                         "insufficient_disk_space")
        self.assertEqual(self.fx.client.get_calls, [])
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)

    def test_plan_hash_source_publisher_and_count_are_bound_without_requests(self):
        plan = self.fx.make_plan()
        path = self.save_plan(plan)
        for changes in ({"expected_plan_sha256": "0" * 64},
                        {"bucket": "different-synthetic-bucket"},
                        {"prefix": "different-synthetic-prefix/"},
                        {"expected_images": 99}):
            with self.subTest(field=next(iter(changes))):
                self.reject(self.load, path, plan, **changes)
        original = self.fx.publisher.read_bytes()
        self.fx.publisher.write_bytes(original + b"\n")
        self.assertEqual(self.reject(self.load, path, plan), "publisher_changed")
        self.fx.publisher.write_bytes(original)
        modified = copy.deepcopy(plan)
        modified["entries"][0]["etag"] = '"synthetic-plan-tamper"'
        path.write_text(json.dumps(modified))
        self.assertEqual(self.reject(self.load, path, plan), "plan_hash_mismatch")
        self.assertEqual(self.fx.client.get_calls, [])

    def test_corrupt_completed_file_is_preserved_and_rejected_before_get(self):
        plan = self.fx.make_plan()
        relative, payload = next(iter(self.fx.payloads.items()))
        changed = b"X" * len(payload)
        target = self.seed_completed(relative, changed)
        self.assertEqual(self.reject(self.fx.fetch, plan), "existing_file_mismatch")
        self.assertEqual(target.read_bytes(), changed)
        self.assertEqual(self.fx.client.get_calls, [])

    def test_uncommitted_partial_is_not_completion_and_is_preserved(self):
        stale = self.fx.data / ".bulk-partial-synthetic-interruption"
        stale.write_bytes(b"synthetic uncommitted evidence")
        result = self.fx.fetch()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["downloaded_objects"], len(self.fx.payloads))
        self.assertEqual(result["reused_objects"], 0)
        self.assertEqual(stale.read_bytes(), b"synthetic uncommitted evidence")

    def install_partial_stream(self, fail_attempts):
        original_get = self.fx.client.get_object
        attempts = []
        policies = []

        def get(**kwargs):
            response = original_get(**kwargs)
            attempts.append(copy.deepcopy(kwargs))
            if len(attempts) <= fail_attempts:
                payload = response["Body"].getvalue()
                response["Body"].close()
                body = PartialFailureBody(payload)
                self.fx.client.bodies.append(body)
                response["Body"] = body
            return response

        def policy(error, retry_index):
            policies.append((type(error), retry_index))
            if isinstance(error, SyntheticStreamFailure) and retry_index == 0:
                return 0.0
            return None

        self.fx.client.get_object = get
        self.fx.client.stream_retry_delay = policy
        return attempts, policies, original_get

    def test_partial_stream_retry_restarts_hash_and_temp_then_verifies(self):
        relative, payload = self.fx.one_object()
        plan = self.fx.make_plan()
        attempts, policies, _ = self.install_partial_stream(fail_attempts=1)
        result = self.fx.fetch(plan, workers=1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["downloaded_objects"], 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(policies, [(SyntheticStreamFailure, 0)])
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)
        self.assertTrue(all(body.closed for body in self.fx.client.bodies))
        self.assert_no_new_partials()

    def test_exhausted_stream_retry_has_no_final_file_and_later_resumes(self):
        relative, payload = self.fx.one_object()
        plan = self.fx.make_plan()
        attempts, policies, original_get = self.install_partial_stream(fail_attempts=99)
        self.assertEqual(self.reject(self.fx.fetch, plan, workers=1), "object_transfer_failed")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(policies, [(SyntheticStreamFailure, 0), (SyntheticStreamFailure, 1)])
        self.assertFalse((self.fx.data / relative).exists())
        self.assertTrue(all(body.closed for body in self.fx.client.bodies))
        self.assert_no_new_partials()
        self.fx.client.get_object = original_get
        self.fx.client.get_calls.clear()
        self.assertEqual(self.fx.fetch(plan)["status"], "complete")
        self.assertEqual(len(self.fx.client.get_calls), 1)
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)

    def test_request_failure_and_timeout_do_not_trigger_stream_restart(self):
        relative, payload = self.fx.one_object()
        plan = self.fx.make_plan()
        original_get = self.fx.client.get_object
        policy_calls = []
        self.fx.client.stream_retry_delay = lambda error, index: policy_calls.append(type(error))
        for failure in (OSError("SOURCE_KEY_DO_NOT_LOG request failed"),
                        TimeoutError("SOURCE_KEY_DO_NOT_LOG request timeout")):
            calls = []

            def failed_get(**kwargs):
                calls.append(kwargs)
                raise failure

            self.fx.client.get_object = failed_get
            self.assertEqual(self.reject(self.fx.fetch, plan), "object_transfer_failed")
            self.assertEqual(len(calls), 1)
            self.assertFalse((self.fx.data / relative).exists())
            self.assert_no_new_partials()
        self.assertEqual(policy_calls, [OSError, TimeoutError])
        self.fx.client.get_object = original_get
        self.assertEqual(self.fx.fetch(plan)["status"], "complete")
        self.assertEqual((self.fx.data / relative).read_bytes(), payload)

    def test_identity_size_and_checksum_failures_never_invoke_retry_policy(self):
        relative, payload = self.fx.one_object()
        plan = self.fx.make_plan()
        policy_calls = []
        self.fx.client.stream_retry_delay = lambda *args: policy_calls.append(args) or 0
        for scenario in ("etag", "size", "hash", "short", "extra"):
            self.fx.client.objects[self.fx.prefix + relative]["payload"] = payload
            self.fx.client.response_overrides.clear()
            if scenario == "etag":
                self.fx.client.response_overrides[self.fx.prefix + relative] = {"ETag": '"wrong"'}
            elif scenario == "size":
                self.fx.client.response_overrides[self.fx.prefix + relative] = {"ContentLength": len(payload) + 1}
            else:
                changed = {"hash": b"Z" * len(payload), "short": payload[:-1],
                           "extra": payload + b"extra"}[scenario]
                self.fx.client.objects[self.fx.prefix + relative]["payload"] = changed
                self.fx.client.response_overrides[self.fx.prefix + relative] = {"ContentLength": len(payload)}
            with self.subTest(scenario=scenario):
                self.reject(self.fx.fetch, plan)
                self.assertFalse((self.fx.data / relative).exists())
                self.assert_no_new_partials()
        self.assertEqual(policy_calls, [])
        self.assertTrue(all(body.closed for body in self.fx.client.bodies))

    def test_publication_race_never_overwrites_an_appearing_destination(self):
        relative, _ = self.fx.one_object()
        plan = self.fx.make_plan()
        target = self.fx.data / relative
        original_get = self.fx.client.get_object

        def racing_get(**kwargs):
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            target.write_bytes(b"synthetic competing writer evidence")
            return original_get(**kwargs)

        self.fx.client.get_object = racing_get
        self.reject(self.fx.fetch, plan)
        self.assertEqual(target.read_bytes(), b"synthetic competing writer evidence")
        self.assert_no_new_partials()

    def test_duplicate_manager_is_rejected_before_get_and_first_can_finish(self):
        self.fx.one_object()
        plan = self.fx.make_plan()
        entered, release = threading.Event(), threading.Event()
        original_get = self.fx.client.get_object
        outcome = {}

        def blocked_get(**kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("synthetic test barrier deadline")
            return original_get(**kwargs)

        def run_first():
            try:
                outcome["result"] = self.fx.fetch(plan, workers=1)
            except BaseException as error:
                outcome["error"] = error

        self.fx.client.get_object = blocked_get
        worker = threading.Thread(target=run_first, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(5), "first synthetic manager did not enter GET")
            second = fixtures.FakeS3(self.fx.prefix, self.fx.payloads)
            self.assertEqual(self.reject(
                bulk.fetch_plan, second, plan, self.fx.data, self.fx.receipt,
                expected_plan_sha256=plan["plan_sha256"], byte_budget=10000,
                reserve_bytes=1000, workers=1, free_bytes=lambda: 20000), "lock_exists")
            self.assertEqual(second.get_calls, [])
            self.assertTrue((self.fx.data / ".bulk-dataset-sync.lock").exists())
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"]["status"], "complete")
        self.assertFalse((self.fx.data / ".bulk-dataset-sync.lock").exists())

    def test_cooperative_drain_preserves_submitted_work_and_resumes_remaining(self):
        self.fx.payloads = {fixtures.synthetic_relative(index): b"synthetic drain payload"
                            for index in range(1, 7)}
        self.fx.write_publisher()
        self.fx.client = fixtures.FakeS3(self.fx.prefix, self.fx.payloads)
        plan = self.fx.make_plan()
        shutdown, entered, release = threading.Event(), threading.Event(), threading.Event()
        original_get = self.fx.client.get_object
        outcome = {}

        class GatedBody(io.BytesIO):
            def read(self, size=-1):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError("synthetic test drain deadline")
                return super().read(size)

        def gated_get(**kwargs):
            response = original_get(**kwargs)
            old = response["Body"]
            body = GatedBody(old.getvalue())
            old.close()
            self.fx.client.bodies.append(body)
            response["Body"] = body
            return response

        def run():
            try:
                outcome["result"] = self.fx.fetch(plan, workers=1, shutdown_event=shutdown)
            except BaseException as error:
                outcome["error"] = error

        self.fx.client.get_object = gated_get
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            shutdown.set()
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        result = outcome["result"]
        self.assertEqual(result["status"], "interrupted")
        self.assertNotIn("integrity", result)
        self.assertGreaterEqual(result["downloaded_objects"], 1)
        self.assertLessEqual(result["downloaded_objects"], 2)
        self.assertEqual(len(self.fx.client.get_calls), result["downloaded_objects"])
        with closing(sqlite3.connect(self.fx.receipt)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT value FROM metadata WHERE name='status'").fetchone()[0], "interrupted")
            self.assertIsNone(connection.execute("SELECT value FROM metadata WHERE name='completion'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0], result["downloaded_objects"])
        self.assertTrue(all(body.closed for body in self.fx.client.bodies))
        self.assert_no_new_partials()
        self.fx.client.get_object = original_get
        self.fx.client.get_calls.clear()
        resumed = self.fx.fetch(plan, workers=1)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["reused_objects"], result["downloaded_objects"])
        self.assertEqual(len(self.fx.client.get_calls), len(self.fx.payloads) - result["downloaded_objects"])

    def test_preexisting_shutdown_event_prevents_all_new_gets(self):
        shutdown = threading.Event()
        shutdown.set()
        result = self.fx.fetch(shutdown_event=shutdown)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["downloaded_objects"], 0)
        self.assertEqual(self.fx.client.get_calls, [])
        self.assertFalse((self.fx.data / ".bulk-dataset-sync.lock").exists())

    def test_supported_worker_levels_and_upper_guard(self):
        plan = self.fx.make_plan()
        base = self.fx.data
        for workers in (16, 32, 64):
            with self.subTest(workers=workers):
                self.fx.data = base / str(workers)
                self.fx.data.mkdir(mode=0o700)
                self.fx.receipt = self.fx.data / ".bulk-receipts.sqlite3"
                self.fx.client.get_calls.clear()
                self.assertEqual(self.fx.fetch(plan, workers=workers)["status"], "complete")
                self.assertEqual(len(self.fx.client.get_calls), len(self.fx.payloads))
        self.fx.client.get_calls.clear()
        self.assertEqual(self.reject(self.fx.fetch, plan, workers=65), "invalid_worker_count")
        self.assertEqual(self.fx.client.get_calls, [])

    def test_invalid_shutdown_event_is_rejected_before_get(self):
        self.assertEqual(self.reject(self.fx.fetch, shutdown_event=object()), "invalid_shutdown_event")
        self.assertEqual(self.fx.client.get_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
