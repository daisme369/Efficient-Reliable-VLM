from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "ci"))
TEST_TEMP_ROOT = REPOSITORY_ROOT / ".test-tmp"

from build_release_bundle import build_bundle, select_release_paths, sha256_file  # noqa: E402


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.root = Path(self.temporary.name)
        self.write("README.md", b"# Research source\n")
        self.write("LICENSE", b"MIT\n")
        self.write("src/example.py", b"VALUE = 1\n")
        self.write(
            ".github/workflows/ci.yml",
            b"name: CI\non: push\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-24.04\n",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: bytes) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_allowlist_excludes_workflows_and_unknown_roots(self) -> None:
        selected = select_release_paths(
            [
                ".dockerignore",
                "README.md",
                "LICENSE",
                "src/example.py",
                "docker/Dockerfile.gpu",
                "requirements/gpu-cu130.txt",
                "requirements/gpu-runtime.txt",
                ".github/workflows/ci.yml",
                "notes.txt",
                "tests/fixtures/mimic-cxr/report.txt",
                "tests/fixtures/synthetic/example.json",
            ]
        )

        self.assertEqual(
            [
                ".dockerignore",
                "LICENSE",
                "README.md",
                "docker/Dockerfile.gpu",
                "requirements/gpu-cu130.txt",
                "requirements/gpu-runtime.txt",
                "src/example.py",
                "tests/fixtures/synthetic/example.json",
            ],
            selected,
        )

    def test_bundle_contains_only_allowlisted_source_and_manifest(self) -> None:
        sources = ["README.md", "LICENSE", "src/example.py", ".github/workflows/ci.yml"]
        archive, manifest_path, checksums_path = build_bundle(
            self.root,
            self.root / "dist",
            "v1.2.3",
            source_paths=sources,
            commit="a" * 40,
        )

        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
        prefix = "Efficient-Reliable-VLM-v1.2.3/"
        self.assertEqual(
            [
                prefix + "LICENSE",
                prefix + "README.md",
                prefix + "src/example.py",
                prefix + "release-manifest.json",
            ],
            names,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("a" * 40, manifest["source_commit"])
        self.assertEqual("explicit-input", manifest["source_state"])
        self.assertEqual("v1.2.3", manifest["version"])
        self.assertIn(archive.name, checksums_path.read_text(encoding="ascii"))

    def test_bundle_is_deterministic(self) -> None:
        sources = ["README.md", "LICENSE", "src/example.py"]
        first, _, _ = build_bundle(
            self.root, self.root / "first", "v1.0.0", source_paths=sources, commit="b" * 40
        )
        second, _, _ = build_bundle(
            self.root, self.root / "second", "v1.0.0", source_paths=sources, commit="b" * 40
        )

        self.assertEqual(sha256_file(first), sha256_file(second))

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "SemVer"):
            build_bundle(
                self.root,
                self.root / "dist",
                "latest",
                source_paths=["README.md"],
                commit="c" * 40,
            )


if __name__ == "__main__":
    unittest.main()
