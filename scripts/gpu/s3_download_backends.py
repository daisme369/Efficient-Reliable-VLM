"""Optional S3 transports; imports are offline and do not require boto3.

Integrity, publication, locking and receipts belong to bulk_dataset_sync. These
adapters never publish a JPEG or select a different source after a failure.
Only aggregate counters and bounded latency histograms leave this boundary.
"""
from __future__ import annotations

import bisect
from http.client import IncompleteRead
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Mapping


class BackendError(RuntimeError):
    """A fixed code, never a provider message, object key or credential."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StreamReadFailure(BackendError):
    def __init__(self):
        super().__init__("transient_body_read_failed")


_COUNTERS = (
    "get_calls", "http_attempts", "successful_gets", "failed_gets", "sdk_retries", "bytes_read",
    "body_failures", "body_retries", "http_2xx", "http_3xx", "http_4xx",
    "http_5xx", "http_other", "attempt_http_2xx", "attempt_http_3xx",
    "attempt_http_4xx", "attempt_http_5xx", "attempt_http_other",
    "attempt_transport_failures",
)
_PHASES = (
    "headers_seconds", "body_read_seconds", "hash_write_seconds",
    "file_fsync_seconds", "publish_seconds", "object_seconds",
    "fsync_seconds", "total_seconds",
)
_BOUNDARIES = tuple(2 ** (index / 8) / 1000 for index in range(161))


class TransferMetrics:
    """Thread-safe constant-space telemetry, with histogram quantile bounds."""

    def __init__(self, *, http_telemetry_available: bool = False):
        self._guard = threading.Lock()
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self._latencies: dict[str, dict[str, Any]] = {}
        self.http_telemetry_available = http_telemetry_available
        self.http_attempt_telemetry_available = False
        self.http_attempt_status_telemetry_available = False

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in _COUNTERS or type(amount) is not int or amount < 0:
            raise BackendError("invalid_metric")
        with self._guard:
            self._counts[name] += amount

    def observe(self, name: str, seconds: float) -> None:
        if (name not in _PHASES or isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0):
            raise BackendError("invalid_metric")
        with self._guard:
            row = self._latencies.setdefault(name, {
                "count": 0, "sum": 0.0, "min": seconds, "max": seconds,
                "buckets": [0] * (len(_BOUNDARIES) + 1),
            })
            row["count"] += 1
            row["sum"] += seconds
            row["min"] = min(row["min"], seconds)
            row["max"] = max(row["max"], seconds)
            row["buckets"][bisect.bisect_left(_BOUNDARIES, seconds)] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            result: dict[str, Any] = {
                "counters": dict(self._counts), "latencies": {},
                "http_telemetry_available": self.http_telemetry_available,
                "http_attempt_telemetry_available": self.http_attempt_telemetry_available,
                "http_attempt_status_telemetry_available": self.http_attempt_status_telemetry_available,
            }
            for name, row in self._latencies.items():
                out = {key: row[key] for key in ("count", "min", "max")}
                out["mean"] = row["sum"] / row["count"]
                for label, fraction in (("p50_upper_bound", .50), ("p95_upper_bound", .95)):
                    rank, cumulative = math.ceil(row["count"] * fraction), 0
                    for index, count in enumerate(row["buckets"]):
                        cumulative += count
                        if cumulative >= rank:
                            out[label] = (_BOUNDARIES[index] if index < len(_BOUNDARIES)
                                          else row["max"])
                            break
                result["latencies"][name] = out
            result["latency_quantiles"] = "fixed_histogram_upper_bounds_seconds"
            return result


def _arguments(arguments: Mapping[str, Any]) -> None:
    if (not {"Bucket", "Key", "IfMatch"}.issubset(arguments)
            or set(arguments) - {"Bucket", "Key", "IfMatch", "VersionId"}
            or any(not isinstance(value, str) or not value for value in arguments.values())):
        raise BackendError("invalid_conditional_get")


def _status(metrics: TransferMetrics, response: Any) -> None:
    if not isinstance(response, Mapping):
        return
    metadata = response.get("ResponseMetadata", {})
    if not isinstance(metadata, Mapping):
        return
    retries = metadata.get("RetryAttempts", 0)
    if type(retries) is int and retries >= 0:
        metrics.increment("sdk_retries", retries)
    code = metadata.get("HTTPStatusCode")
    if type(code) is int:
        category = f"http_{code // 100}xx" if 200 <= code < 600 else "http_other"
        metrics.increment(category)


class _ObservedBody:
    def __init__(self, body: Any, metrics: TransferMetrics, transport_errors: tuple[type, ...]):
        self._body = body
        self._metrics = metrics
        self._transport_errors = transport_errors

    def read(self, size: int = -1) -> bytes:
        started = time.monotonic()
        try:
            value = self._body.read(size)
        except Exception as error:
            self._metrics.increment("body_failures")
            if isinstance(error, self._transport_errors):
                raise StreamReadFailure() from None
            raise BackendError("body_read_failed") from None
        finally:
            self._metrics.observe("body_read_seconds", time.monotonic() - started)
        if isinstance(value, bytes):
            self._metrics.increment("bytes_read", len(value))
        return value

    def close(self) -> None:
        try:
            self._body.close()
        except Exception:
            raise BackendError("body_close_failed") from None


class Boto3Backend:
    """One reusable low-level client, initialized before worker threads exist.

    Request retries stay inside standard-mode botocore (four total attempts).
    Only a recognized interrupted body permits one whole-object restart. Thus
    an object uses at most two SDK calls / eight request attempts. Integrity
    failures and exhausted request failures never trigger that body retry.
    """

    def __init__(self, workers: int, region_name: str = "us-east-1", *,
                 session_factory=None, config_factory=None, metrics=None):
        if type(workers) is not int or not 1 <= workers <= 64:
            raise BackendError("invalid_worker_count")
        if region_name != "us-east-1":
            raise BackendError("invalid_source_region")
        transport_errors: tuple[type, ...] = (OSError, TimeoutError, IncompleteRead)
        if session_factory is None or config_factory is None:
            try:
                from boto3.session import Session
                from botocore.config import Config
                from botocore.exceptions import (
                    IncompleteReadError, ReadTimeoutError, ResponseStreamingError,
                )
            except ImportError:
                raise BackendError("boto3_backend_not_installed") from None
            session_factory = session_factory or Session
            config_factory = config_factory or Config
            transport_errors += (IncompleteReadError, ReadTimeoutError, ResponseStreamingError)
        self.metrics = metrics if metrics is not None else TransferMetrics(http_telemetry_available=True)
        self.metrics.http_telemetry_available = True
        self.pool_connections = max(32, workers * 2)
        self._transport_errors = transport_errors
        self._close_guard = threading.Lock()
        self._closed = False
        try:
            config = config_factory(
                max_pool_connections=self.pool_connections,
                connect_timeout=20, read_timeout=120,
                retries={"mode": "standard", "total_max_attempts": 4},
            )
            self._session = session_factory()
            self._client = self._session.client("s3", region_name=region_name, config=config)
            events = getattr(getattr(self._client, "meta", None), "events", None)
            register = getattr(events, "register", None)
            if callable(register):
                # Registered once, before threads. Never inspect the event's
                # request object, which can carry restricted keys or headers.
                register("before-send.s3.GetObject", self._before_send)
                register("needs-retry.s3.GetObject", self._observe_attempt)
                self.metrics.http_attempt_telemetry_available = True
                self.metrics.http_attempt_status_telemetry_available = True
        except Exception:
            raise BackendError("s3_client_initialization_failed") from None

    def _before_send(self, **unused) -> None:
        self.metrics.increment("http_attempts")

    def _observe_attempt(self, response=None, caught_exception=None, **unused) -> None:
        """Observe status only; returning None leaves SDK retry policy intact."""
        if caught_exception is not None:
            self.metrics.increment("attempt_transport_failures")
        status = None
        # The SDK event supplies (HTTPResponse, parsed_response). Deliberately
        # never read the request, headers, response body or exception message.
        if isinstance(response, (tuple, list)) and response:
            status = getattr(response[0], "status_code", None)
            if type(status) is not int and len(response) > 1 and isinstance(response[1], Mapping):
                metadata = response[1].get("ResponseMetadata", {})
                if isinstance(metadata, Mapping):
                    status = metadata.get("HTTPStatusCode")
        if type(status) is int:
            category = (f"attempt_http_{status // 100}xx" if 200 <= status < 600
                        else "attempt_http_other")
            self.metrics.increment(category)

    def get_object(self, **arguments) -> Mapping[str, Any]:
        if self._closed:
            raise BackendError("s3_client_closed")
        _arguments(arguments)
        self.metrics.increment("get_calls")
        started = time.monotonic()
        try:
            response = self._client.get_object(**arguments)
        except Exception as error:
            self.metrics.increment("failed_gets")
            _status(self.metrics, getattr(error, "response", None))
            raise BackendError("s3_request_failed") from None
        finally:
            self.metrics.observe("headers_seconds", time.monotonic() - started)
        self.metrics.increment("successful_gets")
        _status(self.metrics, response)
        if not isinstance(response, Mapping):
            raise BackendError("invalid_get_response")
        result = dict(response)
        body = result.get("Body")
        if body is not None and callable(getattr(body, "read", None)) and callable(getattr(body, "close", None)):
            result["Body"] = _ObservedBody(body, self.metrics, self._transport_errors)
        return result

    def stream_retry_delay(self, error: Exception, retry_index: int) -> float | None:
        if isinstance(error, StreamReadFailure) and retry_index == 0:
            self.metrics.increment("body_retries")
            return .25
        return None

    def record_phase(self, name: str, seconds: float) -> None:
        self.metrics.observe(name, seconds)

    def close(self) -> None:
        with self._close_guard:
            if self._closed:
                return
            self._closed = True
        try:
            self._client.close()
        except Exception:
            raise BackendError("s3_client_close_failed") from None


class _PrivateCLIStream:
    def __init__(self, path: Path, metrics: TransferMetrics):
        self._path = path
        self._stream = path.open("rb")
        self._metrics = metrics

    def read(self, size: int = -1) -> bytes:
        value = self._stream.read(size)
        self._metrics.increment("bytes_read", len(value))
        return value

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            self._path.unlink(missing_ok=True)


class CLIBackend:
    """Explicit fallback using the existing conditional AWS CLI wrapper.

    Request latency/HTTP retry telemetry is unavailable here. The CLI operation
    is a full-body download, so it is deliberately not recorded as headers time.
    """

    def __init__(self, s3: Any, scratch: Path, *, metrics=None):
        self._s3 = s3
        self._scratch = Path(scratch)
        self.metrics = metrics if metrics is not None else TransferMetrics()
        self.metrics.http_telemetry_available = False
        self.metrics.http_attempt_telemetry_available = False
        self.metrics.http_attempt_status_telemetry_available = False
        if (not self._scratch.is_dir()
                or any(path.is_symlink() for path in (self._scratch, *self._scratch.parents))):
            raise BackendError("invalid_cli_scratch")
        if os.name == "posix":
            info = self._scratch.stat()
            if info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise BackendError("unsafe_cli_scratch")

    def get_object(self, **arguments) -> Mapping[str, Any]:
        _arguments(arguments)
        self.metrics.increment("get_calls")
        descriptor, filename = tempfile.mkstemp(prefix="cli-object-", dir=self._scratch)
        os.close(descriptor)
        path = Path(filename)
        try:
            response = self._s3.call("get-object", arguments, outfile=path)
            result = dict(response)
            result["Body"] = _PrivateCLIStream(path, self.metrics)
            self.metrics.increment("successful_gets")
            return result
        except Exception:
            path.unlink(missing_ok=True)
            self.metrics.increment("failed_gets")
            raise BackendError("cli_request_failed") from None

    def record_phase(self, name: str, seconds: float) -> None:
        self.metrics.observe(name, seconds)

    def close(self) -> None:
        pass
