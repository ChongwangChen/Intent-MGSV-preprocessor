from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.annotation_options import (
    EMOTION_GROUPS,
    EMOTION_OPTIONS,
    SCENE_GROUPS,
    SCENE_OPTIONS,
    STYLE_GROUPS,
    STYLE_OPTIONS,
    merge_group_values,
    split_group_values,
    split_values,
)
from intent_mgsv_pipeline.server.assignment import (
    annotation_progress,
    claim_next,
    get_previous_annotation,
)
from intent_mgsv_pipeline.server.db import DEFAULT_DB
from intent_mgsv_pipeline.server.media_paths import browser_safe_video_path
from intent_mgsv_pipeline.server.peer_annotation import (
    SCORE_OPTIONS,
    complete_peer_annotation,
    save_peer_patch,
)
from intent_mgsv_pipeline.server.segment_ui import (
    MAX_SCORE_SLOTS,
    SEGMENT_VIDEO_CSS,
    segment_close_js,
    segment_form_updates,
    segment_pip_js,
    segment_play_js,
    segment_playback_updates,
)


def _split_values(value: Any) -> list[str]:
    return split_values(value)


def _video_index() -> dict[str, str]:
    if not PATHS.douk_download_root.exists():
        return {}
    return {
        path.name: str(path)
        for path in PATHS.douk_download_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}
    }


VIDEO_INDEX = _video_index()


