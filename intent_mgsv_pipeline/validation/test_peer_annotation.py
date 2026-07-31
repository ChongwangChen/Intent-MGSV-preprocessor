from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.server.assignment import claim_next, get_annotation_record
from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.peer_annotation import (
    complete_peer_annotation,
    expected_score_counts,
    peer_required_missing,
    save_peer_patch,
)


class PeerAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "peer.sqlite3"
        init_db(self.db_path)
        with connect(self.db_path) as conn:
            video_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, video_title, row_json)
                VALUES ('video-1', '/data/video-1.mp4', 'Example', '{}')
                """
            ).lastrowid
            conn.execute(
                """
                INSERT INTO annotations(
                    video_id, annotator_id, sync_level, shot_points_3,
                    shot_points_5, vocal_presence, genre, status, row_json
                )
                VALUES (?, 'owner', 'Yes', '3/6/9', '2/4/6/8/10',
                        'Full', 'Pop', 'completed', '{}')
                """,
                (video_id,),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_two_annotators_can_claim_the_same_video(self) -> None:
        first = claim_next(self.db_path, "annotator_a")
        second = claim_next(self.db_path, "annotator_b")
        self.assertEqual(first["video_id"], "video-1")
        self.assertEqual(second["video_id"], "video-1")
        self.assertEqual(first["genre"], "Pop")
        self.assertEqual(second["vocal_presence"], "Full")

    def test_same_annotator_resumes_active_video(self) -> None:
        first = claim_next(self.db_path, "annotator_a")
        resumed = claim_next(self.db_path, "annotator_a")
        self.assertEqual(first["video_id"], resumed["video_id"])

    def test_peer_completion_requires_labels_and_all_visible_scores(self) -> None:
        row = claim_next(self.db_path, "annotator_a")
        self.assertEqual(expected_score_counts(row), (4, 6))
        missing = peer_required_missing(row)
        self.assertIn("emotion", missing)
        self.assertIn("seg_scores_3 (0/4)", missing)
        self.assertIn("seg_scores_5 (0/6)", missing)

        completed, missing = complete_peer_annotation(
            self.db_path,
            "annotator_a",
            "video-1",
            emotion=["欢乐"],
            style=["卡点"],
            usage_scene=["舞蹈"],
            seg_scores_3=["5", "4", "5", "4"],
            seg_scores_5=["5", "4", "4", "5", "4", "5"],
        )
        self.assertTrue(completed)
        self.assertEqual(missing, [])
        saved = get_annotation_record(self.db_path, "annotator_a", "video-1")
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["emotion"], "欢乐")

    def test_missing_middle_score_is_not_collapsed(self) -> None:
        claim_next(self.db_path, "annotator_a")
        save_peer_patch(
            self.db_path,
            "annotator_a",
            "video-1",
            emotion=["欢乐"],
            style=["卡点"],
            usage_scene=["舞蹈"],
            seg_scores_3=["5", None, "4", "5"],
            seg_scores_5=["5", "4", "4", "5", "4", "5"],
        )
        saved = get_annotation_record(self.db_path, "annotator_a", "video-1")
        self.assertEqual(saved["seg_scores_3"], "5/-/4/5")
        self.assertIn("seg_scores_3 (3/4)", peer_required_missing(saved))


if __name__ == "__main__":
    unittest.main()
