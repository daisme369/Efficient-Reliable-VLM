"""Selection rules for the NIH demo subset, on made-up label rows (no real data)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("select_nih_demo", ROOT / "scripts" / "demo_data" / "select_nih_demo.py")
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


def row(index, label, patient, age="045Y", view="PA"):
    return {"Image Index": index, "Finding Labels": label, "Patient ID": patient, "Patient Age": age,
            "Patient Gender": "F", "View Position": view}


class DemoSelectionTests(unittest.TestCase):
    def test_rules(self):
        rows = [
            row("a.png", "Effusion", "1"), row("b.png", "Effusion", "1"),           # same patient: one kept
            row("c.png", "Effusion", "2", view="AP"),                                # AP view: skipped
            row("d.png", "Effusion", "3", age="012Y"),                               # child: skipped
            row("e.png", "Effusion|Edema", "4"),                                     # multi-label: skipped
            row("f.png", "Pneumothorax", "5", age="70"), row("g.png", "No Finding", "6", age="003M"),
        ]
        chosen = selector.select(rows, per_finding=2)
        by_label = {c["nih_label"]: c["image"] for c in chosen}
        self.assertEqual(len(chosen), 2)
        self.assertIn(by_label["Effusion"], {"a.png", "b.png"})  # one image for patient 1
        self.assertEqual(by_label["Pneumothorax"], "f.png")
        self.assertEqual(selector.select(rows, 2), chosen)  # deterministic
        self.assertEqual((selector.age_years("060Y"), selector.age_years("7"), selector.age_years("003M")), (60, 7, None))


if __name__ == "__main__":
    unittest.main()
