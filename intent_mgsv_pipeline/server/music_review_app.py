from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.music_preparation.genre import GENRE_OPTIONS
from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import DEFAULT_DB
from intent_mgsv_pipeline.server.music_review import (
    build_aligned_preview,
    claim_next_music_review,
    confirm_music_review,
    music_review_progress,
    reject_music_review,
)


def _metadata(row: dict[str, Any]) -> str:
    return (
        f"### {row.get('video_title') or row.get('video_id')}\n"
        f"识曲：{row.get('recognized_title') or '未知'} - "
        f"{row.get('recognized_artist') or '未知歌手'}  \n"
        f"识曲置信度：{row.get('recognition_confidence') or 0}，"
        f"多窗口票数：{row.get('recognition_votes') or 0}  \n"
        f"下载来源：{row.get('download_source') or '未知'}，"
        f"QQ song_mid：{row.get('qq_song_mid') or '无'}  \n"
        f"对齐分：{row.get('match_score') or 0}，"
        f"机器状态：{row.get('preparation_status') or '未知'}  \n"
        f"视频中音乐开始：{row.get('video_audio_start') or 0}s"
    )


def _empty(message: str, reviewer_id: str = "") -> tuple[Any, ...]:
    return (
        reviewer_id,
        "",
        None,
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
    with gr.Blocks(title="Intent-MGSV 音乐最终核验") as app:
        reviewer_state = gr.State("")
        video_id_state = gr.State("")

        gr.Markdown("# Intent-MGSV 音乐与自动对齐最终核验")
        with gr.Row():
            reviewer_input = gr.Textbox(label="核验者 ID", value=owner_id)
            start_button = gr.Button("开始 / 继续", variant="primary")

        status = gr.Markdown("请输入核验者 ID 后开始。")
        metadata = gr.Markdown()
        with gr.Row():
            video = gr.Video(label="原视频")
            aligned_audio = gr.Audio(label="按自动 offset 裁出的完整歌曲片段")

        with gr.Row():
            offset = gr.Number(label="完整歌曲 offset（秒）", minimum=0)
            genre = gr.Dropdown(
                choices=list(GENRE_OPTIONS),
                label="Genre（必填，可修改）",
                allow_custom_value=True,
            )
        note = gr.Textbox(label="核验备注", lines=2)

        with gr.Row():
            confirm_button = gr.Button("歌曲和对齐均正确", variant="primary")
            reject_song_button = gr.Button("歌曲错误")
            reject_alignment_button = gr.Button("歌曲正确但对齐错误")

        outputs = [
            reviewer_state,
            video_id_state,
            video,
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
            return (
                reviewer_id,
                str(row["video_id"]),
                row.get("video_path"),
                str(preview) if preview else None,
                _metadata(row),
                f"正在核验：{row['video_id']}  \n{progress_text}",
                row.get("corrected_offset")
                if row.get("corrected_offset") is not None
                else row.get("song_offset") or 0,
                row.get("final_genre") or row.get("genre_suggestion") or "Pop",
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
                row[5] = message
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

        start_button.click(load_next, inputs=[reviewer_input], outputs=outputs)
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
