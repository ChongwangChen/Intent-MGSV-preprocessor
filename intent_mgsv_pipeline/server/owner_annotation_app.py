from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.assignment import (
    claim_next_owner,
    get_previous_annotation,
    owner_annotation_progress,
)
from intent_mgsv_pipeline.server.db import DEFAULT_DB
from intent_mgsv_pipeline.server.music_review import build_aligned_preview
from intent_mgsv_pipeline.server.owner_annotation import (
    SYNC_OPTIONS,
    VOCAL_OPTIONS,
    complete_owner_annotation,
    save_owner_patch,
)
from intent_mgsv_pipeline.server.peer_annotation import (
    SCORE_OPTIONS,
    join_scores,
)
from intent_mgsv_pipeline.server.peer_annotation_app import (
    EMOTION_OPTIONS,
    MAX_SCORE_SLOTS,
    SCENE_OPTIONS,
    STYLE_OPTIONS,
    _resolve_video,
    _score_updates,
    _split_values,
)


def _aligned_preview(row: dict[str, Any]) -> str | None:
    try:
        start = max(0.0, float(row.get("music_start") or 0))
        end = float(row.get("music_end") or 0)
    except (TypeError, ValueError):
        return None
    duration = end - start
    if duration <= 0:
        duration = float(row.get("duration") or 45)
    preview = build_aligned_preview(
        {
            **row,
            "corrected_offset": start,
            "aligned_duration": duration,
        }
    )
    return str(preview) if preview else None


def _metadata(row: dict[str, Any]) -> str:
    return (
        f"### {row.get('video_title') or row.get('video_id')}\n"
        f"作者：{row.get('creator_name') or '未知'}  \n"
        f"歌曲：{row.get('song_title') or '未知'} - "
        f"{row.get('song_artist') or '未知歌手'}  \n"
        f"歌曲区间：{row.get('music_start') or 0}s - "
        f"{row.get('music_end') or 0}s  \n"
        f"识曲确认：{row.get('song_verified') or 'No'}"
    )


def _score_form_updates(
    sync_level: Any,
    shot_points_3: Any,
    shot_points_5: Any,
    *score_values: Any,
) -> tuple[Any, ...]:
    row = {
        "sync_level": sync_level,
        "shot_points_3": shot_points_3,
        "shot_points_5": shot_points_5,
        "seg_scores_3": join_scores(score_values[:MAX_SCORE_SLOTS]),
        "seg_scores_5": join_scores(score_values[MAX_SCORE_SLOTS:]),
    }
    return _score_updates(row)