def _resolve_video(row: dict[str, Any]) -> str | None:
    raw = str(row.get("video_path", "") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PATHS.project_root / path
        if path.exists():
            return str(browser_safe_video_path(path))
    fallback = VIDEO_INDEX.get(str(row.get("video_id", "") or ""))
    return str(browser_safe_video_path(Path(fallback))) if fallback else None


def _score_updates(row: dict[str, Any]) -> tuple[Any, ...]:
    return segment_form_updates(
        row,
        video_elem_id="peer-video",
    )[1:]


def _metadata(row: dict[str, Any]) -> str:
    title = row.get("video_title", "") or row.get("video_id", "")
    creator = row.get("creator_name", "") or "未知作者"
    song = row.get("song_title", "") or "未识别"
    artist = row.get("song_artist", "") or "未知歌手"
    sync = row.get("sync_level", "") or "未设置"
    return (
        f"### {title}\n"
        f"作者：{creator}  \n"
        f"音乐：{song} - {artist}  \n"
        f"主标注已确定：**是否卡点 = {sync}**"
    )


def _group_values(row: dict[str, Any]) -> tuple[list[str], ...]:
    return (
        *split_group_values(row.get("emotion"), EMOTION_GROUPS),
        *split_group_values(row.get("style"), STYLE_GROUPS),
        *split_group_values(row.get("usage_scene"), SCENE_GROUPS),
    )


def _make_group_components(
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[gr.CheckboxGroup]:
    return [
        gr.CheckboxGroup(list(options), label=label)
        for label, options in groups
    ]


def build_app(db_path: Path, owner_id: str = "owner") -> gr.Blocks:
    with gr.Blocks(title="Intent-MGSV 多人复标") as app:
        annotator_state = gr.State("")
        video_id_state = gr.State("")

        gr.Markdown("# Intent-MGSV 多人复标")
        with gr.Row():
            annotator_input = gr.Textbox(
                label="标注者 ID",
                placeholder="例如 annotator_b",
            )
            start_button = gr.Button("开始 / 继续", variant="primary")

        status = gr.Markdown("请输入自己的标注者 ID。")
        video = gr.Video(label="当前视频", elem_id="peer-video")
        metadata = gr.Markdown()

        gr.Markdown("## 1. 分段契合度")
        segment_player = gr.HTML()
        segment_data = gr.JSON(value={}, visible=False)
        with gr.Row():
            close_segment_video = gr.Button("关闭视频小窗", size="sm")
            pip_segment_video = gr.Button("浏览器画中画", size="sm")

        play_buttons_a: list[gr.Button] = []
        scores_a: list[gr.Radio] = []
        with gr.Accordion("方案 A 分段评分", open=True):
            for start in range(0, MAX_SCORE_SLOTS, 4):
                with gr.Row():
                    for index in range(start, start + 4):
                        with gr.Column(min_width=180):
                            play_buttons_a.append(
                                gr.Button(
                                    f"播放 A段{index + 1}",
                                    visible=False,
                                    size="sm",
                                )
                            )
                            scores_a.append(
                                gr.Radio(
                                    SCORE_OPTIONS,
                                    label=f"A段{index + 1}",
                                    visible=False,
                                )
                            )

        play_buttons_b: list[gr.Button] = []
        scores_b: list[gr.Radio] = []
        with gr.Accordion("方案 B 分段评分", open=False):
            for start in range(0, MAX_SCORE_SLOTS, 4):
                with gr.Row():
                    for index in range(start, start + 4):
                        with gr.Column(min_width=180):
                            play_buttons_b.append(
                                gr.Button(
                                    f"播放 B段{index + 1}",
                                    visible=False,
                                    size="sm",
                                )
                            )
                            scores_b.append(
                                gr.Radio(
                                    SCORE_OPTIONS,
                                    label=f"B段{index + 1}",
                                    visible=False,
                                )
                            )

        gr.Markdown("## 2. 标签选择")
        with gr.Tabs():
            with gr.Tab("Emotion"):
                emotion_components = _make_group_components(EMOTION_GROUPS)
            with gr.Tab("Style"):
                style_components = _make_group_components(STYLE_GROUPS)
            with gr.Tab("Usage Scene"):
                scene_components = _make_group_components(SCENE_GROUPS)

        group_components = [
            *emotion_components,
            *style_components,
            *scene_components,
        ]
        playback_components = [*play_buttons_a, *play_buttons_b]
        score_components = [*scores_a, *scores_b]

        with gr.Row():
            previous_button = gr.Button("上一条")
            save_button = gr.Button("保存当前修改")
            next_button = gr.Button("保存并进入下一条", variant="primary")

        outputs = [
            annotator_state,
            video_id_state,
            video,
            metadata,
            status,
            segment_player,
            segment_data,
            *playback_components,
            *score_components,
            *group_components,
        ]

        def empty(
            annotator_id: str,
            message: str,
        ) -> tuple[Any, ...]:
            return (
                annotator_id,
                "",
                None,
                "",
                message,
                "",
                {},
                *(
                    gr.update(visible=False)
                    for _ in playback_components
                ),
                *(
                    gr.update(visible=False, value=None)
                    for _ in score_components
                ),
                *([] for _ in group_components),
            )

        def present(
            annotator_id: str,
            row: dict[str, Any],
            message: str = "",
        ) -> tuple[Any, ...]:
            progress = annotation_progress(db_path, annotator_id, owner_id)
            progress_text = (
                f"进度：{progress['completed']}/{progress['total']}，"
                f"剩余 {progress['remaining']} 条"
            )
            video_id = str(row["video_id"])
            form_updates = segment_form_updates(
                row,
                video_elem_id="peer-video",
            )
            playback_updates = segment_playback_updates(row)
            return (
                annotator_id,
                video_id,
                _resolve_video(row),
                _metadata(row),
                message or f"正在标注：{video_id}  \n{progress_text}",
                form_updates[0],
                *playback_updates,
                *form_updates[1:],
                *_group_values(row),
            )

        def load_next(annotator_id: str) -> tuple[Any, ...]:
            annotator_id = str(annotator_id or "").strip()
            if not annotator_id:
                return empty("", "请先填写标注者 ID。")
            row = claim_next(db_path, annotator_id, owner_id=owner_id)
            if row is None:
                progress = annotation_progress(db_path, annotator_id, owner_id)
                return empty(
                    annotator_id,
                    f"当前没有待标样本。进度："
                    f"{progress['completed']}/{progress['total']}",
                )
            return present(annotator_id, row)

        emotion_count = len(EMOTION_GROUPS)
        style_count = len(STYLE_GROUPS)
        scene_count = len(SCENE_GROUPS)
        group_count = len(group_components)

        def unpack_values(values: tuple[Any, ...]) -> tuple[
            list[str],
            list[str],
            list[str],
            tuple[Any, ...],
        ]:
            groups = values[:group_count]
            scores = values[group_count:]
            emotion = merge_group_values(*groups[:emotion_count])
            style_start = emotion_count
            style_end = style_start + style_count
            style = merge_group_values(*groups[style_start:style_end])
            scene = merge_group_values(
                *groups[style_end:style_end + scene_count]
            )
            return emotion, style, scene, scores

        def save_current(
            annotator_id: str,
            video_id: str,
            *values: Any,
        ) -> str:
            if not annotator_id or not video_id:
                return "当前没有可保存的样本。"
            emotion, style, scene, scores = unpack_values(values)
            saved = save_peer_patch(
                db_path,
                annotator_id,
                video_id,
                emotion=emotion,
                style=style,
                usage_scene=scene,
                seg_scores_3=scores[:MAX_SCORE_SLOTS],
                seg_scores_5=scores[MAX_SCORE_SLOTS:],
            )
            return "当前修改已保存。" if saved else "保存失败，请查看服务器日志。"

        def previous(
            annotator_id: str,
            video_id: str,
            *values: Any,
        ) -> tuple[Any, ...]:
            if not annotator_id:
                return load_next(annotator_id)
            if video_id:
                save_current(annotator_id, video_id, *values)
            row = get_previous_annotation(db_path, annotator_id, video_id)
            if row is None:
                current = claim_next(
                    db_path,
                    annotator_id,
                    owner_id=owner_id,
                )
                if current is None:
                    return load_next(annotator_id)
                return present(
                    annotator_id,
                    current,
                    "没有更早的已完成标注，已保留当前样本。",
                )
            return present(
                annotator_id,
                row,
                "正在修改历史标注；切换前已自动保存当前表单。",
            )

        def complete_and_next(
            annotator_id: str,
            video_id: str,
            *values: Any,
        ) -> tuple[Any, ...]:
            if not annotator_id or not video_id:
                return load_next(annotator_id)
            emotion, style, scene, scores = unpack_values(values)
            completed, missing = complete_peer_annotation(
                db_path,
                annotator_id,
                video_id,
                emotion=emotion,
                style=style,
                usage_scene=scene,
                seg_scores_3=scores[:MAX_SCORE_SLOTS],
                seg_scores_5=scores[MAX_SCORE_SLOTS:],
            )
            if not completed:
                row = claim_next(
                    db_path,
                    annotator_id,
                    owner_id=owner_id,
                )
                if row is None:
                    return empty(
                        annotator_id,
                        "必填项未完成：" + "、".join(missing),
                    )
                return present(
                    annotator_id,
                    row,
                    "必填项未完成：" + "、".join(missing),
                )
            return load_next(annotator_id)

        form_inputs = [
            annotator_state,
            video_id_state,
            *group_components,
            *score_components,
        ]
        start_button.click(load_next, inputs=[annotator_input], outputs=outputs)
        save_button.click(save_current, inputs=form_inputs, outputs=[status])
        previous_button.click(previous, inputs=form_inputs, outputs=outputs)
        next_button.click(
            complete_and_next,
            inputs=form_inputs,
            outputs=outputs,
        )
        for index, button in enumerate(play_buttons_a):
            button.click(
                fn=None,
                inputs=[segment_data],
                js=segment_play_js("peer-video", "A", index),
                queue=False,
                show_progress="hidden",
            )
        for index, button in enumerate(play_buttons_b):
            button.click(
                fn=None,
                inputs=[segment_data],
                js=segment_play_js("peer-video", "B", index),
                queue=False,
                show_progress="hidden",
            )
        close_segment_video.click(
            fn=None,
            js=segment_close_js("peer-video"),
            queue=False,
            show_progress="hidden",
        )
        pip_segment_video.click(
            fn=None,
            js=segment_pip_js("peer-video"),
            queue=False,
            show_progress="hidden",
        )
        for component in [*group_components, *score_components]:
            component.input(
                save_current,
                inputs=form_inputs,
                outputs=status,
            )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intent-MGSV peer annotation server."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7861)
    args = parser.parse_args()

    app = build_app(Path(args.db), args.owner_id)
    app.launch(
        server_name=args.host,
        server_port=args.port,
        css=SEGMENT_VIDEO_CSS,
        allowed_paths=[
            str(PATHS.project_root),
            str(PATHS.douk_download_root),
            str(PATHS.output_dir),
        ],
        show_error=True,
    )


if __name__ == "__main__":
    main()
