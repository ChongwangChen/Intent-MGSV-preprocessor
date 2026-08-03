from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    connect,
    init_db,
    loads_json,
    number,
)
from intent_mgsv_pipeline.server.music_review import confirm_music_review
from intent_mgsv_pipeline.server.verification import is_song_verified


def _read_only_connection(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].casefold() if text else ""


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _song_identity_matches(
    current: dict[str, Any],
    evidence: dict[str, Any],
) -> bool:
    current_mid = _normalized_text(current.get("qq_song_mid"))
    evidence_mid = _normalized_text(evidence.get("qq_song_mid"))
    if current_mid and evidence_mid:
        return current_mid == evidence_mid

    current_name = _basename(current.get("full_song_path"))
    evidence_name = _basename(evidence.get("full_song_path"))
    if current_name and evidence_name:
        return current_name == evidence_name

    current_title = _normalized_text(current.get("song_title"))
    evidence_title = _normalized_text(evidence.get("song_title"))
    current_artist = _normalized_text(current.get("song_artist"))
    evidence_artist = _normalized_text(evidence.get("song_artist"))
    return bool(
        current_title
        and current_artist
        and current_title == evidence_title
        and current_artist == evidence_artist
    )


def _annotation_rows(
    conn: sqlite3.Connection,
    owner_id: str,
) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            v.video_id, a.song_verified, a.music_start, a.genre,
            s.title AS song_title, s.artist AS song_artist,
            s.full_song_path, s.qq_song_mid
        FROM annotations a
        JOIN videos v ON v.id=a.video_id
        LEFT JOIN songs s ON s.id=a.song_id
        WHERE a.annotator_id=? AND v.deleted_at IS NULL
        """,
        (owner_id,),
    ).fetchall()
    return {str(row["video_id"]): dict(row) for row in rows}


def inspect_legacy_confirmation_candidates(
    db_path: Path,
    evidence_db: Path,
    *,
    owner_id: str = "owner",
    offset_tolerance: float = 0.25,
    limit: int = 0,
) -> list[dict[str, Any]]:
    init_db(db_path)
    if not evidence_db.is_file():
        raise FileNotFoundError(f"Evidence database not found: {evidence_db}")

    with _read_only_connection(evidence_db) as evidence_conn:
        evidence_rows = _annotation_rows(evidence_conn, owner_id)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                v.video_id, p.status AS preparation_status,
                p.download_source, p.raw_json AS preparation_raw_json,
                p.song_offset, p.qq_song_mid,
                p.full_song_path AS preparation_song_path,
                a.song_verified, a.music_start, a.genre,
                s.title AS song_title, s.artist AS song_artist,
                s.full_song_path AS song_path, s.qq_song_mid AS song_mid
            FROM music_preparations p
            JOIN videos v ON v.id=p.video_id
            JOIN annotations a
              ON a.video_id=v.id AND a.annotator_id=?
            LEFT JOIN songs s ON s.id=p.song_id
            WHERE v.deleted_at IS NULL
              AND p.status='needs_review'
            ORDER BY v.id
            """,
            (owner_id,),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        video_id = str(row["video_id"])
        evidence = evidence_rows.get(video_id)
        raw = loads_json(row["preparation_raw_json"])
        current_identity = {
            "qq_song_mid": row["qq_song_mid"] or row["song_mid"],
            "full_song_path": (
                row["preparation_song_path"] or row["song_path"]
            ),
            "song_title": row["song_title"],
            "song_artist": row["song_artist"],
        }

        if (
            row["download_source"] != "restored_annotation"
            or not raw.get("restored_from_annotation")
        ):
            state = "not_restored_queue"
        elif evidence is None:
            state = "missing_evidence"
        elif not is_song_verified(evidence.get("song_verified")):
            state = "not_previously_verified"
        elif not _song_identity_matches(current_identity, evidence):
            state = "song_mismatch"
        else:
            current_offset = number(row["song_offset"])
            evidence_offset = number(evidence.get("music_start"))
            if current_offset is None or evidence_offset is None:
                state = "missing_offset"
            elif abs(current_offset - evidence_offset) > offset_tolerance:
                state = "offset_mismatch"
            elif not str(row["genre"] or evidence.get("genre") or "").strip():
                state = "missing_genre"
            else:
                state = "ready"

        results.append(
            {
                "state": state,
                "video_id": video_id,
                "song_offset": number(row["song_offset"]),
                "evidence_offset": (
                    number(evidence.get("music_start")) if evidence else None
                ),
                "genre": str(
                    row["genre"]
                    or (evidence.get("genre") if evidence else "")
                    or ""
                ).strip(),
                "current_song": current_identity,
                "evidence_song": evidence or {},
                "current_song_verified": str(row["song_verified"] or ""),
            }
        )
        if limit and len(results) >= limit:
            break
    return results


def recover_legacy_music_confirmations(
    db_path: Path,
    evidence_db: Path,
    *,
    owner_id: str = "owner",
    reviewer_id: str = "",
    offset_tolerance: float = 0.25,
    limit: int = 0,
    apply: bool = False,
) -> dict[str, Any]:
    reviewer_id = str(reviewer_id or "").strip() or owner_id
    candidates = inspect_legacy_confirmation_candidates(
        db_path,
        evidence_db,
        owner_id=owner_id,
        offset_tolerance=offset_tolerance,
        limit=limit,
    )
    counts = Counter(item["state"] for item in candidates)
    confirmed = 0
    errors: list[str] = []
    if apply:
        for item in candidates:
            if item["state"] != "ready":
                continue
            ok, message = confirm_music_review(
                db_path,
                reviewer_id,
                item["video_id"],
                corrected_offset=float(item["song_offset"]),
                final_genre=item["genre"],
                note=f"Recovered from verified backup: {evidence_db.name}",
                owner_id=owner_id,
            )
            if ok:
                confirmed += 1
            else:
                errors.append(f"{item['video_id']}: {message}")
    return {
        "mode": "apply" if apply else "dry-run",
        "owner_id": owner_id,
        "reviewer_id": reviewer_id,
        "evidence_db": str(evidence_db),
        "scanned": len(candidates),
        "ready": counts["ready"],
        "not_previously_verified": counts["not_previously_verified"],
        "missing_evidence": counts["missing_evidence"],
        "song_mismatch": counts["song_mismatch"],
        "offset_mismatch": counts["offset_mismatch"],
        "missing_offset": counts["missing_offset"],
        "missing_genre": counts["missing_genre"],
        "not_restored_queue": counts["not_restored_queue"],
        "confirmed": confirmed,
        "errors": errors,
        "items": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recover trusted legacy song confirmations from a SQLite backup."
        )
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--evidence-db", required=True)
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument(
        "--reviewer-id",
        default="",
        help="Defaults to --owner-id so restored reviews count in owner progress.",
    )
    parser.add_argument("--offset-tolerance", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report",
        default=str(
            PATHS.output_dir
            / "server"
            / "diagnostics"
            / "recover_legacy_music_confirmations.json"
        ),
    )
    args = parser.parse_args()
    summary = recover_legacy_music_confirmations(
        Path(args.db),
        Path(args.evidence_db),
        owner_id=args.owner_id,
        reviewer_id=args.reviewer_id,
        offset_tolerance=max(0.0, args.offset_tolerance),
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
