from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    connect,
    dumps_json,
    init_db,
    loads_json,
    log_event,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _copy_owner_scaffold(
    conn: Any,
    video_db_id: int,
    annotator_id: str,
    owner_id: str,
) -> None:
    if annotator_id == owner_id:
        return
    exists = conn.execute(
        "SELECT 1 FROM annotations WHERE video_id=? AND annotator_id=?",
        (video_db_id, annotator_id),
    ).fetchone()
    if exists:
        return
    owner = conn.execute(
        "SELECT * FROM annotations WHERE video_id=? AND annotator_id=?",
        (video_db_id, owner_id),
    ).fetchone()
    if owner is None:
        conn.execute(
            """
            INSERT INTO annotations(video_id, annotator_id, status, row_json)
            VALUES (?, ?, 'in_progress', '{}')
            """,
            (video_db_id, annotator_id),
        )
        return
    conn.execute(
        """
        INSERT INTO annotations(
            video_id, song_id, annotator_id, music_id, sync_level,
            music_start, music_end, shot_points, shot_points_3, shot_points_5,
            vocal_presence, genre, song_verified, recog_confidence,
            recog_note, match_score, status, row_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?)
        """,
        (
            video_db_id,
            owner["song_id"],
            annotator_id,
            owner["music_id"],
            owner["sync_level"],
            owner["music_start"],
            owner["music_end"],
            owner["shot_points"],
            owner["shot_points_3"],
            owner["shot_points_5"],
            owner["vocal_presence"],
            owner["genre"],
            owner["song_verified"],
            owner["recog_confidence"],
            owner["recog_note"],
            owner["match_score"],
            owner["row_json"],
        ),
    )


