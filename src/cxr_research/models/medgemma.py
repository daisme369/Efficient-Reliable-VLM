"""MedGemma loading and language-only LoRA attachment."""

from __future__ import annotations

import re
import torch
from cxr_research.utils.errors import ContractError


def load_model(config, *, training: bool):
    from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig
    from transformers.utils import logging
    logging.set_verbosity_error()
    m = config.medgemma
    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[config.train.precision]
    processor = AutoProcessor.from_pretrained(m.model_id, revision=m.revision,
                local_files_only=m.local_files_only, trust_remote_code=False)
    processor.tokenizer.padding_side = "right" if training else "left"
    model = AutoModelForImageTextToText.from_pretrained(m.model_id, revision=m.revision,
            local_files_only=m.local_files_only, trust_remote_code=False, dtype=dtype,
            device_map={"": 0}, attn_implementation="eager",
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype))
    if training:
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                gradient_checkpointing_kwargs={"use_reentrant": False})
        target_pattern = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"
        if not any(re.fullmatch(target_pattern, name) for name, _ in model.named_modules()):
            raise ContractError("language-only LoRA target modules were not found")
        model = get_peft_model(model, LoraConfig(r=m.lora_rank, lora_alpha=m.lora_alpha,
                lora_dropout=m.lora_dropout, target_modules=target_pattern,
                bias="none", task_type="CAUSAL_LM"))
        model.config.use_cache = False
        names = [name for name, parameter in model.named_parameters() if parameter.requires_grad]
        if not names or any("lora_" not in name or "language_model" not in name for name in names):
            raise ContractError("trainable parameter scope differs from language-only LoRA")
    else:
        model.requires_grad_(False)
        model.eval()
    return model, processor
