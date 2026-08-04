from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from intent_mgsv_pipeline.music_preparation.alignment import (
    AlignmentResult,
    align_video_to_song,
    probe_media_duration,
)
from intent_mgsv_pipeline.music_preparation.genre import suggest_genre
from intent_mgsv_pipeline.music_preparation.qqmusic import (
    QQMusicCandidate,
    candidate_is_acceptable,
    download_fallback,
    download_qq_candidate,
    normalize_song_text,
    search_qqmusic,
)
from intent_mgsv_pipeline.runtime_config import PATHS, RuntimePaths
from intent_mgsv_pipeline.server.db import (
    DEFAULT_DB,
    connect,
    dumps_json,
    init_db,
    loads_json,
    log_event,
)


FINAL_PREPARATION_STATUSES = {"ready_for_review", "needs_review", "verified"}
RETRYABLE_PREPARATION_STATUSES = {
    "recognition_failed",
    "search_failed",
    "download_failed",
    "alignment_failed",
    "needs_manual",
    "needs_realign",
}


def _value(record: dict[str, Any], name: str) -> str:
    value = record.get(name, "")
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _number(record: dict[str, Any], name: str) -> float | None:
    value = record.get(name)
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_recognition_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"recognition tracking file does not exist: {path}")
    data = pd.read_excel(path, keep_default_na=False)
    if "video_id" not in data.columns:
        raise RuntimeError(f"tracking file has no video_id column: {path}")
    return data.to_dict("records")


def load_dataset_video_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = pd.read_excel(path, usecols=lambda name: name == "video_id")
    if "video_id" not in data.columns:
        return set()
    return {
        str(value).strip()
        for value in data["video_id"].dropna()
        if str(value).strip()
    }


def _video_index(paths: RuntimePaths) -> dict[str, Path]:
    if not paths.douk_download_root.exists():
        return {}
    return {
        path.name: path
        for path in paths.douk_download_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    }


def _resolve_video_path(
    record: dict[str, Any],
    video_index: dict[str, Path],
    paths: RuntimePaths,
) -> Path | None:
    raw = _value(record, "video_path")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = paths.project_root / path
        if path.exists():
            return path
    return video_index.get(_value(record, "video_id"))


def _ensure_video(conn: Any, record: dict[str, Any], video_path: Path) -> int:
    video_id = _value(record, "video_id")
    row = conn.execute("SELECT id FROM videos WHERE video_id=?", (video_id,)).fetchone()
    if row:
        conn.execute(
            """
            UPDATE videos
            SET video_path=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (str(video_path), row["id"]),
        )
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO videos(video_id, video_path, video_title, row_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            video_id,
            str(video_path),
            video_path.stem,
            dumps_json({"video_id": video_id, "video_path": str(video_path)}),
        ),
    )
    return int(cursor.lastrowid)


def _upsert_preparation(
    conn: Any,
    video_db_id: int,
    values: dict[str, Any],
) -> None:
    allowed = {
        "song_id",
        "recognized_title",
        "recognized_artist",
        "recognition_confidence",
        "recognition_votes",
        "genre_suggestion",
        "genre_source",
        "genre_confidence",
        "qq_song_mid",
        "qq_match_score",
        "download_source",
        "full_song_path",
        "song_offset",
        "video_audio_start",
        "aligned_duration",
        "match_score",
        "status",
        "error",
        "raw_json",
    }
    values = {key: value for key, value in values.items() if key in allowed}
    existing = conn.execute(
        "SELECT video_id FROM music_preparations WHERE video_id=?",
        (video_db_id,),
    ).fetchone()
    if existing:
        assignments = ", ".join(f"{key}=?" for key in values)
        conn.execute(
            f"""
            UPDATE music_preparations
            SET {assignments}, updated_at=CURRENT_TIMESTAMP
            WHERE video_id=?
            """,
            tuple(values.values()) + (video_db_id,),
        )
        return
    columns = ["video_id", *values.keys()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO music_preparations({", ".join(columns)})
        VALUES ({placeholders})
        """,
        (video_db_id, *values.values()),
    )


