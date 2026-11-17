import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cxr_research.experiments.source import source_provenance
from cxr_research.utils.errors import ContractError


class SourceExportTests(unittest.TestCase):
    def test_exact_frozen_source_preserves_parent_commit_and_rejects_modified_code(self):
        scratch = Path(__file__).resolve().parents[2] / ".test-tmp" if os.name == "nt" else None
        if scratch:
            scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            code = root / "frozen"
            files = {"AGENTS-INSTRUCTIONS.md": b"synthetic policy", "src/cxr_research/cli.py": b"synthetic code",
                "scripts/ci/check_gpu_environment.py": b"synthetic runtime check"}
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, data in files.items():
                    target = code / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    bundle.writestr(name, data)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            metadata = root / "source.json"
            metadata.write_text(json.dumps({"git_commit": "a" * 40, "git_dirty": True,
                "snapshot_sha256": checksum, "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}))
            protocol = root / "protocol.json"
            protocol.write_text(json.dumps({"status": "approved", "source_snapshot_sha256": checksum}))
            with patch.dict(os.environ, {"CXR_SOURCE_SNAPSHOT": str(metadata), "CXR_SOURCE_ARCHIVE": str(archive)}):
                evidence = source_provenance(SimpleNamespace(protocol=str(protocol)), code)
                self.assertEqual(evidence, {"git_commit": "a" * 40, "git_dirty": True,
                    "source_kind": "verified_source_export", "source_snapshot_sha256": checksum})
                (code / "src/cxr_research/cli.py").write_bytes(b"changed synthetic code")
                with self.assertRaisesRegex(ContractError, "frozen source bytes"):
                    source_provenance(SimpleNamespace(protocol=str(protocol)), code)


if __name__ == "__main__":
    unittest.main()
