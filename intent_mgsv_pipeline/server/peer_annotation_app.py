from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import gradio as gr

from intent_mgsv_pipeline.runtime_config import PATHS
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
    expected_score_counts,
    parse_score_slots,
    save_peer_patch,
)


EMOTION_OPTIONS = [
    "热血", "激昂", "欢乐", "兴奋", "轻快", "愉悦", "甜蜜", "浪漫", "幸福",
    "治愈", "温暖", "希望", "感动", "放松", "平静", "神秘", "空灵", "梦幻",
    "深沉", "庄重", "克制", "高级", "孤独", "怀旧", "伤感", "悲伤", "压抑",
    "紧张", "悬疑", "恐怖", "愤怒", "焦虑", "绝望",
]
STYLE_OPTIONS = [
    "青春", "成长", "校园", "恋爱", "回忆", "励志", "高级感", "电影感",
    "科技感", "未来感", "赛博朋克", "质感", "极简", "梦幻", "文艺", "松弛",
    "治愈系", "温馨", "清新", "夏日感", "冬日感", "慵懒", "国风", "中国风",
    "古风", "日系", "韩系", "欧美感", "二次元", "卡点", "转场", "混剪",
    "高燃", "节奏感强", "踩鼓点", "剧情感", "大片感", "旅行", "冒险", "探索",
    "都市", "街头", "潮流", "时尚", "电竞",
]
SCENE_OPTIONS = [
    "散步", "跑步", "运动", "健身", "开车", "骑行", "通勤", "学习", "工作",
    "阅读", "写作", "睡前", "日常Vlog", "表白", "恋爱", "情侣", "约会", "婚礼",
    "毕业", "聚会", "生日", "纪念日", "旅行Vlog", "探店", "美食", "宠物",
    "风景", "城市记录", "露营", "航拍", "街拍", "开箱", "测评", "剧情短片",
    "搞笑视频", "宣传片", "舞蹈", "手势舞", "古风舞蹈", "变装", "走秀",
    "游戏剪辑", "动漫剪辑", "影视剪辑", "MV混剪", "音乐现场", "夜晚", "清晨",
    "黄昏", "雨天", "海边", "公路", "森林", "雪景", "夏天", "冬天", "舞台",
    "节日",
]
MAX_SCORE_SLOTS = 12


def _split_values(value: Any) -> list[str]:
    return [
        item.strip()
        for item in str(value or "").split("/")
        if item.strip()
    ]


def _video_index() -> dict[str, str]:
    if not PATHS.douk_download_root.exists():
        return {}
    return {
        path.name: str(path)
        for path in PATHS.douk_download_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}
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
    count_a, count_b = expected_score_counts(row)
    values_a = parse_score_slots(row.get("seg_scores_3"))
    values_b = parse_score_slots(row.get("seg_scores_5"))
    updates: list[Any] = []
    for index in range(MAX_SCORE_SLOTS):
        updates.append(
            gr.update(
                visible=index < count_a,
                value=values_a[index] if index < len(values_a) else None,
                label=f"A段{index + 1}",
            )
        )
    for index in range(MAX_SCORE_SLOTS):
        updates.append(
            gr.update(
                visible=index < count_b,
                value=values_b[index] if index < len(values_b) else None,
                label=f"B段{index + 1}",
            )
        )
    return tuple(updates)


