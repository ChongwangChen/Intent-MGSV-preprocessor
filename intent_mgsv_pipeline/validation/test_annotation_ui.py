from __future__ import annotations

import unittest

from intent_mgsv_pipeline.server.annotation_options import (
    EMOTION_GROUPS,
    merge_group_values,
    split_group_values,
)
from intent_mgsv_pipeline.server.owner_annotation import normalize_sync
from intent_mgsv_pipeline.server.segment_ui import (
    FLOATING_VIDEO_CLASS,
    segment_html,
    segment_payload,
    segment_play_js,
    segment_schemes,
)
from intent_mgsv_pipeline.server.shot_detection import build_shot_fields


class AnnotationUiTests(unittest.TestCase):
    def test_legacy_numeric_sync_value_is_normalized(self) -> None:
        self.assertEqual(normalize_sync("2"), "Yes")
        self.assertEqual(normalize_sync(2), "Yes")

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

    def test_segment_player_labels_schemes_and_opens_mini_video(self) -> None:
        markup = segment_html(
            {
                "sync_level": "Yes",
                "duration": 12,
                "shot_points_3": "3/8",
                "shot_points_5": "2/5/9",
            },
            video_elem_id="owner-video",
        )

        self.assertIn("Top-3", markup)
        self.assertIn("Top-5", markup)
        payload = segment_payload(
            {
                "sync_level": "Yes",
                "duration": 12,
                "shot_points_3": "3/8",
                "shot_points_5": "2/5/9",
            }
        )
        script = segment_play_js("owner-video", "A", 0)
        self.assertEqual(payload["A"][0], {"start": 0.0, "end": 3.0})
        self.assertIn("#owner-video video", script)
        self.assertIn(FLOATING_VIDEO_CLASS, script)
        self.assertIn("video.play()", script)

    def test_grouped_values_round_trip_to_original_field(self) -> None:
        groups = split_group_values("欢乐/神秘/伤感", EMOTION_GROUPS)
        self.assertEqual(groups, (["欢乐"], ["神秘"], ["伤感"]))
        self.assertEqual(
            merge_group_values(*groups),
            ["欢乐", "神秘", "伤感"],
        )

    def test_shot_detection_builds_two_even_schemes(self) -> None:
        fields = build_shot_fields(
            [
                (0.1, 0.9),
                (2.0, 0.8),
                (4.0, 0.7),
                (7.0, 0.9),
                (9.8, 0.9),
            ],
            10.0,
            threshold=0.35,
        )

        self.assertEqual(fields["shot_points"], "2.00/4.00/7.00")
        self.assertEqual(fields["shot_points_3"], "2.00/4.00/7.00")
        self.assertEqual(fields["shot_points_5"], "SAME")


if __name__ == "__main__":
    unittest.main()
