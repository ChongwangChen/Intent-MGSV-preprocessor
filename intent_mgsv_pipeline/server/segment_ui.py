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
FLOATING_VIDEO_STYLE = f"""
<style>
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
</style>
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


def _play_button(
    elem_id: str,
    scheme: str,
    index: int,
    start: float,
    end: float | None,
) -> str:
    selector = html.escape(f"#{elem_id} video", quote=True)
    stop = (
        "video.ontimeupdate=null;"
        if end is None
        else (
            "video.ontimeupdate=function(){"
            f"if(video.currentTime>={end:.3f}-0.05){{"
            "video.pause();video.ontimeupdate=null;}}};"
        )
    )
    script = (
        f"const video=document.querySelector('{selector}');"
        "if(video){"
        f"video.classList.add('{FLOATING_VIDEO_CLASS}');"
        f"video.currentTime={start:.3f};video.play();{stop}"
        "}"
    )
    label = f"▶ {scheme}段{index}　{_range_text(start, end)}"
    return (
        f'<button type="button" onclick="{script}" '
        'style="margin:3px;padding:6px 10px;border:1px solid #888;'
        'border-radius:6px;background:transparent;cursor:pointer">'
        f"{html.escape(label)}</button>"
    )


def _mini_player_controls(elem_id: str) -> str:
    selector = html.escape(f"#{elem_id} video", quote=True)
    close_script = (
        f"const video=document.querySelector('{selector}');"
        "if(video){"
        f"video.classList.remove('{FLOATING_VIDEO_CLASS}');"
        "if(document.pictureInPictureElement===video){"
        "document.exitPictureInPicture();}"
        "}"
    )
    pip_script = (
        f"const video=document.querySelector('{selector}');"
        "if(video&&video.requestPictureInPicture){"
        "if(document.pictureInPictureElement===video){"
        "document.exitPictureInPicture();"
        "}else{video.requestPictureInPicture();}"
        "}"
    )
    button_style = (
        "margin:3px;padding:6px 10px;border:1px solid #888;"
        "border-radius:6px;background:transparent;cursor:pointer"
    )
    return (
        '<div style="margin-bottom:8px">'
        f'<button type="button" onclick="{close_script}" '
        f'style="{button_style}">关闭小窗</button>'
        f'<button type="button" onclick="{pip_script}" '
        f'style="{button_style}">浏览器画中画</button>'
        "</div>"
    )


def segment_html(
    row: dict[str, Any],
    *,
    video_elem_id: str,
) -> str:
    bounds_a, caption, bounds_b = segment_schemes(row)
    if not bounds_a:
        return f'<p style="color:#b45309">{html.escape(caption)}</p>'

    chunks = [
        FLOATING_VIDEO_STYLE,
        _mini_player_controls(video_elem_id),
        f"<p><strong>{html.escape(caption)}</strong></p><div>",
    ]
    chunks.extend(
        _play_button(video_elem_id, "A", index, start, end)
        for index, (start, end) in enumerate(bounds_a, start=1)
    )
    chunks.append("</div>")
    if bounds_b:
        chunks.append(
            f"<p><strong>方案 B（Top-5 分镜点）："
            f"{len(bounds_b)} 段</strong></p><div>"
        )
        chunks.extend(
            _play_button(video_elem_id, "B", index, start, end)
            for index, (start, end) in enumerate(bounds_b, start=1)
        )
        chunks.append("</div>")
    return "".join(chunks)


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
