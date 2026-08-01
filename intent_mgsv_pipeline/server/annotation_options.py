from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


EMOTION_GROUPS = (
    (
        "正向",
        (
            "热血", "激昂", "欢乐", "兴奋", "轻快", "愉悦", "甜蜜",
            "浪漫", "幸福", "治愈", "温暖", "希望", "感动", "放松", "平静",
        ),
    ),
    (
        "中性",
        ("神秘", "空灵", "梦幻", "深沉", "庄重", "克制", "高级", "孤独", "怀旧"),
    ),
    (
        "负向",
        ("伤感", "悲伤", "压抑", "紧张", "悬疑", "恐怖", "愤怒", "焦虑", "绝望"),
    ),
)

STYLE_GROUPS = (
    ("人物与叙事", ("青春", "成长", "校园", "恋爱", "回忆", "励志")),
    ("视觉质感", ("高级感", "电影感", "科技感", "未来感", "赛博朋克", "质感", "极简")),
    (
        "氛围",
        ("梦幻", "文艺", "松弛", "治愈系", "温馨", "清新", "夏日感", "冬日感", "慵懒"),
    ),
    ("文化审美", ("国风", "中国风", "古风", "日系", "韩系", "欧美感", "二次元")),
    (
        "剪辑表达",
        ("卡点", "转场", "混剪", "高燃", "节奏感强", "踩鼓点", "剧情感", "大片感"),
    ),
    ("内容气质", ("旅行", "冒险", "探索", "都市", "街头", "潮流", "时尚", "电竞")),
)

SCENE_GROUPS = (
    (
        "日常活动",
        (
            "散步", "跑步", "运动", "健身", "开车", "骑行", "通勤", "学习",
            "工作", "阅读", "写作", "睡前", "日常Vlog",
        ),
    ),
    ("社交关系", ("表白", "恋爱", "情侣", "约会", "婚礼", "毕业", "聚会", "生日", "纪念日")),
    (
        "内容创作",
        (
            "旅行Vlog", "探店", "美食", "宠物", "风景", "城市记录", "露营",
            "航拍", "街拍", "开箱", "测评", "剧情短片", "搞笑视频", "宣传片",
            "舞蹈", "手势舞", "古风舞蹈", "变装", "走秀", "游戏剪辑",
            "动漫剪辑", "影视剪辑", "MV混剪", "音乐现场",
        ),
    ),
    (
        "特殊时空",
        ("夜晚", "清晨", "黄昏", "雨天", "海边", "公路", "森林", "雪景", "夏天", "冬天", "舞台", "节日"),
    ),
)


def flatten_options(
    groups: Sequence[tuple[str, Sequence[str]]],
) -> list[str]:
    return [option for _, options in groups for option in options]


EMOTION_OPTIONS = flatten_options(EMOTION_GROUPS)
STYLE_OPTIONS = flatten_options(STYLE_GROUPS)
SCENE_OPTIONS = flatten_options(SCENE_GROUPS)


def split_values(value: Any) -> list[str]:
    return [
        item.strip()
        for item in str(value or "").split("/")
        if item.strip()
    ]


def split_group_values(
    value: Any,
    groups: Sequence[tuple[str, Sequence[str]]],
) -> tuple[list[str], ...]:
    selected = set(split_values(value))
    return tuple(
        [option for option in options if option in selected]
        for _, options in groups
    )


def merge_group_values(*values: Iterable[Any] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in values:
        for raw in group or ():
            value = str(raw or "").strip()
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
    return merged
