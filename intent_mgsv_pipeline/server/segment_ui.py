from __future__ import annotations

import html
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.server.peer_annotation import (
    is_sync_yes,
    parse_points,
    parse_score_slots,
)


MAX_SCORE_SLOTS = 12
FLOATING_VIDEO_CLASS = "mgsv-segment-mini-video"
SEGMENT_VIDEO_CSS = f"""
video.{FLOATING_VIDEO_CLASS} {{
    position: fixed !important;
    top: 14px !important;
    right: 14px !important;
    width: min(380px, calc(100vw - 28px)) !important;
    height: auto !important;
    max-height: 44vh !important;
    z-index: 9999 !important;
    object-fit: contain !important;
    background: #000 !important;
    border-radius: 6px !important;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.45) !important;
}}
@media (max-width: 640px) {{
    video.{FLOATING_VIDEO_CLASS} {{
        top: 8px !important;
        right: 8px !important;
        width: calc(100vw - 16px) !important;
        max-height: 38vh !important;
    }}
}}
"""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def video_duration(row: dict[str, Any]) -> float | None:
    for key in ("duration", "video_total_duration"):
        duration = _number(row.get(key))
        if duration is not None and duration > 0:
            return duration
    return None


def _bounds(
    points: list[float],
    duration: float | None,
) -> list[tuple[float, float | None]]:
    ends: list[float | None] = [*points, duration]
    return list(zip([0.0, *points], ends))


def segment_schemes(
    row: dict[str, Any],
) -> tuple[
    list[tuple[float, float | None]],
    str,
    list[tuple[float, float | None]] | None,
]:
    duration = video_duration(row)
    if not is_sync_yes(row.get("sync_level")):
        return _bounds([], duration), "整段评分（非卡点视频）", None

    raw_a = str(row.get("shot_points_3", "") or "").strip()
    if not raw_a or raw_a.casefold() == "nan":
        return [], "尚无方案 A 分镜点", None
    if raw_a.upper() == "NONE":
        return _bounds([], duration), "未检测到分镜点，按整段评分", None

    points_a = [
        point
        for point in parse_points(raw_a)
        if point > 0 and (duration is None or point < duration)
    ]
    if not points_a:
        return _bounds([], duration), "分镜点不在视频时间范围内，按整段评分", None

    bounds_a = _bounds(points_a, duration)
    caption = f"方案 A（Top-3 分镜点）：{len(bounds_a)} 段"

    raw_b = str(row.get("shot_points_5", "") or "").strip()
    bounds_b = None
    if raw_b and raw_b.upper() not in {"SAME", "NONE"} and raw_b.casefold() != "nan":
        points_b = [
            point
            for point in parse_points(raw_b)
            if point > 0 and (duration is None or point < duration)
        ]
        if points_b and points_b != points_a:
            bounds_b = _bounds(points_b, duration)
    return bounds_a, caption, bounds_b


def _range_text(start: float, end: float | None) -> str:
    if end is None:
        return f"{start:.1f}s - 结尾"
    return f"{start:.1f}s - {end:.1f}s"


def segment_html(
    row: dict[str, Any],
    *,
    video_elem_id: str,
) -> str:
    bounds_a, caption, bounds_b = segment_schemes(row)
    if not bounds_a:
        return f'<p style="color:#b45309">{html.escape(caption)}</p>'

    chunks = [f"<p><strong>{html.escape(caption)}</strong></p>"]
    if bounds_b:
        chunks.append(
            f"<p><strong>方案 B（Top-5 分镜点）："
            f"{len(bounds_b)} 段</strong></p>"
        )
    return "".join(chunks)


def segment_payload(row: dict[str, Any]) -> dict[str, list[dict[str, float | None]]]:
    bounds_a, _, bounds_b = segment_schemes(row)
    return {
        "A": [
            {"start": start, "end": end}
            for start, end in bounds_a
        ],
        "B": [
            {"start": start, "end": end}
            for start, end in (bounds_b or [])
        ],
    }


