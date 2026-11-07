"""Private full-JPEG transfer, independent of QA selection or mapping.

The caller supplies an already authorized S3-compatible client and private paths.
This module imports only the standard library and emits no stdout or raw errors.
Client contract: list_objects_v2(**kwargs), get_object(**kwargs) -> mapping with
ETag, ContentLength and a Body exposing read(n) and close().  The client must be
safe for the chosen number of threads and configure its own network timeouts.

Full-download completion means every publisher JPEG has matching size/SHA256;
it does not approve a training protocol, inspect labels, or map any QA.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import threading
import time
from typing import Any, Callable, Iterable, Mapping
import uuid


class BulkSyncError(RuntimeError):
    """Only a fixed non-sensitive error code may leave the transfer boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE = re.compile(
    r"files/p(?P<shard>[0-9]{2})/p(?P<subject>[0-9]{8})/s[0-9]{8}/"
    r"(?:[0-9a-f]{8}-){4}[0-9a-f]{8}\.jpg\Z"
)
_PLAN_FIELDS = {
    "version", "kind", "bucket", "prefix", "publisher_sha256", "object_count",
    "total_bytes", "byte_budget", "reserve_bytes", "entries", "plan_sha256",
}
_ENTRY_FIELDS = {"key", "size", "etag", "sha256", "relative_path"}
_KIND = "mimic-cxr-jpg-full-raw"
_CHUNK = 1024 * 1024


def _fail(code: str) -> None:
    raise BulkSyncError(code) from None


