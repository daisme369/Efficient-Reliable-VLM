"""Exercise the complete cached-pair/index/loader path with synthetic pixels."""
import contextlib
import csv
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.data import test_unet_pool as fixtures
from cxr_research.data.segmentation_cache import cache_pair, cache_unet
from cxr_research.data.manifest import DataConfig, load_manifest
from cxr_research.experiments.config import config_from_dict, IOConfig
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


@unittest.skipUnless(importlib.util.find_spec("torch"), "CPU torch needed for tensor equivalence checks")
class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        torch.set_num_threads(1)

    def setUp(self):
        fixtures.PooledUNetTests.setUp(self)
        fixtures.PooledUNetTests.convert(self)
        self.prepared = Path(self.spec["prepared_dir"])
        provenance = json.loads((self.prepared / "provenance.json").read_text())
        provenance["synthetic"] = True
        (self.prepared / "provenance.json").write_text(json.dumps(provenance))
        self.config = config_from_dict({"run_id": "cached-synthetic", "task": "unet", "baseline": "S1",
            "data": {"root": str(self.root), "manifest": str(self.prepared / "manifest.jsonl"),
                     "provenance": str(self.prepared / "provenance.json"), "synthetic": True},
            "output_root": str(self.root / "runs"), "train": {"epochs": 1, "batch_size": 2},
            "unet": {"size": 32, "base_channels": 8}})
        self.config_path = self.root / "source.json"
        self.config_path.write_text(json.dumps(self.config.as_dict()))

    def convert(self, name="cache", **options):
        output = self.root / name
        with patch("cxr_research.data.segmentation_cache.subprocess.check_output", return_value="a" * 40):
            result = cache_unet(self.config_path, output, self.repository, workers=2, shard_bytes=4096, **options)
        cached = replace(self.config,
            data=DataConfig(str(self.root), str(output / "manifest.jsonl"), str(output / "provenance.json"), True),
            io=IOConfig(backend="webdataset", shard_index=str(output / "index.json"),
                        shard_index_sha256=file_hash(output / "index.json")))
        return cached, result

    def test_odd_rectangles_preserve_all_model_tensors(self):
        import numpy as np
        import torch
        from PIL import Image
        from cxr_research.preprocessing.segmentation import transform_pair
        for width, height in ((49, 33), (33, 49), (91, 19), (2, 3)):
            pixels = np.arange(width * height, dtype=np.uint8).reshape(height, width)
            image = Image.fromarray(pixels)
            mask = Image.fromarray((pixels > 100).astype(np.uint8) * 255)
            original = transform_pair(image, mask, self.config.unet, training=False, seed=42)
            pair = cache_pair(image, mask, 32)
            cached = transform_pair(*pair, self.config.unet, training=False, seed=42)
            self.assertTrue(all(torch.equal(a, b) for a, b in zip(original[:3], cached[:3], strict=True)))
            if (width, height) == (49, 33):
                self.assertEqual(cached[2].sum().item(), 704)

    def test_cache_index_loader_and_actual_cpu_training(self):
        import torch
        from cxr_research.data.datasets import SegmentationDataset
        from cxr_research.data.loaders import SegmentationLoader
        from cxr_research.data.shards import ShardStore
        from cxr_research.cli import main
        cached, result = self.convert()
        self.assertEqual(result["rows"], 80)
        source = load_manifest(self.config.data)
        manifest = load_manifest(cached.data, verify_files=False)
        store = ShardStore(cached, manifest)
        store.verify_all()
        source_sample = source.select("unet", "validation")[0]
        expected = SegmentationDataset(source, [source_sample], self.config.unet, training=False, seed=42)[0]
        actual = next(iter(SegmentationLoader(cached, manifest, "validation", store=store)))
        self.assertTrue(all(torch.equal(a, b[0]) for a, b in zip(expected, actual, strict=True)))
        store.close()
        path = self.root / "cached.json"
        path.write_text(json.dumps(cached.as_dict()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--repository", str(self.repository), "train", "--config", str(path)]), 0)
        metrics = json.loads((Path(cached.output_root) / cached.run_id / "metrics.json").read_text())
        self.assertEqual(metrics["epochs_completed"], 1)
        self.assertTrue((Path(cached.output_root) / cached.run_id / "epoch-0001.pt").is_file())

    def test_cache_rejects_a_different_training_resolution(self):
        from cxr_research.data.shards import ShardStore
        cached, _ = self.convert()
        manifest = load_manifest(cached.data, verify_files=False)
        changed = replace(cached, unet=replace(cached.unet, size=64))
        with self.assertRaisesRegex(ContractError, "cache.*resolution"):
            ShardStore(changed, manifest)

    def empty_parent(self, *, both=True):
        from cxr_research.data.catalog import build
        from tests.data.test_build_manifests import csv_file
        with self.mask_source.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["Left Lung"] = ""
        if both:
            rows[0]["Right Lung"] = ""
        csv_file(self.mask_source, list(rows[0]), rows)
        snapshot = self.root / "empty-catalog"
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, snapshot, "mimic-official", repository=self.repository)
        self.spec.update(catalog_dir=str(snapshot), catalog_report_sha256=file_hash(snapshot / "report.json"),
                         prepared_dir=str(self.root / "prepared/empty-parent"))
        fixtures.PooledUNetTests.convert(self)
        prepared = Path(self.spec["prepared_dir"])
        p = json.loads((prepared / "provenance.json").read_text())
        p["synthetic"] = True
        (prepared / "provenance.json").write_text(json.dumps(p))
        self.config = replace(self.config, data=replace(self.config.data,
            manifest=str(prepared / "manifest.jsonl"), provenance=str(prepared / "provenance.json")))
        self.config_path.write_text(json.dumps(self.config.as_dict()))

    def test_only_reviewed_both_empty_training_mask_is_excluded(self):
        self.empty_parent()
        with self.assertRaisesRegex(ContractError, "source review"):
            self.convert()
        cached, result = self.convert("approved-cache", empty_mask_policy="exclude-both-empty",
            expected_exclusions=1, qc_decision="synthetic fixture review only")
        self.assertEqual(result["rows"], 79)
        self.assertEqual(result["excluded_both_empty"], 1)
        manifest = load_manifest(cached.data, verify_files=False)
        self.assertEqual(len(manifest.samples), 79)
        self.assertNotIn("unet-mimic-cxr-synthetic-a", {s.sample_id for s in manifest.samples})

    def test_one_side_empty_remains_blocked_with_exclusions_enabled(self):
        self.empty_parent(both=False)
        with self.assertRaisesRegex(ContractError, "source review"):
            self.convert(empty_mask_policy="exclude-both-empty", expected_exclusions=1,
                         qc_decision="synthetic fixture review only")

    def test_exclusion_count_must_match_reviewed_count(self):
        self.empty_parent()
        with self.assertRaisesRegex(ContractError, "count differs"):
            self.convert(empty_mask_policy="exclude-both-empty", expected_exclusions=2,
                         qc_decision="synthetic fixture review only")


if __name__ == "__main__":
    unittest.main()
