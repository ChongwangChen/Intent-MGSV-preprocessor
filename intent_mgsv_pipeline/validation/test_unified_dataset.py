from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.data_collection.build_cross_modal_templates import (
    build_templates,
)
from intent_mgsv_pipeline.data_collection.build_unified_manifest import (
    build_unified_manifest,
)
from intent_mgsv_pipeline.datasets.unified_intent_mgsv_dataset import (
    DatasetValidationError,
    UnifiedIntentMGSVRowDataset,
    unified_row_collate,
)


class UnifiedIntentMGSVDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.song = self.root / "song.mp3"
        self.video = self.root / "video.mp4"
        self.image = self.root / "image.jpg"
        self.song.write_bytes(b"song")
        self.video.write_bytes(b"video")
        self.image.write_bytes(b"image")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _base(self, sample_id: str, content_type: str) -> dict[str, object]:
        return {
            "sample_id": sample_id,
            "content_type": content_type,
            "content_id": sample_id,
            "music_id": "song",
            "full_song_path": str(self.song),
            "music_start": 12.5,
            "music_end": 27.5,
            "song_title": "Song",
            "song_artist": "Artist",
            "genre": "Pop",
            "vocal_presence": "Full",
            "emotion": "Happy/Energetic",
            "style": "Bright",
            "usage_scene": "Travel",
            "annotation_status": "completed",
        }

    def test_loads_video_image_and_text_manifests(self) -> None:
        video = self._base("video-1", "video")
        video.update(
            {
                "video_id": "video-1",
                "video_path": str(self.video),
                "video_total_duration": 20,
                "sync_level": 1,
                "shot_points_3": "3/8",
                "seg_scores_3": "4/5/3",
            }
        )
        image = self._base("image-1", "image")
        image["content_path"] = str(self.image)
        image["overall_score"] = 4
        text = self._base("text-1", "text")
        text["content_text"] = "A quiet street after rain."
        text["overall_score"] = 5
        manifest = self.root / "all.csv"
        pd.DataFrame([video, image, text]).to_csv(manifest, index=False)

        dataset = UnifiedIntentMGSVRowDataset(
            manifest,
            validate_files=True,
        )

        self.assertEqual(len(dataset), 3)
        self.assertEqual([dataset[i]["modality"] for i in range(3)], [
            "video",
            "image",
            "text",
        ])
        self.assertEqual(dataset[0]["rhythm"]["sync_level"], 1)
        self.assertEqual(dataset[0]["rhythm"]["seg_scores_3"], [4, 5, 3])
        self.assertEqual(dataset[1]["rhythm"]["sync_level"], 0)
        self.assertEqual(dataset[2]["content"]["text"], "A quiet street after rain.")
        self.assertEqual(
            dataset[0]["grounding"]["target_center_width"],
            [0.05, 0.0375],
        )
        batch = unified_row_collate([dataset[0], dataset[1]])
        self.assertEqual(batch["sample_ids"], ["video-1", "image-1"])
        self.assertEqual(batch["modalities"], ["video", "image"])

    def test_strict_mode_rejects_missing_text(self) -> None:
        row = self._base("text-invalid", "text")
        manifest = self.root / "invalid.csv"
        pd.DataFrame([row]).to_csv(manifest, index=False)

        with self.assertRaises(DatasetValidationError) as raised:
            UnifiedIntentMGSVRowDataset(manifest)

        self.assertIn(
            "missing_content_text",
            {issue.code for issue in raised.exception.issues},
        )

    def test_legacy_video_columns_are_canonicalized(self) -> None:
        row = self._base("legacy-video", "")
        row.update(
            {
                "video_id": "legacy-video",
                "video_path": str(self.video),
                "video_total_duration": 18,
                "sync_level": "No",
            }
        )
        manifest = self.root / "legacy.csv"
        pd.DataFrame([row]).to_csv(manifest, index=False)

        item = UnifiedIntentMGSVRowDataset(
            manifest,
            validate_files=True,
        )[0]

        self.assertEqual(item["modality"], "video")
        self.assertEqual(item["ids"]["video_id"], "legacy-video")
        self.assertEqual(item["content"]["path"], str(self.video.resolve()))

    def test_template_builder_creates_image_and_text_files(self) -> None:
        outputs = build_templates(self.root / "templates")

        self.assertEqual(set(outputs), {
            "image_template",
            "image_example",
            "text_template",
            "text_example",
        })
        for path in outputs.values():
            self.assertTrue(path.is_file())
        text_example = pd.read_csv(outputs["text_example"])
        self.assertEqual(text_example.loc[0, "content_type"], "text")
        self.assertTrue(text_example.loc[0, "content_text"])

    def test_build_unified_manifest_converts_legacy_video_table(self) -> None:
        row = self._base("legacy-video", "")
        row.update(
            {
                "video_id": "legacy-video",
                "video_path": str(self.video),
                "video_total_duration": 18,
                "sync_level": 0,
            }
        )
        source = self.root / "legacy.csv"
        output = self.root / "unified.csv"
        pd.DataFrame([row]).to_csv(source, index=False)

        result = build_unified_manifest([source], output)

        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["invalid_rows"], 0)
        converted = pd.read_csv(output, keep_default_na=False)
        self.assertEqual(converted.loc[0, "content_type"], "video")
        self.assertEqual(converted.loc[0, "content_id"], "legacy-video")
        self.assertEqual(converted.loc[0, "content_path"], str(self.video))

    def test_builder_resolves_video_id_and_keeps_none_vocal_label(self) -> None:
        nested = self.root / "download" / "account" / "nested.mp4"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"video")
        row = self._base("nested.mp4", "")
        row.update(
            {
                "video_id": "nested.mp4",
                "video_path": "",
                "vocal_presence": "None",
                "sync_level": 0,
            }
        )
        source = self.root / "source.csv"
        output = self.root / "unified.csv"
        pd.DataFrame([row]).to_csv(source, index=False)

        result = build_unified_manifest(
            [source],
            output,
            video_root=self.root / "download",
        )

        self.assertEqual(result["invalid_rows"], 0)
        converted = pd.read_csv(output, keep_default_na=False)
        self.assertEqual(converted.loc[0, "content_path"], str(nested.resolve()))
        self.assertEqual(converted.loc[0, "vocal_presence"], "None")


if __name__ == "__main__":
    unittest.main()
