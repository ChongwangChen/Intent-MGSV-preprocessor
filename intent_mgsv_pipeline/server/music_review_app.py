from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.music_preparation.genre import GENRE_OPTIONS
from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import DEFAULT_DB
from intent_mgsv_pipeline.server.media_paths import browser_safe_audio_path
from intent_mgsv_pipeline.server.music_review import (
    REALIGN_PRESETS,
    build_aligned_preview,
    claim_next_music_review,
    confirm_music_review,
    get_music_review_record,
    music_review_progress,
    realign_music_review,
    reject_music_review,
)
from intent_mgsv_pipeline.server.peer_annotation_app import _resolve_video


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_full_song(row: dict[str, Any]) -> str | None:
    raw = str(row.get("full_song_path", "") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = PATHS.project_root / path
    if not path.is_file():
        return None
    return str(browser_safe_audio_path(path))


def _full_song_update(row: dict[str, Any]) -> Any:
    path = _resolve_full_song(row)
    offset = _number(
        row.get("corrected_offset")
        if row.get("corrected_offset") is not None
        else row.get("song_offset")
    )
    if not path:
        return gr.update(value=None, playback_position=0)
    return gr.update(value=path, playback_position=max(0.0, offset))


def _metadata(row: dict[str, Any]) -> str:
    title = row.get("recognized_title") or "未知歌曲"
    artist = row.get("recognized_artist") or "未知歌手"
    offset = _number(
        row.get("corrected_offset")
        if row.get("corrected_offset") is not None
        else row.get("song_offset")
    )
    full_song_path = row.get("full_song_path") or "未建立"
    return (
        f"### {row.get('video_title') or row.get('video_id')}\n"
        f"识曲结果：**{title} - {artist}**  \n"
        f"识曲置信度：{row.get('recognition_confidence') or 0}；"
        f"多窗口票数：{row.get('recognition_votes') or 0}  \n"
        f"完整歌曲 offset：**{offset:.3f}s**；"
        f"自动对齐分：{row.get('match_score') or 0}  \n"
        f"下载来源：{row.get('download_source') or '未知'}；"
        f"QQ song_mid：{row.get('qq_song_mid') or '无'}  \n"
        f"完整歌曲文件：`{full_song_path}`  \n"
        f"数据库映射：`videos.id={row.get('video_db_id')}`"
        f" → music_preparations.video_id；"
        f"`music_preparations.song_id={row.get('song_id')}`"
        f" → songs.id；核验写入 `song_reviews`，确认后同步到 `annotations`。"
    )


def _empty(message: str, reviewer_id: str = "") -> tuple[Any, ...]:
    return (
        reviewer_id,
        "",
        None,
        gr.update(value=None, playback_position=0),
        None,
        "",
        message,
        0,
        None,
        "",
    )


def build_app(
    db_path: Path,
    owner_id: str = "owner",
) -> gr.Blocks:
    with gr.Blocks(title="Intent-MGSV 音乐核验") as app:
        reviewer_state = gr.State("")
        video_id_state = gr.State("")

        gr.Markdown("# Intent-MGSV 音乐与自动对齐核验")
        with gr.Row():
            reviewer_input = gr.Textbox(label="核验者 ID", value=owner_id)
            start_button = gr.Button("开始 / 继续", variant="primary")

        status = gr.Markdown("输入核验者 ID 后开始。")
        metadata = gr.Markdown()

        with gr.Row():
            video = gr.Video(label="视频原声", elem_id="music-review-video")
            full_song = gr.Audio(
                label="完整歌曲（整首，加载后自动定位到 offset）",
                editable=False,
                playback_position=0,
            )

        aligned_audio = gr.Audio(
            label="自动对齐预览（仅截取与视频对应的长度）",
            editable=False,
        )

        gr.Markdown("### 对齐调整")
        with gr.Row():
            realign_preset = gr.Dropdown(
                choices=list(REALIGN_PRESETS),
                value="精细（推荐）",
                label="重新自动对齐模式",
            )
            realign_button = gr.Button("重新自动对齐当前视频", variant="secondary")
            refresh_preview_button = gr.Button("按当前 offset 刷新试听")

        with gr.Row():
            offset = gr.Number(
                label="完整歌曲 offset（秒）",
                minimum=0,
                step=0.01,
            )
            seek_button = gr.Button("定位到 offset")
            genre = gr.Dropdown(
                choices=list(GENRE_OPTIONS),
                label="Genre（必填，可修改）",
                allow_custom_value=True,
            )
        note = gr.Textbox(label="核验备注", lines=2)

        with gr.Row():
            confirm_button = gr.Button(
                "歌曲和对齐均正确",
                variant="primary",
            )
            reject_song_button = gr.Button("歌曲错误")
            reject_alignment_button = gr.Button("暂时移出，稍后处理")

        outputs = [
            reviewer_state,
            video_id_state,
            video,
            full_song,
            aligned_audio,
            metadata,
            status,
            offset,
            genre,
            note,
        ]

        def load_next(reviewer_id: str) -> tuple[Any, ...]:
            reviewer_id = str(reviewer_id or "").strip()
            if not reviewer_id:
                return _empty("请先填写核验者 ID。")
            row = claim_next_music_review(db_path, reviewer_id)
            progress = music_review_progress(db_path, reviewer_id)
            progress_text = (
                f"进度：{progress['completed']}/{progress['total']}，"
                f"剩余 {progress['remaining']} 条"
            )
            if row is None:
                return _empty(
                    f"当前没有待核验歌曲。{progress_text}",
                    reviewer_id,
                )
            preview = build_aligned_preview(row)
            current_offset = (
                row.get("corrected_offset")
                if row.get("corrected_offset") is not None
                else row.get("song_offset") or 0
            )
            return (
                reviewer_id,
                str(row["video_id"]),
                _resolve_video(row),
                _full_song_update(row),
                str(preview) if preview else None,
                _metadata(row),
                f"正在核验：{row['video_id']}  \n{progress_text}",
                current_offset,
                row.get("final_genre")
                or row.get("genre_suggestion")
                or "Pop",
                row.get("note") or "",
            )

        def confirm_and_next(
            reviewer_id: str,
            video_id: str,
            corrected_offset: float,
            final_genre: str,
            review_note: str,
        ) -> tuple[Any, ...]:
            if not reviewer_id or not video_id:
                return _empty("当前没有可确认的样本。", reviewer_id)
            ok, message = confirm_music_review(
                db_path,
                reviewer_id,
                video_id,
                corrected_offset=corrected_offset,
                final_genre=final_genre,
                note=review_note,
                owner_id=owner_id,
            )
            if not ok:
                row = list(load_next(reviewer_id))
                row[6] = message
                return tuple(row)
            return load_next(reviewer_id)

        def reject_and_next(
            reviewer_id: str,
            video_id: str,
            review_note: str,
            reason: str,
        ) -> tuple[Any, ...]:
            if not reviewer_id or not video_id:
                return _empty("当前没有可退回的样本。", reviewer_id)
            reject_music_review(
                db_path,
                reviewer_id,
                video_id,
                reason=reason,
                note=review_note,
            )
            return load_next(reviewer_id)

        def seek_to_offset(value: Any) -> Any:
            return gr.update(playback_position=max(0.0, _number(value)))

        def realign_current(
            reviewer_id: str,
            video_id: str,
            preset: str,
        ) -> tuple[Any, ...]:
            if not reviewer_id or not video_id:
                return (
                    gr.update(),
                    None,
                    gr.update(),
                    "当前没有可重对齐的样本。",
                    gr.update(),
                )
            ok, message, row = realign_music_review(
                db_path,
                reviewer_id,
                video_id,
                preset=preset,
            )
            if row is None:
                return gr.update(), None, gr.update(), message, gr.update()
            preview = build_aligned_preview(row)
            current_offset = (
                row.get("corrected_offset")
                if row.get("corrected_offset") is not None
                else row.get("song_offset") or 0
            )
            prefix = "✅" if ok else "⚠️"
            return (
                _full_song_update(row),
                str(preview) if preview else None,
                _metadata(row),
                f"{prefix} {message}",
                current_offset,
            )

        def refresh_manual_preview(
            reviewer_id: str,
            video_id: str,
            current_offset: Any,
        ) -> tuple[Any, ...]:
            if not reviewer_id or not video_id:
                return None, gr.update(), "当前没有可调整的样本。"
            row = get_music_review_record(db_path, reviewer_id, video_id)
            if row is None:
                return None, gr.update(), "当前视频记录不存在。"
            value = max(0.0, _number(current_offset))
            preview_row = dict(row)
            preview_row["corrected_offset"] = value
            preview = build_aligned_preview(preview_row)
            message = (
                f"已按 offset={value:.3f}s 刷新试听。"
                "这里只更新试听，点击“歌曲和对齐均正确”后才会写入数据库。"
            )
            return (
                str(preview) if preview else None,
                gr.update(playback_position=value),
                message,
            )

        start_button.click(load_next, inputs=[reviewer_input], outputs=outputs)
        seek_button.click(
            seek_to_offset,
            inputs=[offset],
            outputs=[full_song],
        )
        offset.change(
            seek_to_offset,
            inputs=[offset],
            outputs=[full_song],
        )
        realign_button.click(
            realign_current,
            inputs=[reviewer_state, video_id_state, realign_preset],
            outputs=[full_song, aligned_audio, metadata, status, offset],
        )
        refresh_preview_button.click(
            refresh_manual_preview,
            inputs=[reviewer_state, video_id_state, offset],
            outputs=[aligned_audio, full_song, status],
        )
        confirm_button.click(
            confirm_and_next,
            inputs=[reviewer_state, video_id_state, offset, genre, note],
            outputs=outputs,
        )
        reject_song_button.click(
            lambda reviewer, video_id, value: reject_and_next(
                reviewer,
                video_id,
                value,
                "song_rejected",
            ),
            inputs=[reviewer_state, video_id_state, note],
            outputs=outputs,
        )
        reject_alignment_button.click(
            lambda reviewer, video_id, value: reject_and_next(
                reviewer,
                video_id,
                value,
                "alignment_rejected",
            ),
            inputs=[reviewer_state, video_id_state, note],
            outputs=outputs,
        )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review prepared songs and automatic alignments."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7862)
    args = parser.parse_args()
    app = build_app(Path(args.db), owner_id=args.owner_id)
    app.launch(
        server_name=args.host,
        server_port=args.port,
        allowed_paths=[
            str(PATHS.project_root),
            str(PATHS.douk_download_root),
            str(PATHS.output_dir),
        ],
        show_error=True,
    )


if __name__ == "__main__":
    main()