def get_annotation_record(
    db_path: Path,
    annotator_id: str,
    video_id: str,
) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                v.id AS video_db_id, v.video_id, v.video_path,
                v.clip_audio_path, v.creator_name,
                v.video_title, v.hashtags, v.full_desc, v.duration,
                v.row_json AS video_row_json,
                a.sync_level, a.music_start, a.music_end, a.shot_points,
                a.shot_points_3, a.shot_points_5, a.emotion, a.style,
                a.usage_scene, a.seg_scores_3, a.seg_scores_5,
                a.vocal_presence, a.genre, a.song_verified,
                a.recog_confidence, a.recog_note, a.match_score,
                a.status, a.row_json AS annotation_row_json,
                s.title AS song_title, s.artist AS song_artist,
                s.full_song_path
            FROM videos v
            JOIN annotations a ON a.video_id=v.id AND a.annotator_id=?
            LEFT JOIN songs s ON s.id=a.song_id
            WHERE v.video_id=? AND v.deleted_at IS NULL
            """,
            (annotator_id, video_id),
        ).fetchone()
    if row is None:
        return None
    result = loads_json(row["video_row_json"])
    result.update(loads_json(row["annotation_row_json"]))
    for key in row.keys():
        if key not in {"video_row_json", "annotation_row_json"}:
            value = row[key]
            if value is not None:
                result[key] = value
    return result


def get_previous_annotation(
    db_path: Path,
    annotator_id: str,
    before_video_id: str,
) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        current = conn.execute(
            "SELECT id FROM videos WHERE video_id=? AND deleted_at IS NULL",
            (before_video_id,),
        ).fetchone()
        before_id = int(current["id"]) if current else 2**63 - 1
        row = conn.execute(
            """
            SELECT v.video_id
            FROM videos v
            JOIN annotations a
              ON a.video_id=v.id AND a.annotator_id=?
            WHERE v.deleted_at IS NULL
              AND v.id < ?
              AND a.status='completed'
            ORDER BY v.id DESC
            LIMIT 1
            """,
            (annotator_id, before_id),
        ).fetchone()
    if row is None:
        return None
    return get_annotation_record(db_path, annotator_id, str(row["video_id"]))


def annotation_progress(
    db_path: Path,
    annotator_id: str,
    owner_id: str = "owner",
) -> dict[str, int]:
    init_db(db_path)
    require_completed_owner = int(annotator_id != owner_id)
    eligibility = """
        v.deleted_at IS NULL
        AND (
            ?=0 OR EXISTS (
                SELECT 1
                FROM annotations owner
                WHERE owner.video_id=v.id
                  AND owner.annotator_id=?
                  AND owner.status='completed'
                  AND LOWER(COALESCE(owner.song_verified, '')) IN
                      ('yes', 'true', '1', 'confirmed',
                       char(26159), char(24050, 30830, 35748))
            )
        )
    """
    with connect(db_path) as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM videos v WHERE {eligibility}",
                (require_completed_owner, owner_id),
            ).fetchone()[0]
        )
        completed = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM videos v
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id=?
                WHERE {eligibility}
                  AND a.status='completed'
                """,
                (annotator_id, require_completed_owner, owner_id),
            ).fetchone()[0]
        )
        in_progress = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM videos v
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id=?
                WHERE {eligibility}
                  AND a.status='in_progress'
                """,
                (annotator_id, require_completed_owner, owner_id),
            ).fetchone()[0]
        )
    return {
        "completed": completed,
        "in_progress": in_progress,
        "total": total,
        "remaining": max(0, total - completed),
    }


def owner_annotation_progress(
    db_path: Path,
    owner_id: str = "owner",
) -> dict[str, int]:
    init_db(db_path)
    verified = """
        v.deleted_at IS NULL
        AND LOWER(COALESCE(a.song_verified, '')) IN
            ('yes', 'true', '1', 'confirmed',
             char(26159), char(24050, 30830, 35748))
    """
    with connect(db_path) as conn:
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM videos v
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id=?
                WHERE {verified}
                """,
                (owner_id,),
            ).fetchone()[0]
        )
        completed = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM videos v
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id=?
                WHERE {verified} AND a.status='completed'
                """,
                (owner_id,),
            ).fetchone()[0]
        )
    return {
        "completed": completed,
        "total": total,
        "remaining": max(0, total - completed),
    }


def claim_next_owner(
    db_path: Path,
    owner_id: str = "owner",
    lease_minutes: int = 120,
) -> dict[str, Any] | None:
    init_db(db_path)
    lease_until = _iso(_utc_now() + timedelta(minutes=lease_minutes))
    now = _iso(_utc_now())
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT v.id, v.video_id
            FROM annotation_assignments aa
            JOIN videos v ON v.id=aa.video_id
            JOIN annotations a
              ON a.video_id=v.id AND a.annotator_id=aa.annotator_id
            WHERE aa.annotator_id=?
              AND aa.status='in_progress'
              AND (aa.lease_until IS NULL OR aa.lease_until > ?)
              AND a.status!='completed'
              AND LOWER(COALESCE(a.song_verified, '')) IN
                  ('yes', 'true', '1', 'confirmed',
                   char(26159), char(24050, 30830, 35748))
              AND v.deleted_at IS NULL
            ORDER BY aa.updated_at DESC
            LIMIT 1
            """,
            (owner_id, now),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT v.id, v.video_id
                FROM videos v
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id=?
                LEFT JOIN annotation_assignments aa
                  ON aa.video_id=v.id AND aa.annotator_id=?
                WHERE v.deleted_at IS NULL
                  AND a.status!='completed'
                  AND LOWER(COALESCE(a.song_verified, '')) IN
                      ('yes', 'true', '1', 'confirmed',
                       char(26159), char(24050, 30830, 35748))
                  AND (
                      aa.video_id IS NULL
                      OR aa.status!='in_progress'
                      OR aa.lease_until IS NULL
                      OR aa.lease_until <= ?
                  )
                ORDER BY v.id
                LIMIT 1
                """,
                (owner_id, owner_id, now),
            ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            """
            INSERT INTO annotation_assignments(
                video_id, annotator_id, status, lease_until
            )
            VALUES (?, ?, 'in_progress', ?)
            ON CONFLICT(video_id, annotator_id) DO UPDATE SET
                status='in_progress',
                lease_until=excluded.lease_until,
                updated_at=CURRENT_TIMESTAMP
            """,
            (row["id"], owner_id, lease_until),
        )
        log_event(
            conn,
            "claim_next_owner",
            actor=owner_id,
            target_type="video",
            target_id=row["video_id"],
            payload={"lease_until": lease_until},
        )
        conn.commit()
        video_id = str(row["video_id"])
    return get_annotation_record(db_path, owner_id, video_id)


def claim_next(
    db_path: Path,
    annotator_id: str,
    lease_minutes: int = 60,
    owner_id: str = "owner",
) -> dict[str, Any] | None:
    init_db(db_path)
    lease_until = _iso(_utc_now() + timedelta(minutes=lease_minutes))
    now = _iso(_utc_now())
    require_completed_owner = int(annotator_id != owner_id)

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT v.id, v.video_id, v.video_title, v.creator_name
            FROM annotation_assignments aa
            JOIN videos v ON v.id=aa.video_id
            LEFT JOIN annotations a
              ON a.video_id=v.id AND a.annotator_id=aa.annotator_id
            WHERE aa.annotator_id=?
              AND aa.status='in_progress'
              AND (aa.lease_until IS NULL OR aa.lease_until > ?)
              AND COALESCE(a.status, 'in_progress') != 'completed'
              AND (
                  ?=0 OR EXISTS (
                      SELECT 1
                      FROM annotations owner
                      WHERE owner.video_id=v.id
                        AND owner.annotator_id=?
                        AND owner.status='completed'
                        AND LOWER(COALESCE(owner.song_verified, '')) IN
                            ('yes', 'true', '1', 'confirmed',
                             char(26159), char(24050, 30830, 35748))
                  )
              )
              AND v.deleted_at IS NULL
            ORDER BY aa.updated_at DESC
            LIMIT 1
            """,
            (annotator_id, now, require_completed_owner, owner_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
            """
            SELECT v.id, v.video_id, v.video_title, v.creator_name
            FROM videos v
            LEFT JOIN annotation_assignments aa
              ON aa.video_id = v.id
             AND aa.annotator_id = ?
             AND aa.status = 'in_progress'
             AND (aa.lease_until IS NULL OR aa.lease_until > ?)
            LEFT JOIN annotations a
              ON a.video_id = v.id
             AND a.annotator_id = ?
             AND a.status = 'completed'
            WHERE v.deleted_at IS NULL
              AND aa.video_id IS NULL
              AND a.id IS NULL
              AND (
                  ?=0 OR EXISTS (
                      SELECT 1
                      FROM annotations owner
                      WHERE owner.video_id=v.id
                        AND owner.annotator_id=?
                        AND owner.status='completed'
                        AND LOWER(COALESCE(owner.song_verified, '')) IN
                            ('yes', 'true', '1', 'confirmed',
                             char(26159), char(24050, 30830, 35748))
                  )
              )
            ORDER BY v.id
            LIMIT 1
            """,
            (
                annotator_id,
                now,
                annotator_id,
                require_completed_owner,
                owner_id,
            ),
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
        _copy_owner_scaffold(conn, int(row["id"]), annotator_id, owner_id)
        log_event(
            conn,
            "claim_next",
            actor=annotator_id,
            target_type="video",
            target_id=row["video_id"],
            payload={"lease_until": lease_until},
        )
        conn.commit()
        video_id = str(row["video_id"])
    record = get_annotation_record(db_path, annotator_id, video_id)
    return (record or dict(row)) | {"lease_until": lease_until}


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
    parser.add_argument("--owner-id", default="owner")
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
        result = claim_next(
            db_path,
            args.annotator_id,
            args.lease_minutes,
            args.owner_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "release":
        print(release_assignment(db_path, args.annotator_id, args.video_id, args.status))
    elif args.command == "save-patch":
        print(save_annotation_patch(db_path, args.annotator_id, args.video_id, json.loads(args.patch_json)))


if __name__ == "__main__":
    main()
