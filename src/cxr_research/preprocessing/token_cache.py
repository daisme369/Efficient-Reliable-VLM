"""Cache exact multimodal token fields using the checkpoint's reference processor."""

from __future__ import annotations

from importlib.metadata import version
import json
import torch

from cxr_research.preprocessing.medgemma import VQACollator
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest


def processor_identity(processor, settings):
    if not processor.tokenizer.is_fast:
        raise ContractError("token caching requires the checkpoint's fast tokenizer")
    tokenizer = json.loads(processor.tokenizer.backend_tokenizer.to_str())
    # Calls can change these transient fields; pad/truncate policy is fixed below.
    tokenizer.pop("padding", None)
    tokenizer.pop("truncation", None)
    return {"model_id": settings.model_id, "revision": settings.revision,
            "prompt": settings.prompt, "max_length": settings.max_length,
            "processor": digest(processor.to_dict()),
            "image_processor": digest(processor.image_processor.to_dict()),
            "tokenizer": digest(processor.tokenizer.get_vocab()),
            "tokenizer_backend": digest(tokenizer), "tokenizers": version("tokenizers"),
            "special_tokens": digest(processor.tokenizer.special_tokens_map),
            "chat_template": digest(processor.chat_template),
            "transformers": version("transformers"), "cache_version": 1}


def cache_example(processor, settings, image, question, answer):
    collator = VQACollator(processor, settings)
    examples = [{"image": image, "question": question, "answer": answer}]
    full = collator(examples)
    prompt = collator.encode(examples, supervised=False)
    def fields(batch):
        allowed = {"input_ids", "attention_mask", "token_type_ids", "labels", "pixel_values"}
        if set(batch) - allowed:
            raise ContractError("processor returned unsupported cache fields")
        return {key: value[0].tolist() for key, value in batch.items() if key != "pixel_values"}
    return {"full": fields(full), "prompt": fields(prompt),
            "pixel_shape": list(full["pixel_values"].shape),
            "length": int(full["input_ids"].shape[-1])}


def pad_cached(records, pad_token_id: int, *, supervised: bool):
    """Pad only to this batch's maximum; generation uses left padding."""
    selected = [r["full" if supervised else "prompt"] for r in records]
    width = max(len(r["input_ids"]) for r in selected)
    keys = set(selected[0])
    if any(set(row) != keys for row in selected):
        raise ContractError("inconsistent token cache fields")
    output = {}
    for key in keys:
        fill = -100 if key == "labels" else pad_token_id if key == "input_ids" else 0
        result = torch.full((len(selected), width), fill, dtype=torch.long)
        for index, row in enumerate(selected):
            values = row[key]
            if len(values) != len(row["input_ids"]):
                raise ContractError("invalid token cache length")
            start = 0 if supervised else width - len(values)
            result[index, start:start + len(values)] = torch.tensor(values, dtype=torch.long)
        output[key] = result
    return output
