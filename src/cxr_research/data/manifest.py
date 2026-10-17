"""Canonical records and full-population split validation."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from cxr_research.data.splits import SplitAudit
from cxr_research.experiments.config import DataConfig
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


@dataclass(frozen=True)
class Sample:
    sample_id: str
    patient_id: str
    patient_namespace: str
    image_id: str
    image: str
    split: str
    task: str
    view: str
    image_sha256: str
    question: str = ""
    answer: str = ""
    answer_type: str = ""
    template_id: str = ""
    primary: bool = False
    mask: str = ""
    mask_sha256: str = ""


@dataclass(frozen=True)
class Manifest:
    root: Path
    samples: tuple[Sample, ...]
    sha256: str
    provenance_sha256: str

    def select(self, task: str, split: str) -> tuple[Sample, ...]:
        if split not in {"train", "validation"}:
            raise ContractError("only development splits are enabled; final test needs a separate locked evaluation")
        result = tuple(s for s in self.samples if s.task == task and s.split == split)
        if not result:
            raise ContractError("requested task/split is empty")
        return result

    def summary(self) -> dict:
        return {"rows": len(self.samples),
                "patients": len({(s.patient_namespace, s.patient_id) for s in self.samples}),
                "splits": dict(Counter(s.split for s in self.samples)), "sha256": self.sha256}


def local_file(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise ContractError("manifest requires relative paths within its data root")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()) or (must_exist and not resolved.is_file()):
        raise ContractError("manifest asset is missing or escapes data root")
    return resolved


def load_manifest(config: DataConfig, *, verify_files: bool = True) -> Manifest:
    root, path = Path(config.root), Path(config.manifest)
    provenance = json.loads(Path(config.provenance).read_text(encoding="utf-8"))
    required = ("schema_version", "sources", "created_at", "producer_commit", "exclusions",
                "exclusion_stages", "split_policy", "rows_before", "rows_after")
    if not isinstance(provenance, dict) or any(k not in provenance for k in required):
        raise ContractError("incomplete provenance")
    if provenance["schema_version"] != 3 or not provenance["sources"]:
        raise ContractError("unsupported provenance schema")
    for key in ("created_at", "producer_commit", "split_policy"):
        value = provenance[key]
        if not isinstance(value, str) or not value.strip() or value.strip().upper() == "UNRESOLVED":
            raise ContractError("top-level provenance decisions are unresolved")
    for source in provenance["sources"]:
        required_source = ("kind", "name", "version", "access", "license", "sha256")
        if (not isinstance(source, dict)
                or any(not isinstance(source.get(k), str) or not source[k].strip()
                       or source[k].strip().upper() == "UNRESOLVED" for k in required_source)):
            raise ContractError("resolved source version, access, license and checksum are required")
        if source["kind"] not in {"generated", "images", "qa", "masks"}:
            raise ContractError("unsupported provenance source kind")
        if source["kind"] == "qa":
            derivation = source.get("derivation")
            keys = ("producer", "method", "source_report_section", "filters")
            if (not isinstance(derivation, dict)
                    or any(not isinstance(derivation.get(k), str) or not derivation[k].strip()
                           or derivation[k].strip().upper() == "UNRESOLVED" for k in keys)):
                raise ContractError("derived QA provenance is unresolved")
    before, after = provenance["rows_before"], provenance["rows_after"]
    if type(before) is not int or type(after) is not int or before < after or after < 0:
        raise ContractError("invalid provenance row counts")
    running = before
    stages = provenance["exclusion_stages"]
    exclusions = provenance["exclusions"]
    if (not isinstance(stages, list) or not isinstance(exclusions, dict)
            or any(not isinstance(name, str) or not name or type(count) is not int or count < 0
                   for name, count in exclusions.items())):
        raise ContractError("invalid exclusion stages")
    stage_counts = {}
    for stage in stages:
        if (not isinstance(stage, dict)
                or any(k not in stage for k in ("stage", "reason", "rows_before", "excluded", "rows_after"))
                or not isinstance(stage["stage"], str) or not stage["stage"]
                or not isinstance(stage["reason"], str) or not stage["reason"]
                or type(stage["excluded"]) is not int or stage["excluded"] < 0
                or stage["rows_before"] != running
                or stage["rows_after"] != running - stage["excluded"]):
            raise ContractError("invalid exclusion stage")
        if stage["stage"] in stage_counts:
            raise ContractError("duplicate exclusion stage")
        stage_counts[stage["stage"]] = stage["excluded"]
        running = stage["rows_after"]
    if running != after or stage_counts != exclusions:
        raise ContractError("exclusion stages do not reconcile row counts")
    actual_hash = file_hash(path)
    if provenance.get("manifest_sha256") != actual_hash:
        raise ContractError("manifest checksum differs from provenance")
    if provenance.get("synthetic") is not config.synthetic:
        raise ContractError("synthetic flag differs from provenance")
    samples, ids, verified_assets = [], set(), {}
    split_audit = SplitAudit()

    def verify_asset(path: Path, expected: str) -> None:
        if not verify_files:
            return
        if path not in verified_assets:
            verified_assets[path] = file_hash(path)
        if verified_assets[path] != expected:
            raise ContractError("asset checksum differs from the manifest")

    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                sample = Sample(**json.loads(line))
            except (TypeError, ValueError):
                raise ContractError("invalid canonical manifest row") from None
            for key in ("sample_id", "patient_id", "patient_namespace", "image_id", "image", "view"):
                if not isinstance(getattr(sample, key), str) or not getattr(sample, key):
                    raise ContractError("missing manifest identity or view")
            if sample.split not in {"train", "validation", "calibration", "test"}:
                raise ContractError("invalid split")
            if sample.task not in {"medgemma", "unet"} or type(sample.primary) is not bool:
                raise ContractError("invalid task or primary flag")
            if sample.sample_id in ids:
                raise ContractError("duplicate sample key")
            ids.add(sample.sample_id)
            image_path = local_file(root, sample.image, must_exist=verify_files)
            split_audit.add(sample.patient_namespace, sample.patient_id, sample.image_id, image_path, sample.split)
            verify_asset(image_path, sample.image_sha256)
            if sample.split == "test":
                raise ContractError("development manifest must not contain final-test payload")
            if sample.task == "medgemma":
                if (not isinstance(sample.question, str) or not isinstance(sample.answer, str)
                        or not sample.question.strip() or not sample.answer.strip() or not sample.template_id):
                    raise ContractError("VQA supervision and template metadata are required")
                if sample.answer_type not in {"closed", "open"}:
                    raise ContractError("answer type must come from the recorded taxonomy")
            else:
                verify_asset(local_file(root, sample.mask, must_exist=verify_files), sample.mask_sha256)
            samples.append(sample)
    if not samples or after != len(samples):
        raise ContractError("manifest row count does not match provenance")
    return Manifest(root.resolve(), tuple(samples), actual_hash, file_hash(Path(config.provenance)))
