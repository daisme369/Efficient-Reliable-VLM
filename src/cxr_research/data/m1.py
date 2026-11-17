"""Read the audited M1 development bundle and construct assistant targets."""

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from itertools import groupby
import json
from math import ceil, floor
from pathlib import Path
import sqlite3

from PIL import Image
from cxr_research.data.datasets import VQADataset
from cxr_research.data.manifest import local_file
from cxr_research.preprocessing.annotations import validate_box
from cxr_research.preprocessing.labels import normalize_text
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash

SPLITS = ("train", "validation")
VALIDATION_FOLDS = ("selection", "calibration", "report")
GRID = 1000
OBJECTIVE = "m1_v1_vqa_region_phrase_impression"
REGION_PROMPT = (
    "Locate the evidence for this question. Return a JSON list of [x1,y1,x2,y2] "
    "boxes on a 0 to 1000 grid, or [] if you cannot localize it. Question: "
)
PHRASE_PROMPT = (
    "Locate this finding. Return a JSON list of [x1,y1,x2,y2] "
    "boxes on a 0 to 1000 grid, or [] if you cannot localize it. Finding: "
)
REPORT_PROMPT = "Write the radiology impression supported by this image."


def validation_partition(patient):
    value = hashlib.sha256(f"m1-validation-v1:{patient}".encode()).digest()[0]
    return VALIDATION_FOLDS[value % len(VALIDATION_FOLDS)]


@dataclass(frozen=True)
class Target:
    kind: str
    image: str
    question: str
    answer: str
    patient: str


class MixedM1Dataset:
    def __init__(self, manifest, samples, regions, phrases, reports):
        self.qa = VQADataset(manifest, samples)
        self.targets = (*regions, *phrases, *reports)
        self.root = manifest.root

    def __len__(self):
        return len(self.qa) + len(self.targets)

    def __getitem__(self, index):
        if index < len(self.qa):
            return self.qa[index]
        target = self.targets[index - len(self.qa)]
        with Image.open(local_file(self.root, target.image)) as image:
            return {"image": image.convert("RGB"), "question": target.question, "answer": target.answer}


