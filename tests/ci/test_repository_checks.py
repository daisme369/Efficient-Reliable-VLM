from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "ci"))
TEST_TEMP_ROOT = REPOSITORY_ROOT / ".test-tmp"

from check_repository import inspect_files  # noqa: E402


class RepositoryCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: bytes) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def rules_for(self, paths: list[str]) -> set[str]:
        return {item.rule for item in inspect_files(self.root, paths)}

    def test_accepts_source_and_synthetic_fixture(self) -> None:
        self.write("src/model.py", b"def answer(question: str) -> str:\n    return question\n")
        self.write("tests/fixtures/synthetic/example.json", b'{"synthetic": true}\n')

        violations = inspect_files(
            self.root,
            ["src/model.py", "tests/fixtures/synthetic/example.json"],
        )

        self.assertEqual([], violations)

    def test_rejects_medical_data_and_model_weights(self) -> None:
        self.write("data/mimic/patient.dcm", b"synthetic placeholder")
        self.write("checkpoints/model.safetensors", b"synthetic placeholder")

        rules = self.rules_for(["data/mimic/patient.dcm", "checkpoints/model.safetensors"])

        self.assertIn("restricted-path", rules)
        self.assertIn("restricted-format", rules)

    def test_rejects_non_synthetic_fixture_tree(self) -> None:
        path = "tests/fixtures/mimic-cxr/reports/s12345678.txt"
        self.write(path, b"synthetic placeholder")

        self.assertIn("non-synthetic-fixture", self.rules_for([path]))

    def test_rejects_credential_without_echoing_it(self) -> None:
        token = ("gh" + "p_" + "A" * 30).encode("ascii")
        self.write("config.txt", b"token=" + token)

        violations = inspect_files(self.root, ["config.txt"])

        self.assertEqual("github-token", violations[0].rule)
        self.assertNotIn(token.decode("ascii"), violations[0].message)

    def test_rejects_notebook_outputs(self) -> None:
        notebook = {
            "cells": [{"cell_type": "code", "execution_count": 1, "outputs": [{"output_type": "stream"}]}],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        self.write("analysis.ipynb", json.dumps(notebook).encode("utf-8"))

        self.assertIn("notebook-output", self.rules_for(["analysis.ipynb"]))

    def test_rejects_unpinned_or_remote_requirements(self) -> None:
        self.write(
            "requirements.txt",
            b"torch>=2.0\ntransformers\ngit+https://example.invalid/package.git\n",
        )

        rules = self.rules_for(["requirements.txt"])

        self.assertIn("requirements-pin", rules)
        self.assertIn("requirements-source", rules)

    def test_accepts_exact_requirement_pins(self) -> None:
        self.write("requirements.txt", b"torch==2.14.1\ntransformers==5.19.0\n")

        self.assertEqual([], inspect_files(self.root, ["requirements.txt"]))

    def test_requires_digest_pinned_container_images(self) -> None:
        self.write("docker/Dockerfile.gpu", b"FROM python:3.12-slim\nCOPY . /workspace\n")

        rules = self.rules_for(["docker/Dockerfile.gpu"])

        self.assertIn("dockerfile-base", rules)
        self.assertIn("dockerfile-context", rules)

    def test_accepts_minimal_allowlisted_docker_context(self) -> None:
        digest = b"a" * 64
        self.write("docker/Dockerfile.gpu", b"FROM python@sha256:" + digest + b"\nCOPY requirements.txt /tmp/\n")
        self.write(".dockerignore", b"**\n!requirements.txt\n")

        self.assertEqual(
            [],
            inspect_files(self.root, ["docker/Dockerfile.gpu", ".dockerignore"]),
        )

    def test_rejects_permissive_dockerignore(self) -> None:
        self.write(".dockerignore", b"data/\n.env\n")

        self.assertIn("dockerignore-default", self.rules_for([".dockerignore"]))

    def test_requires_full_action_commit_sha(self) -> None:
        self.write(
            ".github/workflows/example.yml",
            b"name: Example\non: push\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-24.04\n"
            b"    steps:\n      - uses: actions/checkout@v7\n",
        )

        self.assertIn("unpinned-action", self.rules_for([".github/workflows/example.yml"]))

    def test_rejects_flow_style_privileged_trigger_and_write_permission(self) -> None:
        sha = b"a" * 40
        self.write(
            ".github/workflows/example.yml",
            b"name: Example\non: [pull_request_target]\npermissions: {contents: write}\njobs:\n  test:\n"
            b"    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@" + sha + b"\n",
        )

        rules = self.rules_for([".github/workflows/example.yml"])

        self.assertIn("privileged-trigger", rules)
        self.assertIn("workflow-permissions", rules)
        self.assertIn("workflow-write", rules)

    def test_accepts_pinned_action_and_least_privilege_permissions(self) -> None:
        sha = b"a" * 40
        self.write(
            ".github/workflows/example.yml",
            b"name: Example\non: push\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-24.04\n"
            b"    steps:\n      - uses: actions/checkout@" + sha + b"\n",
        )

        self.assertEqual([], inspect_files(self.root, [".github/workflows/example.yml"]))


if __name__ == "__main__":
    unittest.main()
