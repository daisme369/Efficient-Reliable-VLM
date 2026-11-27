"""Demo signed readings for the Similar cases tab."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api.test_reading import HAS_API_DEPS, ReadingBase  # noqa: E402


@unittest.skipUnless(HAS_API_DEPS, "API dependencies are not installed")
class SeedCasesTests(ReadingBase):
    def test_seeds_once_and_search_finds_cases(self):
        from thorax_api.seed_cases import seed
        first = seed(self.db)
        self.assertGreaterEqual(first, 6)
        self.assertEqual(seed(self.db), 0)  # re-running adds nothing
        body = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "pleural effusion"}).json()
        self.assertGreaterEqual(len(body["cases"]), 3)
        # "No pleural effusion" (study 2450) is never an effusion case.
        texts = " ".join(" ".join(c["findings"]) for c in body["cases"])
        self.assertNotIn("No pleural effusion", texts)
        # The current November study stays unread for the demo.
        self.assertEqual(self.doctor.get("/api/v1/studies/2431").json()["status"], "new")


if __name__ == "__main__":
    unittest.main()
