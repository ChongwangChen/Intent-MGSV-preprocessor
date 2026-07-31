from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from intent_mgsv_pipeline.server.assignment import (
    get_annotation_record,
    release_assignment,
    save_annotation_patch,
)


SCORE_OPTIONS = ("1", "2", "3", "4", "5")
PEER_LABEL_FIELDS = ("emotion", "style", "usage_scene")
PEER_SCORE_FIELDS = ("seg_scores_3", "seg_scores_5")


def join_values(values: Iterable[Any] | str | None) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values.strip()
    return "/".join(
        str(value).strip()
        for value in values
        if value is not None
        and str(value).strip()
        and str(value).strip().casefold() not in {"none", "null"}
    )


def join_scores(values: Iterable[Any] | str | None) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values.strip()
    slots = [
        str(value).strip() if str(value).strip() in SCORE_OPTIONS else "-"
        for value in values
    ]
    while slots and slots[-1] == "-":
        slots.pop()
    return "/".join(slots)


def parse_points(value: Any) -> list[float]:
    points: list[float] = []
    for item in str(value or "").split("/"):
        try:
            points.append(float(item.strip()))
        except (TypeError, ValueError):
            continue
    return sorted(points)


def parse_scores(value: Any) -> list[str]:
    return [
        item.strip()
        for item in str(value or "").split("/")
        if item.strip() in SCORE_OPTIONS
    ]


def parse_score_slots(value: Any) -> list[str | None]:
    return [
        item.strip() if item.strip() in SCORE_OPTIONS else None
        for item in str(value or "").split("/")
    ]


def is_sync_yes(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    if text in {"yes", "true", "1", "是"}:
        return True
    try:
        return float(text) >= 1
    except (TypeError, ValueError):
        return False


def expected_score_counts(row: dict[str, Any]) -> tuple[int, int]:
    if not is_sync_yes(row.get("sync_level")):
        return 1, 0
    points_3 = parse_points(row.get("shot_points_3"))
    count_3 = len(points_3) + 1
    raw_5 = str(row.get("shot_points_5", "") or "").strip().upper()
    points_5 = parse_points(raw_5)
    count_5 = len(points_5) + 1 if points_5 and raw_5 not in {"SAME", "NONE"} else 0
    return count_3, count_5


def peer_required_missing(row: dict[str, Any]) -> list[str]:
    missing = [
        field
        for field in PEER_LABEL_FIELDS
        if not str(row.get(field, "") or "").strip()
    ]
    count_3, count_5 = expected_score_counts(row)
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


def save_peer_patch(
    db_path: Path,
    annotator_id: str,
    video_id: str,
    *,
    emotion: Iterable[str] | str | None,
    style: Iterable[str] | str | None,
    usage_scene: Iterable[str] | str | None,
    seg_scores_3: Iterable[str] | str | None,
    seg_scores_5: Iterable[str] | str | None,
) -> bool:
    patch = {
        "emotion": join_values(emotion),
        "style": join_values(style),
        "usage_scene": join_values(usage_scene),
        "seg_scores_3": join_scores(seg_scores_3),
        "seg_scores_5": join_scores(seg_scores_5),
        "status": "in_progress",
    }
    return save_annotation_patch(db_path, annotator_id, video_id, patch)


def complete_peer_annotation(
    db_path: Path,
    annotator_id: str,
    video_id: str,
    **values: Any,
) -> tuple[bool, list[str]]:
    if not save_peer_patch(db_path, annotator_id, video_id, **values):
        return False, ["save failed"]
    row = get_annotation_record(db_path, annotator_id, video_id)
    if row is None:
        return False, ["record missing"]
    missing = peer_required_missing(row)
    if missing:
        return False, missing
    return release_assignment(
        db_path,
        annotator_id,
        video_id,
        status="completed",
    ), []
