from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from intent_mgsv_pipeline.server.assignment import (
    get_annotation_record,
    release_assignment,
    save_annotation_patch,
)
from intent_mgsv_pipeline.server.peer_annotation import (
    PEER_LABEL_FIELDS,
    SCORE_OPTIONS,
    expected_score_counts,
    is_sync_yes,
    join_scores,
    join_values,
    parse_points,
    parse_score_slots,
)
from intent_mgsv_pipeline.server.verification import is_song_verified


VOCAL_OPTIONS = ("None", "Partial", "Full")
SYNC_OPTIONS = ("Yes", "No")


def normalize_sync(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text in {"yes", "true", "1", "是"}:
        return "Yes"
    if text in {"no", "false", "0", "否"}:
        return "No"
    try:
        return "Yes" if float(text) >= 1 else "No"
    except (TypeError, ValueError):
        pass
    return ""


def owner_required_missing(row: dict[str, Any]) -> list[str]:
    missing = [
        field
        for field in PEER_LABEL_FIELDS
        if not str(row.get(field, "") or "").strip()
    ]
    vocal = str(row.get("vocal_presence", "") or "").strip()
    if vocal not in VOCAL_OPTIONS:
        missing.append("vocal_presence")
    if not str(row.get("genre", "") or "").strip():
        missing.append("genre")
    if not is_song_verified(row.get("song_verified", "")):
        missing.append("song confirmation")

    sync = normalize_sync(row.get("sync_level"))
    if not sync:
        missing.append("sync_level")
    raw_points_3 = str(row.get("shot_points_3", "") or "").strip().upper()
    if (
        sync == "Yes"
        and raw_points_3 != "NONE"
        and not parse_points(raw_points_3)
    ):
        missing.append("shot_points_3")

    count_3, count_5 = expected_score_counts(
        {**row, "sync_level": sync}
    )
    scores_3 = parse_score_slots(row.get("seg_scores_3"))
    scores_5 = parse_score_slots(row.get("seg_scores_5"))
    completed_3 = sum(
        index < len(scores_3) and scores_3[index] in SCORE_OPTIONS
        for index in range(count_3)
    )
    completed_5 = sum(
        index < len(scores_5) and scores_5[index] in SCORE_OPTIONS
        for index in range(count_5)
    )
    if completed_3 < count_3:
        missing.append(f"seg_scores_3 ({completed_3}/{count_3})")
    if count_5 and completed_5 < count_5:
        missing.append(f"seg_scores_5 ({completed_5}/{count_5})")
    return missing


def save_owner_patch(
    db_path: Path,
    owner_id: str,
    video_id: str,
    *,
    sync_level: Any,
    shot_points_3: Any,
    shot_points_5: Any,
    vocal_presence: Any,
    genre: Any,
    emotion: Iterable[str] | str | None,
    style: Iterable[str] | str | None,
    usage_scene: Iterable[str] | str | None,
    seg_scores_3: Iterable[str] | str | None,
    seg_scores_5: Iterable[str] | str | None,
) -> bool:
    vocal = str(vocal_presence or "").strip()
    patch = {
        "sync_level": normalize_sync(sync_level),
        "shot_points_3": str(shot_points_3 or "").strip(),
        "shot_points_5": str(shot_points_5 or "").strip(),
        "vocal_presence": vocal if vocal in VOCAL_OPTIONS else "",
        "genre": str(genre or "").strip(),
        "emotion": join_values(emotion),
        "style": join_values(style),
        "usage_scene": join_values(usage_scene),
        "seg_scores_3": join_scores(seg_scores_3),
        "seg_scores_5": join_scores(seg_scores_5),
    }
    saved = save_annotation_patch(db_path, owner_id, video_id, patch)
    if not saved:
        return False
    row = get_annotation_record(db_path, owner_id, video_id)
    if (
        row is not None
        and row.get("status") == "completed"
        and owner_required_missing(row)
    ):
        save_annotation_patch(
            db_path,
            owner_id,
            video_id,
            {"status": "in_progress"},
        )
    return True


def complete_owner_annotation(
    db_path: Path,
    owner_id: str,
    video_id: str,
    **values: Any,
) -> tuple[bool, list[str]]:
    if not save_owner_patch(db_path, owner_id, video_id, **values):
        return False, ["save failed"]
    row = get_annotation_record(db_path, owner_id, video_id)
    if row is None:
        return False, ["record missing"]
    missing = owner_required_missing(row)
    if missing:
        return False, missing
    return release_assignment(
        db_path,
        owner_id,
        video_id,
        status="completed",
    ), []
