"""Synthetic integrity tests for a full raw-JPEG transfer, independent of QA.

No test needs boto3, network, AWS credentials, restricted rows, or actual images.
The sibling implementation can run here as a proposal or from scripts/gpu after
the main agent integrates and reviews it.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest


_HERE = Path(__file__).resolve()
_IMPLEMENTATIONS = (
    _HERE.with_name("bulk_dataset_sync.py"),
    _HERE.parents[2] / "scripts" / "gpu" / "bulk_dataset_sync.py",
)
_SOURCE = next((p for p in _IMPLEMENTATIONS if p.is_file()), None)
if _SOURCE is None:
    raise ImportError("bulk_dataset_sync proposal or integrated module is missing")
_SPEC = importlib.util.spec_from_file_location("cxr_test_bulk_sync", _SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
bulk = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bulk
_SPEC.loader.exec_module(bulk)


def synthetic_relative(index: int) -> str:
    """Return an obviously test-generated, structurally valid publisher path."""
    subject = 10000000 + index
    study = 50000000 + index
    dicom = "-".join(f"{index + part:08x}" for part in range(5))
    return f"files/p10/p{subject}/s{study}/{dicom}.jpg"


class SyntheticBody(io.BytesIO):
    def __init__(self, value: bytes, *, raises: bool = False):
        super().__init__(value)
        self.raises = raises

    def read(self, size: int = -1) -> bytes:
        if self.raises:
            raise OSError("synthetic backend failure with SOURCE_KEY_DO_NOT_LOG")
        return super().read(size)


class SyntheticPreconditionFailed(RuntimeError):
    pass


class FakeS3:
    """Small boto3-shaped client; checks conditions instead of trusting tests."""

    def __init__(self, prefix: str, payloads: dict[str, bytes], *, page_size: int = 2):
        self.prefix = prefix
        self.page_size = page_size
        self.objects = {
            prefix + relative: {
                "payload": payload,
                "etag": '"' + hashlib.md5(payload).hexdigest() + '"',
            }
            for relative, payload in payloads.items()
        }
        self.listed = [
            {"Key": key, "Size": len(obj["payload"]), "ETag": obj["etag"]}
            for key, obj in sorted(self.objects.items())
        ]
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.bodies: list[SyntheticBody] = []
        self.response_overrides: dict[str, dict] = {}
        self.raise_list = False
        self.raise_get = False
        self.raise_body = False
        self._guard = threading.Lock()

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(copy.deepcopy(kwargs))
        if self.raise_list:
            raise OSError("SOURCE_KEY_DO_NOT_LOG: synthetic listing failure")
        if kwargs.get("Prefix") != self.prefix + "files/":
            raise AssertionError("listing must use the exact images subtree")
        start = int(kwargs.get("ContinuationToken", "0"))
        end = start + self.page_size
        response = {
            "Contents": copy.deepcopy(self.listed[start:end]),
            "IsTruncated": end < len(self.listed),
        }
        if response["IsTruncated"]:
            response["NextContinuationToken"] = str(end)
        return response

    def get_object(self, **kwargs):
        with self._guard:
            self.get_calls.append(copy.deepcopy(kwargs))
        if self.raise_get:
            raise OSError("SOURCE_KEY_DO_NOT_LOG: synthetic GET failure")
        key = kwargs["Key"]
        obj = self.objects[key]
        if kwargs.get("IfMatch") != obj["etag"]:
            raise SyntheticPreconditionFailed("SOURCE_KEY_DO_NOT_LOG: changed source")
        body = SyntheticBody(obj["payload"], raises=self.raise_body)
        with self._guard:
            self.bodies.append(body)
        response = {
            "Body": body,
            "ContentLength": len(obj["payload"]),
            "ETag": obj["etag"],
        }
        response.update(self.response_overrides.get(key, {}))
        return response


def rebound_plan(plan: dict) -> dict:
    """Make malicious but self-consistently hashed fixtures, not a safe plan."""
    copied = copy.deepcopy(plan)
    copied.pop("plan_sha256", None)
    digest = hashlib.sha256(
        json.dumps(copied, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    copied["plan_sha256"] = digest
    return copied


class BulkSyncTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory(prefix="cxr-synthetic-bulk-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / "private-data"
        self.data.mkdir(mode=0o700)
        self.receipt = self.data / ".bulk-receipts.sqlite3"
        self.publisher = self.base / "synthetic-SHA256SUMS.txt"
        self.prefix = "synthetic-dataset/"
        self.payloads = {
            synthetic_relative(index): f"synthetic jpeg bytes {index}".encode()
            for index in range(1, 4)
        }
        self.write_publisher()
        self.client = FakeS3(self.prefix, self.payloads)
        self.total = sum(map(len, self.payloads.values()))
        self.progress: list[dict] = []

    def write_publisher(self, lines: list[str] | None = None):
        if lines is None:
            lines = [
                hashlib.sha256(payload).hexdigest() + "  " + relative
                for relative, payload in sorted(self.payloads.items())
            ]
        self.publisher.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def make_plan(self, **overrides):
        options = dict(byte_budget=10000, free_bytes=20000, reserve_bytes=1000)
        options.update(overrides)
        return bulk.make_plan(
            self.client, "synthetic-bucket", self.prefix, self.publisher, **options
        )

    def fetch(self, plan=None, **overrides):
        if plan is None:
            plan = self.make_plan()
        options = dict(
            expected_plan_sha256=plan["plan_sha256"],
            byte_budget=10000,
            reserve_bytes=1000,
            workers=2,
            free_bytes=lambda: 20000,
            progress=self.progress.append,
        )
        options.update(overrides)
        return bulk.fetch_plan(self.client, plan, self.data, self.receipt, **options)

    def one_object(self):
        relative, payload = next(iter(self.payloads.items()))
        self.payloads = {relative: payload}
        self.write_publisher()
        self.client = FakeS3(self.prefix, self.payloads)
        return relative, payload

    def assert_rejected(self, func, *args, **kwargs):
        with self.assertRaises(bulk.BulkSyncError) as captured:
            func(*args, **kwargs)
        self.assertTrue(getattr(captured.exception, "code", None))
        rendered = str(captured.exception)
        self.assertNotIn("SOURCE_KEY_DO_NOT_LOG", rendered)
        self.assertNotIn(self.prefix, rendered)
        self.assertNotIn("synthetic-bucket", rendered)
        for relative in self.payloads:
            self.assertNotIn(relative, rendered)
            self.assertNotIn(Path(relative).stem, rendered)
        return captured.exception

    def test_plan_includes_every_published_image_without_qa_input_and_pages(self):
        plan = self.make_plan(expected_images=3)
        self.assertEqual(plan["object_count"], 3)
        self.assertEqual(plan["total_bytes"], self.total)
        self.assertEqual(
            {entry["relative_path"] for entry in plan["entries"]}, set(self.payloads)
        )
        self.assertEqual(len(self.client.list_calls), 2)
        self.assertNotIn("ContinuationToken", self.client.list_calls[0])
        self.assertEqual(self.client.list_calls[1]["ContinuationToken"], "2")
        self.assertEqual(self.client.get_calls, [])

    def test_plan_deterministic_and_bound_to_publisher_bytes(self):
        first = self.make_plan()
        self.client.listed.reverse()
        second = self.make_plan()
        self.assertEqual(first, second)
        self.assertEqual(
            first["publisher_sha256"], hashlib.sha256(self.publisher.read_bytes()).hexdigest()
        )

    def test_safe_nonimage_publisher_entries_do_not_become_image_requests(self):
        content = self.publisher.read_text()
        self.publisher.write_text(content + "a" * 64 + "  LICENSE.txt\n")
        self.assertEqual(len(bulk.parse_publisher(self.publisher)), 3)
        self.assertEqual(self.make_plan()["object_count"], 3)

    def test_missing_published_image_fails_before_any_get(self):
        self.client.listed.pop()
        self.assert_rejected(self.make_plan)
        self.assertEqual(self.client.get_calls, [])

    def test_unknown_image_fails_before_any_get(self):
        extra = synthetic_relative(90)
        self.client.listed.append({"Key": self.prefix + extra, "Size": 3, "ETag": '"x"'})
        self.assert_rejected(self.make_plan)
        self.assertEqual(self.client.get_calls, [])

    def test_duplicate_listing_rejected_even_across_pages(self):
        self.client.listed.append(copy.deepcopy(self.client.listed[0]))
        self.assert_rejected(self.make_plan)

    def test_duplicate_publisher_path_is_rejected(self):
        lines = self.publisher.read_text().splitlines()
        self.write_publisher(lines + [lines[0]])
        self.assert_rejected(bulk.parse_publisher, self.publisher)

    def test_invalid_publisher_jpeg_paths_and_hashes_are_rejected(self):
        digest = "b" * 64
        paths = [
            "../" + synthetic_relative(1),
            "/" + synthetic_relative(1),
            synthetic_relative(1).replace("/", "\\"),
            synthetic_relative(1).replace("p10/", "p11/"),
            synthetic_relative(1).replace("files/", "files/../files/"),
            synthetic_relative(1).replace(".jpg", ".jpg/other.jpg"),
        ]
        for bad in paths:
            with self.subTest(kind=paths.index(bad)):
                self.write_publisher([digest + "  " + bad])
                self.assert_rejected(bulk.parse_publisher, self.publisher)
        self.write_publisher(["not-a-hash  " + synthetic_relative(1)])
        self.assert_rejected(bulk.parse_publisher, self.publisher)

    def test_invalid_prefix_does_not_reach_s3(self):
        for prefix in ("../data/", "/absolute/", "data\\nested/", "data//nested/"):
            with self.subTest(prefix=prefix):
                self.assert_rejected(
                    bulk.make_plan,
                    self.client,
                    "synthetic-bucket",
                    prefix,
                    self.publisher,
                    byte_budget=10000,
                    free_bytes=20000,
                    reserve_bytes=1000,
                )
        self.assertEqual(self.client.list_calls, [])

    def test_count_budget_and_disk_reserve_fail_closed(self):
        for options in (
            {"expected_images": 4},
            {"byte_budget": self.total - 1},
            {"free_bytes": self.total + 999},
        ):
            with self.subTest(options=options):
                self.assert_rejected(self.make_plan, **options)
        self.assertEqual(self.client.get_calls, [])

    def test_bad_listing_size_or_etag_fails_before_get(self):
        original = copy.deepcopy(self.client.listed)
        for field, value in (("Size", -1), ("Size", True), ("ETag", "")):
            self.client.listed = copy.deepcopy(original)
            self.client.listed[0][field] = value
            with self.subTest(field=field, value=value):
                self.assert_rejected(self.make_plan)
        self.assertEqual(self.client.get_calls, [])

    def test_listing_exception_is_sanitized(self):
        self.client.raise_list = True
        self.assert_rejected(self.make_plan)

    def test_truncated_listing_without_next_token_is_rejected(self):
        self.client.list_objects_v2 = lambda **kwargs: {
            "Contents": copy.deepcopy(self.client.listed),
            "IsTruncated": True,
        }
        self.assert_rejected(self.make_plan)

    def test_listing_cannot_smuggle_key_from_outside_prefix(self):
        self.client.listed[0]["Key"] = "different-source/" + synthetic_relative(1)
        self.assert_rejected(self.make_plan)
        self.assertEqual(self.client.get_calls, [])

    def test_download_verifies_all_bytes_uses_ifmatch_and_closes_bodies(self):
        plan = self.make_plan()
        result = self.fetch(plan)
        self.assertEqual(result["status"], "complete")
        for relative, expected in self.payloads.items():
            self.assertEqual((self.data / relative).read_bytes(), expected)
        self.assertEqual(len(self.client.get_calls), len(self.payloads))
        expected_etags = {entry["key"]: entry["etag"] for entry in plan["entries"]}
        for call in self.client.get_calls:
            self.assertEqual(set(call), {"Bucket", "Key", "IfMatch"})
            self.assertEqual(call["IfMatch"], expected_etags[call["Key"]])
        self.assertTrue(all(body.closed for body in self.client.bodies))
        self.assertTrue(self.receipt.is_file())
        self.assertEqual(self.receipt.read_bytes()[:16], b"SQLite format 3\x00")
        connection = sqlite3.connect(self.receipt)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_certificate_and_progress_are_aggregate_only(self):
        result = self.fetch()
        self.assertTrue(self.progress)
        for output in [result, *self.progress]:
            serialized = json.dumps(output, sort_keys=True)
            self.assertNotIn(self.prefix, serialized)
            self.assertNotIn("synthetic-bucket", serialized)
            for relative in self.payloads:
                self.assertNotIn(relative, serialized)
                self.assertNotIn(Path(relative).stem, serialized)
            self.assertNotIn("entries", output)
            self.assertNotIn("key", output)

    def test_successful_resume_rehashes_existing_files_without_get(self):
        plan = self.make_plan()
        self.fetch(plan)
        self.client.get_calls.clear()
        second = self.fetch(plan)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(self.client.get_calls, [])

    def test_tampered_file_is_not_overwritten_or_trusted_on_resume(self):
        relative, original = self.one_object()
        plan = self.make_plan()
        self.fetch(plan)
        target = self.data / relative
        changed = b"X" * len(original)
        target.write_bytes(changed)
        self.client.get_calls.clear()
        self.assert_rejected(self.fetch, plan)
        self.assertEqual(target.read_bytes(), changed)
        self.assertEqual(self.client.get_calls, [])

    def test_unreceipted_valid_existing_file_can_be_verified_and_reused(self):
        relative, expected = self.one_object()
        target = self.data / relative
        target.parent.mkdir(parents=True, mode=0o700)
        target.write_bytes(expected)
        self.assertEqual(self.fetch()["status"], "complete")
        self.assertEqual(self.client.get_calls, [])

    def test_unreceipted_invalid_existing_file_is_never_overwritten(self):
        relative, _ = self.one_object()
        target = self.data / relative
        target.parent.mkdir(parents=True, mode=0o700)
        target.write_bytes(b"keep existing evidence")
        self.assert_rejected(self.fetch)
        self.assertEqual(target.read_bytes(), b"keep existing evidence")
        self.assertEqual(self.client.get_calls, [])

    def test_source_etag_change_does_not_publish_a_file(self):
        relative, _ = self.one_object()
        plan = self.make_plan()
        self.client.objects[self.prefix + relative]["etag"] = '"new-source"'
        self.assert_rejected(self.fetch, plan)
        self.assertFalse((self.data / relative).exists())

    def test_response_identity_and_length_are_checked(self):
        relative, payload = self.one_object()
        plan = self.make_plan()
        for override in ({"ETag": '"wrong"'}, {"ContentLength": len(payload) + 1}):
            with self.subTest(override=override):
                self.client.response_overrides = {self.prefix + relative: override}
                self.assert_rejected(self.fetch, plan)
                self.assertFalse((self.data / relative).exists())
                self.assertTrue(all(body.closed for body in self.client.bodies))

    def test_short_extra_or_wrong_payload_is_not_published(self):
        relative, payload = self.one_object()
        plan = self.make_plan()
        for changed in (payload[:-1], payload + b"extra", b"Z" * len(payload)):
            with self.subTest(length=len(changed)):
                self.client.objects[self.prefix + relative]["payload"] = changed
                self.client.response_overrides = {
                    self.prefix + relative: {"ContentLength": len(payload)}
                }
                self.assert_rejected(self.fetch, plan)
                self.assertFalse((self.data / relative).exists())
                self.assertTrue(all(body.closed for body in self.client.bodies))

    def test_stream_failure_is_sanitized_and_body_closed(self):
        relative, _ = self.one_object()
        self.client.raise_body = True
        self.assert_rejected(self.fetch)
        self.assertFalse((self.data / relative).exists())
        self.assertTrue(all(body.closed for body in self.client.bodies))

    def test_get_failure_is_sanitized_and_never_certified(self):
        relative, _ = self.one_object()
        self.client.raise_get = True
        self.assert_rejected(self.fetch)
        self.assertFalse((self.data / relative).exists())

    def test_wrong_expected_plan_hash_fails_before_any_get(self):
        plan = self.make_plan()
        self.assert_rejected(self.fetch, plan, expected_plan_sha256="0" * 64)
        self.assertEqual(self.client.get_calls, [])

    def test_malicious_but_rehashed_plan_paths_still_fail_before_get(self):
        plan = self.make_plan()
        for bad in ("../escape.jpg", "/absolute.jpg", "files\\escape.jpg"):
            with self.subTest(path=bad):
                malicious = copy.deepcopy(plan)
                malicious["entries"][0]["relative_path"] = bad
                malicious = rebound_plan(malicious)
                self.assert_rejected(self.fetch, malicious)
        self.assertEqual(self.client.get_calls, [])

    def test_receipt_cannot_be_rebound_to_different_plan(self):
        relative, payload = self.one_object()
        first = self.make_plan()
        self.fetch(first)
        self.payloads[relative] = payload + b"new publisher version"
        self.write_publisher()
        self.client = FakeS3(self.prefix, self.payloads)
        second = self.make_plan()
        self.assertNotEqual(first["plan_sha256"], second["plan_sha256"])
        self.assert_rejected(self.fetch, second)
        self.assertEqual(self.client.get_calls, [])
        self.assertEqual((self.data / relative).read_bytes(), payload)

    def test_receipt_sidecar_symlink_is_rejected_before_sqlite_or_get(self):
        outside = self.base / "outside-sidecar"
        outside.write_bytes(b"synthetic evidence must remain unchanged")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.receipt) + suffix)
            try:
                sidecar.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("creating symlinks is not available for this user")
            try:
                self.assert_rejected(self.fetch)
                self.assertEqual(outside.read_bytes(), b"synthetic evidence must remain unchanged")
                self.assertTrue(sidecar.is_symlink())
                self.assertEqual(self.client.get_calls, [])
            finally:
                sidecar.unlink()

    def test_partial_success_survives_failure_and_resumes_only_missing_files(self):
        plan = self.make_plan()
        original_get = self.client.get_object
        failed_key = plan['entries'][1]['key']

        def interrupted_get(**kwargs):
            if kwargs['Key'] == failed_key:
                raise OSError('synthetic interrupted transfer')
            return original_get(**kwargs)

        self.client.get_object = interrupted_get
        self.assert_rejected(self.fetch, plan, workers=1)
        preserved = {
            relative for relative in self.payloads if (self.data / relative).is_file()
        }
        self.assertTrue(preserved)
        self.assertLess(len(preserved), len(self.payloads))
        for relative in preserved:
            self.assertEqual((self.data / relative).read_bytes(), self.payloads[relative])
        self.client.get_object = original_get
        self.client.get_calls.clear()
        result = self.fetch(plan, workers=1)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['reused_objects'], len(preserved))
        requested = {call['Key'] for call in self.client.get_calls}
        self.assertEqual(requested, {
            self.prefix + relative for relative in self.payloads if relative not in preserved
        })

    def test_existing_lock_is_not_cleared_even_if_old(self):
        lock = self.data / ".bulk-dataset-sync.lock"
        lock.write_text("stale synthetic operator evidence")
        os.utime(lock, (1, 1))
        self.assert_rejected(self.fetch)
        self.assertEqual(lock.read_text(), "stale synthetic operator evidence")
        self.assertEqual(self.client.get_calls, [])

    def test_worker_and_runtime_budget_limits_reject_before_get(self):
        plan = self.make_plan()
        for options in (
            {"workers": 0},
            {"workers": 65},
            {"workers": True},
            {"byte_budget": self.total - 1},
            {"free_bytes": lambda: 0},
        ):
            with self.subTest(option=next(iter(options))):
                self.assert_rejected(self.fetch, plan, **options)
        self.assertEqual(self.client.get_calls, [])

    def test_target_symlink_cannot_redirect_output(self):
        relative, payload = self.one_object()
        target = self.data / relative
        outside = self.base / "outside"
        outside.mkdir()
        outside_file = outside / "keep.bin"
        outside_file.write_bytes(payload)
        target.parent.mkdir(parents=True, mode=0o700)
        try:
            target.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not available for this user")
        self.assert_rejected(self.fetch)
        self.assertEqual(outside_file.read_bytes(), payload)
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.client.get_calls, [])

    def test_parent_symlink_cannot_redirect_output(self):
        self.one_object()
        outside = self.base / "outside-parent"
        outside.mkdir()
        parent_link = self.data / "files"
        try:
            parent_link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not available for this user")
        self.assert_rejected(self.fetch)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(parent_link.is_symlink())
        self.assertEqual(self.client.get_calls, [])

    def test_file_created_during_get_is_not_overwritten_when_publishing(self):
        relative, _ = self.one_object()
        target = self.data / relative
        original_get = self.client.get_object

        def racing_get(**kwargs):
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(b"synthetic concurrent writer evidence")
            return original_get(**kwargs)

        self.client.get_object = racing_get
        self.assert_rejected(self.fetch)
        self.assertEqual(target.read_bytes(), b"synthetic concurrent writer evidence")
        self.assertTrue(all(body.closed for body in self.client.bodies))

    @unittest.skipUnless(os.name == "posix", "Unix privacy mode contract")
    def test_created_files_and_directories_are_private(self):
        self.fetch()
        for path in self.data.rglob("*"):
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o077, 0)
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
