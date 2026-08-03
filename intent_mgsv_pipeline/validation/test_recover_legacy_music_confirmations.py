from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.recover_legacy_music_confirmations import (
    recover_legacy_music_confirmations,
)
from intent_mgsv_pipeline.server.verification import is_song_verified


class RecoverLegacyMusicConfirmationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "current.sqlite3"
        self.evidence_db = self.root / "evidence.sqlite3"
        init_db(self.db_path)
        init_db(self.evidence_db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _insert_record(
        self,
        db_path: Path,
        video_id: str,
        *,
        verified: str,
        song_name: str,
        with_preparation: bool,
    ) -> None:
        with connect(db_path) as conn:
            video_db_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, duration, row_json)
                VALUES (?, ?, 15, '{}')
                """,
                (video_id, str(self.root / video_id)),
            ).lastrowid
            song_db_id = conn.execute(
                """
                INSERT INTO songs(
                    title, artist, full_song_path, row_json
                )
                VALUES ('Song', 'Artist', ?, '{}')
                """,
                (str(self.root / song_name),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO annotations(
                    video_id, song_id, annotator_id, sync_level,
                    music_start, music_end, emotion, style, usage_scene,
                    seg_scores_3, vocal_presence, genre, song_verified,
                    status, row_json
                )
                VALUES (?, ?, 'owner', 'No', 8.5, 23.5, 'Happy',
                        'Film', 'Vlog', '4', 'Full', 'Pop', ?,
                        'in_progress', '{}')
                """,
                (video_db_id, song_db_id, verified),
            )
            if with_preparation:
                conn.execute(
                    """
                    INSERT INTO music_preparations(
                        video_id, song_id, recognized_title,
                        recognized_artist, download_source,
                        full_song_path, song_offset, aligned_duration,
                        status, raw_json
                    )
                    VALUES (?, ?, 'Song', 'Artist',
                            'restored_annotation', ?, 8.5, 15,
                            'needs_review',
                            '{"restored_from_annotation": true}')
                    """,
                    (
                        video_db_id,
                        song_db_id,
                        str(self.root / song_name),
                    ),
                )

    def test_chinese_confirmation_is_recognized(self) -> None:
        self.assertTrue(is_song_verified("\u662f"))
        self.assertTrue(is_song_verified("\u5df2\u786e\u8ba4"))
        self.assertFalse(is_song_verified(""))

    def test_recover_only_matching_previously_verified_rows(self) -> None:
        self._insert_record(
            self.evidence_db,
            "old-confirmed.mp4",
            verified="\u662f",
            song_name="old.mp3",
            with_preparation=False,
        )
        self._insert_record(
            self.db_path,
            "old-confirmed.mp4",
            verified="\u662f",
            song_name="old.mp3",
            with_preparation=True,
        )
        self._insert_record(
            self.evidence_db,
            "recent.mp4",
            verified="",
            song_name="recent.mp3",
            with_preparation=False,
        )
        self._insert_record(
            self.db_path,
            "recent.mp4",
            verified="",
            song_name="recent.mp3",
            with_preparation=True,
        )
        self._insert_record(
            self.evidence_db,
            "mismatch.mp4",
            verified="\u662f",
            song_name="previous.mp3",
            with_preparation=False,
        )
        self._insert_record(
            self.db_path,
            "mismatch.mp4",
            verified="\u662f",
            song_name="current.mp3",
            with_preparation=True,
        )

        dry_run = recover_legacy_music_confirmations(
            self.db_path,
            self.evidence_db,
        )
        self.assertEqual(dry_run["ready"], 1)
        self.assertEqual(dry_run["not_previously_verified"], 1)
        self.assertEqual(dry_run["song_mismatch"], 1)
        self.assertEqual(dry_run["confirmed"], 0)

        applied = recover_legacy_music_confirmations(
            self.db_path,
            self.evidence_db,
            apply=True,
        )
        self.assertEqual(applied["confirmed"], 1)
        with connect(self.db_path) as conn:
            confirmed = conn.execute(
                """
                SELECT p.status AS preparation_status,
                       a.song_verified, a.status AS annotation_status
                FROM videos v
                JOIN music_preparations p ON p.video_id=v.id
                JOIN annotations a
                  ON a.video_id=v.id AND a.annotator_id='owner'
                WHERE v.video_id='old-confirmed.mp4'
                """
            ).fetchone()
            remaining = conn.execute(
                """
                SELECT COUNT(*)
                FROM music_preparations
                WHERE status='needs_review'
                """
            ).fetchone()[0]
            reviews = conn.execute(
                """
                SELECT COUNT(*)
                FROM song_reviews
                WHERE reviewer_id='owner'
                  AND status='completed'
                """
            ).fetchone()[0]
        self.assertEqual(confirmed["preparation_status"], "verified")
        self.assertEqual(confirmed["song_verified"], "Yes")
        self.assertEqual(confirmed["annotation_status"], "completed")
        self.assertEqual(remaining, 2)
        self.assertEqual(reviews, 1)


if __name__ == "__main__":
    unittest.main()
