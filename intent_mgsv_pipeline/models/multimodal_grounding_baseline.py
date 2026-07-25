from __future__ import annotations

import torch
from torch import nn


def masked_mean(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask = mask.unsqueeze(-1).to(x.dtype)
    denom = mask.sum(dim=1).clamp_min(1.0)
    return (x * mask).sum(dim=1) / denom


class MultimodalGroundingBaseline(nn.Module):
    """
    Lightweight video-to-music grounding model.

    It treats the video as a query and the full song as a time sequence. The
    predicted interval is a normalized [center, width] pair.
    """

    def __init__(
        self,
        video_dim: int,
        audio_dim: int,
        hidden_dim: int = 128,
        dropout: float = 0.15,
    ):
        super().__init__()
        self.video_proj = nn.Sequential(
            nn.Linear(video_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.audio_proj = nn.Sequential(
            nn.Linear(audio_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.query = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
        )
        self.width_head = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
            nn.Sigmoid(),
        )

    def forward(
        self,
        video_feats: torch.Tensor,
        video_mask: torch.Tensor,
        audio_feats: torch.Tensor,
        audio_mask: torch.Tensor,
        audio_times: torch.Tensor,
        max_m_duration: float = 400.0,
    ) -> dict[str, torch.Tensor]:
        v = self.video_proj(video_feats)
        a = self.audio_proj(audio_feats)
        v_global = self.query(masked_mean(v, video_mask))

        scores = torch.einsum("bd,btd->bt", v_global, a) / (a.shape[-1] ** 0.5)
        scores = scores.masked_fill(audio_mask <= 0, -1e4)
        attn = torch.softmax(scores, dim=1)

        center_seconds = (attn * audio_times).sum(dim=1)
        center = (center_seconds / float(max_m_duration)).clamp(0.0, 1.0)
        audio_context = torch.einsum("bt,btd->bd", attn, a)
        width = self.width_head(torch.cat([v_global, audio_context], dim=-1)).squeeze(-1)
        width = width.clamp(0.001, 1.0)
        pred = torch.stack([center, width], dim=-1)
        return {"pred": pred, "scores": scores, "attn": attn}


def grounding_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    center_loss = nn.functional.smooth_l1_loss(pred[:, 0], target[:, 0])
    width_loss = nn.functional.smooth_l1_loss(pred[:, 1], target[:, 1])
    return center_loss + width_loss
