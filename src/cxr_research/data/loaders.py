"""Task loaders with CPU read-ahead and main-process accelerator preprocessing."""

import torch
from torch.utils.data import DataLoader

from cxr_research.data.datasets import SegmentationDataset
from cxr_research.data.shards import ShardDataset, encoded_collate
from cxr_research.preprocessing.decoding import make_decoder
from cxr_research.preprocessing.segmentation import transform_pair
from cxr_research.preprocessing.tensor_segmentation import transform_tensor_pair


def loader_options(config):
    options = {"num_workers": config.io.num_workers,
               "pin_memory": config.io.pin_memory and config.device == "cuda"}
    if config.io.num_workers:
        # CUDA initialized in the parent must never be inherited by fork workers.
        options.update(prefetch_factor=config.io.prefetch_factor, multiprocessing_context="spawn")
    return options


class SegmentationLoader:
    def __init__(self, config, manifest, split, *, epoch=0, store=None, decoder=None):
        self.config, self.epoch, self.training = config, epoch, split == "train"
        samples = manifest.select("unet", split)
        self.count = len(samples)
        self.sharded = store is not None
        dataset = ShardDataset(store, samples) if self.sharded else SegmentationDataset(
            manifest, samples, config.unet, training=self.training, seed=config.seed)
        if not self.sharded:
            dataset.epoch = epoch
        self.decoder = decoder if decoder is not None else (
            make_decoder(config.io, config.train.batch_size, grayscale=True) if self.sharded else None)
        self.loader = DataLoader(dataset, batch_size=config.train.batch_size, shuffle=self.training,
                                 generator=torch.Generator().manual_seed(config.seed + epoch),
                                 collate_fn=encoded_collate if self.sharded else None, **loader_options(config))

    def __len__(self):
        return len(self.loader)

    def __iter__(self):
        for batch in self.loader:
            if not self.sharded:
                yield batch
                continue
            examples = batch["examples"]
            images = self.decoder([e["encoded_image"] for e in examples])
            masks = self.decoder([e["encoded_mask"] for e in examples])
            output = []
            for image, mask, example in zip(images, masks, examples):
                seed = self.config.seed + self.epoch * self.count + example["index"]
                if self.config.io.decoder == "dali":
                    output.append(transform_tensor_pair(image, mask, self.config.unet,
                                                        training=self.training, seed=seed))
                else:
                    output.append(transform_pair(image, mask, self.config.unet,
                                                 training=self.training, seed=seed)[:3])
            yield tuple(torch.stack(values) for values in zip(*output))
