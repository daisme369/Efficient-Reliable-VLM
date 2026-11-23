"""Build lossless, input-equivalent U-Net thumbnails in indexed private tar shards."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from cxr_research.data.manifest import Sample, local_file
from cxr_research.data.segmentation_images import EmptyLungMask, read_pair
from cxr_research.experiments.config import check_protocol, check_storage, load_config
from cxr_research.preprocessing.images import letterbox
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest, file_hash
from cxr_research.utils.io import write_json


def cache_pair(image, mask, size):
    image, mask, valid, geometry = letterbox(image, mask, size)
    bounds = (geometry.left, geometry.top, geometry.left + geometry.width, geometry.top + geometry.height)
    pair = image.crop(bounds), mask.crop(bounds)
    restored = letterbox(*pair, size)[:3]
    if any(a.tobytes() != b.tobytes() for a, b in zip((image, mask, valid), restored, strict=True)):
        raise ContractError("cache changes model input pixels or valid region")
    return pair


class _Writer:
    def __init__(self, root, split, shards, target_bytes):
        self.root, self.split, self.shards, self.target_bytes = root, split, shards, target_bytes
        self.archive, self.number = None, 0

    def ensure(self):
        if self.archive is not None and self.archive.offset >= self.target_bytes:
            self.close()
        if self.archive is None:
            self.path = self.root / f"{self.split}-{self.number:06d}.tar"
            self.number += 1
            self.archive = tarfile.open(self.path, "w", format=tarfile.USTAR_FORMAT)

    def add(self, name, data):
        member = tarfile.TarInfo(name)
        member.size, member.mtime, member.mode = len(data), 0, 0o444
        offset = self.archive.offset + 512
        self.archive.addfile(member, io.BytesIO(data))
        return {"shard": self.path.name, "offset": offset, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def close(self):
        if self.archive is not None:
            self.archive.close()
            self.shards.append({"path": self.path.name, "size": self.path.stat().st_size,
                "sha256": file_hash(self.path), "split": self.split})
            self.archive = None


@dataclass(frozen=True)
class _Cached:
    source_sha: str
    sample: Sample
    encoded: tuple[bytes, bytes]


@dataclass(frozen=True)
class _Excluded:
    sample: Sample


def cache_unet(config_path: Path, output: Path, repository: Path, *, workers=8, shard_bytes=512 * 1024**2,
               empty_mask_policy="stop", expected_exclusions=None, qc_decision=None):
    config, output = load_config(config_path), output.resolve()
    check_storage(config, repository)
    if ((config.task, config.baseline, config.io.backend) != ("unet", "S1", "files")
            or any((config.unet.rotation_degrees, config.unet.brightness, config.unet.contrast))
            or output.is_relative_to(repository.resolve()) or output.exists()
            or type(workers) is not int or not 1 <= workers <= 16
            or type(shard_bytes) is not int or shard_bytes <= 0):
        raise ContractError("cache requires a new private directory and an unaugmented U-Net files config")
    if (empty_mask_policy not in {"stop", "exclude-both-empty"}
            or (empty_mask_policy == "exclude-both-empty" and
                (type(expected_exclusions) is not int or expected_exclusions <= 0
                 or not isinstance(qc_decision, str) or not qc_decision.strip()
                 or qc_decision.strip().upper() == "UNRESOLVED"))):
        raise ContractError("empty-mask exclusions require an explicit QC decision and expected count")
    parent_path, provenance_path = Path(config.data.manifest), Path(config.data.provenance)
    parent_hash, provenance_hash = file_hash(parent_path), file_hash(provenance_path)
    provenance = json.loads(provenance_path.read_text())
    status = json.loads((parent_path.parent / "status.json").read_text())
    if (status.get("status") != "complete" or status.get("sha256") != parent_hash
            or provenance.get("manifest_sha256") != parent_hash or provenance.get("schema_version") != 3
            or provenance.get("synthetic") is not config.data.synthetic):
        raise ContractError("cache requires an unchanged completed canonical preparation")
    check_protocol(config, parent_hash, provenance_hash)
    root, samples, patients, identities = Path(config.data.root).resolve(), [], {}, {}
    with parent_path.open() as stream:
        for line in stream:
            sample = Sample(**json.loads(line))
            if sample.task != "unet" or sample.split not in {"train", "validation"} or not sample.patient_id or not sample.patient_namespace:
                raise ContractError("cache requires patient-isolated development segmentation samples")
            group = (sample.patient_namespace, sample.patient_id)
            if patients.setdefault(group, sample.split) != sample.split:
                raise ContractError("patient split overlap detected")
            identity = (sample.patient_namespace, sample.image_id)
            if identities.setdefault(identity, (group, sample.split, sample.image)) != (group, sample.split, sample.image):
                raise ContractError("conflicting source image identity")
            for relative in (sample.image, sample.mask):
                path = PurePosixPath(relative)
                if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
                    raise ContractError("source path escapes data root")
            samples.append(sample)
    if len(samples) != provenance["rows_after"] or len({s.sample_id for s in samples}) != len(samples):
        raise ContractError("source preparation counts or identities differ")
    mask_hashes = {}
    for sample in samples:
        if sample.mask in mask_hashes and mask_hashes[sample.mask] != sample.mask_sha256:
            raise ContractError("conflicting mask identity")
        mask_hashes[sample.mask] = sample.mask_sha256
    for path, expected in mask_hashes.items():
        if file_hash(local_file(root, path)) != expected:
            raise ContractError("source mask bytes changed before caching")
    if shutil.disk_usage(output.parent).free < len(samples) * (2 * config.unet.size**2 + 16384) + 8 * 1024**3:
        raise ContractError("insufficient private disk space for cache")
    output.mkdir(parents=True)
    write_json(output / "status.json", {"status": "preparing", "phase": "cache_pairs", "total": len(samples)})
    index = {"schema_version": 1, "task": "unet", "synthetic": config.data.synthetic,
        "token_identity": None, "shards": [], "rows": [],
        "unet_cache": {"size": config.unet.size, "geometry": "crop_letterbox_valid_rectangle_v1"}}
    writers = {split: _Writer(output, split, index["shards"], shard_bytes) for split in ("train", "validation")}
    def convert(item):
        from PIL import Image
        number, sample = item
        if file_hash(local_file(root, sample.image)) != sample.image_sha256:
            raise ContractError("source image checksum differs before caching")
        try:
            pair = read_pair(root, sample)
        except EmptyLungMask as error:
            if (empty_mask_policy == "exclude-both-empty" and error.both_empty
                    and sample.patient_namespace == "mimic-cxr" and sample.split == "train"):
                return _Excluded(sample)
            raise
        image, mask = cache_pair(*pair, config.unet.size)
        encoded = []
        for value in (image, mask):
            buffer = io.BytesIO()
            value.save(buffer, format="PNG")
            payload = buffer.getvalue()
            with Image.open(io.BytesIO(payload)) as decoded:
                if decoded.size != value.size or decoded.tobytes() != value.tobytes():
                    raise ContractError("PNG cache changed source pixels")
            encoded.append(payload)
        child = replace(sample, image=f"{output.name}/assets/{number:09d}.png",
            mask=f"{output.name}/assets/{number:09d}.mask.png", mask_format="png", mask_reference=None,
            image_sha256=hashlib.sha256(encoded[0]).hexdigest(), mask_sha256=hashlib.sha256(encoded[1]).hexdigest())
        return _Cached(sample.image_sha256, child, (encoded[0], encoded[1]))
    try:
        counts, image_splits, included_patients = Counter(), {}, set()
        exclusions = 0
        with ThreadPoolExecutor(max_workers=workers) as pool, (output / "manifest.jsonl").open("w", newline="\n") as manifest:
            results = (result for start in range(0, len(samples), 64)
                       for result in pool.map(convert, enumerate(samples[start:start + 64], start)))
            for number, result in enumerate(results):
                if isinstance(result, _Excluded):
                    exclusions += 1
                    continue
                source_sha, sample, encoded = result.source_sha, result.sample, result.encoded
                if image_splits.setdefault(source_sha, sample.split) != sample.split:
                    raise ContractError("duplicate source bytes cross development splits")
                writer = writers[sample.split]
                writer.ensure()
                key = f"{sample.split}-{number:09d}"
                image_ref = writer.add(key + ".png", encoded[0])
                mask_ref = writer.add(key + ".mask.png", encoded[1])
                sample_sha = digest(asdict(sample))
                meta = writer.add(key + ".json", json.dumps([{"sample_sha256": sample_sha, "tokens": None}]).encode())
                index["rows"].append({"sample_id": sample.sample_id, "split": sample.split, "sample_sha256": sample_sha,
                    "image": image_ref, "mask": mask_ref, "metadata": meta, "record_index": 0, "length": 0})
                manifest.write(json.dumps(asdict(sample), sort_keys=True) + "\n")
                counts[f"{sample.patient_namespace}:{sample.split}"] += 1
                included_patients.add((sample.patient_namespace, sample.patient_id))
                if (number + 1) % 1000 == 0:
                    write_json(output / "status.json", {"status": "preparing", "phase": "cache_pairs",
                        "pairs_written": len(index["rows"]), "source_samples_checked": number + 1,
                        "excluded_both_empty": exclusions, "total": len(samples)})
        if empty_mask_policy == "exclude-both-empty" and exclusions != expected_exclusions:
            raise ContractError("empty-mask exclusion count differs from the reviewed QC decision")
        for writer in writers.values():
            writer.close()
        child_provenance = dict(provenance)
        child_provenance.update(created_at=datetime.now(timezone.utc).isoformat(),
            manifest_sha256=file_hash(output / "manifest.jsonl"),
            parent_manifest_sha256=parent_hash, parent_provenance_sha256=provenance_hash,
            producer_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
            producer_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repository)),
            producer_module_sha256=file_hash(Path(__file__)),
            unet_cache={**index["unet_cache"], "image_codec": "lossless PNG",
                "equivalence": "existing PIL letterbox then crop valid rectangle; loader re-letterboxes at the same size",
                "source_image_hashes_verified": True, "source_mask_hashes_verified": True})
        child_provenance["unet_cache"]["pixel_equivalence_verified_pairs"] = len(index["rows"])
        child_provenance["mask_qc"] = {"policy": empty_mask_policy, "decision_record": qc_decision,
            "excluded_both_empty": exclusions, "scope": "mimic-cxr train only; one-side-empty still stops"}
        child_provenance["counts_by_dataset_split"] = dict(counts)
        if exclusions:
            child_provenance["exclusion_stages"] = [*provenance["exclusion_stages"],
                {"stage": "empty_lung_target", "reason": "reviewed exclusion of both-empty training targets",
                 "rows_before": len(samples), "excluded": exclusions, "rows_after": len(index["rows"])}]
            child_provenance["exclusions"] = {**provenance["exclusions"], "empty_lung_target": exclusions}
            child_provenance["rows_after"] = len(index["rows"])
        write_json(output / "provenance.json", child_provenance)
        index.update(manifest_sha256=child_provenance["manifest_sha256"], provenance_sha256=file_hash(output / "provenance.json"))
        write_json(output / "index.json", index)
        result = {"status": "complete", "rows": len(index["rows"]), "patients": len(included_patients),
            "counts_by_dataset_split": dict(counts), "size": config.unet.size, "shards": len(index["shards"]),
            "shard_bytes": sum(s["size"] for s in index["shards"]), "index_sha256": file_hash(output / "index.json"),
            "sha256": index["manifest_sha256"], "parent_manifest_sha256": parent_hash, "training_ready": False,
            "excluded_both_empty": exclusions}
        write_json(output / "status.json", result)
        return result
    except BaseException as error:
        for writer in writers.values():
            writer.close()
        write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__})
        raise