def _find_existing_song(
    conn: Any,
    *,
    song_mid: str = "",
    title: str = "",
    artist: str = "",
) -> Any | None:
    if song_mid:
        row = conn.execute(
            "SELECT * FROM songs WHERE qq_song_mid=?",
            (song_mid,),
        ).fetchone()
        if row and row["full_song_path"] and Path(row["full_song_path"]).exists():
            return row
    title_key = normalize_song_text(title)
    artist_key = normalize_song_text(artist)
    if not title_key:
        return None
    for row in conn.execute(
        "SELECT * FROM songs WHERE full_song_path IS NOT NULL"
    ).fetchall():
        if not Path(row["full_song_path"]).exists():
            continue
        if normalize_song_text(row["title"]) != title_key:
            continue
        if artist_key and normalize_song_text(row["artist"]) != artist_key:
            continue
        return row
    return None


def _upsert_song(
    conn: Any,
    *,
    title: str,
    artist: str,
    album: str,
    path: Path,
    song_mid: str,
    source: str,
    metadata: dict[str, Any],
) -> int:
    existing = _find_existing_song(
        conn,
        song_mid=song_mid,
        title=title,
        artist=artist,
    )
    if existing:
        song_id = int(existing["id"])
        conn.execute(
            """
            UPDATE songs
            SET title=?, artist=?, album=?, full_song_path=?, qq_song_mid=?,
                source=?, row_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                title,
                artist,
                album,
                str(path),
                song_mid or None,
                source,
                dumps_json(metadata),
                song_id,
            ),
        )
        return song_id
    cursor = conn.execute(
        """
        INSERT INTO songs(
            title, artist, album, full_song_path, qq_song_mid, source, row_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            artist,
            album,
            str(path),
            song_mid or None,
            source,
            dumps_json(metadata),
        ),
    )
    return int(cursor.lastrowid)


