"""Frozen or adapter-backed VQA generation and aggregate scoring."""

from __future__ import annotations

import json
import time
from pathlib import Path
import torch
from cxr_research.data.datasets import VQADataset
from cxr_research.experiments.checkpoints import verify_artifacts
from cxr_research.experiments.identity import _identity, _verify_parent
from cxr_research.metrics.vqa import vqa_metrics
from cxr_research.models.medgemma import load_model
from cxr_research.preprocessing.medgemma import VQACollator
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest
from cxr_research.utils.io import write_json


@torch.inference_mode()
def generate(config, manifest, directory: Path, *, adapter: Path | None = None, store=None):
    if (config.baseline == "B1") != (adapter is not None):
        raise ContractError("B1 evaluation requires an adapter; B0 must not load one")
    model, processor = load_model(config, training=False)
    identity = _identity(config, manifest, processor)
    if adapter:
        from peft import PeftModel
        _verify_parent(adapter / "model_identity.json", identity)
        adapter_files = verify_artifacts(adapter)
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False)
        model.eval()
        identity["loaded_adapter_sha256"] = digest(adapter_files)
    write_json(directory / "model_identity.json", identity)
    samples = manifest.select("medgemma", "validation")
    dataset = VQADataset(manifest, samples)
    collator = VQACollator(processor, config.medgemma)
    cached_images = None
    if store is not None:
        from cxr_research.data.shards import ShardDataset
        from cxr_research.preprocessing.cached_medgemma import CachedImageProcessor, CachedVQACollator
        dataset = ShardDataset(store, samples)
        collator = CachedVQACollator(processor.tokenizer.pad_token_id, supervised=False)
        cached_images = CachedImageProcessor(processor, config, store)
    metrics, durations, token_counts = [], [], []
    # Row-level output stays in the explicitly configured private run directory.
    with (directory / "predictions.jsonl").open("w", encoding="utf-8") as output:
        for index, sample in enumerate(samples):
            example = dataset[index]
            if cached_images is not None:
                batch = {k: v.to(model.device) for k, v in cached_images(collator([example])).items()}
            else:
                batch = collator.encode([example], supervised=False).to(model.device)
            torch.cuda.synchronize()
            started = time.perf_counter()
            tokens = model.generate(**batch, do_sample=False, max_new_tokens=config.medgemma.max_new_tokens)
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            continuation = tokens[0, batch["input_ids"].shape[-1]:]
            answer = processor.decode(continuation, skip_special_tokens=True)
            output.write(json.dumps({"index": index, "prediction": answer,
                                    "reference": sample.answer}) + "\n")
            metrics.append((answer, sample.answer, sample.answer_type, sample.primary))
            durations.append(elapsed)
            token_counts.append(len(continuation))
    result = vqa_metrics(metrics)
    result["timing"] = {"scope": "generation_only", "batch_size": 1,
                        "seconds_total": sum(durations), "generated_tokens": sum(token_counts),
                        "warmup_discarded": 0, "benchmark_status": "unwarmed_exploratory"}
    write_json(directory / "metrics.json", result)
    return result
