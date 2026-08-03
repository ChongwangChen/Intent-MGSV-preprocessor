from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.server.assignment import (
    annotation_progress,
    claim_next,
    get_annotation_record,
)
from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.export_consensus import export_consensus
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
                    shot_points_5, vocal_presence, genre, song_verified,
                    emotion, style, usage_scene, seg_scores_3, seg_scores_5,
                    status, row_json
                )
                VALUES (?, 'owner', 'Yes', '3/6/9', '2/4/6/8/10',
                        'Full', 'Pop', 'Yes', '欢乐', '卡点', '舞蹈',
                        '4/4/4/4', '4/4/4/4/4/4', 'completed', '{}')
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
        progress = annotation_progress(self.db_path, "annotator_a")
        self.assertEqual(progress["completed"], 0)
        self.assertEqual(progress["total"], 1)

    def test_same_annotator_resumes_active_video(self) -> None:
        first = claim_next(self.db_path, "annotator_a")
        resumed = claim_next(self.db_path, "annotator_a")
        self.assertEqual(first["video_id"], resumed["video_id"])

    def test_peer_cannot_claim_until_owner_is_completed_and_song_verified(self) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE annotations
                SET status='in_progress', song_verified=''
                WHERE annotator_id='owner'
                """
            )
        self.assertIsNone(claim_next(self.db_path, "annotator_a"))

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

    def test_database_consensus_uses_union_and_segment_mean(self) -> None:
        claim_next(self.db_path, "annotator_a")
        completed, missing = complete_peer_annotation(
            self.db_path,
            "annotator_a",
            "video-1",
            emotion=["治愈"],
            style=["转场"],
            usage_scene=["舞蹈"],
            seg_scores_3=["2", "4", "2", "4"],
            seg_scores_5=["2", "4", "2", "4", "2", "4"],
        )
        self.assertTrue(completed)
        self.assertEqual(missing, [])
        out_path = Path(self.temp_dir.name) / "consensus.xlsx"
        summary = export_consensus(
            self.db_path,
            out_path,
            peer_id="annotator_a",
        )
        self.assertEqual(summary["completed_pairs"], 1)
        import pandas as pd

        result = pd.read_excel(out_path, keep_default_na=False).iloc[0]
        self.assertEqual(result["emotion"], "欢乐/治愈")
        self.assertEqual(result["style"], "卡点/转场")
        self.assertEqual(result["usage_scene"], "舞蹈")
        self.assertEqual(result["seg_scores_3"], "3/4/3/4")


    def test_consensus_supports_multiple_peer_annotators(self) -> None:
        peer_values = {
            "annotator_a": {
                "emotion": ["peer-emotion-a"],
                "style": ["peer-style-a"],
                "usage_scene": ["peer-scene-a"],
                "seg_scores_3": ["2", "4", "2", "4"],
                "seg_scores_5": ["2", "4", "2", "4", "2", "4"],
            },
            "annotator_b": {
                "emotion": ["peer-emotion-b"],
                "style": ["peer-style-b"],
                "usage_scene": ["peer-scene-b"],
                "seg_scores_3": ["5", "3", "5", "3"],
                "seg_scores_5": ["5", "3", "5", "3", "5", "3"],
            },
        }
        for annotator_id, values in peer_values.items():
            claim_next(self.db_path, annotator_id)
            completed, missing = complete_peer_annotation(
                self.db_path,
                annotator_id,
                "video-1",
                **values,
            )
            self.assertTrue(completed)
            self.assertEqual(missing, [])

        out_path = Path(self.temp_dir.name) / "multi-consensus.xlsx"
        summary = export_consensus(
            self.db_path,
            out_path,
            peer_ids=["annotator_a", "annotator_b"],
        )
        self.assertEqual(
            summary["annotator_ids"],
            ["owner", "annotator_a", "annotator_b"],
        )
        import pandas as pd

        result = pd.read_excel(out_path, keep_default_na=False).iloc[0]
        self.assertIn("peer-emotion-a", result["emotion"])
        self.assertIn("peer-emotion-b", result["emotion"])
        self.assertIn("peer-style-a", result["style"])
        self.assertIn("peer-scene-b", result["usage_scene"])
        self.assertEqual(result["seg_scores_3"], "3.67/3.67/3.67/3.67")


if __name__ == "__main__":
    unittest.main()
