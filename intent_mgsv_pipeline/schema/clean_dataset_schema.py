import argparse
import os
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT
DEFAULT_INPUT = ROOT / "outputs" / "MGSV_Master_Dataset.xlsx"
DEFAULT_OUT_DIR = ROOT / "outputs" / "intent_mgsv_dataset"

DROP_COLUMNS = [
    "music_grounding_need",
    "song_offset",
    "song_start",
    "song_end",
]

REQUIRED_COLUMNS = [
    "video_id",
    "music_id",
    "full_song_path",
    "music_start",
    "music_end",
    "sync_level",
    "vocal_presence",
    "genre",
    "emotion",
    "style",
    "usage_scene",
]

FRONT_COLUMNS = [
    "video_id",
    "music_id",
    "douyin_video_id",
    "creator_name",
    "video_title",
    "hashtags",
    "full_desc",
    "video_start",
    "video_end",
    "music_start",
    "music_end",
    "full_song_path",
    "song_title",
    "song_artist",
    "genre",
    "sync_level",
    "vocal_presence",
    "emotion",
    "style",
    "usage_scene",
    "shot_points",
    "shot_points_3",
    "shot_points_5",
    "seg_scores_3",
    "seg_scores_5",
    "bpm",
    "rhythm_category",
    "auto_rhythm_points_audio",
    "auto_rhythm_points_visual",
    "match_score",
    "song_verified",
    "recog_confidence",
    "recog_note",
]


def _is_blank(value) -> bool:
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "null", "unmarked"}


def _is_missing_required(column: str, value) -> bool:
    text = str(value).strip()
    low = text.lower()
    if column == "vocal_presence":
        return low not in {"none", "partial", "full"}
    if column == "sync_level":
        return text not in {"0", "1"} and value not in {0, 1}
    return _is_blank(value)


def normalize_sync(value):
    text = str(value).strip().lower()
    if text in {"1", "yes", "y", "true", "是", "卡点", "sync", "soft sync", "hard sync"}:
        return 1
    if text in {"2"}:
        return 1
    if text in {"0", "no", "n", "false", "否", "不是", "非卡点", "ambient"}:
        return 0
    return ""


def coerce_float(series):
    return pd.to_numeric(series, errors="coerce").round(3)


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in ["music_start", "song_offset", "song_start"]:
        if col in df.columns:
            df[col] = coerce_float(df[col])
    for col in ["music_end", "song_end"]:
        if col in df.columns:
            df[col] = coerce_float(df[col])

    if "song_start" in df.columns:
        df["music_start"] = df["song_start"].where(df["song_start"].notna(), df.get("music_start", ""))
    elif "song_offset" in df.columns:
        df["music_start"] = df["song_offset"].where(df["song_offset"].notna(), df.get("music_start", ""))

    if "song_end" in df.columns:
        df["music_end"] = df["song_end"].where(df["song_end"].notna(), df.get("music_end", ""))

    if "sync_level" in df.columns:
        df["sync_level"] = df["sync_level"].apply(normalize_sync)

    for col in DROP_COLUMNS:
        if col in df.columns:
            df = df.drop(columns=[col])

    existing_front = [c for c in FRONT_COLUMNS if c in df.columns]
    remaining = [c for c in df.columns if c not in existing_front]
    df = df[existing_front + remaining]

    return df


def add_quality_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    missing_by_row = []
    for _, row in df.iterrows():
        missing = [c for c in REQUIRED_COLUMNS if c in df.columns and _is_missing_required(c, row.get(c, ""))]
        missing += [c for c in REQUIRED_COLUMNS if c not in df.columns]
        missing_by_row.append("/".join(missing))

    df["missing_required"] = missing_by_row
    df["is_ready"] = df["missing_required"].eq("")
    return df


def write_outputs(df: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    all_df = add_quality_columns(df)
    ready_df = all_df[all_df["is_ready"]].drop(columns=["missing_required", "is_ready"])

    all_xlsx = out_dir / "intent_mgsv_clean_all.xlsx"
    all_csv = out_dir / "intent_mgsv_clean_all.csv"
    ready_xlsx = out_dir / "intent_mgsv_clean_ready.xlsx"
    ready_csv = out_dir / "intent_mgsv_clean_ready.csv"

    all_df.to_excel(all_xlsx, index=False)
    all_df.to_csv(all_csv, index=False, encoding="utf-8-sig")
    ready_df.to_excel(ready_xlsx, index=False)
    ready_df.to_csv(ready_csv, index=False, encoding="utf-8-sig")

    return {
        "all_xlsx": all_xlsx,
        "all_csv": all_csv,
        "ready_xlsx": ready_xlsx,
        "ready_csv": ready_csv,
        "all_rows": len(all_df),
        "ready_rows": len(ready_df),
        "not_ready_rows": len(all_df) - len(ready_df),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)

    df = pd.read_excel(input_path, keep_default_na=False)
    clean_df = clean_dataframe(df)
    result = write_outputs(clean_df, out_dir)

    print(f"input: {input_path}")
    print(f"output_dir: {out_dir}")
    print(f"all_rows: {result['all_rows']}")
    print(f"ready_rows: {result['ready_rows']}")
    print(f"not_ready_rows: {result['not_ready_rows']}")
    print(f"all_xlsx: {result['all_xlsx']}")
    print(f"ready_xlsx: {result['ready_xlsx']}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
