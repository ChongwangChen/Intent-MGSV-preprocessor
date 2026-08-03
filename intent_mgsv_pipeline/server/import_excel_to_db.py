from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    dumps_json,
    init_db,
    integer,
    log_event,
    number,
    scalar,
    text,
)
from intent_mgsv_pipeline.server.verification import is_song_verified


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "MGSV_Master_Dataset.xlsx"


def _row_dict(row: pd.Series) -> dict[str, object]:
    return {str(k): scalar(v) for k, v in row.to_dict().items()}


def _upsert_song(
    conn,
    data: dict[str, object],
    *,
    update_existing: bool,
) -> int | None:
    title = text(data.get("song_title"))
    artist = text(data.get("song_artist"))
    full_song_path = text(data.get("full_song_path")) or None
    qq_song_mid = text(data.get("qq_song_mid")) or None
    if not any([title, artist, full_song_path, qq_song_mid]):
        return None

    existing = None
    if qq_song_mid:
        existing = conn.execute("SELECT id FROM songs WHERE qq_song_mid=?", (qq_song_mid,)).fetchone()
    if existing is None and full_song_path:
        existing = conn.execute("SELECT id FROM songs WHERE full_song_path=?", (full_song_path,)).fetchone()

    row_json = dumps_json(data)
    if existing:
        song_id = int(existing["id"])
        if update_existing:
            conn.execute(
                """
                UPDATE songs
                SET title=?, artist=?, full_song_path=?, qq_song_mid=?,
                    row_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    title,
                    artist,
                    full_song_path,
                    qq_song_mid,
                    row_json,
                    song_id,
                ),
            )
        return song_id

    cur = conn.execute(
        """
        INSERT INTO songs(title, artist, full_song_path, qq_song_mid, row_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, artist, full_song_path, qq_song_mid, row_json),
    )
    return int(cur.lastrowid)


