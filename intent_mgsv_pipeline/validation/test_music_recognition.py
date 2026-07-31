from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from intent_mgsv_pipeline.runtime_config import load_runtime_paths
from yt_dy_auto import (
    RECOGNITION_VERSION,
    RecognitionCandidate,
    build_sample_starts,
    choose_candidate,
    should_process,
)


class MusicRecognitionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
