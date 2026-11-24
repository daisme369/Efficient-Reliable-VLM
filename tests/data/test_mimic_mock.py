"""MIMIC-shaped mock database: layout, ID ranges, CSVs and determinism, on synthetic images only."""

from __future__ import annotations

import csv
import importlib.util
import io
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HAS_PIL = importlib.util.find_spec("PIL") is not None
spec = importlib.util.spec_from_file_location("mimic_mock", ROOT / "scripts" / "demo_data" / "mimic_mock.py")
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)


@unittest.skipUnless(HAS_PIL, "requires pillow")
class MimicMockTests(unittest.TestCase):
    def test_acute_and_trauma_indications_are_urgent(self):
        self.assertEqual([mock.priority_for(text) for text in ("Acute chest pain", "TRAUMA, fall", "Follow-up effusion")],
                         ["urgent", "urgent", "routine"])

    def test_layout_ids_and_csvs(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            images, out = Path(directory) / "images", Path(directory) / "out"
            images.mkdir()
            for i in range(3):
                buffer = io.BytesIO()
                Image.new("L", (40 + i, 50), 30 * i).save(buffer, format="PNG")
                (images / f"img{i}.png").write_bytes(buffer.getvalue())
            (images / "selection.csv").write_text("image,nih_label,app_finding,age,sex,view\nimg1.png,Effusion,pleural effusion,61,F,PA\n", encoding="utf-8")
            rows = mock.build(images, out, images / "selection.csv")

            self.assertEqual(len(rows), 3)
            for row in rows:
                self.assertTrue(10_000_000 <= row["subject_id"] <= 19_999_999 and 50_000_000 <= row["study_id"] <= 59_999_999)
                self.assertRegex(row["dicom_id"], r"^([0-9a-f]{8}-){4}[0-9a-f]{8}$")
                self.assertRegex(row["_path"], rf"^files/p{str(row['subject_id'])[:2]}/p{row['subject_id']}/s{row['study_id']}/{row['dicom_id']}\.jpg$")
                self.assertTrue((out / row["_path"]).is_file())
                report = (out / row["_path"]).parent.with_suffix(".txt").read_text(encoding="utf-8")
                self.assertTrue(re.search(r"FINDINGS: .+\n\n IMPRESSION: .+", report))
            with (out / "mimic-cxr-2.0.0-metadata.csv").open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, mock.METADATA_FIELDS)
                self.assertEqual(len(list(reader)), 3)
            with (out / "mimic-cxr-2.0.0-chexpert.csv").open(newline="", encoding="utf-8") as handle:
                effusion = [r for r in csv.DictReader(handle) if r["Pleural Effusion"] == "1.0"]
            self.assertEqual(len(effusion), 1)
            self.assertEqual([r["dicom_id"] for r in mock.build(images, Path(directory) / "again", images / "selection.csv")],
                             [r["dicom_id"] for r in rows])  # same images, same database


if __name__ == "__main__":
    unittest.main()
