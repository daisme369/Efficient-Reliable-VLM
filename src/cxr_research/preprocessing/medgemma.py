"""Pinned chat processing and assistant-only supervision."""

from __future__ import annotations

import torch
from cxr_research.utils.errors import ContractError


def dialogue(question: str, prompt: str, answer: str | None = None):
    messages = [{"role": "system", "content": [{"type": "text", "text": prompt}]},
                {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": question}]}]
    if answer is not None:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
    return messages


def answer_labels(full_ids, attention_mask, prompt_ids, prompt_attention):
    labels = full_ids.clone()
    for index in range(len(full_ids)):
        full_positions = attention_mask[index].bool().nonzero().flatten()
        prefix = prompt_ids[index][prompt_attention[index].bool()]
        sequence = full_ids[index][full_positions]
        if len(sequence) <= len(prefix) or not torch.equal(sequence[:len(prefix)], prefix):
            raise ContractError("processed prompt is not an exact prefix of supervised input")
        labels[index, ~attention_mask[index].bool()] = -100
        labels[index, full_positions[:len(prefix)]] = -100
    return labels


class VQACollator:
    def __init__(self, processor, settings):
        self.processor, self.settings = processor, settings

    def encode(self, examples, *, supervised: bool):
        texts, images = [], []
        for example in examples:
            messages = dialogue(example["question"], self.settings.prompt,
                                example["answer"] if supervised else None)
            texts.append(self.processor.apply_chat_template(messages, tokenize=False,
                         add_generation_prompt=not supervised))
            images.append([example["image"]])
        batch = self.processor(text=texts, images=images, return_tensors="pt", padding=True,
                               truncation=False, add_special_tokens=False)
        if batch["input_ids"].shape[-1] > self.settings.max_length:
            raise ContractError("input exceeds token budget; truncation is disabled")
        return batch

    def __call__(self, examples):
        full = self.encode(examples, supervised=True)
        prompt = self.encode(examples, supervised=False)
        labels = answer_labels(full["input_ids"], full["attention_mask"],
                               prompt["input_ids"], prompt["attention_mask"])
        # All image positions belong to the masked prompt. Also reject image
        # placeholders in an answer rather than trying to supervise them.
        special = self.processor.tokenizer.special_tokens_map
        image_ids = []
        for name in ("boi_token", "eoi_token", "image_token"):
            token = special.get(name) or getattr(self.processor, name, None)
            if token:
                image_ids.append(self.processor.tokenizer.convert_tokens_to_ids(token))
        for token_id in image_ids:
            if token_id is not None:
                labels[full["input_ids"] == token_id] = -100
        if (labels != -100).sum(dim=1).min().item() == 0:
            raise ContractError("example contains no supervised answer tokens")
        full["labels"] = labels
        return full
