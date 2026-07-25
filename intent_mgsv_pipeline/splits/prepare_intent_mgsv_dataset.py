import argparse
import json
import os
import random
import subprocess
from pathlib import Path

import pandas as pd
from mutagen import File as MutagenFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT
DEFAULT_INPUT = ROOT / "outputs" / "intent_mgsv_dataset" / "intent_mgsv_clean_ready.xlsx"
DEFAULT_OUT_DIR = ROOT / "outputs" / "intent_mgsv_dataset" / "splits"
DOWNLOAD_DIR = ROOT / "DouK-Source" / "Volume" / "Download"
MGSV_EC_COLUMNS = [
    "video_id",
    "music_id",
    "video_start",
    "video_end",
    "music_start",
    "music_end",
    "music_total_duration",
    "video_segment_duration",
    "music_segment_duration",
    "music_path",
    "video_total_duration",
    "video_width",
    "video_height",
    "video_total_frames",
    "video_frame_rate",
    "video_category",
]

SPLIT_RATIOS = {
    "train": 0.70,
    "val": 0.15,
    "test": 0.15,
}


def build_video_index(download_dir: Path) -> dict[str, str]:
    index = {}
    for dirpath, _, filenames in os.walk(download_dir):
        for filename in filenames:
            if filename.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                index[filename] = str(Path(dirpath) / filename)
    return index


def normalize_path(path_value, root: Path) -> str:
    text = str(path_value).strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = root / text.replace("/", os.sep)
    return str(path)


def assign_splits(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    split_map = {}

    for _, group in df.groupby("sync_level", sort=True):
        indices = list(group.index)
        rng.shuffle(indices)

        n = len(indices)
        n_train = round(n * SPLIT_RATIOS["train"])
        n_val = round(n * SPLIT_RATIOS["val"])

        if n >= 3:
            n_train = max(1, min(n - 2, n_train))
            n_val = max(1, min(n - n_train - 1, n_val))
        else:
            n_train = max(1, n - 1)
            n_val = 0

        train_idx = indices[:n_train]
        val_idx = indices[n_train:n_train + n_val]
        test_idx = indices[n_train + n_val:]

        for idx in train_idx:
            split_map[idx] = "train"
        for idx in val_idx:
            split_map[idx] = "val"
        for idx in test_idx:
            split_map[idx] = "test"

    out = df.copy()
    out["split"] = out.index.map(split_map)
    return out


def add_paths(df: pd.DataFrame) -> pd.DataFrame:
    video_index = build_video_index(DOWNLOAD_DIR)
    out = df.copy()
    out["video_path"] = out["video_id"].map(video_index).fillna("")
    out["music_path"] = out["full_song_path"].apply(lambda p: normalize_path(p, ROOT))
    return out


def validate_paths(df: pd.DataFrame):
    missing_videos = [p for p in df["video_path"] if not p or not Path(p).exists()]
    missing_music = [p for p in df["music_path"] if not p or not Path(p).exists()]
    if missing_videos or missing_music:
        raise FileNotFoundError(
            f"Missing media files: videos={len(missing_videos)}, music={len(missing_music)}"
        )


def media_duration(path: str) -> float | None:
    try:
        audio = MutagenFile(path)
        if audio is not None and getattr(audio, "info", None) is not None:
            length = getattr(audio.info, "length", None)
            if length:
                return round(float(length), 3)
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        return round(float(result.stdout.strip()), 3)
    except Exception:
        return None


def first_label(value) -> str:
    text = str(value).strip()
    if not text:
        return "Unknown"
    return text.split("/")[0].strip() or "Unknown"


def to_mgsv_ec_format(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["video_id"] = df["video_id"]
    out["music_id"] = df["music_path"].apply(lambda p: Path(str(p)).stem)
    out["video_start"] = pd.to_numeric(df["video_start"], errors="coerce").fillna(0).round(3)
    out["video_end"] = pd.to_numeric(df["video_end"], errors="coerce").round(3)
    out["music_start"] = pd.to_numeric(df["music_start"], errors="coerce").round(3)
    out["music_end"] = pd.to_numeric(df["music_end"], errors="coerce").round(3)
    detected_duration = pd.to_numeric(df["music_path"].apply(media_duration), errors="coerce")
    fallback_duration = pd.to_numeric(df.get("music_total_duration", ""), errors="coerce")
    out["music_total_duration"] = detected_duration.fillna(fallback_duration).round(3)
    out["video_segment_duration"] = (out["video_end"] - out["video_start"]).round(3)
    out["music_segment_duration"] = (out["music_end"] - out["music_start"]).round(3)
    out["music_path"] = df["music_path"]
    out["video_total_duration"] = pd.to_numeric(df["video_total_duration"], errors="coerce").round(3)
    out["video_width"] = pd.to_numeric(df["video_width"], errors="coerce").astype("Int64")
    out["video_height"] = pd.to_numeric(df["video_height"], errors="coerce").astype("Int64")
    out["video_total_frames"] = pd.to_numeric(df["video_total_frames"], errors="coerce").astype("Int64")
    out["video_frame_rate"] = pd.to_numeric(df["video_frame_rate"], errors="coerce").round(3)
    out["video_category"] = df["usage_scene"].apply(first_label)
    return out[MGSV_EC_COLUMNS]


def write_splits(df: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "all.csv", index=False, encoding="utf-8-sig")
    mgsv_dir = out_dir / "mgsv_ec_format"
    mgsv_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "total": int(len(df)),
        "by_split": {},
        "by_sync_level": {},
    }

    for split in ["train", "val", "test"]:
        part = df[df["split"] == split].copy()
        part.to_csv(out_dir / f"{split}.csv", index=False, encoding="utf-8-sig")
        to_mgsv_ec_format(part).to_csv(mgsv_dir / f"{split}_data.csv", index=False, encoding="utf-8-sig")
        summary["by_split"][split] = {
            "rows": int(len(part)),
            "sync_level": {str(k): int(v) for k, v in part["sync_level"].value_counts().sort_index().items()},
        }

    to_mgsv_ec_format(df).to_csv(mgsv_dir / "all_data.csv", index=False, encoding="utf-8-sig")

    summary["by_sync_level"] = {str(k): int(v) for k, v in df["sync_level"].value_counts().sort_index().items()}
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = pd.read_excel(args.input, keep_default_na=False)
    df = add_paths(df)
    validate_paths(df)
    df = assign_splits(df, args.seed)
    summary = write_splits(df, Path(args.out_dir))

    print(f"input: {args.input}")
    print(f"output_dir: {args.out_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
