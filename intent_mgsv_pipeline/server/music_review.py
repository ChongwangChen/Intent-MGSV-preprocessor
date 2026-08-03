from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.music_preparation.alignment import align_video_to_song
from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    connect,
    init_db,
    loads_json,
    log_event,
)


REVIEWABLE_STATUSES = ("ready_for_review", "needs_review")
REALIGN_PRESETS: dict[str, dict[str, Any]] = {
    "标准": {},
    "精细（推荐）": {
        "max_video_duration": 180.0,
        "window_duration": 12.0,
        "max_windows": 9,
        "hop_length": 512,
        "candidate_count": 5,
        "candidate_separation": 5.0,
        "cluster_tolerance": 0.9,
    },
    "短片段": {
        "max_video_duration": 120.0,
        "window_duration": 7.0,
        "max_windows": 10,
        "hop_length": 512,
        "candidate_count": 6,
        "candidate_separation": 4.0,
        "minimum_window_score": 0.14,
        "cluster_tolerance": 1.3,
    },
}


def music_review_progress(
    db_path: Path = DEFAULT_DB,
    reviewer_id: str = "owner",
) -> dict[str, int]:
    init_db(db_path)
    with connect(db_path) as conn:
        total = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM music_preparations p
                JOIN videos v ON v.id=p.video_id
                WHERE p.status IN ('ready_for_review', 'needs_review', 'verified')
                  AND v.deleted_at IS NULL
                """
            ).fetchone()[0]
        )
        completed = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM song_reviews r
                JOIN videos v ON v.id=r.video_id
                WHERE r.reviewer_id=?
                  AND r.status='completed'
                  AND v.deleted_at IS NULL
                """,
                (reviewer_id,),
            ).fetchone()[0]
        )
    return {
        "completed": completed,
        "total": total,
        "remaining": max(0, total - completed),
    }


def _review_record(row: Any) -> dict[str, Any]:
    result = loads_json(row["video_row_json"])
    for key in row.keys():
        if key != "video_row_json" and row[key] is not None:
            result[key] = row[key]
    return result


def _review_query() -> str:
    return """
        SELECT
            v.id AS video_db_id, v.video_id, v.video_path, v.duration,
            v.creator_name, v.video_title, v.row_json AS video_row_json,
            p.song_id,
            COALESCE(p.recognized_title, s.title) AS recognized_title,
            COALESCE(p.recognized_artist, s.artist) AS recognized_artist,
            p.recognition_confidence, p.recognition_votes,
            p.genre_suggestion, p.genre_source, p.genre_confidence,
            COALESCE(p.qq_song_mid, s.qq_song_mid) AS qq_song_mid,
            p.qq_match_score, p.download_source,
            COALESCE(p.full_song_path, s.full_song_path) AS full_song_path,
            p.song_offset, p.video_audio_start,
            p.aligned_duration, p.match_score,
            p.status AS preparation_status, p.error,
            r.status AS review_status, r.corrected_offset,
            r.final_genre, r.note
        FROM music_preparations p
        JOIN videos v ON v.id=p.video_id
        LEFT JOIN songs s ON s.id=p.song_id
        LEFT JOIN song_reviews r
          ON r.video_id=v.id AND r.reviewer_id=?
    """


def claim_next_music_review(
    db_path: Path = DEFAULT_DB,
    reviewer_id: str = "owner",
) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            _review_query()
            + """
            WHERE r.status='in_progress'
              AND v.deleted_at IS NULL
            ORDER BY r.updated_at DESC
            LIMIT 1
            """,
            (reviewer_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                _review_query()
                + """
                WHERE p.status IN ('ready_for_review', 'needs_review')
                  AND (
                      r.video_id IS NULL
                      OR r.status IN (
                          'alignment_rejected',
                          'song_rejected'
                      )
                  )
                  AND v.deleted_at IS NULL
                ORDER BY
                    CASE p.status WHEN 'ready_for_review' THEN 0 ELSE 1 END,
                    p.match_score DESC,
                    v.id
                LIMIT 1
                """,
                (reviewer_id,),
            ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            """
            INSERT INTO song_reviews(video_id, reviewer_id, song_id, status)
            VALUES (?, ?, ?, 'in_progress')
            ON CONFLICT(video_id, reviewer_id) DO UPDATE SET
                song_id=excluded.song_id,
                status='in_progress',
                updated_at=CURRENT_TIMESTAMP
            """,
            (row["video_db_id"], reviewer_id, row["song_id"]),
        )
        conn.commit()
        return _review_record(row)


