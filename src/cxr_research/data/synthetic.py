"""Geometric fixtures; never clinical data or research evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from cxr_research.experiments.config import DataConfig, ExperimentConfig, TrainingConfig, UNetConfig
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json


def create_fixture(destination: Path) -> Path:
    from PIL import Image, ImageDraw
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    assets = destination / "assets"
    assets.mkdir()
    rows = []
    for split, count in (("train", 8), ("validation", 3), ("test", 2)):
        for index in range(count):
            key = f"synthetic-{split}-{index}"
            image = Image.new("L", (48, 32), 15)
            mask = Image.new("L", image.size)
            box = (5+index % 3, 5, 30+index % 3, 25)
            ImageDraw.Draw(image).rectangle(box, fill=220)
            ImageDraw.Draw(mask).rectangle(box, fill=255)
            image.save(assets / f"{key}.png")
            mask.save(assets / f"{key}-mask.png")
            base = {"patient_id": key, "patient_namespace": "synthetic", "image_id": key,
                    "image": f"assets/{key}.png", "split": split, "view": "synthetic",
                    "image_sha256": file_hash(assets / f"{key}.png")}
            if split != "test":
                rows.append({**base, "sample_id": f"{key}-seg", "task": "unet", "mask": f"assets/{key}-mask.png",
                             "mask_sha256": file_hash(assets / f"{key}-mask.png")})
                rows.append({**base, "sample_id": f"{key}-qa", "task": "medgemma", "question": "Is there a rectangle?",
                             "answer": "yes", "answer_type": "closed", "template_id": "synthetic-shape", "primary": True})
    manifest = destination / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    provenance = destination / "provenance.json"
    write_json(provenance, {"schema_version": 3, "synthetic": True, "created_at": "synthetic-fixture-v1",
        "producer_commit": "synthetic-generator-v1", "sources": [{"kind": "generated",
        "name": "generated-rectangles", "version": "1", "access": "synthetic-only",
        "license": "synthetic-fixture", "sha256": file_hash(manifest)}],
        "exclusions": {"test_payload_sealed": 4},
        "exclusion_stages": [{"stage": "test_payload_sealed",
            "reason": "Final-test labels are excluded from the development manifest",
            "rows_before": 26, "excluded": 4, "rows_after": len(rows)}],
        "rows_before": 26, "rows_after": len(rows), "split_policy": "distinct-generated-patients",
        "manifest_sha256": file_hash(manifest)})
    config = ExperimentConfig("unet-smoke", "unet", "S1",
        DataConfig(str(destination), str(manifest), str(provenance), synthetic=True),
        str(destination / "runs"), train=TrainingConfig(epochs=3, batch_size=2, learning_rate=.003),
        unet=UNetConfig(size=32, base_channels=8))
    path = destination / "unet.json"
    write_json(path, asdict(config))
    return path
