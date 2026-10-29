"""Credential boundaries using generated tokens and no external requests."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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

    def test_offer_search_includes_only_approved_training_models_and_capabilities(self):
        offers = [
            {"id": 1, "gpu_name": "H100_SXM", "compute_cap": 900},
            {"id": 2, "gpu_name": "H200", "compute_cap": 900},
            {"id": 3, "gpu_name": "RTX_PRO_6000_Blackwell", "compute_cap": 1200},
            {"id": 4, "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "compute_cap": 1200},
            {"id": 5, "gpu_name": "RTX PRO 6000 Blackwell Workstation Edition", "compute_cap": 1200},
            {"id": 6, "gpu_name": "RTX PRO 6000 Blackwell Max-Q Workstation Edition", "compute_cap": 1200},
            {"id": 7, "gpu_name": "RTX_5090", "compute_cap": 1200},
            {"id": 8, "gpu_name": "RTX_4090", "compute_cap": 890},
            {"id": 9, "gpu_name": "RTX_6000_Ada", "compute_cap": 890},
            {"id": 10, "gpu_name": "RTX_PRO_6000_Blackwell", "compute_cap": 900},
            {"id": 11, "gpu_name": "RTX_PRO_5000_Blackwell", "compute_cap": 1200},
            {"id": 12, "gpu_name": "RTX_PRO_6000", "compute_cap": 1200},
        ]
        client = Mock(spec=["show_user", "search_offers"])
        client.search_offers.side_effect = lambda **kwargs: [row for row in offers
            if row["compute_cap"] == (900 if "compute_cap=900" in kwargs["query"] else 1200)]
        factory = Mock(return_value=client)
        with patch.object(rental_access, "version", return_value="1.8.3"), \
             patch.dict(sys.modules, {"vastai": SimpleNamespace(VastAI=factory)}):
            result = rental_access.vast_status("generated-key", 350)
        self.assertEqual([row["id"] for row in result["offers"]], [1, 2, 3, 4, 5, 6])
        self.assertFalse(result["instance_created"])
        queries = [call.kwargs["query"] for call in client.search_offers.call_args_list]
        self.assertIn("compute_cap=900", queries[0])
        self.assertIn("compute_cap=1200 gpu_name in [", queries[1])
        self.assertIn("RTX_PRO_6000_Blackwell_Server_Edition", queries[1])
        for query in queries:
            for required in ("num_gpus=1", "datacenter=true", "cpu_arch=amd64",
                             "driver_version>=580.0.0", "cuda_vers>=13", "verified=true",
                             "reliability>0.99", "disk_space>=350"):
                self.assertIn(required, query)
        self.assertEqual([call[0] for call in client.method_calls],
                         ["show_user", "search_offers", "search_offers"])

    def test_blackwell_model_filter_prevents_unapproved_offers_consuming_result_limit(self):
        unrelated = [{"id": i, "gpu_name": "RTX_5090", "compute_cap": 1200, "dph_total": 1}
                     for i in range(100)]
        approved = {"id": 100, "gpu_name": "RTX_PRO_6000_Blackwell", "compute_cap": 1200,
                    "dph_total": 2}
        hopper = {"id": 101, "gpu_name": "H100_SXM", "compute_cap": 900, "dph_total": 3}
        def search(*, query, limit, **kwargs):
            if "compute_cap=900" in query:
                return [hopper]
            rows = [approved] if "gpu_name in [" in query else [*unrelated, approved]
            return rows[:limit]
        client = Mock(spec=["show_user", "search_offers"])
        client.search_offers.side_effect = search
        with patch.object(rental_access, "version", return_value="1.8.3"), \
             patch.dict(sys.modules, {"vastai": SimpleNamespace(VastAI=Mock(return_value=client))}):
            result = rental_access.vast_status("generated-key", 350)
        self.assertEqual([row["id"] for row in result["offers"]], [100, 101])

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
