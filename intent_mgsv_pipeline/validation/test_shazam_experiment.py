from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from intent_mgsv_pipeline.music_recognition.shazam_experiment import (
    matches_reference,
    parse_shazam_response,
    prepare_shazam_sample,
    recognize_rows,
    select_experiment_rows,
    summarize_results,
)


class ShazamExperimentTests(unittest.TestCase):
    def test_parse_shazam_response(self) -> None:
        match = parse_shazam_response(
            {
                "track": {
                    "key": "123",
                    "title": "By Your Side",
                    "subtitle": "Jonas Blue & RAYE",
                    "genres": {"primary": "Pop"},
                    "share": {"href": "https://www.shazam.com/track/123"},
                    "sections": [
                        {
                            "metadata": [
                                {"title": "Album", "text": "Blue"},
                            ]
                        }
                    ],
                }
            }
        )
        self.assertEqual(match.title, "By Your Side")
        self.assertEqual(match.artist, "Jonas Blue & RAYE")
        self.assertEqual(match.album, "Blue")
        self.assertEqual(match.genre, "Pop")
        self.assertTrue(match.recognized)

    def test_reference_matching_tolerates_artist_separator(self) -> None:
        matched, title_score, artist_score = matches_reference(
            "By Your Side",
            "Jonas Blue/RAYE",
            "By Your Side",
            "Jonas Blue & RAYE",
        )
        self.assertTrue(matched)
        self.assertEqual(title_score, 1.0)
        self.assertGreaterEqual(artist_score, 0.5)

    def test_selects_control_and_failed_groups(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "video_id": "success.mp4",
                    "status": "recognized",
                    "song_title": "Song",
                    "song_artist": "Artist",
                },
                {
                    "video_id": "failed.mp4",
                    "status": "recognition_failed",
                    "song_title": "",
                    "song_artist": "",
                },
            ]
        )
        rows = select_experiment_rows(
            frame,
            success_samples=1,
            failed_samples=1,
        )
        self.assertEqual(
            [row["experiment_group"] for row in rows],
            ["control_success", "acr_failed"],
        )

    def test_summary_recommends_fallback_for_strong_results(self) -> None:
        controls = [
            {
                "experiment_group": "control_success",
                "shazam_recognized": True,
                "agrees_with_acr": True,
                "error": "",
            }
            for _ in range(10)
        ]
        failures = [
            {
                "experiment_group": "acr_failed",
                "shazam_recognized": index < 4,
                "agrees_with_acr": False,
                "error": "",
            }
            for index in range(10)
        ]
        summary = summarize_results(controls + failures)
        self.assertEqual(summary["acr_failed_rescued"], 4)
        self.assertEqual(
            summary["recommendation"],
            "recommend_fallback_integration",
        )
        self.assertFalse(summary["writes_official_tracking"])

    def test_recognition_prefers_clean_douk_music(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            video = root / "sample.mp4"
            music = root / "sample.mp3"
            video.write_bytes(b"video")
            music.write_bytes(b"m" * 2048)
            calls: list[str] = []

            async def recognize(path: str):
                calls.append(path)
                return {
                    "track": {
                        "title": "Song",
                        "subtitle": "Artist",
                    }
                }

            with patch(
                "intent_mgsv_pipeline.music_recognition.shazam_experiment.prepare_shazam_sample",
                return_value=(music, ""),
            ):
                rows = asyncio.run(
                    recognize_rows(
                        [
                            {
                                "video_id": video.name,
                                "video_path": str(video),
                                "experiment_group": "acr_failed",
                                "song_title": "",
                                "song_artist": "",
                            }
                        ],
                        video_index={video.name: video},
                        recognize=recognize,
                        timeout_seconds=2,
                        request_interval=0,
                    )
                )
            self.assertEqual(calls, [str(music)])
            self.assertEqual(rows[0]["recognition_media_source"], "douk_music")
            self.assertTrue(rows[0]["shazam_recognized"])
            self.assertTrue(rows[0]["needs_manual_review"])

    def test_sample_conversion_falls_back_when_ffmpeg_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "sample.mp3"
            source.write_bytes(b"m" * 2048)
            with patch(
                "intent_mgsv_pipeline.music_recognition.shazam_experiment.find_ffmpeg",
                return_value=None,
            ):
                result, warning = prepare_shazam_sample(source, root / "sample.wav")
            self.assertEqual(result, source)
            self.assertIn("ffmpeg not found", warning)


if __name__ == "__main__":
    unittest.main()