def _playback_button_updates(
    bounds: list[tuple[float, float | None]],
    scheme: str,
) -> list[Any]:
    updates = []
    for index in range(MAX_SCORE_SLOTS):
        if index < len(bounds):
            start, end = bounds[index]
            updates.append(
                gr.update(
                    visible=True,
                    value=(
                        f"播放 {scheme}段{index + 1} "
                        f"({_range_text(start, end)})"
                    ),
                )
            )
        else:
            updates.append(
                gr.update(
                    visible=False,
                    value=f"播放 {scheme}段{index + 1}",
                )
            )
    return updates


def segment_playback_updates(row: dict[str, Any]) -> tuple[Any, ...]:
    bounds_a, _, bounds_b = segment_schemes(row)
    return (
        segment_payload(row),
        *_playback_button_updates(bounds_a, "A"),
        *_playback_button_updates(bounds_b or [], "B"),
    )


def segment_play_js(
    video_elem_id: str,
    scheme: str,
    index: int,
) -> str:
    selector = f"#{video_elem_id} video"
    return f"""(segments) => {{
        const item = segments && segments["{scheme}"]
            ? segments["{scheme}"][{index}]
            : null;
        const video = document.querySelector("{selector}");
        if (!item || !video) return;
        video.classList.add("{FLOATING_VIDEO_CLASS}");
        video.ontimeupdate = null;
        video.currentTime = Number(item.start || 0);
        const end = item.end;
        if (end !== null && end !== undefined) {{
            video.ontimeupdate = () => {{
                if (video.currentTime >= Number(end) - 0.05) {{
                    video.pause();
                    video.ontimeupdate = null;
                }}
            }};
        }}
        const promise = video.play();
        if (promise && promise.catch) promise.catch(() => {{}});
    }}"""


def segment_close_js(video_elem_id: str) -> str:
    selector = f"#{video_elem_id} video"
    return f"""() => {{
        const video = document.querySelector("{selector}");
        if (!video) return;
        video.classList.remove("{FLOATING_VIDEO_CLASS}");
        if (document.pictureInPictureElement === video) {{
            document.exitPictureInPicture();
        }}
    }}"""


def segment_pip_js(video_elem_id: str) -> str:
    selector = f"#{video_elem_id} video"
    return f"""() => {{
        const video = document.querySelector("{selector}");
        if (!video || !video.requestPictureInPicture) return;
        if (document.pictureInPictureElement === video) {{
            document.exitPictureInPicture();
        }} else {{
            const promise = video.requestPictureInPicture();
            if (promise && promise.catch) promise.catch(() => {{}});
        }}
    }}"""


def _score_updates_for_bounds(
    bounds: list[tuple[float, float | None]],
    values: list[str | None],
    scheme: str,
) -> list[Any]:
    updates = []
    for index in range(MAX_SCORE_SLOTS):
        if index < len(bounds):
            start, end = bounds[index]
            updates.append(
                gr.update(
                    visible=True,
                    value=values[index] if index < len(values) else None,
                    label=f"{scheme}段{index + 1}（{_range_text(start, end)}）",
                )
            )
        else:
            updates.append(
                gr.update(
                    visible=False,
                    value=None,
                    label=f"{scheme}段{index + 1}",
                )
            )
    return updates


def segment_form_updates(
    row: dict[str, Any],
    *,
    video_elem_id: str,
) -> tuple[Any, ...]:
    bounds_a, _, bounds_b = segment_schemes(row)
    scores_a = parse_score_slots(row.get("seg_scores_3"))
    scores_b = parse_score_slots(row.get("seg_scores_5"))
    return (
        segment_html(row, video_elem_id=video_elem_id),
        *_score_updates_for_bounds(bounds_a, scores_a, "A"),
        *_score_updates_for_bounds(bounds_b or [], scores_b, "B"),
    )
