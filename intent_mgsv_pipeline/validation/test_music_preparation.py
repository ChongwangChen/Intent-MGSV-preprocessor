from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intent_mgsv_pipeline.music_preparation.alignment import (
    AlignmentResult,
    WindowMatch,
    summarize_matches,
)
from intent_mgsv_pipeline.music_preparation.pipeline import prepare_record
from intent_mgsv_pipeline.music_preparation.pipeline import (
    prepare_recognized_music,
)
from intent_mgsv_pipeline.music_preparation.qqmusic import QQMusicCandidate
from intent_mgsv_pipeline.runtime_config import RuntimePaths
from intent_mgsv_pipeline.server.assignment import claim_next
from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.music_review import confirm_music_review
from intent_mgsv_pipeline.server.music_review import claim_next_music_review
from intent_mgsv_pipeline.server.music_review import music_review_progress
from intent_mgsv_pipeline.server.music_review import reject_music_review
from intent_mgsv_pipeline.server.music_review import realign_music_review


class MusicPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        output_dir = self.root / "outputs"
        self.paths = RuntimePaths(
            project_root=self.root,
            douk_download_root=self.root / "download",
            douk_data_excel=self.root / "data.xlsx",
            output_dir=output_dir,
            full_music_dir=output_dir / "full_music",
            full_songs_dir=output_dir / "full_songs",
            acr_tracking_excel=output_dir / "tracking.xlsx",
            master_excel=output_dir / "master.xlsx",
            server_db=output_dir / "server.sqlite3",
            acr_config_file=self.root / "acr.json",
        )
        self.paths.douk_download_root.mkdir(parents=True)
        self.paths.full_songs_dir.mkdir(parents=True)
        self.db_path = self.paths.server_db
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_multiple_candidates_resolve_repeated_song_sections(self) -> None:
        result = summarize_matches(
            [
                WindowMatch(0.0, 50.0, 0.80),
                WindowMatch(0.0, 10.0, 0.70),
                WindowMatch(12.0, 100.0, 0.82),
                WindowMatch(12.0, 10.2, 0.71),
                WindowMatch(24.0, 150.0, 0.79),
                WindowMatch(24.0, 9.9, 0.69),
            ],
            cluster_tolerance=0.9,
        )
        self.assertEqual(result.status, "ready_for_review")
        self.assertEqual(result.votes, 3)
        self.assertAlmostEqual(result.song_offset, 10.035, places=3)

    def test_reused_song_is_realigned_for_each_video(self) -> None:
        candidate = QQMusicCandidate(
            song_mid="mid-1",
            title="Same Song",
            artist="Same Artist",
            album="Album",
            duration=180,
            title_score=1.0,
            artist_score=1.0,
            match_score=1.0,
        )
        song_path = self.paths.full_songs_dir / "Same Song_Same Artist.mp3"
        song_path.write_bytes(b"x" * 2048)
        videos = []
        for index in (1, 2):
            video = self.paths.douk_download_root / f"video-{index}.mp4"
            video.write_bytes(b"video")
            videos.append(video)
        records = [
            {
                "video_id": video.name,
                "video_path": str(video),
                "song_title": candidate.title,
                "song_artist": candidate.artist,
                "acr_confidence": 100,
                "recognition_votes": 2,
                "video_total_duration": 20,
            }
            for video in videos
        ]
        alignments = [
            AlignmentResult(1.5, 0.0, 0.80, 3, "ready_for_review"),
            AlignmentResult(7.5, 0.0, 0.76, 3, "ready_for_review"),
        ]
        with (
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.search_qqmusic",
                return_value=[candidate],
            ),
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.download_qq_candidate",
                return_value=(song_path, "qqmusic_test"),
            ) as download,
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.align_video_to_song",
                side_effect=alignments,
            ) as align,
            connect(self.db_path) as conn,
        ):
            first = prepare_record(
                conn,
                records[0],
                videos[0],
                self.paths,
                retry_failed=False,
                fallback_sources=(),
            )
            second = prepare_record(
                conn,
                records[1],
                videos[1],
                self.paths,
                retry_failed=False,
                fallback_sources=(),
            )

        self.assertEqual(first, "ready_for_review")
        self.assertEqual(second, "ready_for_review")
        self.assertEqual(download.call_count, 1)
        self.assertEqual(align.call_count, 2)
        with connect(self.db_path) as conn:
            offsets = [
                row["song_offset"]
                for row in conn.execute(
                    "SELECT song_offset FROM music_preparations ORDER BY video_id"
                ).fetchall()
            ]
            song_count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        self.assertEqual(offsets, [1.5, 7.5])
        self.assertEqual(song_count, 1)

    def test_downloaded_qq_candidate_with_bad_alignment_reaches_review(self) -> None:
        candidate = QQMusicCandidate(
            song_mid="mid-review",
            title="Right Song",
            artist="Right Artist",
            album="Album",
            duration=180,
            title_score=1.0,
            artist_score=1.0,
            match_score=1.0,
        )
        video = self.paths.douk_download_root / "video-needs-review.mp4"
        video.write_bytes(b"video")
        song = self.paths.full_songs_dir / "right-song.mp3"
        song.write_bytes(b"x" * 2048)
        record = {
            "video_id": video.name,
            "video_path": str(video),
            "song_title": candidate.title,
            "song_artist": candidate.artist,
            "acr_confidence": 100,
            "recognition_votes": 2,
            "video_total_duration": 20,
        }
        failed_alignment = AlignmentResult(
            None,
            0.0,
            0.12,
            1,
            "alignment_failed",
            "no reliable window match",
        )
        with (
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.search_qqmusic",
                return_value=[candidate],
            ),
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.download_qq_candidate",
                return_value=(song, "qqmusic_test"),
            ),
            patch(
                "intent_mgsv_pipeline.music_preparation.pipeline.align_video_to_song",
                return_value=failed_alignment,
            ),
            connect(self.db_path) as conn,
        ):
            status = prepare_record(
                conn,
                record,
                video,
                self.paths,
                retry_failed=False,
                fallback_sources=(),
            )

        self.assertEqual(status, "needs_review")
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT status, full_song_path, song_id, match_score, error
                FROM music_preparations
                """
            ).fetchone()
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(Path(row["full_song_path"]), song)
        self.assertIsNotNone(row["song_id"])
        self.assertEqual(row["match_score"], 0.12)
        self.assertIn("requires human review", row["error"])

    def test_human_confirmation_is_required_before_peer_annotation(self) -> None:
        video = self.paths.douk_download_root / "video-review.mp4"
        video.write_bytes(b"video")
        song = self.paths.full_songs_dir / "song.mp3"
        song.write_bytes(b"x" * 2048)
        with connect(self.db_path) as conn:
            video_db_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, duration, row_json)
                VALUES (?, ?, 15, '{}')
                """,
                (video.name, str(video)),
            ).lastrowid
            song_db_id = conn.execute(
                """
                INSERT INTO songs(title, artist, full_song_path, row_json)
                VALUES ('Song', 'Artist', ?, '{}')
                """,
                (str(song),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO music_preparations(
                    video_id, song_id, recognized_title, recognized_artist,
                    full_song_path, song_offset, aligned_duration, match_score,
                    status
                )
                VALUES (?, ?, 'Song', 'Artist', ?, 2.0, 15.0, 0.8,
                        'ready_for_review')
                """,
                (video_db_id, song_db_id, str(song)),
            )

        self.assertIsNone(claim_next(self.db_path, "annotator_b"))
        self.assertEqual(
            music_review_progress(self.db_path, "owner"),
            {"completed": 0, "total": 1, "remaining": 1},
        )
        ok, _ = confirm_music_review(
            self.db_path,
            "owner",
            video.name,
            corrected_offset=2.25,
            final_genre="Pop",
        )
        self.assertTrue(ok)
        self.assertEqual(
            music_review_progress(self.db_path, "owner"),
            {"completed": 1, "total": 1, "remaining": 0},
        )
        self.assertIsNone(claim_next(self.db_path, "annotator_b"))

        with connect(self.db_path) as conn:
            annotation = conn.execute(
                """
                SELECT music_start, music_end, song_verified, status
                FROM annotations
                WHERE annotator_id='owner'
                """
            ).fetchone()
            self.assertEqual(annotation["music_start"], 2.25)
            self.assertEqual(annotation["music_end"], 17.25)
            self.assertEqual(annotation["song_verified"], "Yes")
            self.assertEqual(annotation["status"], "in_progress")
            conn.execute(
                """
                UPDATE annotations
                SET status='completed'
                WHERE annotator_id='owner'
                """
            )
        claimed = claim_next(self.db_path, "annotator_b")
        self.assertEqual(claimed["video_id"], video.name)

    def test_current_review_can_be_realigned_without_downloading_again(self) -> None:
        video = self.paths.douk_download_root / "video-realign.mp4"
        video.write_bytes(b"video")
        song = self.paths.full_songs_dir / "song-realign.mp3"
        song.write_bytes(b"x" * 2048)
        with connect(self.db_path) as conn:
            video_db_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, duration, row_json)
                VALUES (?, ?, 20, '{}')
                """,
                (video.name, str(video)),
            ).lastrowid
            song_db_id = conn.execute(
                """
                INSERT INTO songs(title, artist, full_song_path, row_json)
                VALUES ('Song', 'Artist', ?, '{}')
                """,
                (str(song),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO music_preparations(
                    video_id, song_id, full_song_path, song_offset,
                    aligned_duration, match_score, status
                )
                VALUES (?, ?, ?, 1.0, 20.0, 0.4, 'needs_review')
                """,
                (video_db_id, song_db_id, str(song)),
            )
            conn.execute(
                """
                INSERT INTO song_reviews(video_id, reviewer_id, song_id, status)
                VALUES (?, 'owner', ?, 'in_progress')
                """,
                (video_db_id, song_db_id),
            )

        result = AlignmentResult(8.25, 0.5, 0.88, 4, "ready_for_review")
        with patch(
            "intent_mgsv_pipeline.server.music_review.align_video_to_song",
            return_value=result,
        ) as align:
            ok, message, record = realign_music_review(
                self.db_path,
                "owner",
                video.name,
                preset="精细（推荐）",
            )

        self.assertTrue(ok)
        self.assertIn("offset=8.250s", message)
        self.assertEqual(record["song_offset"], 8.25)
        self.assertEqual(record["corrected_offset"], 8.25)
        self.assertEqual(record["match_score"], 0.88)
        self.assertEqual(align.call_count, 1)
        self.assertEqual(align.call_args.args, (video, song))
        self.assertEqual(align.call_args.kwargs["hop_length"], 512)
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT status, song_offset, video_audio_start, aligned_duration
                FROM music_preparations
                WHERE video_id=?
                """,
                (video_db_id,),
            ).fetchone()
        self.assertEqual(row["status"], "ready_for_review")
        self.assertEqual(row["song_offset"], 8.25)
        self.assertEqual(row["video_audio_start"], 0.5)
        self.assertEqual(row["aligned_duration"], 19.5)

    def test_reprocessed_alignment_rejection_returns_to_music_review(self) -> None:
        video = self.paths.douk_download_root / "video-return.mp4"
        video.write_bytes(b"video")
        song = self.paths.full_songs_dir / "song-return.mp3"
        song.write_bytes(b"x" * 2048)
        with connect(self.db_path) as conn:
            video_db_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, duration, row_json)
                VALUES (?, ?, 20, '{}')
                """,
                (video.name, str(video)),
            ).lastrowid
            song_db_id = conn.execute(
                """
                INSERT INTO songs(title, artist, full_song_path, row_json)
                VALUES ('Song', 'Artist', ?, '{}')
                """,
                (str(song),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO music_preparations(
                    video_id, song_id, full_song_path, song_offset,
                    aligned_duration, match_score, status
                )
                VALUES (?, ?, ?, 2.0, 20.0, 0.35, 'needs_review')
                """,
                (video_db_id, song_db_id, str(song)),
            )

        claimed = claim_next_music_review(self.db_path, "owner")
        self.assertEqual(claimed["video_id"], video.name)
        ok, _ = reject_music_review(
            self.db_path,
            "owner",
            video.name,
            reason="alignment_rejected",
        )
        self.assertTrue(ok)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE music_preparations
                SET status='ready_for_review', song_offset=7.5, match_score=0.8
                WHERE video_id=?
                """,
                (video_db_id,),
            )

        reclaimed = claim_next_music_review(self.db_path, "owner")
        self.assertEqual(reclaimed["video_id"], video.name)
        with connect(self.db_path) as conn:
            review = conn.execute(
                """
                SELECT status
                FROM song_reviews
                WHERE video_id=? AND reviewer_id='owner'
                """,
                (video_db_id,),
            ).fetchone()
        self.assertEqual(review["status"], "in_progress")

    def test_standalone_preparation_skips_existing_dataset_by_default(self) -> None:
        video = self.paths.douk_download_root / "existing.mp4"
        video.write_bytes(b"video")
        import pandas as pd

        pd.DataFrame(
            [
                {
                    "video_id": video.name,
                    "video_path": str(video),
                    "song_title": "Song",
                    "song_artist": "Artist",
                    "status": "recognized",
                }
            ]
        ).to_excel(self.paths.acr_tracking_excel, index=False)
        pd.DataFrame([{"video_id": video.name}]).to_excel(
            self.paths.master_excel,
            index=False,
        )
        counts = prepare_recognized_music(
            paths=self.paths,
            db_path=self.db_path,
            retry_failed=True,
        )
        self.assertEqual(counts["processed"], 0)
        self.assertEqual(counts["skipped_dataset"], 1)
        self.assertEqual(counts["failed"], 0)


if __name__ == "__main__":
    unittest.main()
