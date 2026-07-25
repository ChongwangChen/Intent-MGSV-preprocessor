from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


TEXT_COLUMNS = [
    "genre",
    "vocal_presence",
    "emotion",
    "style",
    "usage_scene",
    "rhythm_category",
]

NUMERIC_COLUMNS = [
    "sync_level",
    "bpm",
    "video_total_duration",
    "video_width",
    "video_height",
    "video_frame_rate",
    "music_total_duration",
    "video_segment_duration",
    "music_segment_duration",
    "match_score",
]


def _is_blank(value) -> bool:
    text = str(value or "").strip()
    return text == "" or text.lower() in {"nan", "none", "null", "unmarked"}


def split_labels(value) -> list[str]:
    if _is_blank(value):
        return []
    return [part.strip() for part in str(value).replace(",", "/").split("/") if part.strip()]


def parse_float(value, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out else default
    except Exception:
        return default


@dataclass
class TabularFeatureConfig:
    vocab: dict[str, list[str]]
    numeric_mean: dict[str, float]
    numeric_std: dict[str, float]
    max_m_duration: float = 400.0

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "TabularFeatureConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)


class TabularFeatureEncoder:
    def __init__(self, config: TabularFeatureConfig):
        self.config = config
        self.feature_names = self._build_feature_names()

    @classmethod
    def fit(cls, df: pd.DataFrame, max_m_duration: float = 400.0) -> "TabularFeatureEncoder":
        vocab: dict[str, list[str]] = {}
        for col in TEXT_COLUMNS:
            labels: set[str] = set()
            if col in df.columns:
                for value in df[col].tolist():
                    labels.update(split_labels(value))
            vocab[col] = sorted(labels)

        numeric_mean: dict[str, float] = {}
        numeric_std: dict[str, float] = {}
        for col in NUMERIC_COLUMNS:
            values = pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(dtype=float)
            values = values.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
            mean = float(values.mean()) if len(values) else 0.0
            std = float(values.std()) if len(values) else 1.0
            numeric_mean[col] = mean
            numeric_std[col] = std if std > 1e-6 else 1.0
        return cls(TabularFeatureConfig(vocab, numeric_mean, numeric_std, max_m_duration))

    def _build_feature_names(self) -> list[str]:
        names: list[str] = []
        for col in NUMERIC_COLUMNS:
            names.append(f"num::{col}")
        for col in TEXT_COLUMNS:
            for label in self.config.vocab.get(col, []):
                names.append(f"{col}::{label}")
        return names

    @property
    def dim(self) -> int:
        return len(self.feature_names)

    def transform_row(self, row: pd.Series) -> np.ndarray:
        out = np.zeros(self.dim, dtype=np.float32)
        offset = 0
        for col in NUMERIC_COLUMNS:
            value = parse_float(row.get(col, 0.0))
            mean = self.config.numeric_mean.get(col, 0.0)
            std = self.config.numeric_std.get(col, 1.0)
            out[offset] = (value - mean) / std
            offset += 1
        for col in TEXT_COLUMNS:
            labels = set(split_labels(row.get(col, "")))
            vocab = self.config.vocab.get(col, [])
            for i, label in enumerate(vocab):
                if label in labels:
                    out[offset + i] = 1.0
            offset += len(vocab)
        return out

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if len(df) == 0:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self.transform_row(row) for _, row in df.iterrows()]).astype(np.float32)

    def targets(self, df: pd.DataFrame) -> np.ndarray:
        rows = []
        max_d = float(self.config.max_m_duration)
        for _, row in df.iterrows():
            start = parse_float(row.get("music_start", 0.0))
            end = parse_float(row.get("music_end", start))
            end = min(max(start, end), max_d)
            center = ((start + end) / 2.0) / max_d
            width = max(0.001, (end - start) / max_d)
            rows.append([center, width])
        return np.asarray(rows, dtype=np.float32)

    def denormalize(self, pred_center_width: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        pred = np.asarray(pred_center_width, dtype=np.float32)
        center = np.clip(pred[:, 0], 0.0, 1.0)
        width = np.clip(pred[:, 1], 0.001, 1.0)
        max_d = float(self.config.max_m_duration)
        start = np.clip((center - width / 2.0) * max_d, 0.0, max_d)
        end = np.clip((center + width / 2.0) * max_d, start + 0.001, max_d)
        return start, end


def load_split_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False)


def iter_label_values(df: pd.DataFrame, columns: Iterable[str] = TEXT_COLUMNS) -> dict[str, int]:
    counts: dict[str, int] = {}
    for col in columns:
        if col not in df.columns:
            continue
        for value in df[col].tolist():
            for label in split_labels(value):
                key = f"{col}::{label}"
                counts[key] = counts.get(key, 0) + 1
    return counts
