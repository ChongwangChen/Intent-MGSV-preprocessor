import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT
DEFAULT_SPLIT_DIR = ROOT / "outputs" / "intent_mgsv_dataset" / "splits" / "mgsv_ec_format"


def check_split(csv_path: Path, max_m_duration: float) -> dict:
    df = pd.read_csv(csv_path, keep_default_na=False)
    start = pd.to_numeric(df["music_start"], errors="coerce")
    end = pd.to_numeric(df["music_end"], errors="coerce")
    video_start = pd.to_numeric(df["video_start"], errors="coerce")
    video_end = pd.to_numeric(df["video_end"], errors="coerce")
    music_duration = pd.to_numeric(df["music_total_duration"], errors="coerce")

    center = (start + end.clip(upper=max_m_duration)) / 2.0 / max_m_duration
    width = (end.clip(upper=max_m_duration) - start) / max_m_duration

    bad_numeric = int((start.isna() | end.isna() | music_duration.isna()).sum())
    bad_order = int((end <= start).sum())
    bad_video_order = int((video_end <= video_start).sum())
    beyond_max_duration = int((end > max_m_duration).sum())
    beyond_music_duration = int((end > music_duration + 0.05).sum())
    bad_span = int(((center < 0) | (center > 1) | (width <= 0) | (width > 1)).sum())

    return {
        "file": str(csv_path),
        "rows": int(len(df)),
        "bad_numeric": bad_numeric,
        "bad_order": bad_order,
        "bad_video_order": bad_video_order,
        "beyond_max_duration": beyond_max_duration,
        "beyond_music_duration": beyond_music_duration,
        "bad_span_after_clamp": bad_span,
        "max_music_end": round(float(end.max()), 3),
        "max_music_total_duration": round(float(music_duration.max()), 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--max-m-duration", type=float, default=400.0)
    args = parser.parse_args()

    split_dir = Path(args.split_dir)
    failed = False
    for name in ["train_data.csv", "val_data.csv", "test_data.csv", "all_data.csv"]:
        result = check_split(split_dir / name, args.max_m_duration)
        print(result)
        failed = failed or any(
            result[k] > 0
            for k in ["bad_numeric", "bad_order", "bad_video_order", "beyond_music_duration", "bad_span_after_clamp"]
        )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
