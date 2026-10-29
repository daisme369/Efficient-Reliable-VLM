"""Typed experiment settings and protocol checks."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest


@dataclass(frozen=True)
class DataConfig:
    root: str
    manifest: str
    provenance: str
    synthetic: bool = False


@dataclass(frozen=True)
class TrainingConfig:
    epochs: int = 1
    batch_size: int = 2
    accumulation_steps: int = 1
    learning_rate: float = 0.0002
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    precision: str = "fp32"


@dataclass(frozen=True)
class UNetConfig:
    size: int = 256
    base_channels: int = 32
    rotation_degrees: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    threshold: float = 0.5


@dataclass(frozen=True)
class MedGemmaConfig:
    model_id: str = "google/medgemma-1.5-4b-it"
    revision: str = "UNRESOLVED"
    prompt: str = "Answer the question using the chest X-ray. Give a concise answer."
    max_length: int = 4096
    max_new_tokens: int = 128
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    local_files_only: bool = True
    quantization: str = "nf4"


@dataclass(frozen=True)
class IOConfig:
    backend: str = "files"
    shard_index: str | None = None
    shard_index_sha256: str | None = None
    decoder: str = "pil"
    num_workers: int = 0
    prefetch_factor: int = 2
    pin_memory: bool = True
    bucket_batches: int = 16
    group_by_length: bool = True
    cache_bytes_mb: int = 64
    dali_threads: int = 2


@dataclass(frozen=True)
class ExperimentConfig:
    run_id: str
    task: str
    baseline: str
    data: DataConfig
    output_root: str
    device: str = "cpu"
    seed: int = 42
    jira_issue: str | None = None
    protocol: str | None = None
    train: TrainingConfig = field(default_factory=TrainingConfig)
    unet: UNetConfig = field(default_factory=UNetConfig)
    medgemma: MedGemmaConfig = field(default_factory=MedGemmaConfig)
    io: IOConfig = field(default_factory=IOConfig)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        payload = self.as_dict()
        for key in ("run_id", "output_root", "device", "jira_issue", "protocol"):
            payload.pop(key)
        return digest(payload)


def _construct(cls, value):
    if not isinstance(value, dict) or set(value) - {f.name for f in fields(cls)}:
        raise ContractError("unknown or malformed configuration fields")
    try:
        return cls(**value)
    except TypeError:
        raise ContractError("missing configuration fields") from None


def load_config(path: Path) -> ExperimentConfig:
    return config_from_dict(json.loads(path.read_text(encoding="utf-8")))


def config_from_dict(raw: dict) -> ExperimentConfig:
    """Validate resolved settings without modifying the caller's mapping."""
    if not isinstance(raw, dict):
        raise ContractError("configuration must be an object")
    raw = dict(raw)
    for key, cls in (("data", DataConfig), ("train", TrainingConfig),
                     ("unet", UNetConfig), ("medgemma", MedGemmaConfig), ("io", IOConfig)):
        raw[key] = _construct(cls, raw.get(key, {}))
    config = _construct(ExperimentConfig, raw)
    validate_config(config)
    return config