def build_app(db_path: Path, owner_id: str = "owner") -> gr.Blocks:
    with gr.Blocks(title="Intent-MGSV 主标注") as app:
        video_id_state = gr.State("")

        gr.Markdown("# Intent-MGSV 主标注")
        with gr.Row():
            gr.Markdown(f"当前主标注者：`{owner_id}`")
            start_button = gr.Button("开始 / 继续", variant="primary")
        status = gr.Markdown("点击开始，领取歌曲已核验的样本。")

        with gr.Row():
            video = gr.Video(label="当前视频")
            aligned_audio = gr.Audio(label="按确认 offset 裁出的完整歌曲片段")
        metadata = gr.Markdown()

        with gr.Row():
            sync_level = gr.Radio(
                choices=list(SYNC_OPTIONS),
                label="Sync video",
            )
            vocal_presence = gr.Radio(
                choices=list(VOCAL_OPTIONS),
                label="Vocal presence",
            )
            genre = gr.Textbox(label="Genre", interactive=True)

        with gr.Row():
            shot_points_3 = gr.Textbox(
                label="方案 A 分镜点（秒，使用 / 分隔）",
            )
            shot_points_5 = gr.Textbox(
                label="方案 B 分镜点（秒，使用 / 分隔；可为空）",
            )

        emotion = gr.CheckboxGroup(EMOTION_OPTIONS, label="Emotion")
        style = gr.CheckboxGroup(STYLE_OPTIONS, label="Style")
        usage_scene = gr.CheckboxGroup(SCENE_OPTIONS, label="Usage Scene")

        gr.Markdown("### 方案 A 分段评分")
        scores_a = []
        for start in range(0, MAX_SCORE_SLOTS, 4):
            with gr.Row():
                scores_a.extend(
                    gr.Radio(
                        SCORE_OPTIONS,
                        label=f"A段{index + 1}",
                        visible=False,
                    )
                    for index in range(start, start + 4)
                )
        gr.Markdown("### 方案 B 分段评分")
        scores_b = []
        for start in range(0, MAX_SCORE_SLOTS, 4):
            with gr.Row():
                scores_b.extend(
                    gr.Radio(
                        SCORE_OPTIONS,
                        label=f"B段{index + 1}",
                        visible=False,
                    )
                    for index in range(start, start + 4)
                )
        score_components = scores_a + scores_b

        with gr.Row():
            previous_button = gr.Button("上一条")
            save_button = gr.Button("保存当前修改")
            complete_button = gr.Button(
                "完成并进入下一条",
                variant="primary",
            )

        outputs = [
            video_id_state,
            video,
            aligned_audio,
            metadata,
            status,
            sync_level,
            shot_points_3,
            shot_points_5,
            vocal_presence,
            genre,
            emotion,
            style,
            usage_scene,
            *score_components,
        ]

        def empty(message: str) -> tuple[Any, ...]:
            return (
                "",
                None,
                None,
                "",
                message,
                None,
                "",
                "",
                None,
                "",
                [],
                [],
                [],
                *(
                    gr.update(visible=False, value=None)
                    for _ in score_components
                ),
            )

        def present(
            row: dict[str, Any],
            message: str = "",
        ) -> tuple[Any, ...]:
            progress = owner_annotation_progress(db_path, owner_id)
            progress_text = (
                f"进度：{progress['completed']}/{progress['total']}，"
                f"剩余 {progress['remaining']} 条"
            )
            return (
                str(row["video_id"]),
                _resolve_video(row),
                _aligned_preview(row),
                _metadata(row),
                message or f"正在标注。{progress_text}",
                row.get("sync_level") or None,
                row.get("shot_points_3") or "",
                row.get("shot_points_5") or "",
                row.get("vocal_presence")
                if row.get("vocal_presence") in VOCAL_OPTIONS
                else None,
                row.get("genre") or "",
                _split_values(row.get("emotion")),
                _split_values(row.get("style")),
                _split_values(row.get("usage_scene")),
                *_score_updates(row),
            )

        def load_next() -> tuple[Any, ...]:
            row = claim_next_owner(db_path, owner_id)
            if row is None:
                progress = owner_annotation_progress(db_path, owner_id)
                return empty(
                    "当前没有歌曲已核验且待主标注的样本。"
                    f"进度：{progress['completed']}/{progress['total']}"
                )
            return present(row)

        def save_current(
            video_id: str,
            sync_value: Any,
            points_3: Any,
            points_5: Any,
            vocal_value: Any,
            genre_value: Any,
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> str:
            if not video_id:
                return "当前没有可保存的样本。"
            saved = save_owner_patch(
                db_path,
                owner_id,
                video_id,
                sync_level=sync_value,
                shot_points_3=points_3,
                shot_points_5=points_5,
                vocal_presence=vocal_value,
                genre=genre_value,
                emotion=emotion_values,
                style=style_values,
                usage_scene=scene_values,
                seg_scores_3=score_values[:MAX_SCORE_SLOTS],
                seg_scores_5=score_values[MAX_SCORE_SLOTS:],
            )
            return "当前修改已保存。" if saved else "保存失败，请查看日志。"

        form_inputs = [
            video_id_state,
            sync_level,
            shot_points_3,
            shot_points_5,
            vocal_presence,
            genre,
            emotion,
            style,
            usage_scene,
            *score_components,
        ]

        def previous(
            video_id: str,
            sync_value: Any,
            points_3: Any,
            points_5: Any,
            vocal_value: Any,
            genre_value: Any,
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> tuple[Any, ...]:
            if video_id:
                save_current(
                    video_id,
                    sync_value,
                    points_3,
                    points_5,
                    vocal_value,
                    genre_value,
                    emotion_values,
                    style_values,
                    scene_values,
                    *score_values,
                )
            row = get_previous_annotation(db_path, owner_id, video_id)
            if row is None:
                current = claim_next_owner(db_path, owner_id)
                if current is None:
                    return empty("没有更早的已完成标注。")
                return present(current, "没有更早的已完成标注，已保留当前样本。")
            return present(row, "正在修改历史标注；当前表单切换前已自动保存。")

        def complete_and_next(
            video_id: str,
            sync_value: Any,
            points_3: Any,
            points_5: Any,
            vocal_value: Any,
            genre_value: Any,
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> tuple[Any, ...]:
            if not video_id:
                return load_next()
            completed, missing = complete_owner_annotation(
                db_path,
                owner_id,
                video_id,
                sync_level=sync_value,
                shot_points_3=points_3,
                shot_points_5=points_5,
                vocal_presence=vocal_value,
                genre=genre_value,
                emotion=emotion_values,
                style=style_values,
                usage_scene=scene_values,
                seg_scores_3=score_values[:MAX_SCORE_SLOTS],
                seg_scores_5=score_values[MAX_SCORE_SLOTS:],
            )
            if not completed:
                row = claim_next_owner(db_path, owner_id)
                if row is None:
                    return empty("必填项未完成：" + "、".join(missing))
                return present(row, "必填项未完成：" + "、".join(missing))
            return load_next()

        start_button.click(load_next, outputs=outputs)
        save_button.click(save_current, inputs=form_inputs, outputs=status)
        previous_button.click(previous, inputs=form_inputs, outputs=outputs)
        complete_button.click(
            complete_and_next,
            inputs=form_inputs,
            outputs=outputs,
        )

        score_refresh_inputs = [
            sync_level,
            shot_points_3,
            shot_points_5,
            *score_components,
        ]
        sync_level.change(
            _score_form_updates,
            inputs=score_refresh_inputs,
            outputs=score_components,
        )
        shot_points_3.change(
            _score_form_updates,
            inputs=score_refresh_inputs,
            outputs=score_components,
        )
        shot_points_5.change(
            _score_form_updates,
            inputs=score_refresh_inputs,
            outputs=score_components,
        )

        for component in [
            sync_level,
            shot_points_3,
            shot_points_5,
            vocal_presence,
            genre,
            emotion,
            style,
            usage_scene,
            *score_components,
        ]:
            component.input(
                save_current,
                inputs=form_inputs,
                outputs=status,
            )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intent-MGSV database-backed owner annotation app."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
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
