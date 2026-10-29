"""Hugging Face LoRA/QLoRA training and adapter export."""

from __future__ import annotations

from pathlib import Path
from cxr_research.data.datasets import VQADataset
from cxr_research.experiments.checkpoints import artifact_inventory, verify_artifacts
from cxr_research.experiments.identity import _identity, _verify_parent
from cxr_research.models.medgemma import load_model
from cxr_research.preprocessing.medgemma import VQACollator
from cxr_research.training.losses import answer_loss
from cxr_research.utils.errors import ContractError
from cxr_research.utils.io import write_json


def train(config, manifest, directory: Path, *, resume: Path | None = None, store=None):
    if config.baseline != "B1":
        raise ContractError("only B1 is trainable")
    from transformers import Trainer, TrainingArguments
    model, processor = load_model(config, training=True)
    identity = _identity(config, manifest, processor)
    if resume:
        _verify_parent(resume.parent / "model_identity.json", identity)
        verify_artifacts(resume)
    write_json(directory / "model_identity.json", identity)
    t = config.train
    args = TrainingArguments(output_dir=str(directory), num_train_epochs=t.epochs,
        per_device_train_batch_size=t.batch_size, per_device_eval_batch_size=t.batch_size,
        gradient_accumulation_steps=t.accumulation_steps, learning_rate=t.learning_rate,
        weight_decay=t.weight_decay, max_grad_norm=t.max_grad_norm,
        bf16=t.precision == "bf16", fp16=t.precision == "fp16",
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        eval_strategy="epoch", save_strategy="epoch", logging_strategy="no",
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
        save_total_limit=2, report_to=[], push_to_hub=False, disable_tqdm=True,
        remove_unused_columns=False, label_names=["labels"], dataloader_num_workers=config.io.num_workers,
        dataloader_pin_memory=config.io.pin_memory,
        dataloader_prefetch_factor=config.io.prefetch_factor if config.io.num_workers else None,
        dataloader_multiprocessing_context="spawn" if config.io.num_workers else None,
        prediction_loss_only=True,
        seed=config.seed, data_seed=config.seed, full_determinism=True,
        optim="adamw_torch", save_safetensors=True)
    from transformers import TrainerCallback
    class CheckpointInventory(TrainerCallback):
        def on_save(self, args, state, control, **kwargs):
            checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            write_json(checkpoint / "artifact_hashes.json", artifact_inventory(checkpoint))

    trainer_type, extra = Trainer, {}
    if store is not None:
        from cxr_research.data.shards import ShardDataset
        from cxr_research.preprocessing.cached_medgemma import CachedImageProcessor, CachedVQACollator
        from cxr_research.training.cached_trainer import CachedTrainer
        trainer_type = CachedTrainer
        extra = {"image_preparer": CachedImageProcessor(processor, config, store), "io_settings": config.io}
        training = ShardDataset(store, manifest.select("medgemma", "train"))
        validation = ShardDataset(store, manifest.select("medgemma", "validation"))
        collator = CachedVQACollator(processor.tokenizer.pad_token_id)
    else:
        training = VQADataset(manifest, manifest.select("medgemma", "train"))
        validation = VQADataset(manifest, manifest.select("medgemma", "validation"))
        collator = VQACollator(processor, config.medgemma)
    trainer = trainer_type(model=model, args=args, processing_class=processor, **extra,
        train_dataset=training, eval_dataset=validation,
        data_collator=collator, compute_loss_func=answer_loss,
        callbacks=[CheckpointInventory()])
    trainer.train(resume_from_checkpoint=str(resume) if resume else None)
    export = directory / "adapter"
    model.save_pretrained(export, safe_serialization=True)
    processor.save_pretrained(export)
    write_json(export / "model_identity.json", identity)
    write_json(export / "artifact_hashes.json", artifact_inventory(export))
    result = {"validation_loss": trainer.evaluate()["eval_loss"],
              "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
              "best_checkpoint": trainer.state.best_model_checkpoint,
              "adapter": "adapter", "answer_quality": "run evaluate separately"}
    write_json(directory / "metrics.json", result)
    return result
