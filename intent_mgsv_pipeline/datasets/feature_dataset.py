from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURE_ROOT = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "features"


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out else default
    except Exception:
        return default


def _feature_path(feature_root: Path, kind: str, source: str) -> Path:
    return feature_root / kind / f"{stable_id(str(source))}.npz"


def _sample_sequence(feats: np.ndarray, times: np.ndarray, max_steps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    feats = np.asarray(feats, dtype="float32")
    times = np.asarray(times, dtype="float32")
    if feats.ndim != 2:
        feats = feats.reshape(max(1, feats.shape[0]), -1).astype("float32")
    if len(times) != len(feats):
        times = np.arange(len(feats), dtype="float32")

    valid_len = min(len(feats), max_steps)
    if len(feats) > max_steps:
        idx = np.linspace(0, len(feats) - 1, max_steps).round().astype(int)
        feats = feats[idx]
        times = times[idx]
    mask = np.zeros(max_steps, dtype="float32")
    mask[:valid_len] = 1.0

    out_feats = np.zeros((max_steps, feats.shape[1]), dtype="float32")
    out_times = np.zeros(max_steps, dtype="float32")
    out_feats[:valid_len] = feats[:valid_len]
    out_times[:valid_len] = times[:valid_len]
    return out_feats, out_times, mask


class IntentMGSVFeatureDataset(Dataset):
    def __init__(
        self,
        csv_path: str | Path,
        feature_root: str | Path = DEFAULT_FEATURE_ROOT,
        max_audio_steps: int = 256,
        max_video_steps: int = 32,
        max_m_duration: float = 400.0,
    ):
        self.csv_path = Path(csv_path)
        self.feature_root = Path(feature_root)
        self.max_audio_steps = int(max_audio_steps)
        self.max_video_steps = int(max_video_steps)
        self.max_m_duration = float(max_m_duration)
        self.df = pd.read_csv(self.csv_path, keep_default_na=False)

    def __len__(self) -> int:
        return len(self.df)

    def _load_npz(self, path: Path) -> tuple[np.ndarray, np.ndarray]:
        if not path.exists():
            raise FileNotFoundError(path)
        data = np.load(path)
        return data["feats"].astype("float32"), data["times"].astype("float32")

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.df.iloc[idx]
        video_source = str(row.get("video_path", "")) or str(row.get("video_id", ""))
        audio_source = str(row.get("full_song_path", "")) or str(row.get("music_path", "")) or str(row.get("music_id", ""))

        v_feats, v_times = self._load_npz(_feature_path(self.feature_root, "video", video_source))
        a_feats, a_times = self._load_npz(_feature_path(self.feature_root, "audio", audio_source))
        v_feats, v_times, v_mask = _sample_sequence(v_feats, v_times, self.max_video_steps)
        a_feats, a_times, a_mask = _sample_sequence(a_feats, a_times, self.max_audio_steps)

        music_start = parse_float(row.get("music_start", 0.0))
        music_end = parse_float(row.get("music_end", music_start))
        music_end = min(max(music_end, music_start + 0.001), self.max_m_duration)
        center = ((music_start + music_end) / 2.0) / self.max_m_duration
        width = (music_end - music_start) / self.max_m_duration

        return {
            "video_feats": torch.from_numpy(v_feats),
            "video_times": torch.from_numpy(v_times),
            "video_mask": torch.from_numpy(v_mask),
            "audio_feats": torch.from_numpy(a_feats),
            "audio_times": torch.from_numpy(a_times),
            "audio_mask": torch.from_numpy(a_mask),
            "target": torch.tensor([center, width], dtype=torch.float32),
            "music_start": torch.tensor(music_start, dtype=torch.float32),
            "music_end": torch.tensor(music_end, dtype=torch.float32),
            "video_id": str(row.get("video_id", "")),
            "music_id": str(row.get("music_id", "")),
        }


def feature_collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
    tensor_keys = [
        "video_feats",
        "video_times",
        "video_mask",
        "audio_feats",
        "audio_times",
        "audio_mask",
        "target",
        "music_start",
        "music_end",
    ]
    out: dict[str, Any] = {}
    for key in tensor_keys:
        out[key] = torch.stack([item[key] for item in batch], dim=0)
    out["video_id"] = [item["video_id"] for item in batch]
    out["music_id"] = [item["music_id"] for item in batch]
    return out
