"""WebDataset-compatible tar shards with an integrity-bound local offset index.

One WebDataset sample is an image plus its QA records and optional mask files.
The sidecar expands QA rows without duplicating the image. No tar extraction or
pickle is used. The indexed Dataset preserves finite epochs and Trainer resume.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from collections import OrderedDict, defaultdict
from dataclasses import asdict
from pathlib import Path

from cxr_research.data.manifest import local_file
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest, file_hash
from cxr_research.utils.io import write_json


def pack_shards(config, manifest, output: Path, repository: Path, *, shard_bytes=512 * 1024**2, processor=None):
    from PIL import Image
    from cxr_research.preprocessing.token_cache import cache_example, processor_identity
    output = output.resolve()
    if not config.data.synthetic and output.is_relative_to(repository.resolve()):
        raise ContractError("restricted shards and token caches must be outside the source repository")
    if type(shard_bytes) is not int or shard_bytes <= 0:
        raise ContractError("shard size must be positive")
    if config.task == "medgemma" and processor is None:
        raise ContractError("MedGemma packing requires the pinned processor")
    output.mkdir(parents=True, exist_ok=False)
    status = {"status": "running", "task": config.task}
    write_json(output / "pack.json", status)
    index = {"schema_version": 1, "task": config.task, "synthetic": config.data.synthetic,
             "manifest_sha256": manifest.sha256, "provenance_sha256": manifest.provenance_sha256,
             "token_identity": processor_identity(processor, config.medgemma) if processor else None,
             "shards": [], "rows": []}
    archive = None
    try:
        for split in ("train", "validation"):
            groups = defaultdict(list)
            for sample in manifest.select(config.task, split):
                groups[sample.image].append(sample)
            shard_number = 0
            def close_shard():
                nonlocal archive
                if archive is not None:
                    archive.close()
                    index["shards"].append({"path": archive_path.name,
                                             "size": archive_path.stat().st_size,
                                             "sha256": file_hash(archive_path), "split": split})
                    archive = None
            def add_member(name, data):
                member = tarfile.TarInfo(name)
                member.size, member.mtime, member.mode = len(data), 0, 0o444
                offset = archive.offset + 512
                archive.addfile(member, io.BytesIO(data))
                return {"shard": archive_path.name, "offset": offset, "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}
            for group_number, samples in enumerate(groups.values()):
                if archive is None or archive.offset >= shard_bytes:
                    close_shard()
                    archive_path = output / f"{split}-{shard_number:06d}.tar"
                    shard_number += 1
                    archive = tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT)
                key = f"{split}-{group_number:09d}"
                encoded = local_file(manifest.root, samples[0].image).read_bytes()
                if any(hashlib.sha256(encoded).hexdigest() != s.image_sha256 for s in samples):
                    raise ContractError("source image changed during shard preparation")
                with Image.open(io.BytesIO(encoded)) as source:
                    if source.format not in {"JPEG", "PNG"} or source.mode not in {"L", "RGB"}:
                        raise ContractError("shards support prepared 8-bit JPEG and PNG images")
                    extension = "jpg" if source.format == "JPEG" else "png"
                    image = source.convert("RGB")
                image_ref = add_member(f"{key}.{extension}", encoded)
                records, pending = [], []
                for sample in samples:
                    record = {"sample_sha256": digest(asdict(sample)), "tokens": None}
                    if config.task == "medgemma":
                        record["tokens"] = cache_example(processor, config.medgemma, image, sample.question, sample.answer)
                    mask_ref = None
                    if sample.mask:
                        mask_data = local_file(manifest.root, sample.mask).read_bytes()
                        if hashlib.sha256(mask_data).hexdigest() != sample.mask_sha256:
                            raise ContractError("source mask changed during shard preparation")
                        mask_ref = add_member(f"{key}.mask{len(records):06d}.png", mask_data)
                    pending.append({"sample_id": sample.sample_id, "split": split,
                                    "sample_sha256": record["sample_sha256"], "image": image_ref,
                                    "mask": mask_ref, "record_index": len(records),
                                    "length": record["tokens"]["length"] if record["tokens"] else 0})
                    records.append(record)
                metadata_ref = add_member(f"{key}.json", json.dumps(records, allow_nan=False).encode())
                for row in pending:
                    row["metadata"] = metadata_ref
                    index["rows"].append(row)
            close_shard()
        write_json(output / "index.json", index)
        status.update(status="complete", rows=len(index["rows"]), shards=len(index["shards"]),
                      index_sha256=file_hash(output / "index.json"))
        write_json(output / "pack.json", status)
        return status
    except BaseException as error:
        if archive is not None:
            archive.close()
        status.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      error_type=type(error).__name__)
        write_json(output / "pack.json", status)
        raise


class ShardStore:
    def __init__(self, config, manifest):
        self.path = Path(config.io.shard_index)
        if file_hash(self.path) != config.io.shard_index_sha256:
            raise ContractError("shard index checksum mismatch")
        self.index = json.loads(self.path.read_text(encoding="utf-8"))
        expected = {"schema_version": 1, "task": config.task, "synthetic": config.data.synthetic,
                    "manifest_sha256": manifest.sha256, "provenance_sha256": manifest.provenance_sha256}
        if any(self.index.get(key) != value for key, value in expected.items()):
            raise ContractError("shards do not match the source manifest and task")
        self.root = self.path.parent.resolve()
        self.shards = {}
        for shard in self.index["shards"]:
            path = local_file(self.root, shard["path"])
            if path.suffix != ".tar" or path.stat().st_size != shard["size"] or shard["path"] in self.shards:
                raise ContractError("invalid shard inventory")
            self.shards[shard["path"]] = (path, shard["size"], shard["split"])
        expected_rows = {s.sample_id: s for split in ("train", "validation") for s in manifest.select(config.task, split)}
        self.rows = {}
        for row in self.index["rows"]:
            sample = expected_rows.get(row["sample_id"])
            if (sample is None or row["sample_id"] in self.rows or row["split"] != sample.split
                    or row["sample_sha256"] != digest(asdict(sample))
                    or row["image"]["sha256"] != sample.image_sha256):
                raise ContractError("shard rows differ from audited manifest")
            if config.task == "unet" and (not row["mask"] or row["mask"]["sha256"] != sample.mask_sha256):
                raise ContractError("shard mask differs from audited manifest")
            if type(row["record_index"]) is not int or row["record_index"] < 0:
                raise ContractError("invalid shard record index")
            if type(row["length"]) is not int or (config.task == "medgemma" and row["length"] <= 0):
                raise ContractError("invalid cached sequence length")
            for ref in (row["image"], row["metadata"], row["mask"]):
                if ref is None:
                    continue
                shard = self.shards.get(ref["shard"])
                if (shard is None or shard[2] != sample.split
                        or type(ref["offset"]) is not int or ref["offset"] < 512 or ref["offset"] % 512
                        or type(ref["size"]) is not int or ref["size"] <= 0
                        or ref["offset"] + ref["size"] > shard[1]):
                    raise ContractError("invalid shard offset or split")
            self.rows[sample.sample_id] = row
        if self.rows.keys() != expected_rows.keys():
            raise ContractError("shards omit manifest samples")
        self.cache_limit = config.io.cache_bytes_mb * 1024**2
        self._cache, self._handles, self._cache_size, self._pid = OrderedDict(), OrderedDict(), 0, os.getpid()

    def __getstate__(self):
        state = dict(self.__dict__)
        state.update(_cache=OrderedDict(), _handles=OrderedDict(), _cache_size=0, _pid=None)
        return state

    def close(self):
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def __del__(self):
        if hasattr(self, "_handles"):
            self.close()

    def read(self, ref):
        if self._pid != os.getpid():
            self.close()
            self._cache.clear()
            self._cache_size, self._pid = 0, os.getpid()
        key = (ref["shard"], ref["offset"], ref["size"], ref["sha256"])
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        name = ref["shard"]
        if name not in self._handles:
            if len(self._handles) >= 8:
                self._handles.popitem(last=False)[1].close()
            self._handles[name] = self.shards[name][0].open("rb")
        self._handles.move_to_end(name)
        handle = self._handles[name]
        handle.seek(ref["offset"])
        data = handle.read(ref["size"])
        if len(data) != ref["size"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ContractError("shard member checksum mismatch")
        if len(data) <= self.cache_limit:
            while self._cache and self._cache_size + len(data) > self.cache_limit:
                self._cache_size -= len(self._cache.popitem(last=False)[1])
            self._cache[key] = data
            self._cache_size += len(data)
        return data

    def verify_all(self):
        for shard in self.index["shards"]:
            if file_hash(self.shards[shard["path"]][0]) != shard["sha256"]:
                raise ContractError("whole-shard checksum mismatch")


class ShardDataset:
    def __init__(self, store, samples):
        self.store, self.samples = store, samples
        self.lengths = [store.rows[s.sample_id]["length"] for s in samples]

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        row = self.store.rows[self.samples[index].sample_id]
        records = json.loads(self.store.read(row["metadata"]))
        record = records[row["record_index"]]
        if record["sample_sha256"] != row["sample_sha256"]:
            raise ContractError("cached record identity mismatch")
        return {"encoded_image": self.store.read(row["image"]),
                "encoded_mask": self.store.read(row["mask"]) if row["mask"] else None,
                "tokens": record["tokens"], "index": index,
                "image_key": row["image"]["sha256"]}


def encoded_collate(examples):
    """Workers only read bytes; GPU decoding happens after the loader yields."""
    return {"examples": examples}
