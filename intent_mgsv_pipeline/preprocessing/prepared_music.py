from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


USABLE_PREPARATION_STATUSES = {
    "ready_for_review",
    "needs_review",
    "verified",
}


def load_prepared_music_index(
    db_path: Path,
    *,
    owner_id: str = "owner",
) -> dict[str, dict[str, Any]]:
    """Load music candidates that are safe to expose to auto preprocessing."""
    if not db_path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
            SELECT
                v.video_id,
                p.status AS preparation_status,
                p.song_offset,
                p.video_audio_start,
                p.aligned_duration,
                p.match_score,
                p.download_source,
                p.qq_song_mid,
                COALESCE(
                    NULLIF(p.full_song_path, ''),
                    NULLIF(s.full_song_path, ''),
                    ''
                ) AS full_song_path,
                COALESCE(p.recognized_title, s.title, '') AS song_title,
                COALESCE(p.recognized_artist, s.artist, '') AS song_artist,
                r.corrected_offset,
                r.final_genre,
                r.status AS review_status
            FROM music_preparations p
            JOIN videos v ON v.id=p.video_id
            LEFT JOIN songs s ON s.id=p.song_id
            LEFT JOIN song_reviews r
              ON r.video_id=v.id AND r.reviewer_id=?
            WHERE p.status IN ('ready_for_review', 'needs_review', 'verified')
              AND v.deleted_at IS NULL
                """,
                (owner_id,),
            ).fetchall()
    except sqlite3.Error:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        if item["preparation_status"] not in USABLE_PREPARATION_STATUSES:
            continue
        reviewed = (
            item.get("review_status") == "completed"
            and item.get("corrected_offset") is not None
        )
        item["effective_offset"] = (
            float(item["corrected_offset"])
            if reviewed
            else item.get("song_offset")
        )
        item["alignment_source"] = (
            "human_verified" if reviewed else "music_preparation"
        )
        result[str(item["video_id"])] = item
    return result