def grid_boxes(boxes, width, height):
    result = []
    for box in boxes:
        x1, y1, x2, y2 = validate_box(box, width, height)
        result.append((floor(x1 * GRID / width), floor(y1 * GRID / height),
                       ceil(x2 * GRID / width), ceil(y2 * GRID / height)))
    if not result:
        raise ContractError("M1 positive grounding target is missing its box")
    return json.dumps(sorted(set(result)), separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def report_target(record, image, patient, study, method):
    expected_path = f"mimic/p{patient[:2]}/p{patient}/s{study}/{image}.jpg"
    if (record["image"], record["id"], record["generate_method"]) != (
            expected_path, f"{patient}_{study}", method):
        raise ContractError("LLaVA-Rad record identity differs from mapping")
    value = record.get("impression")
    if value is not None and not isinstance(value, str):
        raise ContractError("LLaVA-Rad impression must be a string or null")
    return normalize_text(value) if value else ""


class M1Bundle:
    def __init__(self, manifest, directory, expected_report_sha256, *, synthetic, pilot=False):
        self.root = manifest.root
        report_path = local_file(self.root, directory.relative_to(self.root).as_posix() + "/report.json")
        if file_hash(report_path) != expected_report_sha256:
            raise ContractError("M1 bundle report does not match the protocol")
        report = read_json(report_path)
        outputs = report["outputs_sha256"]
        mapping = local_file(self.root, directory.relative_to(self.root).as_posix() + "/mapping.sqlite")
        verification = read_json(directory / "verification.json")
        if (report["status"] != "development_review_only"
                or file_hash(mapping) != outputs["mapping.sqlite"]
                or outputs["manifest.jsonl"] != manifest.sha256
                or outputs["provenance.json"] != manifest.provenance_sha256
                or verification["status"] != "passed"
                or verification["manifest_hash"] != manifest.sha256
                or verification["held_or_cross_split_patients"] != 0
                or report["patient_checks"]["held_patient_leaks"] != 0
                or report["patient_checks"]["train_validation_overlap"] != 0):
            raise ContractError("M1 requires a verified bundle that matches the selected manifest")
        self.identity = {"objective": OBJECTIVE, "validation_policy": "patient_sha256_three_part_v1",
                         "population": "pilot_8_per_task_per_partition_v1" if pilot else "full_development",
                         "bundle_report_sha256": expected_report_sha256,
                         "mapping_sha256": outputs["mapping.sqlite"]}
        self.samples = {split: manifest.select("medgemma", split) for split in SPLITS}
        qa = {sample.sample_id: sample for samples in self.samples.values() for sample in samples}
        self.by_split = {split: {kind: [] for kind in ("region", "phrase", "impression")} for split in SPLITS}
        self.regions = {}
        self.exclusions = Counter()
        assets = {}
        expected_assets = {s.image: s.image_sha256 for s in qa.values()}
        publishers = {} if synthetic else self._publisher()

        @lru_cache(maxsize=None)
        def source(relative):
            path = local_file(self.root, relative)
            if file_hash(path) != report["source_metadata"][relative]["sha256"]:
                raise ContractError("M1 source data digest differs from the verified bundle")
            return path

        @lru_cache(maxsize=None)
        def dimensions(relative):
            path = local_file(self.root, relative)
            with Image.open(path) as image:
                return image.size

        def asset(relative):
            if relative not in assets:
                path = local_file(self.root, relative)
                if relative not in expected_assets and not synthetic:
                    expected = publishers.get(relative)
                    if not expected or file_hash(path) != expected:
                        raise ContractError("M1 auxiliary image differs from publisher hash")
                assets[relative] = True
            return relative

        with closing(sqlite3.connect(mapping.as_uri() + "?mode=ro", uri=True)) as db:
            if (db.execute("PRAGMA foreign_key_check").fetchone()
                    or db.execute("SELECT patient_id FROM images GROUP BY patient_id "
                                  "HAVING count(DISTINCT split)>1").fetchone()
                    or db.execute("SELECT 1 FROM images WHERE split NOT IN ('train','validation') "
                                  "OR split IS NULL").fetchone()):
                raise ContractError("M1 mapping violates development split isolation")
            linked = db.execute("SELECT sample_id,image_id,patient_id,split,relative_path,view "
                                "FROM vqa JOIN images USING(image_id)")
            seen = set()
            for sid, image_id, patient, split, relative, view in linked:
                sample = qa.get(sid)
                if sample is None or (image_id, patient, split, relative, view) != (
                        sample.image_id, sample.patient_id, sample.split, sample.image, sample.view):
                    raise ContractError("M1 QA identity differs from the manifest")
                seen.add(sid)
            if seen != qa.keys():
                raise ContractError("M1 mapping does not cover the selected manifest")
            for sid, serialized in db.execute("SELECT sample_id,xyxy FROM chest_imagenome_bbox ORDER BY sample_id"):
                sample = qa[sid]
                target = grid_boxes([json.loads(serialized)], *dimensions(sample.image))
                self.regions[sid] = target
                self.by_split[sample.split]["region"].append(Target(
                    "region", sample.image, REGION_PROMPT + sample.question, target, sample.patient_id))
            self._load_phrases(db, source, asset, dimensions)
            self._load_reports(db, source, asset)
        if pilot:
            self.samples["train"] = self.samples["train"][:8]
            for kind, targets in self.by_split["train"].items():
                self.by_split["train"][kind] = targets[:8]
            validation_samples = sorted(self.samples["validation"],
                                        key=lambda sample: sample.sample_id not in self.regions)
            self.samples["validation"] = [
                sample for fold in VALIDATION_FOLDS for sample in
                [s for s in validation_samples if validation_partition(s.patient_id) == fold][:8]]
            for kind, targets in self.by_split["validation"].items():
                self.by_split["validation"][kind] = [
                    target for fold in VALIDATION_FOLDS for target in
                    [t for t in targets if validation_partition(t.patient) == fold][:8]]
        self.train, self.validation = (
            MixedM1Dataset(manifest, self.samples[split], *self.by_split[split].values()) for split in SPLITS
        )
        def select(patient):
            return validation_partition(patient) == "selection"
        self.selection = MixedM1Dataset(
            manifest, [s for s in self.samples["validation"] if select(s.patient_id)],
            *([target for target in group if select(target.patient)]
              for group in self.by_split["validation"].values()))
        if any(not targets for targets in self.by_split["train"].values()):
            raise ContractError("M1 requires nonempty QA-region, phrase and impression training supervision")
        if not len(self.selection) or not any(
                validation_partition(s.patient_id) == "report" for s in self.samples["validation"]):
            raise ContractError("M1 requires patient-disjoint checkpoint selection and reporting populations")

    def _publisher(self):
        prefix = "mimic-cxr-jpg/2.1.0"
        result = {}
        with local_file(self.root, prefix + "/SHA256SUMS.txt").open(encoding="utf-8-sig") as stream:
            for line in stream:
                if line.strip():
                    sha, name = line.split(maxsplit=1)
                    result[prefix + "/" + name.strip().lstrip("*").removeprefix("./")] = sha.lower()
        return result

    def _load_phrases(self, db, source, asset, dimensions):
        cached = {}
        rows = db.execute("""SELECT image_id,patient_id,split,relative_path,source_ref,category_id,
                               annotation_ids,boxes_xyxy FROM ms_cxr_phrase JOIN images USING(image_id)
                               ORDER BY phrase_id""")
        for image, patient, split, relative, ref, category, ids, boxes in rows:
            if ref not in cached:
                coco = read_json(source(ref))
                cached[ref] = ({a["id"]: a for a in coco["annotations"]},
                               {r["id"]: Path(r["file_name"]).stem for r in coco["images"]})
            annotations, images = cached[ref]
            references = [annotations[i] for i in json.loads(ids)]
            if not references or any(
                    images[a["image_id"]] != image or a["category_id"] != category
                    or a["label_text"] != references[0]["label_text"]
                    or {"train": "train", "val": "validation", "validation": "validation"}.get(a["split"]) != split
                    for a in references):
                raise ContractError("MS-CXR phrase identity differs from mapping")
            native = [tuple(box) for box in json.loads(boxes)]
            expected = {tuple([x, y, x + w, y + h]) for a in references for x, y, w, h in (a["bbox"],)}
            if set(native) != expected:
                raise ContractError("MS-CXR boxes differ from source")
            phrase = normalize_text(references[0]["label_text"])
            if not phrase:
                raise ContractError("MS-CXR phrase is empty")
            ref_path = asset(relative)
            self.by_split[split]["phrase"].append(Target(
                "phrase", ref_path, PHRASE_PROMPT + phrase, grid_boxes(native, *dimensions(ref_path)), patient))

    def _load_reports(self, db, source, asset):
        rows = db.execute("""SELECT i.image_id,patient_id,i.study_id,c.study_id,split,relative_path,
                              source_ref,byte_offset,byte_length,method
                             FROM context_study c JOIN context_image USING(image_id) JOIN images i USING(image_id)
                             ORDER BY source_ref,byte_offset""")
        for ref, items in groupby(rows, key=lambda row: row[6]):
            with source(ref).open("rb") as stream:
                for image, patient, study, selected, split, relative, _, offset, length, method in items:
                    if selected != study or offset <= 0 or not 0 < length <= 1024**2:
                        raise ContractError("invalid report study or range")
                    stream.seek(offset)
                    raw = stream.read(length)
                    if len(raw) != length:
                        raise ContractError("LLaVA-Rad range is incomplete")
                    target = report_target(json.loads(raw), image, patient, study, method)
                    if target:
                        self.by_split[split]["impression"].append(Target(
                            "impression", asset(relative), REPORT_PROMPT, target, patient))
                    else:
                        self.exclusions[f"{split}_empty_impression"] += 1

    def summary(self):
        counts = {fold: Counter() for fold in VALIDATION_FOLDS}
        for sample in self.samples["validation"]:
            counts[validation_partition(sample.patient_id)]["vqa"] += 1
        for target in self.validation.targets:
            counts[validation_partition(target.patient)][target.kind] += 1
        return {"splits": {split: {"vqa": len(self.samples[split]),
                                  **{k: len(v) for k, v in self.by_split[split].items()}}
                           for split in SPLITS},
                "validation_partitions": {fold: dict(count) for fold, count in counts.items()},
                "exclusions": dict(self.exclusions)}
