"""Trainer adapter that decodes after CPU workers and preserves label accounting."""

from transformers import Trainer

from cxr_research.data.batching import BucketSampler


class CachedTrainer(Trainer):
    def __init__(self, *args, image_preparer, io_settings, **kwargs):
        self.image_preparer, self.io_settings = image_preparer, io_settings
        super().__init__(*args, **kwargs)

    def _prepare_inputs(self, inputs):
        if "_encoded_images" in inputs:
            inputs = self.image_preparer(inputs)
        return super()._prepare_inputs(inputs)

    def _get_train_sampler(self, train_dataset=None):
        dataset = self.train_dataset if train_dataset is None else train_dataset
        return BucketSampler(dataset.lengths, self.args.per_device_train_batch_size,
                             self.io_settings.bucket_batches, self.args.data_seed,
                             group_by_length=self.io_settings.group_by_length)
