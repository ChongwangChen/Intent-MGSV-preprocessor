from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from intent_mgsv_pipeline.server.db import DEFAULT_DB, connect, dumps_json, init_db, log_event


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def claim_next(db_path: Path, annotator_id: str, lease_minutes: int = 60) -> dict[str, Any] | None:
    init_db(db_path)
    lease_until = _iso(_utc_now() + timedelta(minutes=lease_minutes))
    now = _iso(_utc_now())

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT v.id, v.video_id, v.video_title, v.creator_name
            FROM videos v
            LEFT JOIN annotation_assignments aa
              ON aa.video_id = v.id
             AND aa.status = 'in_progress'
             AND (aa.lease_until IS NULL OR aa.lease_until > ?)
            LEFT JOIN annotations a
              ON a.video_id = v.id
             AND a.annotator_id = ?
             AND a.status = 'completed'
            WHERE v.deleted_at IS NULL
              AND aa.video_id IS NULL
              AND a.id IS NULL
            ORDER BY v.id
            LIMIT 1
            """,
            (now, annotator_id),
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        conn.execute(
            """
            INSERT INTO annotation_assignments(video_id, annotator_id, status, lease_until)
            VALUES (?, ?, 'in_progress', ?)
            ON CONFLICT(video_id, annotator_id) DO UPDATE SET
                status='in_progress',
                lease_until=excluded.lease_until,
                updated_at=CURRENT_TIMESTAMP
            """,
            (row["id"], annotator_id, lease_until),
        )
        log_event(
            conn,
            "claim_next",
            actor=annotator_id,
            target_type="video",
            target_id=row["video_id"],
            payload={"lease_until": lease_until},
        )
        conn.commit()
        return dict(row) | {"lease_until": lease_until}


def release_assignment(db_path: Path, annotator_id: str, video_id: str, status: str = "completed") -> bool:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE annotation_assignments
            SET status=?, updated_at=CURRENT_TIMESTAMP
            WHERE video_id=? AND annotator_id=?
            """,
            (status, row["id"], annotator_id),
        )
        conn.execute(
            """
            UPDATE annotations
            SET status=?, updated_at=CURRENT_TIMESTAMP
            WHERE video_id=? AND annotator_id=?
            """,
            (status, row["id"], annotator_id),
        )
        log_event(
            conn,
            "release_assignment",
            actor=annotator_id,
            target_type="video",
            target_id=video_id,
            payload={"status": status},
        )
    return True


def save_annotation_patch(db_path: Path, annotator_id: str, video_id: str, patch: dict[str, Any]) -> bool:
    init_db(db_path)
    allowed = {
        "sync_level",
        "music_start",
        "music_end",
        "shot_points",
        "shot_points_3",
        "shot_points_5",
        "emotion",
        "style",
        "usage_scene",
        "seg_scores_3",
        "seg_scores_5",
        "vocal_presence",
        "genre",
        "song_verified",
        "recog_confidence",
        "recog_note",
        "match_score",
        "status",
    }
    values = {k: v for k, v in patch.items() if k in allowed}
    if not values:
        return False

    with connect(db_path) as conn:
        video = conn.execute("SELECT id FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if not video:
            return False
        ann = conn.execute(
            "SELECT id, row_json FROM annotations WHERE video_id=? AND annotator_id=?",
            (video["id"], annotator_id),
        ).fetchone()
        if ann is None:
            conn.execute(
                """
                INSERT INTO annotations(video_id, annotator_id, status, row_json)
                VALUES (?, ?, 'in_progress', ?)
                """,
                (video["id"], annotator_id, dumps_json({})),
            )
            ann = conn.execute(
                "SELECT id, row_json FROM annotations WHERE video_id=? AND annotator_id=?",
                (video["id"], annotator_id),
            ).fetchone()

        assignments = ", ".join([f"{k}=?" for k in values]) + ", updated_at=CURRENT_TIMESTAMP"
        conn.execute(
            f"UPDATE annotations SET {assignments} WHERE id=?",
            tuple(values.values()) + (ann["id"],),
        )
        log_event(
            conn,
            "save_annotation_patch",
            actor=annotator_id,
            target_type="video",
            target_id=video_id,
            payload=values,
        )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim or update server annotation assignments.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--annotator-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim-next")
    claim.add_argument("--lease-minutes", type=int, default=60)

    release = sub.add_parser("release")
    release.add_argument("--video-id", required=True)
    release.add_argument("--status", default="completed")

    patch = sub.add_parser("save-patch")
    patch.add_argument("--video-id", required=True)
    patch.add_argument("--patch-json", required=True)

    args = parser.parse_args()
    db_path = Path(args.db)
    if args.command == "claim-next":
        result = claim_next(db_path, args.annotator_id, args.lease_minutes)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "release":
        print(release_assignment(db_path, args.annotator_id, args.video_id, args.status))
    elif args.command == "save-patch":
        print(save_annotation_patch(db_path, args.annotator_id, args.video_id, json.loads(args.patch_json)))


if __name__ == "__main__":
    main()
