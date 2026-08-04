from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.schema.unified_sample_schema import UNIFIED_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = (
    PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "templates"
)


def _template_frame(content_type: str) -> pd.DataFrame:
    frame = pd.DataFrame(columns=UNIFIED_COLUMNS)
    frame.loc[0] = {column: "" for column in UNIFIED_COLUMNS}
    frame.loc[0, "content_type"] = content_type
    frame.loc[0, "sync_level"] = 0
    frame.loc[0, "annotation_status"] = "pending"
    return frame


def _example_frame(content_type: str) -> pd.DataFrame:
    base = {column: "" for column in UNIFIED_COLUMNS}
    base.update(
        {
            "sample_id": f"{content_type}_example_001",
            "content_type": content_type,
            "content_id": f"{content_type}_example_001",
            "music_id": "song_example",
            "full_song_path": "data/music/song_example.mp3",
            "music_start": 42.5,
            "music_end": 57.5,
            "song_title": "Example Song",
            "song_artist": "Example Artist",
            "genre": "Pop",
            "vocal_presence": "Full",
            "emotion": "Happy/Energetic",
            "style": "Bright",
            "usage_scene": "Travel",
            "sync_level": 0,
            "overall_score": 4,
            "pair_verified": "Yes",
            "source": "example_only",
            "annotator_id": "example",
            "annotation_status": "completed",
            "group_id": f"{content_type}_example",
            "split": "train",
        }
    )
    if content_type == "image":
        base["content_path"] = "data/images/image_example_001.jpg"
        base["content_paths"] = json.dumps(
            [
                "data/images/image_example_001.jpg",
                "data/images/image_example_002.jpg",
            ],
            ensure_ascii=False,
        )
    else:
        base["content_text"] = (
            "A quiet city street after rain, with warm lights reflected "
            "on the pavement."
        )
    return pd.DataFrame([base], columns=UNIFIED_COLUMNS)


def build_templates(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    for content_type in ("image", "text"):
        template = out_dir / f"{content_type}_music_annotation_template.csv"
        example = out_dir / f"{content_type}_music_annotation_example.csv"
        _template_frame(content_type).to_csv(
            template,
            index=False,
            encoding="utf-8-sig",
        )
        _example_frame(content_type).to_csv(
            example,
            index=False,
            encoding="utf-8-sig",
        )
        outputs[f"{content_type}_template"] = template
        outputs[f"{content_type}_example"] = example
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create image/text-to-music annotation manifests."
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()
    outputs = build_templates(Path(args.out_dir))
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