def import_excel(
    input_path: Path,
    db_path: Path,
    annotator_id: str,
    replace: bool = False,
    update_existing: bool = False,
) -> dict[str, int]:
    init_db(db_path)
    df = pd.read_excel(input_path, keep_default_na=False)
    update_existing = bool(update_existing or replace)

    imported = 0
    with __import__("intent_mgsv_pipeline.server.db", fromlist=["connect"]).connect(db_path) as conn:
        if replace:
            conn.execute("DELETE FROM annotation_assignments")
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM songs")
            conn.execute("DELETE FROM videos")

        for _, row in df.iterrows():
            data = _row_dict(row)
            video_id = text(data.get("video_id"))
            if not video_id:
                continue

            song_id = _upsert_song(
                conn,
                data,
                update_existing=update_existing,
            )
            video_row = conn.execute(
                "SELECT * FROM videos WHERE video_id=?",
                (video_id,),
            ).fetchone()
            video_values = (
                video_id,
                text(data.get("douyin_video_id")),
                text(data.get("video_path")),
                text(data.get("full_music_path")),
                text(data.get("creator_name")),
                text(data.get("video_title")),
                text(data.get("hashtags")),
                text(data.get("full_desc")),
                number(data.get("video_total_duration")),
                integer(data.get("video_width")),
                integer(data.get("video_height")),
                integer(data.get("video_total_frames")),
                number(data.get("video_frame_rate")),
                dumps_json(data),
            )
            if video_row:
                db_video_id = int(video_row["id"])
                owner_annotation_exists = conn.execute(
                    """
                    SELECT 1
                    FROM annotations
                    WHERE video_id=? AND annotator_id=?
                    """,
                    (db_video_id, annotator_id),
                ).fetchone()
                if update_existing:
                    conn.execute(
                        """
                        UPDATE videos
                        SET douyin_video_id=?, video_path=?, clip_audio_path=?,
                            creator_name=?, video_title=?, hashtags=?,
                            full_desc=?, duration=?, width=?, height=?,
                            total_frames=?, frame_rate=?, row_json=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        video_values[1:] + (db_video_id,),
                    )
                elif owner_annotation_exists is None:
                    conn.execute(
                        """
                        UPDATE videos
                        SET douyin_video_id=COALESCE(NULLIF(?, ''), douyin_video_id),
                            video_path=COALESCE(NULLIF(?, ''), video_path),
                            clip_audio_path=COALESCE(NULLIF(?, ''), clip_audio_path),
                            creator_name=COALESCE(NULLIF(?, ''), creator_name),
                            video_title=COALESCE(NULLIF(?, ''), video_title),
                            hashtags=COALESCE(NULLIF(?, ''), hashtags),
                            full_desc=COALESCE(NULLIF(?, ''), full_desc),
                            duration=COALESCE(?, duration),
                            width=COALESCE(?, width),
                            height=COALESCE(?, height),
                            total_frames=COALESCE(?, total_frames),
                            frame_rate=COALESCE(?, frame_rate),
                            row_json=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        video_values[1:] + (db_video_id,),
                    )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO videos(
                        video_id, douyin_video_id, video_path, clip_audio_path, creator_name,
                        video_title, hashtags, full_desc, duration, width, height, total_frames,
                        frame_rate, row_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    video_values,
                )
                db_video_id = int(cur.lastrowid)

            status = (
                "completed"
                if is_song_verified(data.get("song_verified"))
                else "in_progress"
            )
            conflict_action = (
                """
                ON CONFLICT(video_id, annotator_id) DO UPDATE SET
                    song_id=excluded.song_id,
                    music_id=excluded.music_id,
                    sync_level=excluded.sync_level,
                    music_start=excluded.music_start,
                    music_end=excluded.music_end,
                    shot_points=excluded.shot_points,
                    shot_points_3=excluded.shot_points_3,
                    shot_points_5=excluded.shot_points_5,
                    emotion=excluded.emotion,
                    style=excluded.style,
                    usage_scene=excluded.usage_scene,
                    seg_scores_3=excluded.seg_scores_3,
                    seg_scores_5=excluded.seg_scores_5,
                    vocal_presence=excluded.vocal_presence,
                    genre=excluded.genre,
                    song_verified=excluded.song_verified,
                    recog_confidence=excluded.recog_confidence,
                    recog_note=excluded.recog_note,
                    match_score=excluded.match_score,
                    status=excluded.status,
                    row_json=excluded.row_json,
                    updated_at=CURRENT_TIMESTAMP
                """
                if update_existing
                else "ON CONFLICT(video_id, annotator_id) DO NOTHING"
            )
            conn.execute(
                f"""
                INSERT INTO annotations(
                    video_id, song_id, annotator_id, music_id, sync_level, music_start, music_end,
                    shot_points, shot_points_3, shot_points_5, emotion, style, usage_scene,
                    seg_scores_3, seg_scores_5, vocal_presence, genre, song_verified,
                    recog_confidence, recog_note, match_score, status, row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                {conflict_action}
                """,
                (
                    db_video_id,
                    song_id,
                    annotator_id,
                    text(data.get("music_id")),
                    text(data.get("sync_level")),
                    number(data.get("music_start")),
                    number(data.get("music_end")),
                    text(data.get("shot_points")),
                    text(data.get("shot_points_3")),
                    text(data.get("shot_points_5")),
                    text(data.get("emotion")),
                    text(data.get("style")),
                    text(data.get("usage_scene")),
                    text(data.get("seg_scores_3")),
                    text(data.get("seg_scores_5")),
                    text(data.get("vocal_presence")),
                    text(data.get("genre")),
                    text(data.get("song_verified")),
                    text(data.get("recog_confidence")),
                    text(data.get("recog_note")),
                    number(data.get("match_score")),
                    status,
                    dumps_json(data),
                ),
            )
            imported += 1

        log_event(
            conn,
            "import_excel",
            actor=annotator_id,
            target_type="database",
            target_id=str(db_path),
            payload={
                "input": str(input_path),
                "rows": imported,
                "replace": replace,
                "update_existing": update_existing,
            },
        )

    return {"rows": imported, "source_rows": len(df)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MGSV Excel annotations into the server database.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--annotator-id", default="owner")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Overwrite existing video/song/annotation rows. Default import is append-only.",
    )
    args = parser.parse_args()

    result = import_excel(
        Path(args.input),
        Path(args.db),
        args.annotator_id,
        args.replace,
        args.update_existing,
    )
    print(f"Imported {result['rows']} rows from {result['source_rows']} source rows")
    print(f"Database: {Path(args.db).resolve()}")


if __name__ == "__main__":
    main()
