"""CPU token collation followed by parent-process image decoding/preprocessing."""

from __future__ import annotations

from cxr_research.preprocessing.decoding import make_decoder
from cxr_research.preprocessing.token_cache import pad_cached, processor_identity
from cxr_research.utils.errors import ContractError


class CachedVQACollator:
    def __init__(self, pad_token_id, *, supervised=True):
        self.pad_token_id, self.supervised = pad_token_id, supervised

    def __call__(self, examples):
        batch = pad_cached([e["tokens"] for e in examples], self.pad_token_id, supervised=self.supervised)
        batch["_encoded_images"] = [e["encoded_image"] for e in examples]
        batch["_pixel_shapes"] = [e["tokens"]["pixel_shape"] for e in examples]
        return batch


class CachedImageProcessor:
    def __init__(self, processor, config, store):
        if store.index["token_identity"] != processor_identity(processor, config.medgemma):
            raise ContractError("token cache processor, prompt or revision mismatch; rebuild shards")
        self.processor = processor
        if config.io.decoder == "dali":
            from transformers.image_processing_backends import TorchvisionBackend
            if not isinstance(processor.image_processor, TorchvisionBackend):
                raise ContractError("DALI needs the checkpoint's tensor-native image processor")
        self.decoder = make_decoder(config.io, config.train.batch_size)

    def __call__(self, batch):
        batch = dict(batch)
        images = self.decoder(batch.pop("_encoded_images"))
        shapes = batch.pop("_pixel_shapes")
        # Match the defaults used by the full multimodal processor at cache build.
        kwargs = dict(self.processor.valid_processor_kwargs._defaults.get("images_kwargs", {}))
        processed = self.processor.image_processor(images, return_tensors="pt", **kwargs)
        processed.pop("num_crops", None)
        if set(processed) != {"pixel_values"}:
            raise ContractError("unsupported image processor outputs for cached tokens")
        pixels = processed["pixel_values"]
        expected = [sum(s[0] for s in shapes), *shapes[0][1:]]
        if any(s[1:] != shapes[0][1:] for s in shapes) or list(pixels.shape) != expected:
            raise ContractError("image preprocessing differs from cached image-token layout")
        batch.update(processed)
        return batch
