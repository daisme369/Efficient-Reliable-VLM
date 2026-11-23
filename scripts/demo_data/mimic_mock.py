"""Build a mock chest X-ray database with MIMIC-CXR-JPG's structure, from public or synthetic images only.

    python scripts/demo_data/mimic_mock.py build --images D:/thorax-demo-images --out D:/thorax-mimic-mock \
        [--selection D:/thorax-demo-images/selection.csv]
    python scripts/demo_data/mimic_mock.py push --root D:/thorax-mimic-mock --site https://thorax-capstone.duckdns.org \
        --email technician@thorax.demo          # password from THORAX_PUSH_PASSWORD or a prompt

`build` copies MIMIC's layout, not its data:
- files/p1X/p1XXXXXXX/s5XXXXXXX/<dicom_id>.jpg
- a report per study (FINDINGS/IMPRESSION)
- mimic-cxr-2.0.0-metadata.csv
- mimic-cxr-2.0.0-chexpert.csv
- patients.csv

IDs fall in MIMIC's ranges (subject_id 10,000,000-19,999,999; study_id 50,000,000-59,999,999; 40-hex dicom_id)
and are derived from the image bytes, so the same images always give the same database.
`--selection` (from select_nih_demo.py) adds the NIH finding, age and sex. Never point this at MIMIC itself:
restricted data must not reach the demo server.

`push` sends every study through the X-ray technician's intake API (register the patient, push the study),
the same path a technician uses. An administrator then assigns the patients to reading doctors.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

METADATA_FIELDS = ["dicom_id", "subject_id", "study_id", "PerformedProcedureStepDescription", "ViewPosition", "Rows", "Columns",
                   "StudyDate", "StudyTime", "ProcedureCodeSequence_CodeMeaning", "ViewCodeSequence_CodeMeaning",
                   "PatientOrientationCodeSequence_CodeMeaning"]
CHEXPERT = ["Atelectasis", "Cardiomegaly", "Consolidation", "Edema", "Enlarged Cardiomediastinum", "Fracture", "Lung Lesion",
            "Lung Opacity", "No Finding", "Pleural Effusion", "Pleural Other", "Pneumonia", "Pneumothorax", "Support Devices"]
# NIH label -> CheXpert column, and the report sentence a radiologist might have written.
NIH = {
    "Effusion": ("Pleural Effusion", "Blunting of the costophrenic angle consistent with a pleural effusion.", "Pleural effusion."),
    "Pneumothorax": ("Pneumothorax", "Visceral pleural line with absent lung markings peripherally.", "Pneumothorax."),
    "Cardiomegaly": ("Cardiomegaly", "The cardiac silhouette is enlarged.", "Cardiomegaly."),
    "Atelectasis": ("Atelectasis", "Linear opacity at the lung base compatible with atelectasis.", "Atelectasis."),
    "Consolidation": ("Consolidation", "Focal airspace consolidation.", "Consolidation; correlate clinically for pneumonia."),
    "Edema": ("Edema", "Interstitial markings and vascular congestion.", "Pulmonary edema."),
    "Nodule": ("Lung Lesion", "A rounded nodular opacity.", "Pulmonary nodule; follow-up recommended."),
    "Mass": ("Lung Lesion", "A rounded mass-like opacity.", "Lung mass; further evaluation recommended."),
    "No Finding": ("No Finding", "The lungs are clear. No effusion or pneumothorax. Normal cardiac silhouette.", "No acute cardiopulmonary process."),
}
INDICATIONS = ["Cough and fever.", "Shortness of breath.", "Chest pain.", "Pre-operative assessment.", "Follow-up chest radiograph."]


def _number(text: str, low: int, span: int, taken: set[int]) -> int:
    value = low + int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) % span
    while value in taken:  # deterministic linear probing keeps IDs unique
        value = low + (value - low + 1) % span
    taken.add(value)
    return value


def dicom_id(data: bytes) -> str:
    digest = hashlib.sha1(data).hexdigest()  # 40 hex, shown in MIMIC's 8-8-8-8-8 groups
    return "-".join(digest[i:i + 8] for i in range(0, 40, 8))


def build(images: Path, out: Path, selection: Path | None = None) -> list[dict]:
    from PIL import Image

    info = {}
    if selection:
        with selection.open(newline="", encoding="utf-8") as handle:
            info = {row["image"]: row for row in csv.DictReader(handle)}
    sources = sorted(p for p in images.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not sources:
        raise SystemExit(f"No PNG or JPEG images in {images}.")
    subjects, studies, rows = set(), set(), []
    for index, source in enumerate(sources):
        data = source.read_bytes()
        key = hashlib.sha256(data).hexdigest()
        subject = _number("subject:" + key, 10_000_000, 10_000_000, subjects)
        study = _number("study:" + key, 50_000_000, 10_000_000, studies)
        meta = info.get(source.name, {})
        label = meta.get("nih_label", "No Finding")
        column, finding, impression = NIH.get(label, NIH["No Finding"])
        with Image.open(source) as raw:
            image = raw.convert("L")
        folder = out / "files" / f"p{str(subject)[:2]}" / f"p{subject}" / f"s{study}"
        folder.mkdir(parents=True, exist_ok=True)
        image_id = dicom_id(data)
        image.save(folder / f"{image_id}.jpg", format="JPEG", quality=95)
        day = 1 + int(key[:4], 16) % 28
        indication = INDICATIONS[index % len(INDICATIONS)]
        (folder.parent / f"s{study}.txt").write_text(
            "FINAL REPORT\n"
            f" EXAMINATION: CHEST (PA)\n\n INDICATION: {indication}\n\n COMPARISON: None.\n\n"
            f" FINDINGS: {finding}\n\n IMPRESSION: {impression}\n", encoding="utf-8")
        view = meta.get("view") or "PA"
        rows.append({
            "dicom_id": image_id, "subject_id": subject, "study_id": study,
            "PerformedProcedureStepDescription": "CHEST (PA AND LAT)", "ViewPosition": view,
            "Rows": image.height, "Columns": image.width, "StudyDate": f"202610{day:02d}", "StudyTime": f"{index % 24:02d}0000.000",
            "ProcedureCodeSequence_CodeMeaning": "CHEST (PA AND LAT)",
            "ViewCodeSequence_CodeMeaning": "postero-anterior" if view == "PA" else "antero-posterior",
            "PatientOrientationCodeSequence_CodeMeaning": "Erect",
            # Not MIMIC columns: carried in a side file for the push step and the app's patient record.
            "_label": column, "_indication": indication, "_age": meta.get("age", ""), "_sex": meta.get("sex", ""),
            "_path": str((folder / f"{image_id}.jpg").relative_to(out)).replace("\\", "/"),
        })
    with (out / "mimic-cxr-2.0.0-metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with (out / "mimic-cxr-2.0.0-chexpert.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["subject_id", "study_id", *CHEXPERT])
        for row in rows:
            writer.writerow([row["subject_id"], row["study_id"], *("1.0" if name == row["_label"] else "" for name in CHEXPERT)])
    with (out / "patients.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["subject_id", "gender", "anchor_age", "anchor_year"])  # MIMIC-IV patients columns
        for row in rows:
            writer.writerow([row["subject_id"], row["_sex"], row["_age"], 2026])
    with (out / "push-manifest.json").open("w", encoding="utf-8") as handle:
        json.dump([{k: row[k] for k in ("subject_id", "study_id", "dicom_id", "ViewPosition", "StudyDate", "_indication", "_age", "_sex", "_path")}
                   for row in rows], handle, indent=1)
    return rows


class Intake:
    """The technician's intake API: sign in (HTTPS session cookie + CSRF token), register patients, push studies."""

    def __init__(self, site: str):
        self.site = site.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def call(self, method: str, path: str, body: bytes | None = None, content_type: str = "application/json") -> dict:
        request = urllib.request.Request(self.site + path, data=body, method=method,
                                         headers={"Origin": self.site, "Content-Type": content_type, "X-CSRF-Token": self.csrf})
        try:
            with self.opener.open(request, timeout=60) as response:  # noqa: S310 - the operator's own site
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as problem:
            detail = json.loads(problem.read() or b"{}").get("error", {}).get("message", "")
            raise SystemExit(f"{method} {path} failed ({problem.code}): {detail}") from None

    def sign_in(self, email: str, password: str) -> None:
        self.csrf = self.call("POST", "/api/v1/auth/login", json.dumps({"email": email, "password": password}).encode())["csrf_token"]

    def push(self, root: Path) -> list[str]:
        codes = []
        for item in json.loads((root / "push-manifest.json").read_text(encoding="utf-8")):
            sex = {"M": "Male", "F": "Female"}.get(item["_sex"])
            birth_year = 2026 - int(item["_age"]) if str(item["_age"]).isdigit() else None
            patient = self.call("POST", "/api/v1/admin/patients", json.dumps(
                {"name": f"Mock subject {item['subject_id']}", "birth_year": birth_year, "sex": sex}).encode())
            date = f"{item['StudyDate'][:4]}-{item['StudyDate'][4:6]}-{item['StudyDate'][6:]}"
            query = urllib.parse.urlencode({"study_date": date, "indication": item["_indication"], "view": item["ViewPosition"], "permitted": "yes"})
            self.call("POST", f"/api/v1/patients/{patient['resource_id']}/studies?{query}", (root / item["_path"]).read_bytes(), "image/jpeg")
            codes.append(f"{patient['code']} <- subject {item['subject_id']} study {item['study_id']}")
        return codes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--images", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--selection", type=Path)
    send = commands.add_parser("push")
    send.add_argument("--root", type=Path, required=True)
    send.add_argument("--site", required=True)
    send.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "build":
        rows = build(args.images, args.out, args.selection)
        print(f"Built {len(rows)} studies in {args.out}")
    else:
        intake = Intake(args.site)
        intake.sign_in(args.email, os.environ.get("THORAX_PUSH_PASSWORD") or getpass.getpass("Password: "))
        for line in intake.push(args.root):
            print(line)
        print("Pushed. Ask an administrator to assign these patients to the reading doctors.", file=sys.stderr)


if __name__ == "__main__":
    main()
