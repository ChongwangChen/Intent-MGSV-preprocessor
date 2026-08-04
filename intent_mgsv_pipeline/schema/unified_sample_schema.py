from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CONTENT_TYPES = ("video", "image", "text")

UNIFIED_COLUMNS = (
    "sample_id",
    "content_type",
    "content_id",
    "content_path",
    "content_paths",
    "content_text",
    "content_duration",
    "music_id",
    "source_audio_path",
    "full_song_path",
    "music_start",
    "music_end",
    "song_title",
    "song_artist",
    "genre",
    "vocal_presence",
    "emotion",
    "style",
    "usage_scene",
    "sync_level",
    "shot_points",
    "shot_points_3",
    "shot_points_5",
    "seg_scores_3",
    "seg_scores_5",
    "overall_score",
    "pair_verified",
    "source",
    "source_url",
    "annotator_id",
    "annotation_status",
    "group_id",
    "split",
)


@dataclass(frozen=True)
class ValidationIssue:
    sample_id: str
    field: str
    code: str
    message: str


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"nan", "null"} else text


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def parse_content_paths(
    value: Any,
    *,
    fallback: Any = "",
) -> list[str]:
    paths: list[str] = []
    if isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        text = _text(value)
        candidates: list[Any] = []
        if text:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, list):
                candidates = parsed
            elif "||" in text:
                candidates = text.split("||")
            else:
                candidates = [text]

    for candidate in candidates:
        path = _text(candidate)
        if path and path not in paths:
            paths.append(path)
    fallback_path = _text(fallback)
    if fallback_path and fallback_path not in paths:
        paths.insert(0, fallback_path)
    return paths


def normalize_content_type(value: Any) -> str:
    text = _text(value).casefold()
    aliases = {
        "video": "video",
        "video_clip": "video",
        "image": "image",
        "picture": "image",
        "photo": "image",
        "text": "text",
        "prompt": "text",
        "\u89c6\u9891": "video",
        "\u56fe\u7247": "image",
        "\u56fe\u50cf": "image",
        "\u6587\u5b57": "text",
        "\u6587\u672c": "text",
    }
    return aliases.get(text, "")


def normalize_sync_level(value: Any, *, content_type: str) -> int:
    if content_type != "video":
        return 0
    text = _text(value).casefold()
    if text in {
        "1",
        "yes",
        "true",
        "sync",
        "soft sync",
        "hard sync",
        "\u662f",
        "\u5361\u70b9",
    }:
        return 1
    if text in {
        "0",
        "no",
        "false",
        "ambient",
        "\u5426",
        "\u975e\u5361\u70b9",
    }:
        return 0
    try:
        return 1 if float(text) >= 1 else 0
    except (TypeError, ValueError):
        return 0


def canonicalize_record(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = {str(key): value for key, value in row.items()}
    content_type = normalize_content_type(raw.get("content_type"))
    if not content_type:
        if _text(raw.get("image_path")):
            content_type = "image"
        elif _text(raw.get("content_text") or raw.get("text_prompt")):
            content_type = "text"
        else:
            content_type = "video"

    content_path = _text(raw.get("content_path"))
    if not content_path:
        source_field = "image_path" if content_type == "image" else "video_path"
        content_path = _text(raw.get(source_field))
    content_paths = parse_content_paths(
        raw.get("content_paths") or raw.get("image_paths"),
        fallback=content_path if content_type == "image" else "",
    )
    if content_type == "image" and not content_path and content_paths:
        content_path = content_paths[0]
    content_text = _text(
        raw.get("content_text")
        or raw.get("text_prompt")
        or raw.get("prompt")
    )
    content_id = _text(raw.get("content_id"))
    if not content_id:
        content_id = _text(
            raw.get("video_id")
            or raw.get("image_id")
            or raw.get("text_id")
        )
    if not content_id and content_path:
        content_id = Path(content_path.replace("\\", "/")).name

    sample_id = _text(raw.get("sample_id")) or content_id
    music_id = _text(raw.get("music_id"))
    song_path = _text(
        raw.get("full_song_path")
        or raw.get("music_path")
        or raw.get("full_music_path")
    )
    if not music_id and song_path:
        music_id = Path(song_path.replace("\\", "/")).stem

    record = {column: _text(raw.get(column)) for column in UNIFIED_COLUMNS}
    record.update(
        {
            "sample_id": sample_id,
            "content_type": content_type,
            "content_id": content_id,
            "content_path": content_path,
            "content_paths": (
                json.dumps(content_paths, ensure_ascii=False)
                if content_paths
                else ""
            ),
            "content_text": content_text,
            "content_duration": (
                _number(
                    raw.get("content_duration")
                    or raw.get("video_total_duration")
                    or raw.get("video_end")
                )
                or 0.0
            ),
            "music_id": music_id,
            "source_audio_path": _text(
                raw.get("source_audio_path")
                or raw.get("douyin_audio_path")
                or raw.get("clip_audio_path")
            ),
            "full_song_path": song_path,
            "music_start": _number(raw.get("music_start")),
            "music_end": _number(raw.get("music_end")),
            "sync_level": normalize_sync_level(
                raw.get("sync_level"),
                content_type=content_type,
            ),
            "overall_score": _number(raw.get("overall_score")),
            "video_id": _text(raw.get("video_id")),
            "image_id": _text(raw.get("image_id")),
            "text_id": _text(raw.get("text_id")),
            "raw": raw,
        }
    )
    return record


def validate_record(
    record: Mapping[str, Any],
    *,
    require_labels: bool = True,
) -> list[ValidationIssue]:
    sample_id = _text(record.get("sample_id")) or "<missing>"
    issues: list[ValidationIssue] = []

    def add(field: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(sample_id, field, code, message))

    content_type = normalize_content_type(record.get("content_type"))
    if content_type not in CONTENT_TYPES:
        add("content_type", "invalid_content_type", "Expected video, image, or text.")
    if not _text(record.get("content_id")):
        add("content_id", "missing_content_id", "A stable content ID is required.")
    if content_type == "video" and not _text(record.get("content_path")):
        add(
            "content_path",
            "missing_content_path",
            "Video samples require a content path.",
        )
    if content_type == "image" and not parse_content_paths(
        record.get("content_paths"),
        fallback=record.get("content_path"),
    ):
        add(
            "content_paths",
            "missing_content_path",
            "Image samples require one or more ordered content paths.",
        )
    if content_type == "text" and not _text(record.get("content_text")):
        add(
            "content_text",
            "missing_content_text",
            "Text samples require non-empty content text.",
        )
    if not _text(record.get("full_song_path")):
        add(
            "full_song_path",
            "missing_song_path",
            "A full-song path is required for grounding.",
        )

    music_start = _number(record.get("music_start"))
    music_end = _number(record.get("music_end"))
    if music_start is None or music_start < 0:
        add(
            "music_start",
            "invalid_music_start",
            "music_start must be a non-negative number.",
        )
    if music_end is None:
        add("music_end", "invalid_music_end", "music_end must be a number.")
    elif music_start is not None and music_end <= music_start:
        add(
            "music_end",
            "invalid_music_interval",
            "music_end must be greater than music_start.",
        )

    if require_labels:
        for field in ("emotion", "style", "usage_scene"):
            if not _text(record.get(field)):
                add(field, "missing_intent_label", f"{field} is required.")
        if not _text(record.get("genre")):
            add("genre", "missing_music_label", "genre is required.")
        if not _text(record.get("vocal_presence")):
            add(
                "vocal_presence",
                "missing_music_label",
                "vocal_presence is required.",
            )
    return issues