def _metadata(row: dict[str, Any]) -> str:
    title = row.get("video_title", "") or row.get("video_id", "")
    creator = row.get("creator_name", "") or "未知作者"
    song = row.get("song_title", "") or "未识别"
    artist = row.get("song_artist", "") or "未知歌手"
    sync = row.get("sync_level", "")
    points_3 = row.get("shot_points_3", "") or "无"
    points_5 = row.get("shot_points_5", "") or "无"
    return (
        f"### {title}\n"
        f"作者：{creator}  \n"
        f"音乐：{song} - {artist}  \n"
        f"卡点状态：{sync}  \n"
        f"方案 A 分镜点：{points_3}  \n"
        f"方案 B 分镜点：{points_5}"
    )


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
        video = gr.Video(label="当前视频")
        metadata = gr.Markdown()

        emotion = gr.CheckboxGroup(EMOTION_OPTIONS, label="Emotion")
        style = gr.CheckboxGroup(STYLE_OPTIONS, label="Style")
        usage_scene = gr.CheckboxGroup(SCENE_OPTIONS, label="Usage Scene")

        gr.Markdown("### 方案 A 分段评分")
        scores_a = []
        for start in range(0, MAX_SCORE_SLOTS, 4):
            with gr.Row():
                scores_a.extend(
                    gr.Radio(SCORE_OPTIONS, label=f"A段{i + 1}", visible=False)
                    for i in range(start, start + 4)
                )
        gr.Markdown("### 方案 B 分段评分")
        scores_b = []
        for start in range(0, MAX_SCORE_SLOTS, 4):
            with gr.Row():
                scores_b.extend(
                    gr.Radio(SCORE_OPTIONS, label=f"B段{i + 1}", visible=False)
                    for i in range(start, start + 4)
                )

        with gr.Row():
            previous_button = gr.Button("上一条")
            save_button = gr.Button("保存当前修改")
            next_button = gr.Button("保存并进入下一条", variant="primary")

        score_components = scores_a + scores_b
        outputs = [
            annotator_state,
            video_id_state,
            video,
            metadata,
            status,
            emotion,
            style,
            usage_scene,
            *score_components,
        ]

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
            return (
                annotator_id,
                video_id,
                _resolve_video(row),
                _metadata(row),
                message or f"正在标注：{video_id}  \n{progress_text}",
                _split_values(row.get("emotion")),
                _split_values(row.get("style")),
                _split_values(row.get("usage_scene")),
                *_score_updates(row),
            )

        def load_next(annotator_id: str) -> tuple[Any, ...]:
            annotator_id = str(annotator_id or "").strip()
            if not annotator_id:
                return (
                    "",
                    "",
                    None,
                    "",
                    "请先填写标注者 ID。",
                    [],
                    [],
                    [],
                    *(gr.update(visible=False, value=None) for _ in score_components),
                )
            row = claim_next(db_path, annotator_id, owner_id=owner_id)
            if row is None:
                progress = annotation_progress(db_path, annotator_id, owner_id)
                progress_text = (
                    f"进度：{progress['completed']}/{progress['total']}，"
                    f"剩余 {progress['remaining']} 条"
                )
                return (
                    annotator_id,
                    "",
                    None,
                    "",
                    f"该标注者当前没有待标样本。{progress_text}",
                    [],
                    [],
                    [],
                    *(gr.update(visible=False, value=None) for _ in score_components),
                )
            return present(annotator_id, row)

        def save_current(
            annotator_id: str,
            video_id: str,
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> str:
            if not annotator_id or not video_id:
                return "当前没有可保存的样本。"
            values_a = score_values[:MAX_SCORE_SLOTS]
            values_b = score_values[MAX_SCORE_SLOTS:]
            saved = save_peer_patch(
                db_path,
                annotator_id,
                video_id,
                emotion=emotion_values,
                style=style_values,
                usage_scene=scene_values,
                seg_scores_3=values_a,
                seg_scores_5=values_b,
            )
            return "当前修改已保存。" if saved else "保存失败，请查看服务器日志。"

        def previous(
            annotator_id: str,
            video_id: str,
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> tuple[Any, ...]:
            if not annotator_id:
                return load_next(annotator_id)
            if video_id:
                save_current(
                    annotator_id,
                    video_id,
                    emotion_values,
                    style_values,
                    scene_values,
                    *score_values,
                )
            row = get_previous_annotation(
                db_path,
                annotator_id,
                video_id,
            )
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
            emotion_values: list[str],
            style_values: list[str],
            scene_values: list[str],
            *score_values: Any,
        ) -> tuple[Any, ...]:
            if not annotator_id or not video_id:
                return load_next(annotator_id)
            values_a = score_values[:MAX_SCORE_SLOTS]
            values_b = score_values[MAX_SCORE_SLOTS:]
            completed, missing = complete_peer_annotation(
                db_path,
                annotator_id,
                video_id,
                emotion=emotion_values,
                style=style_values,
                usage_scene=scene_values,
                seg_scores_3=values_a,
                seg_scores_5=values_b,
            )
            if not completed:
                row = claim_next(db_path, annotator_id, owner_id=owner_id)
                if row is None:
                    return load_next(annotator_id)
                return present(
                    annotator_id,
                    row,
                    "必填项未完成：" + "、".join(missing),
                )
            return load_next(annotator_id)

        form_inputs = [
            annotator_state,
            video_id_state,
            emotion,
            style,
            usage_scene,
            *score_components,
        ]
        start_button.click(load_next, inputs=[annotator_input], outputs=outputs)
        save_button.click(
            save_current,
            inputs=form_inputs,
            outputs=[status],
        )
        previous_button.click(
            previous,
            inputs=form_inputs,
            outputs=outputs,
        )
        next_button.click(
            complete_and_next,
            inputs=form_inputs,
            outputs=outputs,
        )
        for component in [
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
    parser = argparse.ArgumentParser(description="Intent-MGSV peer annotation server.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    app = build_app(Path(args.db), args.owner_id)
    allowed_paths = [
        str(PATHS.project_root),
        str(PATHS.douk_download_root),
        str(PATHS.output_dir),
    ]
    app.launch(
        server_name=args.host,
        server_port=args.port,
        allowed_paths=allowed_paths,
        show_error=True,
    )


if __name__ == "__main__":
    main()