def get_music_review_record(
    db_path: Path,
    reviewer_id: str,
    video_id: str,
) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            _review_query()
            + """
            WHERE v.video_id=?
              AND v.deleted_at IS NULL
            LIMIT 1
            """,
            (reviewer_id, video_id),
        ).fetchone()
    return _review_record(row) if row is not None else None


def _runtime_media_path(value: Any) -> Path:
    path = Path(str(value or "").strip())
    return path if path.is_absolute() else PATHS.project_root / path


def realign_music_review(
    db_path: Path,
    reviewer_id: str,
    video_id: str,
    *,
    preset: str = "精细（推荐）",
) -> tuple[bool, str, dict[str, Any] | None]:
    if preset not in REALIGN_PRESETS:
        return False, f"未知的重对齐模式：{preset}", None
    record = get_music_review_record(db_path, reviewer_id, video_id)
    if record is None:
        return False, "当前视频记录不存在。", None
    video_path = _runtime_media_path(record.get("video_path"))
    song_path = _runtime_media_path(record.get("full_song_path"))
    if not video_path.is_file():
        return False, f"视频文件不存在：{video_path}", record
    if not song_path.is_file():
        return False, f"完整歌曲文件不存在：{song_path}", record

    result = align_video_to_song(
        video_path,
        song_path,
        **REALIGN_PRESETS[preset],
    )
    if result.song_offset is None:
        message = f"{preset}重对齐未找到可靠位置：{result.error or '匹配分过低'}"
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE music_preparations
                SET status='needs_review', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE video_id=?
                """,
                (message, record["video_db_id"]),
            )
            log_event(
                conn,
                "music_realign_failed",
                actor=reviewer_id,
                target_type="video",
                target_id=video_id,
                payload={"preset": preset, "error": result.error},
            )
        return (
            False,
            message + "。原 offset 已保留，可手动调整后刷新试听。",
            get_music_review_record(db_path, reviewer_id, video_id),
        )

    video_duration = record.get("duration")
    aligned_duration = None
    if video_duration is not None:
        aligned_duration = max(
            0.0,
            float(video_duration) - result.video_audio_start,
        )
    review_status = (
        result.status if result.status in REVIEWABLE_STATUSES else "needs_review"
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE music_preparations
            SET song_offset=?, video_audio_start=?, aligned_duration=?,
                match_score=?, status=?, error='', updated_at=CURRENT_TIMESTAMP
            WHERE video_id=?
            """,
            (
                result.song_offset,
                result.video_audio_start,
                aligned_duration,
                result.score,
                review_status,
                record["video_db_id"],
            ),
        )
        conn.execute(
            """
            UPDATE song_reviews
            SET corrected_offset=?, alignment_correct=NULL,
                status='in_progress', updated_at=CURRENT_TIMESTAMP
            WHERE video_id=? AND reviewer_id=?
            """,
            (result.song_offset, record["video_db_id"], reviewer_id),
        )
        log_event(
            conn,
            "music_realigned",
            actor=reviewer_id,
            target_type="video",
            target_id=video_id,
            payload={
                "preset": preset,
                "song_offset": result.song_offset,
                "score": result.score,
                "votes": result.votes,
            },
        )
    updated = get_music_review_record(db_path, reviewer_id, video_id)
    return (
        True,
        (
            f"{preset}重对齐完成：offset={result.song_offset:.3f}s，"
            f"匹配分={result.score or 0:.3f}，有效窗口={result.votes}。"
            "请试听确认后再提交。"
        ),
        updated,
    )


def build_aligned_preview(
    record: dict[str, Any],
    *,
    preview_dir: Path | None = None,
) -> Path | None:
    song_path = Path(str(record.get("full_song_path", "") or ""))
    if not song_path.is_file():
        return None
    preview_dir = preview_dir or PATHS.output_dir / "server" / "review_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    offset = float(
        record.get("corrected_offset")
        if record.get("corrected_offset") is not None
        else record.get("song_offset") or 0
    )
    duration = float(record.get("aligned_duration") or record.get("duration") or 45)
    duration = max(5.0, min(duration, 90.0))
    output = preview_dir / (
        f"{int(record['video_db_id'])}_{offset:.3f}_{duration:.2f}.mp3"
    )
    if output.exists() and output.stat().st_size > 1024:
        return output
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{offset:.3f}",
            "-i",
            str(song_path),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-b:a",
            "192k",
            str(output),
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0 or not output.exists():
        output.unlink(missing_ok=True)
        return None
    return output


