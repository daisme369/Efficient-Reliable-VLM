from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import platform
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
HAS_ML = all(importlib.util.find_spec(n) for n in ("torch", "torchvision", "PIL", "transformers"))


def tiny_processor():
    """A real Gemma3 processor with a tiny local tokenizer, no model download."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast, Gemma3Processor, Gemma3ImageProcessor
    vocabulary = {word: i for i, word in enumerate([
        "<pad>", "<unk>", "<bos>", "<eos>", "<boi>", "<eoi>", "<image>",
        "user", "assistant", "system", "yes", "no", "Is", "there", "a", "rectangle", "?"])}
    backend = Tokenizer(WordLevel(vocabulary, unk_token="<unk>"))
    backend.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="<pad>", unk_token="<unk>",
                                        bos_token="<bos>", eos_token="<eos>",
                                        additional_special_tokens=["<boi>", "<eoi>", "<image>"])
    for name, value in (("boi_token", "<boi>"), ("eoi_token", "<eoi>"), ("image_token", "<image>")):
        setattr(tokenizer, name, value)
        setattr(tokenizer, name + "_id", tokenizer.convert_tokens_to_ids(value))
    template = ("{% for m in messages %}{{ m['role'] }} {% for c in m['content'] %}"
                "{% if c['type'] == 'image' %}<boi>{% else %}{{ c['text'] }} {% endif %}"
                "{% endfor %}{% if m['role'] == 'assistant' %}<eos>{% endif %}{% endfor %}"
                "{% if add_generation_prompt %}assistant {% endif %}")
    return Gemma3Processor(Gemma3ImageProcessor(size={"height": 32, "width": 32}), tokenizer,
                           chat_template=template, image_seq_length=4)


@unittest.skipUnless(HAS_ML, "requires the project CPU ML environment")
class IOPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        torch.set_num_threads(1)

    def setUp(self):
        from cxr_research.data.synthetic import create_fixture
        from cxr_research.experiments.config import load_config
        from cxr_research.data.manifest import load_manifest
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config(create_fixture(self.root / "fixture"))
        self.manifest = load_manifest(self.config.data)

    def pack(self, config=None, processor=None):
        from cxr_research.data.shards import pack_shards, ShardStore
        from cxr_research.experiments.config import IOConfig
        config = config or self.config
        target = self.root / "shards"
        result = pack_shards(config, self.manifest, target, ROOT, shard_bytes=9000, processor=processor)
        configured = replace(config, io=IOConfig(backend="webdataset", shard_index=str(target / "index.json"),
                                                  shard_index_sha256=result["index_sha256"], cache_bytes_mb=0))
        store = ShardStore(configured, self.manifest)
        self.addCleanup(store.close)
        return configured, store

    def test_webdataset_roundtrip_has_finite_splits_and_no_test_payload(self):
        from cxr_research.data.shards import ShardDataset
        config, store = self.pack()
        self.assertEqual(len(store.rows), 11)
        store.verify_all()
        for shard in store.index["shards"]:
            with tarfile.open(store.root / shard["path"]) as tar:
                self.assertTrue(all(m.isfile() and "test-" not in m.name for m in tar))
        samples = self.manifest.select("unet", "train")
        dataset = ShardDataset(store, samples)
        self.assertEqual(dataset[0]["encoded_image"], (self.manifest.root / samples[0].image).read_bytes())
        self.assertEqual(dataset[0]["encoded_mask"], (self.manifest.root / samples[0].mask).read_bytes())

    def test_member_corruption_and_index_change_are_rejected(self):
        from cxr_research.data.shards import ShardDataset, ShardStore
        from cxr_research.utils.errors import ContractError
        config, store = self.pack()
        ref = next(iter(store.rows.values()))["image"]
        with (store.root / ref["shard"]).open("r+b") as file:
            file.seek(ref["offset"])
            file.write(b"BROKEN")
        with self.assertRaisesRegex(ContractError, "checksum"):
            ShardDataset(store, self.manifest.select("unet", "train"))[0]
        with self.assertRaisesRegex(ContractError, "checksum"):
            store.verify_all()
        store.path.write_text("{}")
        with self.assertRaisesRegex(ContractError, "checksum"):
            ShardStore(config, self.manifest)

    def test_original_images_are_not_needed_after_packing(self):
        from cxr_research.data.manifest import load_manifest
        from cxr_research.data.shards import ShardStore, ShardDataset
        config, store = self.pack()
        sample = self.manifest.select("unet", "train")[0]
        # Only this test's generated files are removed.
        (self.manifest.root / sample.image).unlink()
        manifest = load_manifest(config.data, verify_files=False)
        second = ShardStore(config, manifest)
        self.addCleanup(second.close)
        self.assertTrue(ShardDataset(second, manifest.select("unet", "train"))[0]["encoded_image"])
        with self.assertRaises(ValueError):
            load_manifest(config.data)

    def test_shard_loader_matches_file_loader_including_augmentation(self):
        import torch
        from cxr_research.data.loaders import SegmentationLoader
        config, store = self.pack()
        aug = replace(config.unet, rotation_degrees=5, brightness=.1, contrast=.1)
        for epoch in (0, 1):
            reference = SegmentationLoader(replace(self.config, unet=aug), self.manifest, "train", epoch=epoch)
            candidate = SegmentationLoader(replace(config, unet=aug), self.manifest, "train", store=store, epoch=epoch)
            for expected, actual in zip(reference, candidate):
                for left, right in zip(expected, actual):
                    self.assertTrue(torch.equal(left, right))

    def test_bucket_order_is_complete_seeded_and_reduces_padding(self):
        from cxr_research.data.batching import BucketSampler
        lengths = [4, 100, 6, 90, 7, 80, 8, 70]
        sampler = BucketSampler(lengths, 2, 4, 42)
        first = list(sampler)
        self.assertEqual(sorted(first), list(range(8)))
        self.assertEqual(first, list(sampler))
        grouped_cost = sum(2 * max(lengths[i] for i in first[p:p+2]) for p in range(0, 8, 2))
        naive_cost = sum(2 * max(lengths[p:p+2]) for p in range(0, 8, 2))
        self.assertLess(grouped_cost, naive_cost)
        sampler.set_epoch(3)
        other = BucketSampler(lengths, 2, 4, 42)
        other.set_epoch(3)
        self.assertEqual(list(sampler), list(other))

    def test_spawn_worker_reads_shards_with_parent_handles_already_open(self):
        import torch
        from cxr_research.data.loaders import SegmentationLoader
        from cxr_research.data.shards import ShardDataset
        config, store = self.pack()
        ShardDataset(store, self.manifest.select("unet", "train"))[0]
        expected = list(SegmentationLoader(config, self.manifest, "validation", store=store))
        parallel = replace(config, io=replace(config.io, num_workers=1))
        actual = list(SegmentationLoader(parallel, self.manifest, "validation", store=store))
        self.assertEqual(len(expected), len(actual))
        for left, right in zip(expected, actual):
            for a, b in zip(left, right):
                self.assertTrue(torch.equal(a, b))

    def test_cached_trainer_runs_accumulation_and_evaluation(self):
        import torch
        from transformers import TrainingArguments, PretrainedConfig
        from transformers.modeling_outputs import CausalLMOutput
        from cxr_research.experiments.config import MedGemmaConfig
        from cxr_research.data.shards import ShardDataset
        from cxr_research.preprocessing.cached_medgemma import CachedVQACollator, CachedImageProcessor
        from cxr_research.training.cached_trainer import CachedTrainer
        from cxr_research.training.losses import answer_loss

        class TinyModel(torch.nn.Module):
            def __init__(self, vocabulary):
                super().__init__()
                self.config = PretrainedConfig()
                self.embedding = torch.nn.Embedding(vocabulary, 8)
                self.head = torch.nn.Linear(8, vocabulary)
                self.calls = 0

            def forward(self, input_ids, attention_mask, pixel_values, token_type_ids=None):
                self.calls += 1
                # Explicit arguments ensure the model never receives encoded bytes.
                hidden = self.embedding(input_ids) + pixel_values.mean()
                return CausalLMOutput(logits=self.head(hidden))

        processor = tiny_processor()
        config, store = self.pack(replace(self.config, task="medgemma", baseline="B1",
                                          medgemma=MedGemmaConfig(revision="a" * 40)), processor)
        model = TinyModel(len(processor.tokenizer))
        before = model.head.weight.detach().clone()
        denominators = []

        def loss(outputs, labels, num_items_in_batch=None):
            if num_items_in_batch is not None:
                denominators.append(int(num_items_in_batch))
            return answer_loss(outputs, labels, num_items_in_batch)

        args = TrainingArguments(output_dir=str(self.root / "tiny-trainer"), use_cpu=True,
            max_steps=3, per_device_train_batch_size=2, per_device_eval_batch_size=2,
            gradient_accumulation_steps=2, report_to=[], save_strategy="steps", save_steps=1,
            logging_strategy="no",
            remove_unused_columns=False, label_names=["labels"], prediction_loss_only=True,
            dataloader_pin_memory=False, disable_tqdm=True, seed=42, data_seed=42)
        trainer = CachedTrainer(model=model, args=args,
            train_dataset=ShardDataset(store, self.manifest.select("medgemma", "train")),
            eval_dataset=ShardDataset(store, self.manifest.select("medgemma", "validation")),
            data_collator=CachedVQACollator(processor.tokenizer.pad_token_id), compute_loss_func=loss,
            image_preparer=CachedImageProcessor(processor, config, store), io_settings=config.io)
        with contextlib.redirect_stdout(io.StringIO()):
            result = trainer.train()
            metrics = trainer.evaluate()
        self.assertEqual(result.global_step, 3)
        self.assertFalse(torch.equal(model.head.weight.detach(), before))
        self.assertTrue(torch.isfinite(torch.tensor(metrics["eval_loss"])))
        self.assertGreaterEqual(model.calls, 5)
        self.assertTrue(denominators and all(value > 0 for value in denominators))
        resumed_model = TinyModel(len(processor.tokenizer))
        resumed = CachedTrainer(model=resumed_model,
            args=replace(args, output_dir=str(self.root / "resumed-trainer"), save_strategy="no"),
            train_dataset=trainer.train_dataset, data_collator=trainer.data_collator,
            compute_loss_func=loss, image_preparer=CachedImageProcessor(processor, config, store),
            io_settings=config.io)
        with contextlib.redirect_stdout(io.StringIO()):
            resumed.train(resume_from_checkpoint=str(self.root / "tiny-trainer/checkpoint-1"))
        self.assertEqual(resumed.state.global_step, 3)
        for key, value in model.state_dict().items():
            self.assertTrue(torch.equal(value, resumed_model.state_dict()[key]), key)

    def test_actual_gemma_processor_token_cache_matches_reference_and_is_invalidated(self):
        import torch
        from PIL import Image
        from cxr_research.experiments.config import MedGemmaConfig
        from cxr_research.data.shards import ShardDataset
        from cxr_research.preprocessing.cached_medgemma import CachedVQACollator, CachedImageProcessor
        from cxr_research.preprocessing.medgemma import VQACollator
        from cxr_research.utils.errors import ContractError
        processor = tiny_processor()
        settings = MedGemmaConfig(revision="a" * 40)
        config, store = self.pack(replace(self.config, task="medgemma", baseline="B1", medgemma=settings), processor)
        samples = self.manifest.select("medgemma", "train")[:2]
        dataset = ShardDataset(store, samples)
        examples = [dataset[i] for i in range(2)]
        raw = []
        for sample in samples:
            with Image.open(self.manifest.root / sample.image) as image:
                raw.append({"image": image.convert("RGB"), "question": sample.question, "answer": sample.answer})
        candidate = CachedImageProcessor(processor, config, store)(CachedVQACollator(processor.tokenizer.pad_token_id)(examples))
        expected = VQACollator(processor, settings)(raw)
        self.assertEqual(set(candidate), set(expected))
        for key in expected:
            self.assertTrue(torch.equal(candidate[key], expected[key]), key)
        # Generation must use the prompt-only cache, never its reference answer.
        processor.tokenizer.padding_side = "left"
        candidate = CachedImageProcessor(processor, config, store)(CachedVQACollator(processor.tokenizer.pad_token_id, supervised=False)(examples))
        expected = VQACollator(processor, settings).encode(raw, supervised=False)
        for key in expected:
            self.assertTrue(torch.equal(candidate[key], expected[key]), key)
        with self.assertRaisesRegex(ContractError, "mismatch"):
            CachedImageProcessor(processor, replace(config, medgemma=replace(settings, prompt="changed")), store)
        from tokenizers.normalizers import Lowercase
        processor.tokenizer.backend_tokenizer.normalizer = Lowercase()
        with self.assertRaisesRegex(ContractError, "mismatch"):
            CachedImageProcessor(processor, config, store)

    def test_dynamic_padding_masks_answer_and_respects_generation_side(self):
        from cxr_research.preprocessing.token_cache import pad_cached
        records = [{"full": {"input_ids": [1, 2, 3], "attention_mask": [1, 1, 1], "labels": [-100, -100, 3]},
                    "prompt": {"input_ids": [1, 2], "attention_mask": [1, 1]}},
                   {"full": {"input_ids": [1, 4], "attention_mask": [1, 1], "labels": [-100, 4]},
                    "prompt": {"input_ids": [1], "attention_mask": [1]}}]
        train = pad_cached(records, 0, supervised=True)
        self.assertEqual(train["labels"].tolist(), [[-100, -100, 3], [-100, 4, -100]])
        self.assertEqual(train["attention_mask"].tolist(), [[1, 1, 1], [1, 1, 0]])
        generate = pad_cached(records, 0, supervised=False)
        self.assertEqual(generate["input_ids"].tolist(), [[1, 2], [0, 1]])
        self.assertNotIn("labels", generate)

    def test_tensor_segmentation_keeps_binary_masks_and_repeatability(self):
        import torch
        from cxr_research.preprocessing.tensor_segmentation import transform_tensor_pair
        mask = torch.zeros(1, 20, 30, dtype=torch.uint8)
        mask[:, 3:17, 4:22] = 255
        config = replace(self.config.unet, rotation_degrees=5, brightness=.1)
        first = transform_tensor_pair(mask.clone(), mask, config, training=True, seed=123)
        second = transform_tensor_pair(mask.clone(), mask, config, training=True, seed=123)
        for a, b in zip(first, second):
            self.assertTrue(torch.equal(a, b))
            self.assertEqual(tuple(a.shape), (1, 32, 32))
        self.assertEqual(set(first[1].unique().tolist()), {0, 1})

    def test_dali_fails_explicitly_outside_linux_cuda(self):
        from cxr_research.preprocessing.decoding import DALIDecoder
        from cxr_research.utils.errors import ContractError
        with patch("cxr_research.preprocessing.decoding.platform.system", return_value="Windows"):
            with self.assertRaisesRegex(ContractError, "Linux CUDA"):
                DALIDecoder(2)

    def test_cli_pack_validate_train_and_evaluate_shards(self):
        from cxr_research.cli import main
        from cxr_research.experiments.config import IOConfig
        source = self.root / "source.json"
        source.write_text(json.dumps(self.config.as_dict()))
        output = self.root / "cli-shards"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["pack", "--config", str(source), "--output", str(output)]), 0)
        from cxr_research.utils.hashing import file_hash
        config = replace(self.config, run_id="packed", train=replace(self.config.train, epochs=1),
                         io=IOConfig(backend="webdataset", shard_index=str(output / "index.json"),
                                     shard_index_sha256=file_hash(output / "index.json")))
        source.write_text(json.dumps(config.as_dict()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["validate", "--config", str(source)]), 0)
            self.assertEqual(main(["train", "--config", str(source)]), 0)
            source.write_text(json.dumps(replace(config, run_id="packed-eval").as_dict()))
            self.assertEqual(main(["evaluate", "--config", str(source), "--checkpoint",
                                   str(Path(config.output_root) / "packed/epoch-0001.pt")]), 0)


@unittest.skipUnless(HAS_ML and platform.system() == "Linux" and os.environ.get("CXR_TEST_DALI") == "1",
                     "opt-in Linux/CUDA DALI check; run after GPU preflight")
class DALIIntegrationTests(unittest.TestCase):
    def test_cached_processor_and_paired_transforms_stay_on_gpu(self):
        import torch
        from PIL import Image
        from cxr_research.data.synthetic import create_fixture
        from cxr_research.data.manifest import load_manifest
        from cxr_research.data.shards import pack_shards, ShardStore, ShardDataset
        from cxr_research.experiments.config import load_config, MedGemmaConfig, IOConfig
        from cxr_research.preprocessing.cached_medgemma import CachedVQACollator, CachedImageProcessor
        from cxr_research.preprocessing.medgemma import VQACollator
        from cxr_research.preprocessing.decoding import DALIDecoder
        from cxr_research.preprocessing.tensor_segmentation import transform_tensor_pair

        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            config = replace(load_config(create_fixture(root / "fixture")), task="medgemma", baseline="B1",
                             medgemma=MedGemmaConfig(revision="a" * 40))
            manifest = load_manifest(config.data)
            processor = tiny_processor()
            packed = pack_shards(config, manifest, root / "shards", ROOT, processor=processor)
            config = replace(config, device="cuda", io=IOConfig(backend="webdataset", decoder="dali",
                shard_index=str(root / "shards/index.json"), shard_index_sha256=packed["index_sha256"]))
            store = ShardStore(config, manifest)
            try:
                samples = manifest.select("medgemma", "train")[:2]
                dataset = ShardDataset(store, samples)
                batch = CachedImageProcessor(processor, config, store)(
                    CachedVQACollator(processor.tokenizer.pad_token_id)([dataset[0], dataset[1]]))
                self.assertTrue(batch["pixel_values"].is_cuda)
                self.assertTrue(torch.isfinite(batch["pixel_values"]).all())
                raw = []
                for sample in samples:
                    with Image.open(manifest.root / sample.image) as image:
                        raw.append({"image": image.convert("RGB"), "question": sample.question,
                                    "answer": sample.answer})
                reference = VQACollator(processor, config.medgemma)(raw)
                self.assertEqual(batch["pixel_values"].shape, reference["pixel_values"].shape)
                self.assertTrue(torch.equal(batch["labels"], reference["labels"]))
                self.assertTrue(torch.equal(batch["input_ids"], reference["input_ids"]))
            finally:
                store.close()
            sample = manifest.select("unet", "train")[0]
            decoder = DALIDecoder(2, grayscale=True)
            image, mask = decoder([(manifest.root / sample.image).read_bytes(),
                                   (manifest.root / sample.mask).read_bytes()])
            output = transform_tensor_pair(image, mask, config.unet, training=True, seed=42)
            self.assertTrue(all(t.is_cuda and torch.isfinite(t).all() for t in output))
            self.assertTrue(set(output[1].unique().tolist()).issubset({0, 1}))

    def test_jpeg_png_variable_batch_and_lifetime(self):
        from PIL import Image
        import torch
        from cxr_research.preprocessing.decoding import DALIDecoder
        decoder = DALIDecoder(2)
        encoded = []
        for format in ("JPEG", "PNG"):
            buffer = io.BytesIO()
            Image.new("RGB", (20, 16), (80, 80, 80)).save(buffer, format=format)
            encoded.append(buffer.getvalue())
        images = decoder(encoded)
        saved = images[0].clone()
        decoder(encoded[:1])
        self.assertTrue(torch.equal(images[0], saved))
        self.assertTrue(all(x.is_cuda and tuple(x.shape) == (3, 16, 20) for x in images))
        self.assertLessEqual((images[1].float() - 80).abs().max().item(), 1)


if __name__ == "__main__":
    unittest.main()
