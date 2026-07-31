from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from intent_mgsv_pipeline.server.db import DEFAULT_DB, connect, init_db, loads_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "server" / "MGSV_Master_Dataset.export.xlsx"


EXPLICIT_FIELDS = {
    "music_id": "music_id",
    "sync_level": "sync_level",
    "music_start": "music_start",
    "music_end": "music_end",
    "shot_points": "shot_points",
    "shot_points_3": "shot_points_3",
    "shot_points_5": "shot_points_5",
    "emotion": "emotion",
    "style": "style",
    "usage_scene": "usage_scene",
    "seg_scores_3": "seg_scores_3",
    "seg_scores_5": "seg_scores_5",
    "vocal_presence": "vocal_presence",
    "genre": "genre",
    "song_verified": "song_verified",
    "recog_confidence": "recog_confidence",
    "recog_note": "recog_note",
    "match_score": "match_score",
}


def load_annotation_dataframe(
    db_path: Path,
    annotator_id: str | None = "owner",
) -> pd.DataFrame:
    init_db(db_path)
    where = ""
    params: tuple[object, ...] = ()
    if annotator_id:
        where = "WHERE a.annotator_id=?"
        params = (annotator_id,)

    sql = f"""
        SELECT
            v.video_id AS db_video_key,
            v.row_json AS video_row_json,
            s.title AS song_title_db,
            s.artist AS song_artist_db,
            s.full_song_path AS full_song_path_db,
            s.qq_song_mid AS qq_song_mid_db,
            a.*
        FROM annotations a
        JOIN videos v ON v.id = a.video_id
        LEFT JOIN songs s ON s.id = a.song_id
        {where}
        ORDER BY v.id
    """

    rows = []
    with connect(db_path) as conn:
        for record in conn.execute(sql, params).fetchall():
            row = loads_json(record["video_row_json"])
            row.update(loads_json(record["row_json"]))
            row["video_id"] = row.get("video_id") or record["db_video_key"]
            for db_col, out_col in EXPLICIT_FIELDS.items():
                value = record[db_col]
                if value is not None:
                    row[out_col] = value
            if record["song_title_db"]:
                row["song_title"] = record["song_title_db"]
            if record["song_artist_db"]:
                row["song_artist"] = record["song_artist_db"]
            if record["full_song_path_db"]:
                row["full_song_path"] = record["full_song_path_db"]
            if record["qq_song_mid_db"]:
                row["qq_song_mid"] = record["qq_song_mid_db"]
            row["annotator_id"] = record["annotator_id"]
            row["annotation_status"] = record["status"]
            rows.append(row)

    return pd.DataFrame(rows)


def export_db(db_path: Path, out_path: Path, annotator_id: str | None = "owner") -> dict[str, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = load_annotation_dataframe(db_path, annotator_id)
    if out_path.suffix.lower() == ".csv":
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(out_path, index=False)
    return {"rows": len(df), "columns": len(df.columns)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export server database annotations to Excel or CSV.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--annotator-id", default="owner", help="Use empty string to export all annotators.")
    args = parser.parse_args()

    annotator_id = args.annotator_id.strip() or None
    result = export_db(Path(args.db), Path(args.out), annotator_id)
    print(f"Exported {result['rows']} rows and {result['columns']} columns")
    print(f"Output: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
