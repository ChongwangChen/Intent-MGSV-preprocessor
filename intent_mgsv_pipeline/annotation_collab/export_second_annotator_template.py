from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "MGSV_Master_Dataset.xlsx"
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "inter_annotator" / "annotator_b_template.xlsx"

REANNOTATE_COLUMNS = [
    "emotion",
    "style",
    "usage_scene",
    "seg_scores_3",
    "seg_scores_5",
]

FIXED_COLUMNS = [
    "sync_level",
    "shot_points",
    "shot_points_3",
    "shot_points_5",
    "music_start",
    "music_end",
    "full_song_path",
    "song_title",
    "song_artist",
    "vocal_presence",
    "genre",
]


def _relativize_path(value, root: Path) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return text.replace("/", os.sep)
    try:
        return str(path.relative_to(root))
    except Exception:
        return text


def build_template(df: pd.DataFrame, root: Path, annotator_id: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "source_row_id", range(len(out)))
    out.insert(1, "annotator_id", annotator_id)

    for col in REANNOTATE_COLUMNS:
        if col in out.columns:
            out[col] = ""

    if "song_verified" in out.columns:
        out["song_verified"] = "是"

    if "full_song_path" in out.columns:
        out["full_song_path"] = out["full_song_path"].apply(lambda v: _relativize_path(v, root))
    if "full_music_path" in out.columns:
        out["full_music_path"] = out["full_music_path"].apply(lambda v: _relativize_path(v, root))

    out["second_annotator_note"] = ""
    out["fixed_fields_note"] = (
        "Only annotate emotion/style/usage_scene/seg_scores_3/seg_scores_5. "
        "Do not change sync_level, shot_points, music interval, vocal_presence, or genre."
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--annotator-id", default="B")
    args = parser.parse_args()

    input_path = Path(args.input)
    out_path = Path(args.out)
    root = Path(args.project_root).resolve()

    df = pd.read_excel(input_path, keep_default_na=False)
    template = build_template(df, root, args.annotator_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    template.to_excel(out_path, index=False)

    summary = {
        "input": str(input_path),
        "output": str(out_path),
        "rows": int(len(template)),
        "annotator_id": args.annotator_id,
        "reannotate_columns": [c for c in REANNOTATE_COLUMNS if c in template.columns],
        "fixed_columns": [c for c in FIXED_COLUMNS if c in template.columns],
    }
    summary_path = out_path.with_suffix(".summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
