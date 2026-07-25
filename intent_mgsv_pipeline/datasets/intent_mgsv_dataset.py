import ast
from pathlib import Path
from typing import Any

import pandas as pd


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_multi_label(value: Any) -> list[str]:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "unmarked"}:
        return []
    return [part.strip() for part in text.split("/") if part.strip()]


def parse_points(value: Any) -> list[float]:
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "NAN"}:
        return []
    if "/" in text:
        parts = text.split("/")
    else:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [float(x) for x in parsed]
        except Exception:
            pass
        parts = text.replace(",", "/").split("/")

    points = []
    for part in parts:
        try:
            points.append(float(part.strip()))
        except Exception:
            pass
    return points


def parse_scores(value: Any) -> list[int]:
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "NAN"}:
        return []
    scores = []
    for part in text.replace(",", "/").split("/"):
        try:
            scores.append(int(float(part.strip())))
        except Exception:
            pass
    return scores


class IntentMGSVRowDataset:
    """
    Lightweight row-level dataset for the Intent-MGSV CSV files.

    This class intentionally avoids feature loading. It is the stable semantic
    layer above the spreadsheet and below future model-specific dataloaders.
    """

    def __init__(self, csv_path: str | Path, max_m_duration: float = 400.0):
        self.csv_path = Path(csv_path)
        self.max_m_duration = float(max_m_duration)
        self.df = pd.read_csv(self.csv_path, keep_default_na=False)

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.df.iloc[idx]
        music_start = parse_float(row["music_start"])
        music_end = parse_float(row["music_end"])
        clamped_end = min(music_end, self.max_m_duration)
        center = (music_start + clamped_end) / 2.0 / self.max_m_duration
        width = (clamped_end - music_start) / self.max_m_duration

        return {
            "ids": {
                "video_id": str(row.get("video_id", "")),
                "music_id": str(row.get("music_id", "")),
                "douyin_video_id": str(row.get("douyin_video_id", "")),
            },
            "paths": {
                "video_path": str(row.get("video_path", "")),
                "music_path": str(row.get("music_path", row.get("full_song_path", ""))),
                "full_song_path": str(row.get("full_song_path", "")),
            },
            "grounding": {
                "video_start": parse_float(row.get("video_start", 0.0)),
                "video_end": parse_float(row.get("video_end", 0.0)),
                "music_start": music_start,
                "music_end": music_end,
                "target_center_width": [center, width],
            },
            "rhythm": {
                "sync_level": int(parse_float(row.get("sync_level", 0))),
                "bpm": parse_float(row.get("bpm", 0.0)),
                "rhythm_category": str(row.get("rhythm_category", "")),
                "audio_points": parse_points(row.get("auto_rhythm_points_audio", "")),
                "visual_points": parse_points(row.get("shot_points", row.get("auto_rhythm_points_visual", ""))),
                "visual_points_3": parse_points(row.get("shot_points_3", "")),
                "visual_points_5": parse_points(row.get("shot_points_5", "")),
                "seg_scores_3": parse_scores(row.get("seg_scores_3", "")),
                "seg_scores_5": parse_scores(row.get("seg_scores_5", "")),
            },
            "intent": {
                "genre": str(row.get("genre", "")).strip(),
                "vocal_presence": str(row.get("vocal_presence", "")).strip(),
                "emotion": parse_multi_label(row.get("emotion", "")),
                "style": parse_multi_label(row.get("style", "")),
                "usage_scene": parse_multi_label(row.get("usage_scene", "")),
            },
            "metadata": {
                "creator_name": str(row.get("creator_name", "")),
                "video_title": str(row.get("video_title", "")),
                "song_title": str(row.get("song_title", "")),
                "song_artist": str(row.get("song_artist", "")),
                "match_score": parse_float(row.get("match_score", 0.0)),
            },
        }
