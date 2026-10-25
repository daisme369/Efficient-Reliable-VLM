"""Credential boundaries using generated tokens and no external requests."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/gpu"))
import rental_access


class RentalAccessTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "credentials.env"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_aliases_quotes_comments_bom_and_no_shell_expansion(self):
        self.path.write_text('\ufeffexport hf_token="generated-$literal" # note\n'
                             "vastai_api_key='generated-vast'\nphysio_pass=unrelated\n", encoding="utf-8")
        self.assertEqual(rental_access.read_credentials(self.path),
                         {"HF_TOKEN": "generated-$literal", "VAST_API_KEY": "generated-vast"})
        self.assertNotIn("VAST_API_KEY", os.environ)

    def test_duplicate_alias_rejected_without_echoing_values(self):
        self.path.write_text("HF_TOKEN=generated-one\nhf_token=generated-two\n")
        with self.assertRaisesRegex(ValueError, "duplicate") as caught:
            rental_access.read_credentials(self.path)
        self.assertNotIn("generated", str(caught.exception))

    def test_status_only_prints_presence(self):
        self.path.write_text("VAST_API_KEY=generated-secret\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = rental_access.main(["--env-file", str(self.path), "credentials"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"HF_TOKEN": False, "VAST_API_KEY": True})

    def test_provider_failure_cannot_echo_secret_or_response(self):
        self.path.write_text("VAST_API_KEY=generated-secret\n")
        error, output = io.StringIO(), io.StringIO()
        def fail(*args):
            print("generated-secret from provider")
            print("generated-account from provider", file=sys.stderr)
            raise RuntimeError("generated-secret")
        with patch.object(rental_access, "vast_status", side_effect=fail), \
             contextlib.redirect_stderr(error), contextlib.redirect_stdout(output):
            code = rental_access.main(["--env-file", str(self.path), "vast-status"])
        self.assertEqual(code, 2)
        self.assertNotIn("generated", output.getvalue() + error.getvalue())

    def test_model_cache_inside_checkout_rejected_before_network(self):
        config = self.path.parent / "config.json"
        config.write_text(json.dumps({"medgemma": {"model_id": rental_access.MODEL, "revision": "a" * 40}}))
        with self.assertRaisesRegex(ValueError, "outside"):
            rental_access.cache_model(config, ROOT / ".test-tmp/model-cache", "generated-secret")


if __name__ == "__main__":
    unittest.main()
