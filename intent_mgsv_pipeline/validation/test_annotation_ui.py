from __future__ import annotations

import unittest

from intent_mgsv_pipeline.server.annotation_options import (
    EMOTION_GROUPS,
    merge_group_values,
    split_group_values,
)
from intent_mgsv_pipeline.server.segment_ui import segment_schemes


class AnnotationUiTests(unittest.TestCase):
    def test_non_sync_video_uses_one_whole_video_segment(self) -> None:
        bounds_a, caption, bounds_b = segment_schemes(
            {
                "sync_level": "No",
                "duration": 18.5,
                "music_start": 50,
                "music_end": 68.5,
            }
        )

        self.assertEqual(bounds_a, [(0.0, 18.5)])
        self.assertIn("整段", caption)
        self.assertIsNone(bounds_b)

    def test_sync_segments_use_video_timeline_not_song_offset(self) -> None:
        bounds_a, _, bounds_b = segment_schemes(
            {
                "sync_level": "Yes",
                "duration": 12,
                "music_start": 50,
                "music_end": 62,
                "shot_points_3": "3/8",
                "shot_points_5": "2/5/9",
            }
        )

        self.assertEqual(bounds_a, [(0.0, 3.0), (3.0, 8.0), (8.0, 12.0)])
        self.assertEqual(
            bounds_b,
            [(0.0, 2.0), (2.0, 5.0), (5.0, 9.0), (9.0, 12.0)],
        )

    def test_grouped_values_round_trip_to_original_field(self) -> None:
        groups = split_group_values("欢乐/神秘/伤感", EMOTION_GROUPS)
        self.assertEqual(groups, (["欢乐"], ["神秘"], ["伤感"]))
        self.assertEqual(
            merge_group_values(*groups),
            ["欢乐", "神秘", "伤感"],
        )


if __name__ == "__main__":
    unittest.main()
