import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from intent_mgsv_pipeline.datasets.intent_mgsv_dataset import IntentMGSVRowDataset


DEFAULT_SPLIT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits"


def check_dataset(csv_path: Path, max_m_duration: float) -> dict:
    dataset = IntentMGSVRowDataset(csv_path, max_m_duration=max_m_duration)
    bad_target = 0
    missing_intent = 0
    missing_paths = 0
    sync_counts = {0: 0, 1: 0}
    visual_point_rows = 0
    audio_point_rows = 0

    for i in range(len(dataset)):
        item = dataset[i]
        center, width = item["grounding"]["target_center_width"]
        if not (0 <= center <= 1 and 0 < width <= 1):
            bad_target += 1

        intent = item["intent"]
        if not intent["genre"] or not intent["vocal_presence"] or not intent["emotion"] or not intent["style"] or not intent["usage_scene"]:
            missing_intent += 1

        paths = item["paths"]
        if not paths["video_path"] or not paths["music_path"]:
            missing_paths += 1

        sync_level = item["rhythm"]["sync_level"]
        sync_counts[sync_level] = sync_counts.get(sync_level, 0) + 1
        if item["rhythm"]["visual_points"]:
            visual_point_rows += 1
        if item["rhythm"]["audio_points"]:
            audio_point_rows += 1

    return {
        "file": str(csv_path),
        "rows": len(dataset),
        "bad_target": bad_target,
        "missing_intent": missing_intent,
        "missing_paths": missing_paths,
        "sync_counts": {str(k): int(v) for k, v in sorted(sync_counts.items())},
        "visual_point_rows": visual_point_rows,
        "audio_point_rows": audio_point_rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--max-m-duration", type=float, default=400.0)
    args = parser.parse_args()

    failed = False
    results = []
    for name in ["train.csv", "val.csv", "test.csv", "all.csv"]:
        result = check_dataset(Path(args.split_dir) / name, args.max_m_duration)
        print(json.dumps(result, ensure_ascii=False))
        results.append(result)
        failed = failed or any(result[k] for k in ["bad_target", "missing_intent", "missing_paths"])

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