def _candidate_payload(candidate: QQMusicCandidate | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    return {
        "qq_song_mid": candidate.song_mid,
        "qq_title": candidate.title,
        "qq_artist": candidate.artist,
        "qq_album": candidate.album,
        "qq_duration": candidate.duration,
        "qq_title_score": candidate.title_score,
        "qq_artist_score": candidate.artist_score,
        "qq_match_score": candidate.match_score,
    }


def _accept_alignment(result: AlignmentResult) -> bool:
    return result.status in {"ready_for_review", "needs_review"}


def _try_candidate(
    conn: Any,
    video_path: Path,
    candidate: QQMusicCandidate,
    paths: RuntimePaths,
) -> tuple[Path | None, str, AlignmentResult]:
    existing = _find_existing_song(
        conn,
        song_mid=candidate.song_mid,
        title=candidate.title,
        artist=candidate.artist,
    )
    if existing:
        song_path = Path(existing["full_song_path"])
        source = "reused"
    else:
        song_path, source = download_qq_candidate(
            candidate,
            output_dir=paths.full_songs_dir,
        )
    if not song_path:
        return None, source, AlignmentResult(
            None,
            0.0,
            None,
            0,
            "download_failed",
            source,
        )
    alignment = align_video_to_song(video_path, song_path)
    return song_path, source, alignment


def _store_success(
    conn: Any,
    *,
    video_db_id: int,
    video_duration: float | None,
    candidate: QQMusicCandidate | None,
    title: str,
    artist: str,
    album: str,
    song_path: Path,
    source: str,
    alignment: AlignmentResult,
    base_values: dict[str, Any],
) -> None:
    song_mid = candidate.song_mid if candidate else ""
    song_id = _upsert_song(
        conn,
        title=title,
        artist=artist,
        album=album,
        path=song_path,
        song_mid=song_mid,
        source=source,
        metadata=_candidate_payload(candidate),
    )
    aligned_duration = None
    if video_duration is not None:
        aligned_duration = max(0.0, video_duration - alignment.video_audio_start)
    _upsert_preparation(
        conn,
        video_db_id,
        {
            **base_values,
            "song_id": song_id,
            "qq_song_mid": song_mid or None,
            "qq_match_score": candidate.match_score if candidate else None,
            "download_source": source,
            "full_song_path": str(song_path),
            "song_offset": alignment.song_offset,
            "video_audio_start": alignment.video_audio_start,
            "aligned_duration": aligned_duration,
            "match_score": alignment.score,
            "status": alignment.status,
            "error": alignment.error,
            "raw_json": dumps_json(
                {
                    **_candidate_payload(candidate),
                    "alignment_votes": alignment.votes,
                }
            ),
        },
    )


def prepare_record(
    conn: Any,
    record: dict[str, Any],
    video_path: Path,
    paths: RuntimePaths,
    *,
    retry_failed: bool,
    fallback_sources: tuple[str, ...],
) -> str:
    video_db_id = _ensure_video(conn, record, video_path)
    current = conn.execute(
        "SELECT * FROM music_preparations WHERE video_id=?",
        (video_db_id,),
    ).fetchone()
    if current and current["status"] in FINAL_PREPARATION_STATUSES:
        return "skipped"
    if (
        current
        and current["status"] in RETRYABLE_PREPARATION_STATUSES
        and not retry_failed
    ):
        return "skipped"

    title = _value(record, "song_title")
    artist = _value(record, "song_artist")
    acr_genre = _value(record, "genre_suggested") or _value(record, "acr_genre")
    genre, genre_source, genre_confidence = suggest_genre(title, artist, acr_genre)
    base_values = {
        "recognized_title": title,
        "recognized_artist": artist,
        "recognition_confidence": _number(record, "acr_confidence"),
        "recognition_votes": int(_number(record, "recognition_votes") or 0),
        "genre_suggestion": genre,
        "genre_source": genre_source,
        "genre_confidence": genre_confidence,
    }
    if not title:
        _upsert_preparation(
            conn,
            video_db_id,
            {
                **base_values,
                "status": "recognition_failed",
                "error": _value(record, "recognition_error") or "no recognized title",
            },
        )
        return "failed"

    _upsert_preparation(
        conn,
        video_db_id,
        {**base_values, "status": "searching", "error": ""},
    )
    # Do not hold a SQLite write lock while network downloads and alignment run.
    conn.commit()
    errors: list[str] = []
    try:
        search_results = search_qqmusic(title, artist)
    except Exception as exc:
        search_results = []
        errors.append(str(exc))
    rejected_song_mids: set[str] = set()
    if current and current["status"] == "needs_manual":
        rejected_mid = str(current["qq_song_mid"] or "").strip()
        if rejected_mid:
            rejected_song_mids.add(rejected_mid)
    accepted = [
        candidate
        for candidate in search_results
        if (
            candidate.song_mid
            and candidate.song_mid not in rejected_song_mids
            and candidate_is_acceptable(candidate, bool(artist))
        )
    ][:3]
    deferred_review: tuple[
        QQMusicCandidate,
        Path,
        str,
        AlignmentResult,
    ] | None = None

    for candidate in accepted:
        print(
            f"  QQ candidate: {candidate.title} - {candidate.artist} "
            f"(metadata={candidate.match_score:.2f})"
        )
        song_path, source, alignment = _try_candidate(
            conn,
            video_path,
            candidate,
            paths,
        )
        if song_path and _accept_alignment(alignment):
            _store_success(
                conn,
                video_db_id=video_db_id,
                video_duration=(
                    _number(record, "video_total_duration")
                    or probe_media_duration(video_path)
                ),
                candidate=candidate,
                title=candidate.title,
                artist=candidate.artist,
                album=candidate.album,
                song_path=song_path,
                source=source,
                alignment=alignment,
                base_values=base_values,
            )
            conn.commit()
            log_event(
                conn,
                "music_prepared",
                actor="music_pipeline",
                target_type="video",
                target_id=_value(record, "video_id"),
                payload={
                    "source": source,
                    "song_mid": candidate.song_mid,
                    "match_score": alignment.score,
                    "status": alignment.status,
                },
            )
            return alignment.status
        if song_path:
            if (
                deferred_review is None
                or (alignment.score or 0) > (deferred_review[3].score or 0)
            ):
                deferred_review = (candidate, song_path, source, alignment)
        errors.append(
            f"{candidate.song_mid}: {alignment.status}: "
            f"{alignment.error or alignment.score}"
        )

    if fallback_sources:
        song_path, source, error = download_fallback(
            title,
            artist,
            output_dir=paths.full_songs_dir,
            sources=fallback_sources,
        )
        if song_path:
            alignment = align_video_to_song(video_path, song_path)
            if _accept_alignment(alignment):
                _store_success(
                    conn,
                    video_db_id=video_db_id,
                    video_duration=(
                        _number(record, "video_total_duration")
                        or probe_media_duration(video_path)
                    ),
                    candidate=None,
                    title=title,
                    artist=artist,
                    album="",
                    song_path=song_path,
                    source=source,
                    alignment=alignment,
                    base_values=base_values,
                )
                conn.commit()
                return alignment.status
            errors.append(
                f"{source}: {alignment.status}: "
                f"{alignment.error or alignment.score}"
            )
        elif error:
            errors.append(error)

    if deferred_review is not None:
        candidate, song_path, source, alignment = deferred_review
        review_alignment = AlignmentResult(
            alignment.song_offset,
            alignment.video_audio_start,
            alignment.score,
            alignment.votes,
            "needs_review",
            (
                "Automatic alignment was not reliable; "
                "the downloaded QQ candidate requires human review. "
                f"{alignment.error}"
            ).strip(),
        )
        _store_success(
            conn,
            video_db_id=video_db_id,
            video_duration=(
                _number(record, "video_total_duration")
                or probe_media_duration(video_path)
            ),
            candidate=candidate,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
            song_path=song_path,
            source=source,
            alignment=review_alignment,
            base_values=base_values,
        )
        conn.commit()
        log_event(
            conn,
            "music_prepared_for_manual_alignment",
            actor="music_pipeline",
            target_type="video",
            target_id=_value(record, "video_id"),
            payload={
                "source": source,
                "song_mid": candidate.song_mid,
                "match_score": alignment.score,
            },
        )
        return "needs_review"

    final_status = "search_failed" if not accepted else "alignment_failed"
    _upsert_preparation(
        conn,
        video_db_id,
        {
            **base_values,
            "status": final_status,
            "error": " | ".join(errors)[-4000:],
        },
    )
    conn.commit()
    return "failed"


def prepare_recognized_music(
    *,
    paths: RuntimePaths = PATHS,
    db_path: Path = DEFAULT_DB,
    retry_failed: bool = False,
    limit: int = 0,
    video_ids: Iterable[str] | None = None,
    fallback_sources: tuple[str, ...] | None = None,
    include_existing_dataset: bool = False,
) -> dict[str, int]:
    init_db(db_path)
    records = load_recognition_records(paths.acr_tracking_excel)
    requested = {str(value) for value in video_ids} if video_ids is not None else None
    if fallback_sources is None:
        fallback_sources = tuple(
            source.strip()
            for source in os.environ.get(
                "MGSV_MUSIC_FALLBACK_SOURCES",
                "youtube,bilibili",
            ).split(",")
            if source.strip()
        )
    video_index = _video_index(paths)
    dataset_video_ids = (
        set()
        if include_existing_dataset
        else load_dataset_video_ids(paths.master_excel)
    )
    counts = {
        "scanned": 0,
        "skipped_dataset": 0,
        "missing_video": 0,
        "processed": 0,
        "ready_for_review": 0,
        "needs_review": 0,
        "failed": 0,
        "skipped": 0,
    }
    with connect(db_path) as conn:
        for record in records:
            video_id = _value(record, "video_id")
            if requested is not None and video_id not in requested:
                continue
            counts["scanned"] += 1
            if video_id in dataset_video_ids:
                counts["skipped_dataset"] += 1
                continue
            if limit and counts["processed"] >= limit:
                break
            video_path = _resolve_video_path(record, video_index, paths)
            if video_path is None:
                counts["missing_video"] += 1
                continue
            print(f"\nPreparing music: {video_id}")
            result = prepare_record(
                conn,
                record,
                video_path,
                paths,
                retry_failed=retry_failed,
                fallback_sources=fallback_sources,
            )
            conn.commit()
            if result == "skipped":
                counts["skipped"] += 1
                continue
            counts["processed"] += 1
            if result in {"ready_for_review", "needs_review"}:
                counts[result] += 1
            else:
                counts["failed"] += 1
    return counts
