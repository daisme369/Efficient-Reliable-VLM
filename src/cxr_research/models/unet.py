"""U-Net model definition, independent of training and evaluation."""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class ConvBlock(nn.Sequential):
    def __init__(self, incoming: int, outgoing: int):
        super().__init__(nn.Conv2d(incoming, outgoing, 3, padding=1, bias=False),
                         nn.GroupNorm(8, outgoing), nn.ReLU(),
                         nn.Conv2d(outgoing, outgoing, 3, padding=1, bias=False),
                         nn.GroupNorm(8, outgoing), nn.ReLU())


class UNet(nn.Module):
    def __init__(self, base_channels: int = 32):
        super().__init__()
        channels = [base_channels * 2**i for i in range(5)]
        self.down = nn.ModuleList([ConvBlock(1, channels[0])] +
                                 [ConvBlock(channels[i-1], channels[i]) for i in range(1, 5)])
        self.up = nn.ModuleList([ConvBlock(channels[i] + channels[i-1], channels[i-1])
                               for i in range(4, 0, -1)])
        self.output = nn.Conv2d(channels[0], 1, 1)

    def forward(self, image):
        skips = []
        value = image
        for index, block in enumerate(self.down):
            if index:
                value = F.max_pool2d(value, 2)
            value = block(value)
            skips.append(value)
        for block, skip in zip(self.up, reversed(skips[:-1]), strict=True):
            value = F.interpolate(value, size=skip.shape[-2:], mode="nearest")
            value = block(torch.cat([value, skip], dim=1))
        return self.output(value)
