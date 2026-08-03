from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.server.assignment import (
    claim_next_owner,
    get_annotation_record,
    get_previous_annotation,
    owner_annotation_progress,
)
from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.owner_annotation import (
    complete_owner_annotation,
    owner_required_missing,
    save_owner_patch,
)


class OwnerAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "owner.sqlite3"
        init_db(self.db_path)
        with connect(self.db_path) as conn:
            song_id = conn.execute(
                """
                INSERT INTO songs(title, artist, full_song_path, row_json)
                VALUES ('Song', 'Artist', '/data/song.mp3', '{}')
                """
            ).lastrowid
            for index, verified in ((1, "Yes"), (2, "")):
                video_id = conn.execute(
                    """
                    INSERT INTO videos(video_id, video_path, row_json)
                    VALUES (?, ?, '{}')
                    """,
                    (f"video-{index}.mp4", f"/data/video-{index}.mp4"),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO annotations(
                        video_id, song_id, annotator_id, music_start,
                        music_end, song_verified, status, row_json
                    )
                    VALUES (?, ?, 'owner', 2, 17, ?, 'in_progress', '{}')
                    """,
                    (video_id, song_id, verified),
                )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _valid_values(self) -> dict[str, object]:
        return {
            "sync_level": "Yes",
            "shot_points_3": "3/6/9",
            "shot_points_5": "2/4/6/8",
            "vocal_presence": "Full",
            "genre": "Pop",
            "emotion": ["欢乐"],
            "style": ["卡点"],
            "usage_scene": ["舞蹈"],
            "seg_scores_3": ["5", "4", "5", "4"],
            "seg_scores_5": ["5", "4", "5", "4", "5"],
        }

    def test_owner_only_claims_song_verified_rows(self) -> None:
        row = claim_next_owner(self.db_path)
        self.assertEqual(row["video_id"], "video-1.mp4")
        progress = owner_annotation_progress(self.db_path)
        self.assertEqual(progress["total"], 1)
        self.assertEqual(progress["completed"], 0)

    def test_owner_completion_requires_all_fields_and_scores(self) -> None:
        row = claim_next_owner(self.db_path)
        missing = owner_required_missing(row)
        self.assertIn("vocal_presence", missing)
        self.assertIn("genre", missing)
        self.assertIn("emotion", missing)
        completed, missing = complete_owner_annotation(
            self.db_path,
            "owner",
            "video-1.mp4",
            **self._valid_values(),
        )
        self.assertTrue(completed)
        self.assertEqual(missing, [])
        saved = get_annotation_record(
            self.db_path,
            "owner",
            "video-1.mp4",
        )
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(owner_annotation_progress(self.db_path)["completed"], 1)

    def test_detected_no_shot_marker_allows_whole_video_score(self) -> None:
        values = self._valid_values()
        values["shot_points_3"] = "NONE"
        values["shot_points_5"] = "NONE"
        values["seg_scores_3"] = ["4"]
        values["seg_scores_5"] = []
        completed, missing = complete_owner_annotation(
            self.db_path,
            "owner",
            "video-1.mp4",
            **values,
        )

        self.assertTrue(completed)
        self.assertEqual(missing, [])

    def test_invalid_edit_reopens_completed_annotation(self) -> None:
        claim_next_owner(self.db_path)
        complete_owner_annotation(
            self.db_path,
            "owner",
            "video-1.mp4",
            **self._valid_values(),
        )
        values = self._valid_values()
        values["vocal_presence"] = None
        save_owner_patch(
            self.db_path,
            "owner",
            "video-1.mp4",
            **values,
        )
        saved = get_annotation_record(
            self.db_path,
            "owner",
            "video-1.mp4",
        )
        self.assertEqual(saved["status"], "in_progress")
        self.assertIn("vocal_presence", owner_required_missing(saved))

    def test_previous_completed_annotation_can_be_loaded(self) -> None:
        claim_next_owner(self.db_path)
        complete_owner_annotation(
            self.db_path,
            "owner",
            "video-1.mp4",
            **self._valid_values(),
        )
        previous = get_previous_annotation(
            self.db_path,
            "owner",
            "video-2.mp4",
        )
        self.assertEqual(previous["video_id"], "video-1.mp4")


if __name__ == "__main__":
    unittest.main()
