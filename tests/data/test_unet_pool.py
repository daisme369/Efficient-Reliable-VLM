"""Pooling checks use generated pixels and synthetic patient identities only."""
import contextlib
from dataclasses import replace
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.data import test_unet_catalog as fixtures
from tests.data.test_build_manifests import csv_file
from cxr_research.data.catalog import build
from cxr_research.data.segmentation_pool import nih_split, prepare_pool
from cxr_research.data.manifest import load_manifest
from cxr_research.experiments.config import DataConfig, UNetConfig
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


class PooledUNetTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        fixtures.UNetCatalogTests.setUp(self)
        for index, path in enumerate(sorted(self.root.glob("mimic-cxr-jpg/**/*.jpg"))):
            Image.new("L", (2, 2), 200 + index).save(path)
        base = self.root / "ChestX-ray14"
        (base / "images").mkdir(parents=True)
        metadata, masks, development, test = [], [], [], []
        for patient in range(40):
            for followup in range(2):
                key = f"synthetic-{patient}-{followup}.png"
                Image.new("L", (2, 2), 2 * patient + followup).save(base / "images" / key)
                metadata.append({"Image Index": key, "Patient ID": str(patient), "View Position": "PA"})
                masks.append({"Image Index": key, "Height": 2, "Width": 2,
                    "Dice RCA (Mean)": .8, "Left Lung": "1 1", "Right Lung": "4 1", "Heart": "2 1"})
                (test if patient == 39 else development).append(key)
                if patient == 39:
                    masks[-1]["Left Lung"] = "SEALED_MASK_DO_NOT_DECODE"
        csv_file(base / "Data_Entry_2017.csv", list(metadata[0]), metadata)
        (base / "train_val_list.txt").write_text("\n".join(development))
        (base / "test_list.txt").write_text("\n".join(test))
        csv_file(self.root / "chexmask-cxr-segmentation-data/1.0.0/OriginalResolution/ChestX-Ray8.csv",
                 list(masks[0]), masks)
        self.snapshot = self.root / "pool-catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, self.snapshot, "mimic-official", repository=self.repository)
        self.spec.update(catalog_dir=str(self.snapshot), catalog_report_sha256=file_hash(self.snapshot / "report.json"),
            datasets=["mimic-cxr", "nih-chestxray14"], purpose="training", image_limit_per_split=None,
            split_policy="mimic-official+nih-patient-hash-v1", nih_validation_fraction=.1, nih_split_seed=42)
        self.spec["sources"]["nih_images"] = {"name": "NIH ChestX-ray14", "version": "local-release",
            "access": "synthetic", "license": "synthetic"}
        self.spec_path.write_text(json.dumps(self.spec))

    def convert(self):
        self.spec_path.write_text(json.dumps(self.spec))
        with patch("cxr_research.data.segmentation_pool.subprocess.run", return_value=self.git):
            return prepare_pool(self.spec_path, self.repository)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "CPU torch needed for mask pixel checks")
    def test_full_population_patient_groups_and_lazy_lung_pixels(self):
        from cxr_research.data.datasets import SegmentationDataset
        result = self.convert()
        self.assertEqual(result["rows"], 80)
        self.assertFalse(result["test_masks_decoded"])
        output = Path(self.spec["prepared_dir"])
        manifest = load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))
        groups = {}
        for sample in manifest.samples:
            group = (sample.patient_namespace, sample.patient_id)
            self.assertEqual(groups.setdefault(group, sample.split), sample.split)
        self.assertEqual({s.patient_namespace for s in manifest.samples}, {"mimic-cxr", "nih-chestxray14"})
        self.assertNotIn(("nih-chestxray14", "39"), groups)
        dataset = SegmentationDataset(manifest, manifest.samples, UNetConfig(size=16), training=False, seed=42)
        for index in (0, len(dataset) - 1):
            _, mask, valid = dataset[index]
            self.assertEqual(mask.sum().item(), 128)
            self.assertEqual(valid.sum().item(), 256)
            self.assertEqual(mask[0, 0, 0].item(), 1)
            self.assertEqual(mask[0, 0, -1].item(), 0)
            self.assertEqual(mask[0, -1, -1].item(), 1)
        self.assertFalse((output / "masks").exists())

    def test_unknown_population_and_invalid_fraction_stop_before_writes(self):
        self.spec["datasets"].append("vindr-cxr")
        with self.assertRaises(ContractError):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())
        self.spec["datasets"].pop()
        self.spec["nih_validation_fraction"] = True
        with self.assertRaises(ContractError):
            self.convert()

    def test_patient_hash_is_fixed_and_distinguishes_groups(self):
        self.assertEqual(nih_split("0", 42, .1), "train")
        values = [nih_split(str(n), 42, .1) for n in range(40)]
        self.assertEqual(values, [nih_split(str(n), 42, .1) for n in range(40)])
        self.assertEqual(set(values), {"train", "validation"})

    def test_mask_source_corruption_is_rejected_before_dataset_loading(self):
        self.convert()
        output = Path(self.spec["prepared_dir"])
        source = self.root / "chexmask-cxr-segmentation-data/1.0.0/OriginalResolution/ChestX-Ray8.csv"
        with source.open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ContractError, "checksum"):
            load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))

    def test_nih_test_patient_overlap_stops_before_quality_filtering(self):
        def change(rows):
            for row in rows:
                if row["dataset"] == "nih-chestxray14" and row["source_split"] == "test":
                    row.update(patient_id="0", view="LATERAL")
        fixtures.UNetCatalogTests.rewrite_images(self, change)
        with self.assertRaisesRegex(ContractError, "patient split overlap"):
            self.convert()
        self.assertFalse(Path(self.spec["prepared_dir"]).exists())

    @unittest.skipUnless(importlib.util.find_spec("torch"), "CPU torch needed for dataset checks")
    def test_rle_loader_rejects_a_reference_to_another_image(self):
        from cxr_research.data.datasets import SegmentationDataset
        self.convert()
        output = Path(self.spec["prepared_dir"])
        manifest = load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))
        sample = replace(manifest.samples[0], image_id="different-synthetic-image")
        dataset = SegmentationDataset(manifest, [sample], UNetConfig(size=16), training=False, seed=42)
        with self.assertRaisesRegex(ContractError, "identity or dimensions"):
            dataset[0]

    @unittest.skipUnless(importlib.util.find_spec("torch"), "CPU torch needed for dataset checks")
    def test_opaque_nih_rgba_preserves_pixels_and_lung_geometry(self):
        from PIL import Image
        from cxr_research.data.datasets import SegmentationDataset
        path = self.root / "ChestX-ray14/images/synthetic-0-0.png"
        Image.new("RGBA", (2, 2), (150, 150, 150, 255)).save(path)
        snapshot = self.root / "rgba-catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, snapshot, "mimic-official", repository=self.repository)
        self.spec.update(catalog_dir=str(snapshot), catalog_report_sha256=file_hash(snapshot / "report.json"))
        self.convert()
        output = Path(self.spec["prepared_dir"])
        manifest = load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))
        sample = next(s for s in manifest.samples if s.image_id == "synthetic-0-0.png")
        image, mask, _ = SegmentationDataset(manifest, [sample], UNetConfig(size=16), training=False, seed=42)[0]
        self.assertAlmostEqual(image.mean().item(), 150 / 255, places=6)
        self.assertEqual(mask.sum().item(), 128)

    def test_deferred_publisher_hash_still_fails_the_training_data_gate(self):
        def change(rows):
            next(row for row in rows if row["dataset"] == "mimic-cxr")["expected_sha256"] = "0" * 64
        fixtures.UNetCatalogTests.rewrite_images(self, change)
        self.spec["publisher_hash_policy"] = "verify_at_train_boundary"
        self.convert()
        output = Path(self.spec["prepared_dir"])
        with self.assertRaisesRegex(ContractError, "checksum"):
            load_manifest(DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json")))


class SourceImageModeTests(unittest.TestCase):
    def test_nonopaque_alpha_and_unsupported_bit_depth_are_rejected(self):
        from PIL import Image
        from cxr_research.preprocessing.source_images import check_source_mode
        with self.assertRaisesRegex(ContractError, "non-opaque"):
            check_source_mode(Image.new("RGBA", (2, 2), (50, 50, 50, 128)))
        with self.assertRaisesRegex(ContractError, "bit-depth"):
            check_source_mode(Image.new("I;16", (2, 2)))


if __name__ == "__main__":
    unittest.main()
