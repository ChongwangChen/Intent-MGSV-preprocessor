from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from intent_mgsv_pipeline.runtime_config import RuntimePaths, load_runtime_paths
from yt_dy_auto import (
    ACRCloudQuotaExceeded,
    RECOGNITION_VERSION,
    RecognitionCandidate,
    build_sample_starts,
    choose_candidate,
    find_clean_music_for_video,
    process_videos,
    repair_quota_exhausted_records,
    recognize_with_clean_audio_fallback,
    should_process,
    should_process_video,
)


class MusicRecognitionTests(unittest.TestCase):
    def test_repairs_rows_misclassified_after_quota_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            tracking_path = Path(temp_name) / "tracking.xlsx"
            frame = pd.DataFrame(
                [
                    {
                        "video_id": "quota.mp4",
                        "status": "recognition_failed",
                        "recognition_version": RECOGNITION_VERSION,
                        "recognition_error": "ACRCloud code=3003: requests limit exceeded",
                    },
                    {
                        "video_id": "normal.mp4",
                        "status": "recognition_failed",
                        "recognition_version": RECOGNITION_VERSION,
                        "recognition_error": "no match",
                    },
                ]
            )
            repaired, count = repair_quota_exhausted_records(tracking_path, frame)
            self.assertEqual(count, 1)
            quota_row = repaired[repaired["video_id"] == "quota.mp4"].iloc[0]
            normal_row = repaired[repaired["video_id"] == "normal.mp4"].iloc[0]
            self.assertEqual(quota_row["status"], "recognition_deferred_quota")
            self.assertEqual(quota_row["recognition_version"], "")
            self.assertEqual(normal_row["status"], "recognition_failed")
            self.assertTrue(tracking_path.is_file())

    def test_finds_same_stem_douk_music(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "sample.mp4"
            music = root / "sample.mp3"
            video.write_bytes(b"video")
            music.write_bytes(b"m" * 2048)
            self.assertEqual(find_clean_music_for_video(video), music)

    def test_clean_music_is_tried_before_video_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "sample.mp4"
            music = root / "sample.m4a"
            video.write_bytes(b"video")
            music.write_bytes(b"m" * 2048)
            recognized = {
                "title": "Song",
                "artist": "Artist",
                "status": "recognized",
                "recognition_error": "",
            }
            with patch(
                "yt_dy_auto.recognize_music_multi_window",
                return_value=recognized,
            ) as identify:
                result, source_path, source = recognize_with_clean_audio_fallback(
                    video,
                    {},
                    sample_duration=15,
                    max_samples=4,
                    confidence_threshold=75,
                )
            self.assertEqual(result["title"], "Song")
            self.assertEqual(source_path, music)
            self.assertEqual(source, "douk_music")
            identify.assert_called_once()
            self.assertEqual(identify.call_args.args[0], music)

    def test_video_audio_is_fallback_when_clean_music_has_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "sample.mp4"
            music = root / "sample.mp3"
            video.write_bytes(b"video")
            music.write_bytes(b"m" * 2048)
            failed = {
                "status": "recognition_failed",
                "recognition_error": "no match",
            }
            recognized = {
                "title": "Song",
                "artist": "Artist",
                "status": "recognized",
                "recognition_error": "",
            }
            with patch(
                "yt_dy_auto.recognize_music_multi_window",
                side_effect=[failed, recognized],
            ) as identify:
                result, source_path, source = recognize_with_clean_audio_fallback(
                    video,
                    {},
                    sample_duration=15,
                    max_samples=4,
                    confidence_threshold=75,
                )
            self.assertEqual(result["title"], "Song")
            self.assertEqual(source_path, video)
            self.assertEqual(source, "video_fallback")
            self.assertEqual(
                [call.args[0] for call in identify.call_args_list],
                [music, video],
            )

    def test_sample_starts_are_spread_and_bounded(self) -> None:
        starts = build_sample_starts(60.0, sample_duration=15.0, max_samples=4)
        self.assertEqual(len(starts), 4)
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(0.0 <= value <= 45.0 for value in starts))

    def test_high_confidence_single_sample_is_accepted(self) -> None:
        candidate, votes, summary = choose_candidate(
            [RecognitionCandidate("Song", "Artist", 91, 5.0)]
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(votes, 1)
        self.assertIn("5.00s:91", summary)

    def test_two_medium_confidence_votes_are_accepted(self) -> None:
        candidate, votes, _ = choose_candidate(
            [
                RecognitionCandidate("Song", "Artist", 68, 5.0),
                RecognitionCandidate("song", "artist", 70, 25.0),
            ]
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(votes, 2)

    def test_low_confidence_candidate_is_rejected(self) -> None:
        candidate, votes, _ = choose_candidate(
            [RecognitionCandidate("Song", "Artist", 55, 5.0)]
        )
        self.assertIsNone(candidate)
        self.assertEqual(votes, 1)

    def test_failed_legacy_record_is_reprocessed_once(self) -> None:
        previous = {"status": "recognition_failed"}
        self.assertTrue(should_process(previous, retry_failed=False))
        current = {
            "status": "recognition_failed",
            "recognition_version": RECOGNITION_VERSION,
        }
        self.assertFalse(should_process(current, retry_failed=False))
        self.assertTrue(should_process(current, retry_failed=True))

    def test_existing_dataset_video_is_skipped_by_default(self) -> None:
        video_id = "already-annotated.mp4"
        self.assertFalse(
            should_process_video(
                video_id,
                None,
                {video_id},
                retry_failed=True,
                include_existing_dataset=False,
            )
        )
        self.assertTrue(
            should_process_video(
                video_id,
                None,
                {video_id},
                retry_failed=True,
                include_existing_dataset=True,
            )
        )

    def test_runtime_paths_follow_server_environment(self) -> None:
        root = Path("/data/intent_mgsv/repo")
        with patch.dict(os.environ, {"MGSV_ROOT": str(root)}, clear=True):
            paths = load_runtime_paths()
        resolved_root = root.resolve()
        self.assertEqual(paths.project_root, resolved_root)
        self.assertEqual(
            paths.douk_download_root,
            resolved_root / "DouK-Source" / "Volume" / "Download",
        )

    def test_download_mode_prepares_already_recognized_tracking_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            download_root = root / "download"
            output_dir = root / "outputs"
            download_root.mkdir()
            output_dir.mkdir()
            video = download_root / "recognized.mp4"
            video.write_bytes(b"video")
            tracking = output_dir / "tracking.xlsx"
            pd.DataFrame(
                [
                    {
                        "video_id": video.name,
                        "video_path": str(video),
                        "song_title": "Song",
                        "song_artist": "Artist",
                        "status": "recognized",
                        "recognition_version": RECOGNITION_VERSION,
                    }
                ]
            ).to_excel(tracking, index=False)
            paths = RuntimePaths(
                project_root=root,
                douk_download_root=download_root,
                douk_data_excel=root / "data.xlsx",
                output_dir=output_dir,
                full_music_dir=output_dir / "full_music",
                full_songs_dir=output_dir / "full_songs",
                acr_tracking_excel=tracking,
                master_excel=output_dir / "master.xlsx",
                server_db=output_dir / "server.sqlite3",
                acr_config_file=root / "acr.json",
            )
            with (
                patch(
                    "yt_dy_auto.load_acrcloud_config",
                    return_value={"host": "example", "access_key": "x", "access_secret": "y"},
                ),
                patch(
                    "intent_mgsv_pipeline.music_preparation.pipeline.prepare_recognized_music",
                    return_value={"processed": 1},
                ) as prepare,
            ):
                counts = process_videos(paths, recognition_only=False)
            self.assertEqual(counts["processed"], 0)
            prepare.assert_called_once()
            self.assertEqual(
                prepare.call_args.kwargs["video_ids"],
                {video.name},
            )

    def test_quota_exhaustion_stops_batch_without_recording_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            download_root = root / "download"
            output_dir = root / "outputs"
            download_root.mkdir()
            output_dir.mkdir()
            for name in ("one.mp4", "two.mp4"):
                (download_root / name).write_bytes(b"video")
            tracking = output_dir / "tracking.xlsx"
            paths = RuntimePaths(
                project_root=root,
                douk_download_root=download_root,
                douk_data_excel=root / "data.xlsx",
                output_dir=output_dir,
                full_music_dir=output_dir / "full_music",
                full_songs_dir=output_dir / "full_songs",
                acr_tracking_excel=tracking,
                master_excel=output_dir / "master.xlsx",
                server_db=output_dir / "server.sqlite3",
                acr_config_file=root / "acr.json",
            )
            with (
                patch(
                    "yt_dy_auto.load_acrcloud_config",
                    return_value={"host": "example", "access_key": "x", "access_secret": "y"},
                ),
                patch(
                    "yt_dy_auto.recognize_with_clean_audio_fallback",
                    side_effect=ACRCloudQuotaExceeded("ACRCloud code=3003"),
                ) as recognize,
            ):
                counts = process_videos(paths, retry_failed=True)
            self.assertEqual(counts["processed"], 0)
            self.assertEqual(counts["failed"], 0)
            self.assertEqual(counts["quota_exhausted"], 1)
            recognize.assert_called_once()
            self.assertFalse(tracking.exists())


if __name__ == "__main__":
    unittest.main()
