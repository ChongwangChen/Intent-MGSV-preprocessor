from __future__ import annotations

import argparse
import json
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import connect


NEXT_ACTIONS = {
    "recognition_failed": "rerun yt_dy_auto.py --retry-failed --download",
    "search_failed": (
        "rerun prepare_music_pipeline.py --retry-failed "
        "--include-existing-dataset"
    ),
    "download_failed": "check QQ cookies/network, then rerun preparation",
    "alignment_failed": "rerun preparation; inspect song/video if it persists",
    "ready_for_review": "review at the music-review site (port 7862)",
    "needs_review": "review or manually adjust offset at port 7862",
    "needs_manual": (
        "retry preparation with --include-existing-dataset to exclude the "
        "rejected QQ candidate"
    ),
    "needs_realign": (
        "retry preparation with --include-existing-dataset, then review the "
        "new offset at port 7862"
    ),
    "verified": "continue owner annotation at port 7860",
}


def audit_music_pipeline(db_path: Path, *, limit: int = 30) -> dict[str, Any]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT
                v.video_id,
                p.status,
                p.recognized_title,
                p.recognized_artist,
                p.full_song_path,
                p.song_offset,
                p.match_score,
                p.error,
                r.status AS review_status
            FROM music_preparations p
            JOIN videos v ON v.id=p.video_id
            LEFT JOIN song_reviews r
              ON r.video_id=v.id AND r.reviewer_id='owner'
            WHERE v.deleted_at IS NULL
            ORDER BY p.updated_at DESC, v.id DESC
            """
        ).fetchall()

    counts = Counter(str(row["status"] or "(none)") for row in rows)
    pending = []
    for row in rows:
        status = str(row["status"] or "(none)")
        if status == "verified":
            continue
        song_path = Path(str(row["full_song_path"] or "").strip())
        pending.append(
            {
                "video_id": row["video_id"],
                "status": status,
                "song": " - ".join(
                    value
                    for value in (
                        str(row["recognized_title"] or "").strip(),
                        str(row["recognized_artist"] or "").strip(),
                    )
                    if value
                ),
                "song_file_exists": bool(song_path and song_path.is_file()),
                "song_offset": row["song_offset"],
                "match_score": row["match_score"],
                "review_status": row["review_status"] or "",
                "next_action": NEXT_ACTIONS.get(status, "inspect this record"),
                "error": str(row["error"] or "")[:500],
            }
        )
        if len(pending) >= max(0, limit):
            break
    return {
        "database": str(db_path.resolve()),
        "total_preparations": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "pending_preview": pending,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only audit of music recognition, preparation and review states."
    )
    parser.add_argument("--db", default=str(PATHS.server_db))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    print(
        json.dumps(
            audit_music_pipeline(Path(args.db), limit=args.limit),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
