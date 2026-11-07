"""Offline transports tests: synthetic bytes and clients, no S3 or credentials."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_PATH = Path(__file__).resolve().parents[2] / "scripts/gpu/s3_download_backends.py"
_SPEC = importlib.util.spec_from_file_location("cxr_test_s3_backends", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
backend = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backend)

PAYLOAD = b"synthetic jpeg fixture, no medical content"
CONDITION = '"synthetic-etag"'
ARGS = {"Bucket": "arn:aws:s3:us-east-1:000000000000:accesspoint/synthetic",
        "Key": "synthetic/fixture.jpg", "IfMatch": CONDITION}
SENTINEL = "SOURCE_KEY_OR_TOKEN_MUST_NOT_APPEAR"


class SyntheticClientError(Exception):
    def __init__(self, status=503, retries=3):
        super().__init__(SENTINEL)
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status, "RetryAttempts": retries},
                         "Error": {"Code": SENTINEL, "Message": SENTINEL}}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.bodies = []
        self.closed = False
        self.close_calls = 0
        self.error = None
        self.retries = 0
        self.body_factory = lambda: io.BytesIO(PAYLOAD)
        self.guard = threading.Lock()

    def get_object(self, **arguments):
        with self.guard:
            self.calls.append(dict(arguments))
        if self.error:
            raise self.error
        body = self.body_factory()
        with self.guard:
            self.bodies.append(body)
        return {"Body": body, "ETag": CONDITION, "ContentLength": len(PAYLOAD),
                "ResponseMetadata": {"HTTPStatusCode": 200, "RetryAttempts": self.retries}}

    def close(self):
        self.closed = True
        self.close_calls += 1


class FakeEvents:
    def __init__(self):
        self.registrations = []

    def register(self, name, callback):
        self.registrations.append((name, callback, threading.get_ident()))

    def emit(self, name="before-send.s3.GetObject", **arguments):
        results = []
        for registered_name, callback, _ in self.registrations:
            if registered_name != name:
                continue
            # The backend must count the event without inspecting request data.
            results.append(callback(request=object(), event_name=name, **arguments))
        return results


class BrokenBody(io.BytesIO):
    def __init__(self, error):
        super().__init__(PAYLOAD)
        self.error = error

    def read(self, size=-1):
        raise self.error


class BackendTests(unittest.TestCase):
    def setUp(self):
        # A transport test must fail rather than accidentally contact any host.
        self.network = patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.connect = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.connect.start()
        self.addCleanup(self.connect.stop)
        old = os.umask(0o077)
        self.addCleanup(os.umask, old)
        temporary_root = Path(__file__).resolve().parents[2] / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="cxr-synthetic-s3-", dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name)
        if os.name == "posix":
            self.scratch.chmod(0o700)
        self.client = FakeClient()
        self.configs = []
        self.sessions = []
        self.client_constructions = []

    def make(self, workers=16):
        def config_factory(**arguments):
            self.configs.append(arguments)
            return arguments

        class Session:
            def client(inner, service, **arguments):
                self.client_constructions.append((service, arguments, threading.get_ident()))
                return self.client

        def session_factory():
            self.sessions.append(threading.get_ident())
            return Session()

        return backend.Boto3Backend(workers, session_factory=session_factory,
                                    config_factory=config_factory)

    def test_pool_and_timeouts_for_benchmark_worker_counts(self):
        for workers, expected in ((16, 32), (32, 64), (64, 128)):
            with self.subTest(workers=workers):
                current = self.make(workers)
                self.assertEqual(current.pool_connections, expected)
                self.assertEqual(self.configs[-1], {
                    "max_pool_connections": expected, "connect_timeout": 20,
                    "read_timeout": 120,
                    "retries": {"mode": "standard", "total_max_attempts": 4},
                })
                self.assertEqual(self.client_constructions[-1][0], "s3")
                self.assertEqual(self.client_constructions[-1][1]["region_name"], "us-east-1")

    def test_invalid_worker_or_region_rejected_without_sdk_construction(self):
        for workers in (0, 65, True, "16"):
            with self.subTest(workers=workers), self.assertRaises(backend.BackendError):
                self.make(workers)
        with self.assertRaises(backend.BackendError):
            backend.Boto3Backend(16, region_name="wrong-region")
        self.assertEqual(self.sessions, [])

    def test_one_client_created_before_thread_pool_and_shared(self):
        current = self.make()
        constructor_thread = threading.get_ident()

        def get(_):
            result = current.get_object(**ARGS)
            self.assertEqual(result["Body"].read(), PAYLOAD)
            result["Body"].close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(get, range(40)))
        self.assertEqual(self.sessions, [constructor_thread])
        self.assertEqual(len(self.client_constructions), 1)
        self.assertEqual(self.client_constructions[0][2], constructor_thread)
        self.assertEqual(len(self.client.calls), 40)
        self.assertTrue(all(body.closed for body in self.client.bodies))
        self.assertEqual(current.metrics.snapshot()["counters"]["bytes_read"], len(PAYLOAD) * 40)

    def test_get_preserves_condition_access_point_and_optional_version(self):
        current = self.make()
        arguments = dict(ARGS, VersionId="synthetic-version")
        response = current.get_object(**arguments)
        self.assertEqual(self.client.calls, [arguments])
        self.assertEqual(response["ETag"], CONDITION)
        self.assertEqual(response["ContentLength"], len(PAYLOAD))
        self.assertEqual(response["Body"].read(), PAYLOAD)
        response["Body"].close()

    def test_missing_ifmatch_and_unknown_arguments_never_call_client(self):
        current = self.make()
        for arguments in ({"Bucket": "synthetic", "Key": "fixture"},
                          dict(ARGS, Range="bytes=0-1"), dict(ARGS, IfMatch="")):
            with self.assertRaises(backend.BackendError):
                current.get_object(**arguments)
        self.assertEqual(self.client.calls, [])

    def test_success_metadata_records_retries_status_and_latency(self):
        self.client.retries = 2
        current = self.make()
        response = current.get_object(**ARGS)
        response["Body"].read()
        response["Body"].close()
        result = current.metrics.snapshot()
        self.assertTrue(result["http_telemetry_available"])
        self.assertEqual(result["counters"]["sdk_retries"], 2)
        self.assertEqual(result["counters"]["http_2xx"], 1)
        self.assertEqual(result["latencies"]["headers_seconds"]["count"], 1)
        self.assertEqual(result["latencies"]["body_read_seconds"]["count"], 1)

    def test_terminal_request_failure_is_sanitized_and_not_retried_by_adapter(self):
        self.client.error = SyntheticClientError()
        current = self.make()
        with self.assertRaises(backend.BackendError) as caught:
            current.get_object(**ARGS)
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertEqual(str(caught.exception), "s3_request_failed")
        self.assertEqual(len(self.client.calls), 1)
        self.assertIsNone(current.stream_retry_delay(caught.exception, 0))
        result = current.metrics.snapshot()["counters"]
        self.assertEqual(result["failed_gets"], 1)
        self.assertEqual(result["sdk_retries"], 3)
        self.assertEqual(result["http_5xx"], 1)

    def test_request_timeout_is_not_misclassified_as_body_retry(self):
        self.client.error = TimeoutError(SENTINEL)
        current = self.make()
        with self.assertRaises(backend.BackendError) as caught:
            current.get_object(**ARGS)
        self.assertIsNone(current.stream_retry_delay(caught.exception, 0))

    def test_412_precondition_failure_is_not_retried(self):
        self.client.error = SyntheticClientError(status=412, retries=0)
        current = self.make()
        with self.assertRaises(backend.BackendError) as caught:
            current.get_object(**ARGS)
        self.assertIsNone(current.stream_retry_delay(caught.exception, 0))
        self.assertEqual(current.metrics.snapshot()["counters"]["http_4xx"], 1)

    def test_stream_timeout_allows_exactly_one_body_restart(self):
        self.client.body_factory = lambda: BrokenBody(TimeoutError(SENTINEL))
        current = self.make()
        response = current.get_object(**ARGS)
        with self.assertRaises(backend.StreamReadFailure) as caught:
            response["Body"].read()
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertEqual(current.stream_retry_delay(caught.exception, 0), .25)
        self.assertIsNone(current.stream_retry_delay(caught.exception, 1))
        response["Body"].close()
        self.assertTrue(self.client.bodies[0].closed)
        self.assertEqual(current.metrics.snapshot()["counters"]["body_retries"], 1)
        self.assertEqual(current.metrics.snapshot()["counters"]["body_failures"], 1)

    def test_unknown_body_exception_and_integrity_errors_do_not_retry(self):
        self.client.body_factory = lambda: BrokenBody(ValueError(SENTINEL))
        current = self.make()
        response = current.get_object(**ARGS)
        with self.assertRaises(backend.BackendError) as caught:
            response["Body"].read()
        self.assertIsNone(current.stream_retry_delay(caught.exception, 0))
        self.assertIsNone(current.stream_retry_delay(ValueError("checksum mismatch"), 0))
        response["Body"].close()

    def test_client_close_is_called(self):
        current = self.make()
        current.close()
        current.close()
        self.assertTrue(self.client.closed)
        self.assertEqual(self.client.close_calls, 1)
        with self.assertRaises(backend.BackendError) as caught:
            current.get_object(**ARGS)
        self.assertEqual(str(caught.exception), "s3_client_closed")
        self.assertEqual(self.client.calls, [])

    def test_attempt_hook_registered_once_before_threads_and_counts_attempts(self):
        events = FakeEvents()
        self.client.meta = SimpleNamespace(events=events)
        current = self.make()
        self.assertEqual(len(events.registrations), 2)
        self.assertEqual(events.registrations[0][0], "before-send.s3.GetObject")
        self.assertEqual(events.registrations[1][0], "needs-retry.s3.GetObject")
        self.assertEqual(events.registrations[0][2], threading.get_ident())
        self.assertEqual(events.registrations[1][2], threading.get_ident())

        def request(_):
            # Simulated SDK: one logical GET may perform three HTTP attempts.
            for _ in range(3):
                events.emit()
            body = current.get_object(**ARGS)["Body"]
            body.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request, range(30)))
        snapshot = current.metrics.snapshot()
        self.assertTrue(snapshot["http_attempt_telemetry_available"])
        self.assertEqual(snapshot["counters"]["get_calls"], 30)
        self.assertEqual(snapshot["counters"]["http_attempts"], 90)

    def test_attempt_hook_counts_transport_attempts_without_response_metadata(self):
        events = FakeEvents()
        self.client.meta = SimpleNamespace(events=events)
        current = self.make()
        for _ in range(4):
            events.emit()
            self.assertEqual(events.emit("needs-retry.s3.GetObject", response=None,
                                         caught_exception=TimeoutError(SENTINEL)), [None])
        self.client.error = TimeoutError(SENTINEL)
        with self.assertRaises(backend.BackendError):
            current.get_object(**ARGS)
        snapshot = current.metrics.snapshot()
        self.assertEqual(snapshot["counters"]["http_attempts"], 4)
        self.assertEqual(snapshot["counters"]["sdk_retries"], 0)
        self.assertEqual(snapshot["counters"]["failed_gets"], 1)
        self.assertEqual(snapshot["counters"]["attempt_transport_failures"], 4)
        self.assertTrue(snapshot["http_attempt_status_telemetry_available"])

    def test_attempt_statuses_include_retried_503_then_success_200(self):
        events = FakeEvents()
        self.client.meta = SimpleNamespace(events=events)
        self.client.retries = 1
        current = self.make()
        for status in (503, 200):
            events.emit()
            self.assertEqual(events.emit("needs-retry.s3.GetObject",
                                         response=(SimpleNamespace(status_code=status), {}),
                                         caught_exception=None), [None])
        result = current.get_object(**ARGS)
        result["Body"].close()
        snapshot = current.metrics.snapshot()["counters"]
        self.assertEqual(snapshot["get_calls"], 1)
        self.assertEqual(snapshot["http_attempts"], 2)
        self.assertEqual(snapshot["attempt_http_5xx"], 1)
        self.assertEqual(snapshot["attempt_http_2xx"], 1)
        self.assertEqual(snapshot["http_2xx"], 1)
        self.assertEqual(snapshot["http_5xx"], 0)
        self.assertEqual(snapshot["sdk_retries"], 1)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.configs[0]["retries"], {"mode": "standard", "total_max_attempts": 4})

    def test_attempt_status_fallback_and_transport_observation_are_sanitized(self):
        events = FakeEvents()
        self.client.meta = SimpleNamespace(events=events)
        current = self.make()
        self.assertEqual(events.emit("needs-retry.s3.GetObject",
                                     response=(object(), {"ResponseMetadata": {"HTTPStatusCode": 412}}),
                                     caught_exception=None), [None])
        # Body failures after headers have their own counter; these events do
        # not manufacture post-header body retry or request-level error counts.
        for response in (None, (), (object(), None)):
            self.assertEqual(events.emit("needs-retry.s3.GetObject", response=response), [None])
        snapshot = current.metrics.snapshot()
        self.assertEqual(snapshot["counters"]["attempt_http_4xx"], 1)
        self.assertEqual(snapshot["counters"]["body_failures"], 0)
        self.assertNotIn(SENTINEL, json.dumps(snapshot))

    def test_cli_fallback_preserves_conditions_and_cleans_private_temp(self):
        calls = []

        class CLI:
            def call(inner, operation, arguments, outfile=None):
                calls.append((operation, dict(arguments)))
                outfile.write_bytes(PAYLOAD)
                if os.name == "posix":
                    self.assertEqual(outfile.stat().st_mode & 0o777, 0o600)
                return {"ETag": CONDITION, "ContentLength": len(PAYLOAD)}

        current = backend.CLIBackend(CLI(), self.scratch)
        response = current.get_object(**ARGS)
        self.assertEqual(calls, [("get-object", ARGS)])
        self.assertEqual(response["Body"].read(), PAYLOAD)
        self.assertEqual(len(list(self.scratch.iterdir())), 1)
        response["Body"].close()
        self.assertEqual(list(self.scratch.iterdir()), [])
        snapshot = current.metrics.snapshot()
        self.assertFalse(snapshot["http_telemetry_available"])
        self.assertNotIn("headers_seconds", snapshot["latencies"])

    def test_cli_failure_cleans_temp_and_sanitizes_error(self):
        class CLI:
            def call(inner, operation, arguments, outfile=None):
                outfile.write_bytes(b"partial synthetic bytes")
                raise RuntimeError(SENTINEL)

        current = backend.CLIBackend(CLI(), self.scratch)
        with self.assertRaises(backend.BackendError) as caught:
            current.get_object(**ARGS)
        self.assertEqual(str(caught.exception), "cli_request_failed")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_cli_import_and_use_needs_no_boto3(self):
        with patch.dict(sys.modules, {"boto3": None, "botocore": None}):
            spec = importlib.util.spec_from_file_location("cxr_no_sdk_backends", _PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.CLIBackend(object(), self.scratch)
            with self.assertRaises(module.BackendError) as caught:
                module.Boto3Backend(16)
            self.assertEqual(str(caught.exception), "boto3_backend_not_installed")

    def test_metrics_are_bounded_thread_safe_and_do_not_accept_private_labels(self):
        metrics = backend.TransferMetrics()

        def record(_):
            for _ in range(100):
                metrics.increment("bytes_read", 3)
                metrics.observe("headers_seconds", .123)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(20)))
        result = metrics.snapshot()
        self.assertEqual(result["counters"]["bytes_read"], 6000)
        self.assertEqual(result["latencies"]["headers_seconds"]["count"], 2000)
        self.assertGreaterEqual(result["latencies"]["headers_seconds"]["p50_upper_bound"], .123)
        self.assertEqual(len(metrics._latencies["headers_seconds"]["buckets"]), 162)
        with self.assertRaises(backend.BackendError):
            metrics.observe(SENTINEL, .1)
        with self.assertRaises(backend.BackendError):
            metrics.increment(SENTINEL)
        self.assertNotIn(SENTINEL, json.dumps(result))

    def test_invalid_timing_is_rejected_and_snapshot_is_json_safe(self):
        metrics = backend.TransferMetrics()
        for value in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(backend.BackendError):
                metrics.observe("headers_seconds", value)
        metrics.observe("headers_seconds", 999999)
        self.assertEqual(metrics.snapshot()["latencies"]["headers_seconds"]["p95_upper_bound"], 999999)
        json.dumps(metrics.snapshot(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
