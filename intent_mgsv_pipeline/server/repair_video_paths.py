from __future__ import annotations

import argparse
from pathlib import Path, PureWindowsPath

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import DEFAULT_DB, connect, init_db, log_event


VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}


def _candidate_names(video_id: str, stored_path: str) -> list[str]:
    values = [video_id, stored_path]
    names: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if not value:
            continue
        for name in (Path(value).name, PureWindowsPath(value).name):
            if name and name not in names:
                names.append(name)
    return names


def repair_video_paths(
    db_path: Path,
    scan_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    init_db(db_path)
    index: dict[str, list[Path]] = {}
    if scan_root.exists():
        for path in scan_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
                index.setdefault(path.name, []).append(path.resolve())

    counts = {
        "rows": 0,
        "already_valid": 0,
        "repaired": 0,
        "equivalent_duplicates": 0,
        "missing": 0,
        "ambiguous": 0,
    }
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, video_id, video_path
            FROM videos
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        counts["rows"] = len(rows)
        for row in rows:
            stored = str(row["video_path"] or "").strip()
            if stored and Path(stored).is_file():
                counts["already_valid"] += 1
                continue

            matches: list[Path] = []
            for name in _candidate_names(str(row["video_id"]), stored):
                for match in index.get(name, []):
                    if match not in matches:
                        matches.append(match)

            if not matches:
                counts["missing"] += 1
                continue
            if len(matches) > 1:
                sizes = {match.stat().st_size for match in matches}
                if len(sizes) != 1:
                    counts["ambiguous"] += 1
                    continue
                counts["equivalent_duplicates"] += 1
                matches.sort(key=lambda path: (len(path.parts), str(path)))

            counts["repaired"] += 1
            if not dry_run:
                conn.execute(
                    """
                    UPDATE videos
                    SET video_path=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (str(matches[0]), row["id"]),
                )

        if not dry_run:
            log_event(
                conn,
                "repair_video_paths",
                actor="system",
                target_type="database",
                target_id=str(db_path),
                payload={"scan_root": str(scan_root), **counts},
            )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore missing server video paths by matching video filenames."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--scan-root", default=str(PATHS.douk_download_root))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts = repair_video_paths(
        Path(args.db),
        Path(args.scan_root),
        dry_run=args.dry_run,
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
