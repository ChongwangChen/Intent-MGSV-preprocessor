from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.assignment import (
    get_annotation_record,
    save_annotation_patch,
)
from intent_mgsv_pipeline.server.db import DEFAULT_DB


EDGE_PAD_SECONDS = 0.5


def select_even(
    points: list[float],
    duration: float,
    count: int,
) -> list[float]:
    points = sorted(points)
    if len(points) <= count:
        return points
    targets = [
        (index + 1) * duration / (count + 1)
        for index in range(count)
    ]
    available = set(points)
    selected: list[float] = []
    for target in targets:
        best = min(available, key=lambda point: abs(point - target))
        selected.append(best)
        available.remove(best)
    return sorted(selected)


def format_points(points: list[float]) -> str:
    return "/".join(f"{point:.2f}" for point in points)


def build_shot_fields(
    cuts: list[tuple[float, float]],
    duration: float,
    *,
    threshold: float,
    edge_pad: float = EDGE_PAD_SECONDS,
) -> dict[str, str]:
    points = sorted(
        time
        for time, confidence in cuts
        if edge_pad < time < duration - edge_pad
        and confidence >= threshold
    )
    if not points:
        return {
            "shot_points": "NONE",
            "shot_points_3": "NONE",
            "shot_points_5": "NONE",
        }
    points_3 = select_even(points, duration, 3)
    points_5 = select_even(points, duration, 5)
    return {
        "shot_points": format_points(points),
        "shot_points_3": format_points(points_3),
        "shot_points_5": (
            "SAME"
            if points_5 == points_3
            else format_points(points_5)
        ),
    }


def _resolve_video_path(row: dict[str, Any]) -> Path:
    raw = str(row.get("video_path", "") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PATHS.project_root / path
        if path.is_file():
            return path.resolve()
    video_id = str(row.get("video_id", "") or "")
    matches = [
        path
        for path in PATHS.douk_download_root.rglob("*")
        if path.is_file() and path.name == video_id
    ]
    if len(matches) == 1:
        return matches[0].resolve()
    if not matches:
        raise FileNotFoundError(f"找不到视频文件：{video_id}")
    raise RuntimeError(f"视频文件存在多个候选：{video_id}")


def _video_duration(path: Path, row: dict[str, Any]) -> float:
    for key in ("duration", "video_total_duration"):
        try:
            duration = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration

    import cv2

    capture = cv2.VideoCapture(str(path))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    capture.release()
    if fps <= 0 or frames <= 0:
        raise RuntimeError("无法读取视频时长")
    return frames / fps


def detect_cuts(
    video_path: Path,
    *,
    threshold: float,
) -> list[tuple[float, float]]:
    import cv2
    import numpy as np

    from transnetv2 import TransNetV2

    model = TransNetV2(str(PATHS.project_root / "transnetv2-weights"))
    _, single_predictions, _ = model.predict_video(str(video_path))
    scenes = model.predictions_to_scenes(
        single_predictions,
        threshold=threshold,
    )
    if len(scenes) <= 1:
        return []

    capture = cv2.VideoCapture(str(video_path))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    capture.release()
    if fps <= 0:
        fps = 30.0

    cuts: list[tuple[float, float]] = []
    for start_frame in scenes[1:, 0]:
        frame = int(start_frame)
        low = max(0, frame - 3)
        high = min(len(single_predictions), frame + 3)
        confidence = float(np.max(single_predictions[low:high]))
        cuts.append((frame / fps, confidence))
    return cuts


def detect_and_save(
    db_path: Path,
    owner_id: str,
    video_id: str,
    *,
    threshold: float = 0.35,
) -> dict[str, Any]:
    threshold = min(0.95, max(0.05, float(threshold)))
    row = get_annotation_record(db_path, owner_id, video_id)
    if row is None:
        raise RuntimeError(f"数据库中找不到当前标注：{video_id}")
    video_path = _resolve_video_path(row)
    duration = _video_duration(video_path, row)
    cuts = detect_cuts(video_path, threshold=threshold)
    fields = build_shot_fields(
        cuts,
        duration,
        threshold=threshold,
    )
    saved = save_annotation_patch(
        db_path,
        owner_id,
        video_id,
        {
            **fields,
            "sync_level": "Yes",
            "seg_scores_3": "",
            "seg_scores_5": "",
            "status": "in_progress",
        },
    )
    if not saved:
        raise RuntimeError("分镜结果写入数据库失败")
    count = 0 if fields["shot_points"] == "NONE" else len(
        fields["shot_points"].split("/")
    )
    return {
        "video_id": video_id,
        "video_path": str(video_path),
        "duration": duration,
        "threshold": threshold,
        "detected_count": count,
        **fields,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect shot transitions for one database video."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--threshold", type=float, default=0.35)
    args = parser.parse_args()
    try:
        result = detect_and_save(
            Path(args.db),
            args.owner_id,
            args.video_id,
            threshold=args.threshold,
        )
    except Exception as exc:
        print(
            "MGSV_SHOT_RESULT="
            + json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from exc
    print(
        "MGSV_SHOT_RESULT="
        + json.dumps({"ok": True, **result}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
