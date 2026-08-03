from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.runtime_config import PATHS, RuntimePaths
from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    connect,
    dumps_json,
    init_db,
    loads_json,
    log_event,
    number,
)
from intent_mgsv_pipeline.server.owner_annotation import owner_required_missing


AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac"}


def _audio_index(paths: RuntimePaths) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in (paths.full_songs_dir, paths.full_music_dir):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES:
                index.setdefault(path.name.casefold(), []).append(path.resolve())
    return index


def _basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1] if text else ""


def _resolve_song_path(
    raw_values: list[Any],
    paths: RuntimePaths,
    audio_index: dict[str, list[Path]],
) -> Path | None:
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text)
        direct = path if path.is_absolute() else paths.project_root / path
        if direct.is_file():
            return direct.resolve()
        matches = audio_index.get(_basename(text).casefold(), [])
        if len(matches) == 1:
            return matches[0]
    return None


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(data.get(key, "") or "").strip()
        if value and value.casefold() not in {"nan", "none", "null"}:
            return value
    return ""


def inspect_restore_candidates(
    db_path: Path,
    paths: RuntimePaths = PATHS,
    *,
    owner_id: str = "owner",
    limit: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    audio_index = _audio_index(paths)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                v.id AS video_db_id, v.video_id, v.duration,
                a.*, s.title AS song_title_db, s.artist AS song_artist_db,
                s.album AS song_album_db,
                s.full_song_path AS song_path_db,
                s.qq_song_mid AS qq_song_mid_db
            FROM annotations a
            JOIN videos v ON v.id=a.video_id
            LEFT JOIN songs s ON s.id=a.song_id
            LEFT JOIN music_preparations p ON p.video_id=v.id
            WHERE a.annotator_id=?
              AND v.deleted_at IS NULL
              AND p.video_id IS NULL
            ORDER BY v.id
            """,
            (owner_id,),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        row_data = dict(row)
        restored = loads_json(row["row_json"])
        combined = {**restored, **row_data}
        missing = [
            value
            for value in owner_required_missing(combined)
            if value != "song confirmation"
        ]
        song_path = _resolve_song_path(
            [
                row["song_path_db"],
                restored.get("full_song_path"),
                restored.get("full_music_path"),
                restored.get("music_path"),
            ],
            paths,
            audio_index,
        )
        title = str(row["song_title_db"] or "").strip() or _first_text(
            restored,
            "song_title",
            "music_title",
            "title",
        )
        artist = str(row["song_artist_db"] or "").strip() or _first_text(
            restored,
            "song_artist",
            "music_artist",
            "artist",
        )
        if missing:
            state = "missing_labels"
        elif song_path is None:
            state = "missing_song_file"
        else:
            state = "ready"
        results.append(
            {
                "state": state,
                "video_db_id": int(row["video_db_id"]),
                "video_id": str(row["video_id"]),
                "annotation_id": int(row["id"]),
                "song_id": row["song_id"],
                "song_title": title,
                "song_artist": artist,
                "song_album": str(row["song_album_db"] or "").strip(),
                "qq_song_mid": str(row["qq_song_mid_db"] or "").strip(),
                "full_song_path": str(song_path) if song_path else "",
                "music_start": number(row["music_start"]),
                "music_end": number(row["music_end"]),
                "video_duration": number(row["duration"]),
                "match_score": number(row["match_score"]),
                "missing_labels": missing,
            }
        )
        if limit and len(results) >= limit:
            break
    return results


def restore_music_review_queue(
    db_path: Path,
    paths: RuntimePaths = PATHS,
    *,
    owner_id: str = "owner",
    limit: int = 0,
    apply: bool = False,
) -> dict[str, Any]:
    candidates = inspect_restore_candidates(
        db_path,
        paths,
        owner_id=owner_id,
        limit=limit,
    )
    counts = Counter(item["state"] for item in candidates)
    restored_count = 0
    if apply:
        with connect(db_path) as conn:
            for item in candidates:
                if item["state"] != "ready":
                    continue
                song_id = item["song_id"]
                existing = conn.execute(
                    "SELECT id FROM songs WHERE full_song_path=?",
                    (item["full_song_path"],),
                ).fetchone()
                canonical_song_id = (
                    int(existing["id"]) if existing is not None else None
                )
                if canonical_song_id is not None:
                    song_id = canonical_song_id
                    conn.execute(
                        """
                        UPDATE annotations
                        SET song_id=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (song_id, item["annotation_id"]),
                    )
                elif song_id is None:
                    song_id = conn.execute(
                        """
                        INSERT INTO songs(
                            title, artist, album, full_song_path,
                            qq_song_mid, source, row_json
                        )
                        VALUES (?, ?, ?, ?, NULLIF(?, ''),
                                'restored_annotation', '{}')
                        """,
                        (
                            item["song_title"],
                            item["song_artist"],
                            item["song_album"],
                            item["full_song_path"],
                            item["qq_song_mid"],
                        ),
                    ).lastrowid
                    conn.execute(
                        """
                        UPDATE annotations
                        SET song_id=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (song_id, item["annotation_id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE songs
                        SET full_song_path=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (item["full_song_path"], song_id),
                    )

                start = max(0.0, item["music_start"] or 0.0)
                end = item["music_end"]
                aligned_duration = (
                    max(0.0, end - start)
                    if end is not None and end > start
                    else item["video_duration"]
                )
                conn.execute(
                    """
                    INSERT INTO music_preparations(
                        video_id, song_id, recognized_title,
                        recognized_artist, qq_song_mid, download_source,
                        full_song_path, song_offset, video_audio_start,
                        aligned_duration, match_score, status, error, raw_json
                    )
                    VALUES (?, ?, ?, ?, NULLIF(?, ''),
                            'restored_annotation', ?, ?, 0, ?, ?,
                            'needs_review',
                            'Restored from existing owner annotation; human song confirmation is required.',
                            ?)
                    """,
                    (
                        item["video_db_id"],
                        song_id,
                        item["song_title"],
                        item["song_artist"],
                        item["qq_song_mid"],
                        item["full_song_path"],
                        start,
                        aligned_duration,
                        item["match_score"],
                        dumps_json(
                            {
                                "restored_from_annotation": True,
                                "annotation_id": item["annotation_id"],
                            }
                        ),
                    ),
                )
                log_event(
                    conn,
                    "restore_music_review_queue",
                    actor=owner_id,
                    target_type="video",
                    target_id=item["video_id"],
                    payload={
                        "song_id": song_id,
                        "song_offset": start,
                        "full_song_path": item["full_song_path"],
                    },
                )
                restored_count += 1
    return {
        "mode": "apply" if apply else "dry-run",
        "owner_id": owner_id,
        "scanned": len(candidates),
        "ready": counts["ready"],
        "missing_song_file": counts["missing_song_file"],
        "missing_labels": counts["missing_labels"],
        "restored": restored_count,
        "items": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Restore fully labeled owner rows with existing songs into the "
            "human music review queue."
        )
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write ready candidates to music_preparations.",
    )
    parser.add_argument(
        "--report",
        default=str(
            PATHS.output_dir
            / "server"
            / "diagnostics"
            / "restore_music_review_queue.json"
        ),
    )
    args = parser.parse_args()
    summary = restore_music_review_queue(
        Path(args.db),
        owner_id=args.owner_id,
        limit=max(0, args.limit),
        apply=args.apply,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "items"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
