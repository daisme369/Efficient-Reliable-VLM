"""Pick a small, reproducible demo subset from the NIH Chest X-ray sample (Kaggle nih-chest-xrays/sample, CC0).

Usage (images stay outside the repository; never commit them):

    python scripts/demo_data/select_nih_demo.py --labels <sample>/sample_labels.csv \
        [--images <sample>/images] --out D:/thorax-demo-images [--per-finding 2]

Writes selection.csv (and copies the chosen PNGs when --images is given). Rules: adult (18-90), PA view,
one image per patient, single-label images so the shown finding is unambiguous, ordered by a fixed hash
so the same CSV always gives the same selection. See docs/demo-data/README.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import shutil

# NIH label -> the app's finding name (configs/labeling_vocabulary.json).
FINDINGS = {
    "Effusion": "pleural effusion",
    "Pneumothorax": "pneumothorax",
    "Cardiomegaly": "enlarged cardiac silhouette",
    "Atelectasis": "atelectasis",
    "Consolidation": "consolidation",
    "Edema": "pulmonary edema",
    "Nodule": "lung nodule or mass",
    "Mass": "lung nodule or mass",
    "No Finding": "no finding",
}


def age_years(raw: str) -> int | None:
    """The sample writes ages like '060Y' (or M/D for infants); the full set writes plain numbers."""
    raw = raw.strip().upper()
    if raw.isdigit():
        return int(raw)
    return int(raw[:-1]) if raw.endswith("Y") and raw[:-1].isdigit() else None


def select(rows: list[dict], per_finding: int) -> list[dict]:
    chosen, patients = [], set()
    ordered = sorted(rows, key=lambda r: hashlib.sha256(r["Image Index"].encode()).hexdigest())
    for label, finding in FINDINGS.items():
        picked = 0
        for row in ordered:
            age = age_years(row["Patient Age"])
            if picked >= per_finding or row["Finding Labels"] != label or row["View Position"] != "PA" \
                    or age is None or not 18 <= age <= 90 or row["Patient ID"] in patients:
                continue
            patients.add(row["Patient ID"])
            chosen.append({"image": row["Image Index"], "nih_label": label, "app_finding": finding,
                           "age": age, "sex": row["Patient Gender"], "view": row["View Position"]})
            picked += 1
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-finding", type=int, default=2)
    args = parser.parse_args()
    with args.labels.open(newline="", encoding="utf-8") as handle:
        chosen = select(list(csv.DictReader(handle)), args.per_finding)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "selection.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image", "nih_label", "app_finding", "age", "sex", "view"])
        writer.writeheader()
        writer.writerows(chosen)
    if args.images:
        for item in chosen:
            shutil.copy2(args.images / item["image"], args.out / item["image"])
    print(f"Selected {len(chosen)} images into {args.out}")


if __name__ == "__main__":
    main()
