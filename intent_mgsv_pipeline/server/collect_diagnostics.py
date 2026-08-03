from __future__ import annotations

import argparse
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

from intent_mgsv_pipeline.runtime_config import PATHS, RuntimePaths
from intent_mgsv_pipeline.server.db import DEFAULT_DB, connect, init_db


LOG_NAMES = (
    "mgsv-owner.log",
    "mgsv-peer.log",
    "mgsv-music-review.log",
)


def _count_rows(rows: Iterable[object], key: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row[key] if row[key] is not None else "(none)"  # type: ignore[index]
        counts[str(value)] += 1
    return counts


def _format_counts(title: str, counts: Counter[str]) -> list[str]:
    lines = [title]
    if not counts:
        return [*lines, "  (none)"]
    return [*lines, *(f"  {key}: {value}" for key, value in sorted(counts.items()))]


def _git_revision(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return "(unavailable)"


def _recent_video_files(root: Path, limit: int) -> list[Path]:
    if not root.is_dir():
        return []
    files = [path for path in root.rglob("*.mp4") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[: max(1, limit)]


def build_diagnostic_report(
    db_path: Path,
    paths: RuntimePaths = PATHS,
    *,
    recent: int = 20,
    log_lines: int = 80,
) -> str:
    database_existed = db_path.is_file()
    init_db(db_path)
    lines = [
        "Intent-MGSV server diagnostic report",
        f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"git: {_git_revision(paths.project_root)}",
        f"project_root: {paths.project_root}",
        f"scan_root: {paths.douk_download_root}",
        f"scan_root_exists: {paths.douk_download_root.is_dir()}",
        f"database: {db_path}",
        f"database_existed_before_report: {database_existed}",
        "",
    ]

    with connect(db_path) as conn:
        videos = conn.execute(
            "SELECT id, video_id, video_path FROM videos WHERE deleted_at IS NULL"
        ).fetchall()
        preparations = conn.execute(
            "SELECT status FROM music_preparations"
        ).fetchall()
        reviews = conn.execute("SELECT status FROM song_reviews").fetchall()
        annotations = conn.execute(
            "SELECT annotator_id, status FROM annotations"
        ).fetchall()

        lines.extend(
            [
                "DATABASE SUMMARY",
                f"  active_videos: {len(videos)}",
                f"  music_preparations: {len(preparations)}",
                f"  song_reviews: {len(reviews)}",
                f"  annotations: {len(annotations)}",
                "",
            ]
        )
        lines.extend(
            _format_counts(
                "MUSIC PREPARATION STATUS",
                _count_rows(preparations, "status"),
            )
        )
        lines.append("")
        lines.extend(
            _format_counts(
                "MUSIC REVIEW STATUS",
                _count_rows(reviews, "status"),
            )
        )
        lines.append("")
        annotation_counts = Counter(
            f"{row['annotator_id']} / {row['status']}" for row in annotations
        )
        lines.extend(_format_counts("ANNOTATION STATUS", annotation_counts))
        lines.extend(["", f"RECENT {recent} VIDEO FILES (newest mtime first)"])

        for index, video_path in enumerate(
            _recent_video_files(paths.douk_download_root, recent),
            start=1,
        ):
            row = conn.execute(
                """
                SELECT
                    v.id, v.video_id, v.video_path,
                    p.status AS preparation_status, p.error,
                    p.recognized_title, p.recognized_artist,
                    p.song_offset, p.match_score,
                    r.status AS review_status,
                    a.status AS owner_annotation_status,
                    a.song_verified
                FROM videos v
                LEFT JOIN music_preparations p ON p.video_id=v.id
                LEFT JOIN song_reviews r
                  ON r.video_id=v.id AND r.reviewer_id='owner'
                LEFT JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id='owner'
                WHERE v.video_id=?
                LIMIT 1
                """,
                (video_path.name,),
            ).fetchone()
            lines.append(f"\n[{index}] {video_path.name}")
            lines.append(f"  file: {video_path}")
            if row is None:
                lines.append("  pipeline_stage: not_in_database")
                lines.append(
                    "  next_action: run recognition/import for this video"
                )
                continue
            preparation_status = row["preparation_status"] or "(none)"
            review_status = row["review_status"] or "(none)"
            annotation_status = row["owner_annotation_status"] or "(none)"
            lines.extend(
                [
                    f"  preparation_status: {preparation_status}",
                    f"  review_status: {review_status}",
                    f"  owner_annotation_status: {annotation_status}",
                    f"  song_verified: {row['song_verified'] or '(none)'}",
                    (
                        "  recognized: "
                        f"{row['recognized_title'] or '(none)'} - "
                        f"{row['recognized_artist'] or '(none)'}"
                    ),
                    f"  song_offset: {row['song_offset']}",
                    f"  match_score: {row['match_score']}",
                ]
            )
            if row["error"]:
                lines.append(f"  error: {str(row['error']).replace(chr(10), ' ')[:1200]}")

    log_dir = paths.output_dir / "server" / "logs"
    lines.extend(["", "SERVICE LOG TAILS"])
    for name in LOG_NAMES:
        path = log_dir / name
        lines.append(f"\n--- {name} ---")
        if not path.is_file():
            lines.append("(missing)")
            continue
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines.extend(content[-max(1, log_lines) :])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a privacy-safe server status and log report."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--recent", type=int, default=20)
    parser.add_argument("--log-lines", type=int, default=80)
    parser.add_argument(
        "--out",
        default=str(PATHS.output_dir / "server" / "diagnostics" / "latest.txt"),
    )
    args = parser.parse_args()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_diagnostic_report(
        Path(args.db),
        recent=args.recent,
        log_lines=args.log_lines,
    )
    output.write_text(report, encoding="utf-8")
    print(f"Diagnostic report: {output}")


if __name__ == "__main__":
    main()