def confirm_music_review(
    db_path: Path,
    reviewer_id: str,
    video_id: str,
    *,
    corrected_offset: float,
    final_genre: str,
    note: str = "",
    owner_id: str = "owner",
) -> tuple[bool, str]:
    final_genre = str(final_genre or "").strip()
    if not final_genre:
        return False, "Genre is required."
    try:
        corrected_offset = max(0.0, float(corrected_offset))
    except (TypeError, ValueError):
        return False, "Song offset must be a number."

    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT v.id, p.song_id, p.aligned_duration, p.match_score
            FROM videos v
            JOIN music_preparations p ON p.video_id=v.id
            WHERE v.video_id=? AND v.deleted_at IS NULL
            """,
            (video_id,),
        ).fetchone()
        if row is None or row["song_id"] is None:
            return False, "Prepared song candidate is missing."
        music_end = None
        if row["aligned_duration"] is not None:
            music_end = corrected_offset + float(row["aligned_duration"])
        conn.execute(
            """
            INSERT INTO song_reviews(
                video_id, reviewer_id, song_id, song_correct,
                alignment_correct, corrected_offset, final_genre, note, status
            )
            VALUES (?, ?, ?, 1, 1, ?, ?, ?, 'completed')
            ON CONFLICT(video_id, reviewer_id) DO UPDATE SET
                song_id=excluded.song_id,
                song_correct=1,
                alignment_correct=1,
                corrected_offset=excluded.corrected_offset,
                final_genre=excluded.final_genre,
                note=excluded.note,
                status='completed',
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                row["id"],
                reviewer_id,
                row["song_id"],
                corrected_offset,
                final_genre,
                note.strip(),
            ),
        )
        conn.execute(
            """
            UPDATE music_preparations
            SET song_offset=?, status='verified', error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE video_id=?
            """,
            (corrected_offset, row["id"]),
        )
        annotation = conn.execute(
            "SELECT id FROM annotations WHERE video_id=? AND annotator_id=?",
            (row["id"], owner_id),
        ).fetchone()
        if annotation is None:
            conn.execute(
                """
                INSERT INTO annotations(
                    video_id, song_id, annotator_id, music_start, music_end,
                    genre, song_verified, match_score, status, row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, 'Yes', ?, 'in_progress', '{}')
                """,
                (
                    row["id"],
                    row["song_id"],
                    owner_id,
                    corrected_offset,
                    music_end,
                    final_genre,
                    row["match_score"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE annotations
                SET song_id=?, music_start=?, music_end=?, genre=?,
                    song_verified='Yes', match_score=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    row["song_id"],
                    corrected_offset,
                    music_end,
                    final_genre,
                    row["match_score"],
                    annotation["id"],
                ),
            )
        log_event(
            conn,
            "music_review_confirmed",
            actor=reviewer_id,
            target_type="video",
            target_id=video_id,
            payload={
                "song_offset": corrected_offset,
                "genre": final_genre,
                "owner_id": owner_id,
            },
        )
    return True, "Confirmed."


def reject_music_review(
    db_path: Path,
    reviewer_id: str,
    video_id: str,
    *,
    reason: str,
    note: str = "",
) -> tuple[bool, str]:
    if reason not in {"song_rejected", "alignment_rejected"}:
        return False, "Unknown rejection reason."
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM videos WHERE video_id=? AND deleted_at IS NULL",
            (video_id,),
        ).fetchone()
        if row is None:
            return False, "Video is missing."
        song_correct = 0 if reason == "song_rejected" else 1
        alignment_correct = 0
        conn.execute(
            """
            INSERT INTO song_reviews(
                video_id, reviewer_id, song_correct, alignment_correct,
                note, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, reviewer_id) DO UPDATE SET
                song_correct=excluded.song_correct,
                alignment_correct=excluded.alignment_correct,
                note=excluded.note,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                row["id"],
                reviewer_id,
                song_correct,
                alignment_correct,
                note.strip(),
                reason,
            ),
        )
        preparation_status = (
            "needs_manual" if reason == "song_rejected" else "needs_realign"
        )
        conn.execute(
            """
            UPDATE music_preparations
            SET status=?, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE video_id=?
            """,
            (preparation_status, note.strip(), row["id"]),
        )
        log_event(
            conn,
            "music_review_rejected",
            actor=reviewer_id,
            target_type="video",
            target_id=video_id,
            payload={"reason": reason, "note": note.strip()},
        )
    return True, "Rejection saved."