def validate_config(c: ExperimentConfig) -> None:
    if c.io.backend not in {"files", "webdataset"} or c.io.decoder not in {"pil", "dali"}:
        raise ContractError("unsupported I/O backend or decoder")
    if (type(c.io.num_workers) is not int or c.io.num_workers < 0
            or type(c.io.cache_bytes_mb) is not int or c.io.cache_bytes_mb < 0
            or any(type(n) is not int or n <= 0 for n in
                   (c.io.prefetch_factor, c.io.bucket_batches, c.io.dali_threads))
            or type(c.io.pin_memory) is not bool or type(c.io.group_by_length) is not bool):
        raise ContractError("invalid loader settings")
    if c.io.backend == "webdataset":
        if (not isinstance(c.io.shard_index, str) or not Path(c.io.shard_index).is_absolute()
                or not isinstance(c.io.shard_index_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", c.io.shard_index_sha256)):
            raise ContractError("WebDataset requires an absolute index path and its SHA-256")
    elif c.io.decoder != "pil" or c.io.shard_index is not None or c.io.shard_index_sha256 is not None:
        raise ContractError("shard index and DALI require the WebDataset backend")
    if c.io.decoder == "dali" and c.device != "cuda":
        raise ContractError("DALI mixed decoding requires CUDA")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", c.run_id):
        raise ContractError("invalid run ID")
    if (c.task, c.baseline) not in {("unet", "S1"), ("medgemma", "B0"), ("medgemma", "B1")}:
        raise ContractError("unsupported task or baseline")
    if c.device not in {"cpu", "cuda"} or c.train.precision not in {"fp32", "fp16", "bf16"}:
        raise ContractError("unsupported device or precision")
    if type(c.data.synthetic) is not bool or type(c.seed) is not int or c.seed < 0:
        raise ContractError("invalid synthetic flag or seed")
    positive = (c.train.epochs, c.train.batch_size, c.train.accumulation_steps,
                c.unet.size, c.unet.base_channels, c.medgemma.max_length,
                c.medgemma.max_new_tokens, c.medgemma.lora_rank, c.medgemma.lora_alpha)
    if any(type(x) is not int or x <= 0 for x in positive):
        raise ContractError("counts must be positive integers")
    if c.unet.size % 16 or c.unet.base_channels % 8:
        raise ContractError("U-Net size must be divisible by 16 and width by 8")
    numbers = (c.train.learning_rate, c.train.weight_decay, c.train.max_grad_norm,
               c.unet.rotation_degrees, c.unet.brightness, c.unet.contrast,
               c.unet.threshold, c.medgemma.lora_dropout)
    if any(type(x) not in (float, int) or not math.isfinite(x) for x in numbers):
        raise ContractError("numeric settings must be finite")
    if c.train.learning_rate <= 0 or c.train.max_grad_norm <= 0 or c.train.weight_decay < 0:
        raise ContractError("invalid optimizer settings")
    if not (0 <= c.unet.rotation_degrees <= 10 and 0 <= c.unet.brightness <= .2
            and 0 <= c.unet.contrast <= .2 and 0 < c.unet.threshold < 1
            and 0 <= c.medgemma.lora_dropout < 1):
        raise ContractError("invalid augmentation, threshold or dropout")
    if c.device == "cpu" and c.train.precision != "fp32":
        raise ContractError("CPU smoke runs require fp32")
    if c.task == "medgemma" and c.medgemma.model_id != "google/medgemma-1.5-4b-it":
        raise ContractError("backbone is locked to MedGemma 1.5 4B")
    if c.task == "medgemma" and not re.fullmatch(r"[0-9a-f]{40}", c.medgemma.revision):
        raise ContractError("MedGemma requires an immutable checkpoint revision")
    if c.task == "medgemma" and not c.medgemma.prompt.strip():
        raise ContractError("prompt must be recorded")
    if (not isinstance(c.medgemma.quantization, str)
            or c.medgemma.quantization not in {"none", "nf4"}):
        raise ContractError("MedGemma quantization must be none or nf4")


def check_storage(c: ExperimentConfig, repository: Path) -> None:
    paths = [Path(c.data.root), Path(c.data.manifest), Path(c.data.provenance), Path(c.output_root)]
    if c.io.shard_index:
        paths.append(Path(c.io.shard_index))
    if any(not p.is_absolute() for p in paths):
        raise ContractError("data and output paths must be absolute")
    if not c.data.synthetic and any(p.resolve().is_relative_to(repository.resolve()) for p in paths):
        raise ContractError("restricted inputs and outputs must be outside the source repository")
    if c.data.synthetic and not all(p.resolve().is_relative_to(Path(c.data.root).resolve())
                                    for p in paths[1:3]):
        raise ContractError("synthetic manifests must be under their fixture root")


def check_protocol(c: ExperimentConfig, manifest_hash: str, provenance_hash: str) -> None:
    if c.data.synthetic:
        return
    if not isinstance(c.jira_issue, str) or not re.fullmatch(r"[A-Z][A-Z0-9]+-[1-9][0-9]*", c.jira_issue):
        raise ContractError("real runs require a valid Jira issue key")
    if not c.protocol:
        raise ContractError("real runs require a reviewed protocol file")
    p = json.loads(Path(c.protocol).read_text(encoding="utf-8"))
    required = ("decision_record", "split_policy", "input_policy", "uncertain_label_policy",
                "template_policy", "metric_policy", "training_data_overlap", "access_review")
    if p.get("status") != "approved" or any(not p.get(k) or p[k] == "UNRESOLVED" for k in required):
        raise ContractError("research protocol decisions are unresolved")
    if (p.get("config_sha256") != c.fingerprint or p.get("manifest_sha256") != manifest_hash
            or p.get("provenance_sha256") != provenance_hash):
        raise ContractError("protocol fingerprints do not match this experiment")
    segmentation_decision = p.get("segmentation_training_decision")
    if (c.task == "unet"
            and (not isinstance(segmentation_decision, str)
                 or not segmentation_decision.strip()
                 or segmentation_decision.strip().casefold() == "unresolved")):
        raise ContractError("CheXmask training protocol must be recorded separately from anatomical QC")
