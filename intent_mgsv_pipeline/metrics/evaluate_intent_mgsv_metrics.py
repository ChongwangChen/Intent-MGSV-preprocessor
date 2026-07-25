import argparse
import ast
import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT
DEFAULT_DATA = ROOT / "outputs" / "intent_mgsv_dataset" / "splits" / "all.csv"


def parse_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_points(value) -> list[float]:
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "NAN"}:
        return []
    if "/" in text:
        parts = text.split("/")
    else:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [float(x) for x in parsed]
        except Exception:
            pass
        parts = text.replace(",", "/").split("/")
    points = []
    for part in parts:
        try:
            points.append(float(part.strip()))
        except Exception:
            pass
    return points


def temporal_iou(gt_start, gt_end, pred_start, pred_end) -> float:
    inter = max(0.0, min(gt_end, pred_end) - max(gt_start, pred_start))
    union = max(gt_end, pred_end) - min(gt_start, pred_start)
    if union <= 0:
        return 0.0
    return inter / union


def beat_hit_rate(visual_points: list[float], audio_points: list[float], tolerance: float) -> float | None:
    if not visual_points:
        return None
    if not audio_points:
        return 0.0
    hits = 0
    for visual_t in visual_points:
        if min(abs(visual_t - audio_t) for audio_t in audio_points) <= tolerance:
            hits += 1
    return hits / len(visual_points)


def load_predictions(pred_path: str | None) -> pd.DataFrame | None:
    if not pred_path:
        return None
    pred = pd.read_csv(pred_path, keep_default_na=False)
    required = {"video_id", "pred_music_start", "pred_music_end"}
    missing = required - set(pred.columns)
    if missing:
        raise ValueError(f"Prediction file missing columns: {sorted(missing)}")
    return pred[["video_id", "pred_music_start", "pred_music_end"]]


def evaluate(data_path: Path, pred_path: str | None, tolerance: float) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(data_path, keep_default_na=False)
    pred = load_predictions(pred_path)
    if pred is not None:
        df = df.merge(pred, on="video_id", how="left")
    else:
        df["pred_music_start"] = df["music_start"]
        df["pred_music_end"] = df["music_end"]

    rows = []
    for _, row in df.iterrows():
        gt_start = parse_float(row["music_start"])
        gt_end = parse_float(row["music_end"])
        pred_start = parse_float(row["pred_music_start"], gt_start)
        pred_end = parse_float(row["pred_music_end"], gt_end)
        sync_level = int(parse_float(row.get("sync_level", 0)))

        visual_points = parse_points(row.get("shot_points", ""))
        audio_points = parse_points(row.get("auto_rhythm_points_audio", ""))
        bhr = beat_hit_rate(visual_points, audio_points, tolerance)

        rows.append({
            "video_id": row["video_id"],
            "sync_level": sync_level,
            "temporal_iou": temporal_iou(gt_start, gt_end, pred_start, pred_end),
            "start_error": abs(pred_start - gt_start),
            "end_error": abs(pred_end - gt_end),
            "beat_hit_rate": bhr,
            "visual_point_count": len(visual_points),
            "audio_point_count": len(audio_points),
        })

    result = pd.DataFrame(rows)
    summary = {
        "rows": int(len(result)),
        "mean_temporal_iou": round(float(result["temporal_iou"].mean()), 4),
        "mean_start_error": round(float(result["start_error"].mean()), 4),
        "mean_end_error": round(float(result["end_error"].mean()), 4),
        "mean_beat_hit_rate_all_with_points": round(float(result["beat_hit_rate"].dropna().mean()), 4),
        "by_sync_level": {},
    }
    for sync_level, part in result.groupby("sync_level"):
        summary["by_sync_level"][str(sync_level)] = {
            "rows": int(len(part)),
            "mean_temporal_iou": round(float(part["temporal_iou"].mean()), 4),
            "mean_start_error": round(float(part["start_error"].mean()), 4),
            "mean_beat_hit_rate": round(float(part["beat_hit_rate"].dropna().mean()), 4),
        }

    return result, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--pred", default=None)
    parser.add_argument("--tolerance", type=float, default=0.15)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result, summary = evaluate(Path(args.data), args.pred, args.tolerance)
    out_path = Path(args.out) if args.out else Path(args.data).with_name("metrics_sanity.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")

    summary_path = out_path.with_suffix(".summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"metrics: {out_path}")
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
