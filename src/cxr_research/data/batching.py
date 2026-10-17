"""Seeded finite-epoch ordering with bounded length buckets."""

from __future__ import annotations

import random
from torch.utils.data import Sampler


class BucketSampler(Sampler):
    def __init__(self, lengths, batch_size, bucket_batches, seed, *, group_by_length=True):
        self.lengths = lengths
        self.batch_size, self.window = batch_size, batch_size * bucket_batches
        self.seed, self.epoch, self.group_by_length = seed, 0, group_by_length

    def set_epoch(self, epoch):
        self.epoch = epoch

    def __len__(self):
        return len(self.lengths)

    def __iter__(self):
        rng = random.Random(self.seed + self.epoch)
        order = list(range(len(self)))
        rng.shuffle(order)
        for start in range(0, len(order), self.window):
            window = order[start:start + self.window]
            if self.group_by_length:
                window.sort(key=lambda i: self.lengths[i], reverse=True)
            batches = [window[i:i + self.batch_size] for i in range(0, len(window), self.batch_size)]
            # Keep an incomplete final batch at the end, never split it across full batches.
            tail = batches.pop() if batches and len(batches[-1]) < self.batch_size else None
            rng.shuffle(batches)
            for batch in batches:
                yield from batch
            if tail:
                yield from tail
