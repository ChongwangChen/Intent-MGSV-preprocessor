from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import DEFAULT_DB, connect, init_db
from intent_mgsv_pipeline.server.repair_video_paths import (
    VIDEO_SUFFIXES,
    _candidate_names,
)
from intent_mgsv_pipeline.server.verification import is_song_verified


def _disk_video_index(scan_root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    if not scan_root.is_dir():
        return index
    for path in scan_root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in VIDEO_SUFFIXES:
            index.setdefault(path.name.casefold(), []).append(path.resolve())
    return index


def _file_name_index(
    roots: tuple[Path, ...],
    suffixes: set[str],
) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in suffixes:
                index.setdefault(path.name.casefold(), []).append(
                    path.resolve()
                )
    return index


def _video_file_exists(
    video_id: str,
    video_path: str,
    index: dict[str, list[Path]],
) -> bool:
    stored = Path(str(video_path or "").strip())
    if str(stored) and stored.is_file():
        return True
    for name in _candidate_names(video_id, video_path):
        if index.get(name.casefold()):
            return True
    return False


def _song_file_exists(
    song_path: str,
    index: dict[str, list[Path]],
) -> bool:
    raw = str(song_path or "").strip()
    if not raw:
        return False
    stored = Path(raw)
    if stored.is_file():
        return True
    return bool(index.get(stored.name.casefold()))


def build_growth_report(
    db_path: Path,
    scan_root: Path,
    song_roots: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    disk_index = _disk_video_index(scan_root)
    disk_files = [path for paths in disk_index.values() for path in paths]
    resolved_song_roots = song_roots or (
        PATHS.full_songs_dir,
        PATHS.full_music_dir,
    )
    song_index = _file_name_index(
        resolved_song_roots,
        {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac"},
    )
    with connect(db_path) as conn:
        videos = conn.execute(
            """
            SELECT id, video_id, video_path
            FROM videos
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        preparation_counts = Counter(
            str(row["status"] or "missing")
            for row in conn.execute(
                """
                SELECT p.status
                FROM music_preparations p
                JOIN videos v ON v.id=p.video_id
                WHERE v.deleted_at IS NULL
                """
            ).fetchall()
        )
        owner_rows = conn.execute(
            """
            SELECT v.id AS video_db_id, v.video_id,
                   a.status, a.song_verified
            FROM videos v
            LEFT JOIN annotations a
              ON a.video_id=v.id AND a.annotator_id='owner'
            WHERE v.deleted_at IS NULL
            """
        ).fetchall()
        song_rows = conn.execute(
            "SELECT id, full_song_path FROM songs"
        ).fetchall()

    db_names = {
        name.casefold()
        for row in videos
        for name in _candidate_names(row["video_id"], row["video_path"])
    }
    missing_video_files = [
        str(row["video_id"])
        for row in videos
        if not _video_file_exists(
            str(row["video_id"]),
            str(row["video_path"] or ""),
            disk_index,
        )
    ]
    unregistered_disk_files = [
        str(path)
        for name, paths in disk_index.items()
        if name not in db_names
        for path in paths
    ]
    owner_statuses = Counter(
        str(row["status"] or "missing") for row in owner_rows
    )
    owner_verified = sum(
        is_song_verified(row["song_verified"]) for row in owner_rows
    )
    peer_eligible = sum(
        str(row["status"] or "") == "completed"
        and is_song_verified(row["song_verified"])
        for row in owner_rows
    )
    videos_with_preparation = sum(preparation_counts.values())
    missing_song_files = [
        int(row["id"])
        for row in song_rows
        if not _song_file_exists(
            str(row["full_song_path"] or ""),
            song_index,
        )
    ]
    return {
        "db": str(db_path),
        "scan_root": str(scan_root),
        "disk": {
            "video_files": len(disk_files),
            "unique_filenames": len(disk_index),
            "duplicate_filenames": sum(
                len(paths) > 1 for paths in disk_index.values()
            ),
            "unregistered_files": len(unregistered_disk_files),
        },
        "database": {
            "active_videos": len(videos),
            "missing_video_files": len(missing_video_files),
            "songs": len(song_rows),
            "missing_song_files": len(missing_song_files),
        },
        "pipeline": {
            "videos_with_music_preparation": videos_with_preparation,
            "videos_without_music_preparation": (
                len(videos) - videos_with_preparation
            ),
            "music_preparation_statuses": dict(
                sorted(preparation_counts.items())
            ),
            "owner_annotation_statuses": dict(
                sorted(owner_statuses.items())
            ),
            "owner_song_verified": owner_verified,
            "peer_eligible": peer_eligible,
        },
        "gaps": {
            "unregistered_disk_files": unregistered_disk_files,
            "missing_video_file_ids": missing_video_files,
            "missing_song_record_ids": missing_song_files,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit the incremental video dataset growth funnel."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--scan-root",
        default=str(PATHS.douk_download_root),
    )
    parser.add_argument(
        "--report",
        default=str(
            PATHS.output_dir
            / "server"
            / "diagnostics"
            / "dataset_growth_report.json"
        ),
    )
    args = parser.parse_args()
    result = build_growth_report(
        Path(args.db),
        Path(args.scan_root),
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="backslashreplace").decode(encoding))
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
