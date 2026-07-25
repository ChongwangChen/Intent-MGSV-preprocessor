from __future__ import annotations

import torch
from torch import nn


class TabularGroundingMLP(nn.Module):
    """
    Minimal Intent-MGSV baseline.

    Input: structured intent/rhythm metadata features.
    Output: normalized [center, width] for the music grounding interval.
    """

    def __init__(self, input_dim: int, hidden_dim: int = 128, dropout: float = 0.15):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 2),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def interval_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    center_loss = nn.functional.smooth_l1_loss(pred[:, 0], target[:, 0])
    width_loss = nn.functional.smooth_l1_loss(pred[:, 1], target[:, 1])
    return center_loss + width_loss
