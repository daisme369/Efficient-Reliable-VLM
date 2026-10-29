"""Bind model and processor state to experiment inputs."""

from __future__ import annotations

import json
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest


def _identity(config, manifest, processor):
    return {"config_sha256": config.fingerprint, "manifest_sha256": manifest.sha256,
            "provenance_sha256": manifest.provenance_sha256,
            "processor_sha256": digest(processor.to_dict()),
            "chat_template_sha256": digest(processor.chat_template),
            "model_revision": config.medgemma.revision,
            "objective": "assistant_answer_and_end_of_turn_only",
            "precision": config.train.precision,
            "quantization": "nf4_double_quant" if config.medgemma.quantization == "nf4" else "none",
            "adapter_scope": "language_projections"}


def _verify_parent(path, identity):
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if recorded != identity:
        raise ContractError("adapter/checkpoint identity differs from current experiment")
