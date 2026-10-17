"""Encoded image bytes to PIL images or device tensors; CUDA stays in the parent."""

from __future__ import annotations

import io
import platform
from pathlib import Path
import numpy as np
import torch
from PIL import Image

from cxr_research.utils.errors import ContractError


class PILDecoder:
    def __init__(self, *, grayscale=False):
        self.mode = "L" if grayscale else "RGB"

    def __call__(self, encoded):
        images = []
        for data in encoded:
            with Image.open(io.BytesIO(data)) as image:
                images.append(image.convert(self.mode))
        return images


class DALIDecoder:
    def __init__(self, max_batch_size, num_threads=2, *, grayscale=False):
        if (platform.system() != "Linux" or not Path("/.dockerenv").exists()
                or not torch.cuda.is_available() or torch.utils.data.get_worker_info() is not None):
            raise ContractError("DALI requires the main process in the Linux CUDA container")
        try:
            from nvidia.dali import fn, types
            from nvidia.dali.pipeline import Pipeline
        except ImportError:
            raise ContractError("install the pinned DALI GPU runtime in the container") from None
        self.max_batch_size = max_batch_size
        self.pipeline = Pipeline(batch_size=max_batch_size, num_threads=num_threads, device_id=0,
                                 exec_dynamic=True, prefetch_queue_depth=1)
        with self.pipeline:
            data = fn.external_source(name="encoded", device="cpu", dtype=types.UINT8, ndim=1)
            decoded = fn.decoders.image(data, device="mixed", adjust_orientation=False,
                                       output_type=types.GRAY if grayscale else types.RGB)
            self.pipeline.set_outputs(decoded)
        self.pipeline.build()

    def __call__(self, encoded):
        if not encoded or len(encoded) > self.max_batch_size:
            raise ContractError("invalid DALI batch size")
        self.pipeline.feed_input("encoded", [np.frombuffer(data, dtype=np.uint8) for data in encoded])
        output, = self.pipeline.run()
        # Dynamic executor owns the exported buffers; torch's DLPack consumer
        # negotiates the CUDA stream. The contiguous CHW result owns its storage.
        return [torch.from_dlpack(output[i]).permute(2, 0, 1).contiguous() for i in range(len(encoded))]


def make_decoder(settings, batch_size, *, grayscale=False):
    if settings.decoder == "dali":
        return DALIDecoder(batch_size, settings.dali_threads, grayscale=grayscale)
    return PILDecoder(grayscale=grayscale)
