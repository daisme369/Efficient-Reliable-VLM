"""Resume a frozen full-JPEG plan with one private, exclusive download manager.

This command performs downloads only when explicitly invoked. Import and --help
are offline. It never rents instances, creates plans, maps QA, or starts training.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bulk_dataset_sync import (
    BulkSyncError, _assert_no_symlinks, _open_regular, _private_directory,
    fetch_plan, load_plan,
)
from dataset_sync import S3, private_path, source_settings
from s3_download_backends import BackendError, Boto3Backend, CLIBackend


def _hash_private(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular(path) as stream:
        info = os.fstat(stream.fileno())
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise BulkSyncError("unsafe_control_permissions")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_status(path: Path, value: dict) -> None:
    """Called only by the coordinator while it owns the dataset lock."""
    _private_directory(path.parent)
    _assert_no_symlinks(path)
    if path.exists():
        _hash_private(path)
    temporary = path.parent / (".download-status-" + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_certificate(path: Path, value: dict) -> None:
    """Publish a durable, complete JSON certificate without replacing a file."""
    _private_directory(path.parent)
    _assert_no_symlinks(path)
    temporary = path.parent / (".download-certificate-" + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path, follow_symlinks=False)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _credentials_from_stdin() -> dict[str, str]:
    """Accept only the existing RAM-only credential envelope, never source data."""
    allowed = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or set(payload) != {"credentials"}:
            raise ValueError
        values = payload["credentials"]
        if (not isinstance(values, dict) or set(values) - allowed
                or not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}.issubset(values)
                or any(not isinstance(v, str) or not v or any(ord(c) < 32 for c in v)
                       for v in values.values())):
            raise ValueError
        return values
    except Exception:
        raise BulkSyncError("invalid_credential_envelope") from None


def run(args, *, shutdown_event=None, backend_factory=None) -> dict:
    """Validate local control inputs, then resume under fetch_plan's sole lock."""
    for name in ("plan", "publisher", "data_root", "receipt", "status", "completion", "source_env", "access_review"):
        _assert_no_symlinks(Path(getattr(args, name)))
    paths = {name: private_path(getattr(args, name)) for name in (
        "plan", "publisher", "data_root", "receipt", "status", "completion",
        "source_env", "access_review",
    )}
    if len(set(paths.values())) != len(paths):
        raise BulkSyncError("control_paths_overlap")
    # A status/receipt must never alias, replace or live below image payloads.
    for name in ("plan", "publisher", "receipt", "status", "completion", "source_env", "access_review"):
        _assert_no_symlinks(paths[name])
        if paths[name].is_relative_to(paths["data_root"] / "files"):
            raise BulkSyncError("control_path_inside_images")
    if paths["receipt"].parent != paths["data_root"]:
        raise BulkSyncError("receipt_requires_data_root")
    if paths["completion"].exists():
        raise BulkSyncError("completion_certificate_exists")
    if _hash_private(paths["access_review"]) != args.access_review_sha256:
        raise BulkSyncError("access_review_hash_mismatch")
    source = source_settings(paths["source_env"])["images"]
    # Crucially, do NOT call make_plan here. It estimates a first full download.
    # fetch_plan rehashes existing files and checks only missing bytes + reserve.
    plan = load_plan(
        paths["plan"], expected_plan_sha256=args.plan_sha256,
        byte_budget=args.max_bytes, reserve_bytes=args.reserve_bytes,
        bucket=source["bucket"], prefix=source["prefix"],
        publisher_path=paths["publisher"], expected_images=args.expected_images,
    )
    if type(args.workers) is not int or not 1 <= args.workers <= 64:
        raise BulkSyncError("invalid_worker_count")
    if not source["bucket"].startswith("arn:aws:s3:us-east-1:"):
        raise BulkSyncError("expected_us_east_1_access_point")
    shutdown = shutdown_event if shutdown_event is not None else threading.Event()
    client = None
    timer = None
    last_write = 0.0
    try:
        if backend_factory is not None:
            client = backend_factory(args.backend, args.workers)
        elif args.backend == "boto3":
            # Avoid implicit metadata/profile discovery in an unattended rental.
            if not all(os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")):
                raise BulkSyncError("runtime_aws_credentials_required")
            client = Boto3Backend(args.workers, region_name="us-east-1")
        else:
            scratch = _private_directory(paths["status"].parent / "cli-object-temporary")
            client = CLIBackend(S3(paths["source_env"]), scratch)

        def locked_check():
            # Recheck under lock BEFORE touching receipts, closing the race
            # with another manager that completed while we loaded our plan.
            if paths["completion"].exists():
                raise BulkSyncError("completion_certificate_exists")

        def progress(value):
            nonlocal last_write, timer
            # First callback is inside the lock, AFTER existing-file preflight.
            # Never overwrite a live manager's status if lock acquisition fails.
            if timer is None and args.stop_after_seconds:
                timer = threading.Timer(args.stop_after_seconds, shutdown.set)
                timer.daemon = True
                timer.start()
            now = time.monotonic()
            if now - last_write < 5 and value["status"] == "running":
                return
            snapshot = dict(value, backend=args.backend, workers=args.workers,
                            updated_unix=time.time(), shutdown_requested=shutdown.is_set(),
                            mimic_training_started=False, full_mapping_started=False,
                            backend_metrics=client.metrics.snapshot())
            if value["status"] == "complete":
                _write_certificate(paths["completion"], value)
            _write_status(paths["status"], snapshot)
            last_write = now

        result = fetch_plan(
            client, plan, paths["data_root"], paths["receipt"],
            expected_plan_sha256=args.plan_sha256, byte_budget=args.max_bytes,
            reserve_bytes=args.reserve_bytes, workers=args.workers,
            progress=progress, shutdown_event=shutdown, locked_check=locked_check,
        )
        return result
    finally:
        if timer is not None:
            timer.cancel()
        if client is not None:
            client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "publisher", "data-root", "receipt", "status", "completion",
                 "source-env", "access-review", "plan-sha256", "access-review-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--reserve-bytes", type=int, required=True)
    parser.add_argument("--expected-images", type=int, default=377110)
    parser.add_argument("--backend", choices=("boto3", "cli"), default="boto3")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--stop-after-seconds", type=float, default=0,
                        help="Stop scheduling after this timed window; drain submitted work.")
    parser.add_argument("--credentials-stdin", action="store_true")
    args = parser.parse_args(argv)
    shutdown = threading.Event()
    previous_signals = {}
    previous_credentials = {}
    try:
        if not 0 <= args.stop_after_seconds <= 86400:
            raise BulkSyncError("invalid_stop_after_seconds")
        if args.credentials_stdin:
            values = _credentials_from_stdin()
            # Replace the entire group so an inherited session token cannot mix.
            for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
                previous_credentials[key] = os.environ.pop(key, None)
            os.environ.update(values)
            values.clear()
        for number in (signal.SIGINT, signal.SIGTERM):
            previous_signals[number] = signal.signal(number, lambda *_: shutdown.set())
        result = run(args, shutdown_event=shutdown)
        print(json.dumps({k: result[k] for k in ("status", "verified_objects", "verified_bytes")}))
        return 0 if result["status"] == "complete" else 130
    except (BulkSyncError, BackendError) as error:
        print(json.dumps({"status": "failed", "error_code": error.code}))
        return 1
    except Exception:
        print(json.dumps({"status": "failed", "error_code": "download_failed"}))
        return 1
    finally:
        for number, handler in previous_signals.items():
            signal.signal(number, handler)
        for key, value in previous_credentials.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