def _integer(value: Any, code: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def _safe_path_text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        _fail("unsafe_path")
    if value.startswith("/") or "\\" in value or ":" in value:
        _fail("unsafe_path")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        _fail("unsafe_path")
    if any(part in ("", ".", "..") for part in value.split("/")):
        _fail("unsafe_path")
    return value


def _image_path(value: Any) -> str:
    text = _safe_path_text(value)
    match = _IMAGE.fullmatch(text)
    if match is None or not match["subject"].startswith(match["shard"]):
        _fail("unsafe_image_path")
    return text


def _prefix(value: Any) -> str:
    if not isinstance(value, str):
        _fail("invalid_prefix")
    if value == "":
        return value
    if not value.endswith("/"):
        _fail("invalid_prefix")
    _safe_path_text(value[:-1])
    return value


def _bucket(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        _fail("invalid_bucket")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value) or "?" in value:
        _fail("invalid_bucket")
    if value.startswith(("http:", "https:", "s3:")):
        _fail("invalid_bucket")
    return value


def _etag(value: Any) -> str:
    if not isinstance(value, str) or not 3 <= len(value) <= 512:
        _fail("invalid_etag")
    if not value.startswith('"') or not value.endswith('"'):
        _fail("invalid_etag")
    if '"' in value[1:-1] or any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        _fail("invalid_etag")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def plan_sha256(plan: Mapping[str, Any]) -> str:
    """Hash the complete canonical plan excluding its own digest field."""
    return hashlib.sha256(_canonical({k: v for k, v in plan.items() if k != "plan_sha256"})).hexdigest()


def _publisher(path: Path) -> tuple[dict[str, str], str]:
    checksums: dict[str, str] = {}
    digest = hashlib.sha256()
    try:
        with _open_regular(Path(path)) as handle:
            before = os.fstat(handle.fileno())
            for raw in handle:
                digest.update(raw)
                line = raw.decode("ascii").rstrip("\r\n")
                if not line:
                    continue
                match = re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+(\*?)(.+)", line)
                if match is None:
                    _fail("invalid_publisher_manifest")
                relative = match[3]
                if relative.startswith("./"):
                    relative = relative[2:]
                _safe_path_text(relative)
                if not relative.lower().endswith((".jpg", ".jpeg")):
                    continue
                relative = _image_path(relative)
                if relative in checksums:
                    _fail("duplicate_publisher_image")
                checksums[relative] = match[1].lower()
            after = os.fstat(handle.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                _fail("publisher_changed_during_read")
    except BulkSyncError:
        raise
    except (OSError, UnicodeError, ValueError):
        _fail("publisher_read_failed")
    if not checksums:
        _fail("empty_publisher_images")
    return checksums, digest.hexdigest()


def parse_publisher(path: Path) -> dict[str, str]:
    """Return publisher JPEG checksums without accessing QA or image payloads."""
    return _publisher(path)[0]


def _listing(client: Any, bucket: str, prefix: str) -> Iterable[Mapping[str, Any]]:
    token = None
    seen_tokens: set[str] = set()
    while True:
        arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix + "files/", "MaxKeys": 1000}
        if token is not None:
            arguments["ContinuationToken"] = token
        try:
            page = client.list_objects_v2(**arguments)
        except Exception:
            _fail("listing_failed")
        if not isinstance(page, Mapping) or type(page.get("IsTruncated", False)) is not bool:
            _fail("invalid_listing_page")
        items = page.get("Contents", [])
        if not isinstance(items, list):
            _fail("invalid_listing_page")
        yield from items
        if not page.get("IsTruncated", False):
            return
        token = page.get("NextContinuationToken")
        if not isinstance(token, str) or not token or token in seen_tokens:
            _fail("invalid_listing_pagination")
        seen_tokens.add(token)


def make_plan(
    client: Any,
    bucket: str,
    prefix: str,
    publisher_path: Path,
    *,
    byte_budget: int,
    free_bytes: int,
    reserve_bytes: int,
    expected_images: int | None = None,
    listing: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan EVERY publisher JPEG; never select or join using QA tables.

    An optional captured listing is validated identically to live pagination.
    Planning conservatively requires room for all bytes plus reserve.  Fetching
    recomputes remaining capacity after validating existing files for resume.
    """
    bucket, prefix = _bucket(bucket), _prefix(prefix)
    byte_budget = _integer(byte_budget, "invalid_byte_budget", 1)
    free_bytes = _integer(free_bytes, "invalid_free_bytes")
    reserve_bytes = _integer(reserve_bytes, "invalid_reserve_bytes")
    if expected_images is not None:
        expected_images = _integer(expected_images, "invalid_expected_count", 1)
    published, publisher_hash = _publisher(Path(publisher_path))
    if expected_images is not None and len(published) != expected_images:
        _fail("publisher_count_mismatch")
    entries: dict[str, dict[str, Any]] = {}
    total = 0
    try:
        objects = _listing(client, bucket, prefix) if listing is None else listing
        for item in objects:
            if not isinstance(item, Mapping):
                _fail("invalid_listing_object")
            key = item.get("Key")
            if not isinstance(key, str) or not key.startswith(prefix + "files/"):
                _fail("listing_prefix_mismatch")
            relative = key[len(prefix):]
            _safe_path_text(relative)
            if not relative.lower().endswith((".jpg", ".jpeg")):
                continue
            relative = _image_path(relative)
            if relative not in published:
                _fail("unpublished_listing_image")
            if relative in entries:
                _fail("duplicate_listing_image")
            size = _integer(item.get("Size"), "invalid_object_size", 1)
            entries[relative] = {
                "key": key, "relative_path": relative, "size": size,
                "etag": _etag(item.get("ETag")), "sha256": published[relative],
            }
            total += size
            if total > byte_budget:
                _fail("byte_budget_exceeded")
    except BulkSyncError:
        raise
    except Exception:
        _fail("listing_failed")
    if entries.keys() != published.keys():
        _fail("missing_publisher_image")
    if total + reserve_bytes > free_bytes:
        _fail("insufficient_disk_space")
    plan: dict[str, Any] = {
        "version": 1, "kind": _KIND, "bucket": bucket, "prefix": prefix,
        "publisher_sha256": publisher_hash, "object_count": len(entries),
        "total_bytes": total, "byte_budget": byte_budget, "reserve_bytes": reserve_bytes,
        "entries": [entries[name] for name in sorted(entries)],
    }
    plan["plan_sha256"] = plan_sha256(plan)
    return plan


def _validate_plan(plan: Mapping[str, Any], expected: str, budget: int, reserve: int) -> dict[str, Any]:
    if not isinstance(plan, Mapping) or set(plan) != _PLAN_FIELDS:
        _fail("invalid_plan_schema")
    if not isinstance(expected, str) or _SHA.fullmatch(expected) is None:
        _fail("invalid_expected_plan_hash")
    if plan.get("version") != 1 or type(plan.get("version")) is not int or plan.get("kind") != _KIND:
        _fail("invalid_plan_schema")
    if not isinstance(plan.get("publisher_sha256"), str) or _SHA.fullmatch(plan["publisher_sha256"]) is None:
        _fail("invalid_publisher_hash")
    bucket, prefix = _bucket(plan.get("bucket")), _prefix(plan.get("prefix"))
    budget = _integer(budget, "invalid_byte_budget", 1)
    reserve = _integer(reserve, "invalid_reserve_bytes")
    planned_budget = _integer(plan.get("byte_budget"), "invalid_byte_budget", 1)
    planned_reserve = _integer(plan.get("reserve_bytes"), "invalid_reserve_bytes")
    if reserve < planned_reserve:
        _fail("reserve_below_plan")
    raw_entries = plan.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        _fail("invalid_plan_entries")
    entries, seen, total = [], set(), 0
    for item in raw_entries:
        if not isinstance(item, Mapping) or set(item) != _ENTRY_FIELDS:
            _fail("invalid_plan_entry")
        relative = _image_path(item.get("relative_path"))
        if relative in seen or item.get("key") != prefix + relative:
            _fail("invalid_plan_identity")
        seen.add(relative)
        size = _integer(item.get("size"), "invalid_object_size", 1)
        checksum = item.get("sha256")
        if not isinstance(checksum, str) or _SHA.fullmatch(checksum) is None:
            _fail("invalid_object_hash")
        entries.append({"relative_path": relative, "key": prefix + relative, "size": size,
                        "sha256": checksum, "etag": _etag(item.get("etag"))})
        total += size
    if _integer(plan.get("object_count"), "invalid_object_count", 1) != len(entries):
        _fail("plan_count_mismatch")
    if _integer(plan.get("total_bytes"), "invalid_total_bytes", 1) != total:
        _fail("plan_byte_count_mismatch")
    if total > budget or total > planned_budget:
        _fail("byte_budget_exceeded")
    # Copy before hashing and use only this detached, validated snapshot afterward.
    snapshot = dict(plan)
    snapshot.update(bucket=bucket, prefix=prefix, entries=entries)
    try:
        actual = plan_sha256(snapshot)
    except (TypeError, ValueError, UnicodeError):
        _fail("invalid_plan_schema")
    if plan.get("plan_sha256") != expected or actual != expected:
        _fail("plan_hash_mismatch")
    return snapshot


def load_plan(
    path: Path, *, expected_plan_sha256: str, byte_budget: int,
    reserve_bytes: int, bucket: str, prefix: str, publisher_path: Path,
    expected_images: int | None = None,
) -> dict[str, Any]:
    """Load the existing frozen plan WITHOUT requiring full-release free space.

    The hash is supplied by the operator, not trusted from the file itself.
    Remaining capacity and reusable payloads are checked under the fetch lock.
    This loader never lists S3, opens receipts or modifies existing plans.
    """
    try:
        with _open_regular(Path(path)) as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > 512 * 1024**2:
                _fail("plan_file_too_large")
            if os.name == "posix" and (before.st_mode & 0o077 or before.st_uid != os.getuid()):
                _fail("unsafe_plan_permissions")
            value = json.load(stream)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                _fail("plan_changed_during_read")
        snapshot = _validate_plan(value, expected_plan_sha256, byte_budget, reserve_bytes)
        if snapshot["bucket"] != _bucket(bucket) or snapshot["prefix"] != _prefix(prefix):
            _fail("plan_source_mismatch")
        published, publisher_hash = _publisher(Path(publisher_path))
        if snapshot["publisher_sha256"] != publisher_hash:
            _fail("publisher_changed")
        planned = {entry["relative_path"]: entry["sha256"] for entry in snapshot["entries"]}
        if planned != published:
            _fail("plan_publisher_mismatch")
        if expected_images is not None and len(planned) != _integer(expected_images, "invalid_expected_images", 1):
            _fail("publisher_image_count_mismatch")
        return snapshot
    except BulkSyncError:
        raise
    except Exception:
        _fail("plan_load_failed")


def _assert_no_symlinks(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            _fail("symlink_rejected")


def _private_directory(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    _assert_no_symlinks(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        _assert_no_symlinks(directory)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        _fail("not_a_directory")
    if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        _fail("unsafe_directory_permissions")
    return path


@contextmanager
def _open_regular(path: Path):
    _assert_no_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("not_a_regular_file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            yield handle
    finally:
        os.close(descriptor)


def _verify_existing(path: Path, entry: Mapping[str, Any]) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        _assert_no_symlinks(path.parent)
        return False
    with _open_regular(path) as handle:
        before = os.fstat(handle.fileno())
        if before.st_size != entry["size"]:
            _fail("existing_file_mismatch")
        if os.name == "posix" and (before.st_mode & 0o077 or before.st_uid != os.getuid()):
            _fail("unsafe_file_permissions")
        digest = hashlib.sha256()
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            _fail("existing_file_changed")
        if digest.hexdigest() != entry["sha256"]:
            _fail("existing_file_mismatch")
    return True


@contextmanager
def _exclusive_lock(root: Path, plan_hash: str):
    path = root / ".bulk-dataset-sync.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail("lock_exists")
    identity = os.fstat(descriptor)
    try:
        content = _canonical({"pid": os.getpid(), "plan_sha256": plan_hash, "started_unix": time.time()})
        os.write(descriptor, content)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


def _receipt_open(path: Path, plan_hash: str) -> sqlite3.Connection:
    _private_directory(path.parent)
    _assert_no_symlinks(path)
    # SQLite can open/recover sidecars during connect or the first PRAGMA.
    # Inspect ALL conventional sidecar names before it can touch any of them.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        _assert_no_symlinks(sidecar)
        if sidecar.exists():
            info = sidecar.lstat()
            if not stat.S_ISREG(info.st_mode):
                _fail("unsafe_receipt_sidecar")
            if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
                _fail("unsafe_receipt_permissions")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with _open_regular(path) as handle:
            info = os.fstat(handle.fileno())
            if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
                _fail("unsafe_receipt_permissions")
    else:
        os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS files (relative_path TEXT PRIMARY KEY, size INTEGER NOT NULL, sha256 TEXT NOT NULL, etag TEXT NOT NULL, verified_unix REAL NOT NULL)")
        bound = connection.execute("SELECT value FROM metadata WHERE name='plan_sha256'").fetchone()
        if bound is not None and bound[0] != plan_hash:
            _fail("receipt_plan_mismatch")
        if bound is None and connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]:
            _fail("unbound_receipt")
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('plan_sha256',?)", (plan_hash,))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('status','running')")
        connection.commit()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                _assert_no_symlinks(sidecar)
                if os.name == "posix" and sidecar.stat().st_mode & 0o077:
                    _fail("unsafe_receipt_permissions")
        return connection
    except BaseException:
        connection.close()
        raise


def _receipt_record(connection: sqlite3.Connection, entry: Mapping[str, Any]) -> None:
    connection.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
                       (entry["relative_path"], entry["size"], entry["sha256"], entry["etag"], time.time()))


def _download(
    client: Any,
    bucket: str,
    entry: Mapping[str, Any],
    root: Path,
    reserve: int,
    available: Callable[[], int],
    stopped: threading.Event,
) -> None:
    started = time.monotonic()
    # SDK request retries belong to the backend. Only its explicitly identified
    # post-header stream failure may restart the object, at most once. Each
    # attempt cleans its own private temp before a fresh conditional GET.
    for retry_index in range(2):
        try:
            _transfer_once(client, bucket, entry, root, reserve, available, stopped)
            _record_phase(client, "object_seconds", time.monotonic() - started)
            return
        except BulkSyncError:
            raise
        except Exception as error:
            policy = getattr(client, "stream_retry_delay", None)
            delay = policy(error, retry_index) if callable(policy) else None
            if retry_index or type(delay) not in (int, float) or not 0 <= delay <= 5:
                _fail("object_transfer_failed")
            if stopped.wait(delay):
                _fail("transfer_cancelled")
    _fail("object_transfer_failed")


def _record_phase(client: Any, name: str, seconds: float) -> None:
    recorder = getattr(client, "record_phase", None)
    if callable(recorder):
        recorder(name, seconds)


def _transfer_once(
    client: Any, bucket: str, entry: Mapping[str, Any], root: Path,
    reserve: int, available: Callable[[], int], stopped: threading.Event,
) -> None:
    if stopped.is_set():
        _fail("transfer_cancelled")
    destination = root.joinpath(*PurePosixPath(entry["relative_path"]).parts)
    parent = _private_directory(destination.parent)
    _assert_no_symlinks(destination)
    if destination.exists():
        # A file appearing after preflight is never overwritten, even if equal.
        _fail("destination_appeared")
    if _integer(available(), "invalid_free_bytes") < entry["size"] + reserve:
        _fail("insufficient_disk_space")
    temp = parent / (".bulk-partial-" + uuid.uuid4().hex)
    body = None
    descriptor = None
    try:
        response = client.get_object(Bucket=bucket, Key=entry["key"], IfMatch=entry["etag"])
        if not isinstance(response, Mapping):
            _fail("invalid_get_response")
        body = response.get("Body")
        if response.get("ETag") != entry["etag"]:
            _fail("source_identity_changed")
        if type(response.get("ContentLength")) is not int or response["ContentLength"] != entry["size"]:
            _fail("source_size_changed")
        if body is None or not callable(getattr(body, "read", None)) or not callable(getattr(body, "close", None)):
            _fail("invalid_get_body")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        digest, received = hashlib.sha256(), 0
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            while True:
                if stopped.is_set():
                    _fail("transfer_cancelled")
                chunk = body.read(_CHUNK)
                if not isinstance(chunk, bytes):
                    _fail("invalid_body_chunk")
                if not chunk:
                    break
                received += len(chunk)
                if received > entry["size"]:
                    _fail("download_size_mismatch")
                if _integer(available(), "invalid_free_bytes") < len(chunk) + reserve:
                    _fail("insufficient_disk_space")
                write_started = time.monotonic()
                output.write(chunk)
                digest.update(chunk)
                _record_phase(client, "hash_write_seconds", time.monotonic() - write_started)
            output.flush()
            fsync_started = time.monotonic()
            os.fsync(output.fileno())
            _record_phase(client, "file_fsync_seconds", time.monotonic() - fsync_started)
        if received != entry["size"] or digest.hexdigest() != entry["sha256"]:
            _fail("download_integrity_mismatch")
        os.close(descriptor)
        descriptor = None
        _assert_no_symlinks(destination)
        # Hard-link publication fails if ANY destination already exists.
        publish_started = time.monotonic()
        os.link(temp, destination, follow_symlinks=False)
        if os.name == "posix":
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        _record_phase(client, "publish_seconds", time.monotonic() - publish_started)
    except BulkSyncError:
        raise
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass
        if descriptor is not None:
            os.close(descriptor)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def fetch_plan(
    client: Any,
    plan: Mapping[str, Any],
    data_root: Path,
    receipt_path: Path,
    *,
    expected_plan_sha256: str,
    byte_budget: int,
    reserve_bytes: int,
    workers: int = 16,
    free_bytes: Callable[[], int] | None = None,
    progress: Callable[[dict[str, Any]], Any] | None = None,
    shutdown_event: threading.Event | None = None,
    locked_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Download or hash-verify every planned image, then return aggregate proof.

    Existing files and receipts never replace content verification. Interrupted
    runs can resume under the SAME hash-bound plan; stale process locks require
    deliberate operator inspection. All SQLite mutations happen in this thread.
    The function returns no certificate on failure and never starts QA mapping.
    """
    snapshot = _validate_plan(plan, expected_plan_sha256, byte_budget, reserve_bytes)
    workers = _integer(workers, "invalid_worker_count", 1)
    if workers > 64:
        _fail("invalid_worker_count")
    if shutdown_event is not None and not isinstance(shutdown_event, threading.Event):
        _fail("invalid_shutdown_event")
    shutdown = shutdown_event if shutdown_event is not None else threading.Event()
    started, connection, stopped = time.time(), None, threading.Event()
    result: dict[str, Any] = {
        "status": "running", "plan_sha256": expected_plan_sha256,
        "objects": snapshot["object_count"], "total_bytes": snapshot["total_bytes"],
        "verified_objects": 0, "verified_bytes": 0, "downloaded_objects": 0,
        "downloaded_bytes": 0, "reused_objects": 0, "reused_bytes": 0,
    }
    try:
        root = _private_directory(Path(data_root))
        receipt = Path(os.path.abspath(receipt_path))
        available = free_bytes if free_bytes is not None else lambda: shutil.disk_usage(root).free
        if not callable(available) or (progress is not None and not callable(progress)):
            _fail("invalid_callback")
        with _exclusive_lock(root, expected_plan_sha256):
            if locked_check is not None:
                locked_check()
            try:
                connection = _receipt_open(receipt, expected_plan_sha256)
                pending = []
                # Check all existing destinations before issuing ANY GET.
                for entry in snapshot["entries"]:
                    destination = root.joinpath(*PurePosixPath(entry["relative_path"]).parts)
                    if _verify_existing(destination, entry):
                        _receipt_record(connection, entry)
                        result["reused_objects"] += 1
                        result["reused_bytes"] += entry["size"]
                        result["verified_objects"] += 1
                        result["verified_bytes"] += entry["size"]
                        if result["verified_objects"] % 64 == 0:
                            connection.commit()
                    else:
                        pending.append(entry)
                required = snapshot["total_bytes"] - result["reused_bytes"] + reserve_bytes
                if _integer(available(), "invalid_free_bytes") < required:
                    _fail("insufficient_disk_space")
                connection.commit()
                if progress is not None:
                    progress(dict(result, elapsed_seconds=time.time() - started))
                iterator = iter(pending)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    in_flight = {}
                    def fill() -> None:
                        while len(in_flight) < workers * 2 and not shutdown.is_set():
                            entry = next(iterator, None)
                            if entry is None:
                                break
                            future = pool.submit(_download, client, snapshot["bucket"], entry,
                                                 root, reserve_bytes, available, stopped)
                            in_flight[future] = entry
                    fill()
                    try:
                        while in_flight:
                            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                            for future in done:
                                entry = in_flight.pop(future)
                                future.result()
                                _receipt_record(connection, entry)
                                result["downloaded_objects"] += 1
                                result["downloaded_bytes"] += entry["size"]
                                result["verified_objects"] += 1
                                result["verified_bytes"] += entry["size"]
                                if result["verified_objects"] % 64 == 0:
                                    connection.commit()
                                if progress is not None:
                                    progress(dict(result, elapsed_seconds=time.time() - started))
                            fill()
                    except BaseException:
                        stopped.set()
                        for future in in_flight:
                            future.cancel()
                        raise
                complete = (result["verified_objects"] == snapshot["object_count"]
                            and result["verified_bytes"] == snapshot["total_bytes"])
                if not complete and not shutdown.is_set():
                    _fail("incomplete_verification")
                result.update(status="complete" if complete else "interrupted",
                              elapsed_seconds=time.time() - started)
                connection.execute("INSERT OR REPLACE INTO metadata VALUES ('status',?)", (result["status"],))
                if complete:
                    result.update(integrity="publisher_sha256_and_sizes_for_all_images",
                                  conditional_source_identity="S3_IfMatch_ETag")
                    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('completion',?)", (_canonical(result).decode("ascii"),))
                else:
                    connection.execute("DELETE FROM metadata WHERE name='completion'")
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except BaseException:
                stopped.set()
                if connection is not None:
                    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('status','failed')")
                    connection.commit()
                raise
            finally:
                if connection is not None:
                    connection.close()
                    connection = None
            receipt_digest = hashlib.sha256()
            with _open_regular(receipt) as handle:
                while chunk := handle.read(_CHUNK):
                    receipt_digest.update(chunk)
            result["receipt_sha256"] = receipt_digest.hexdigest()
            if progress is not None:
                progress(dict(result))
            return result
    except BulkSyncError:
        raise
    except Exception:
        _fail("bulk_transfer_failed")
