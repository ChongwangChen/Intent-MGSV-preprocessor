from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.runtime_config import RuntimePaths
from intent_mgsv_pipeline.server.db import connect, init_db
from intent_mgsv_pipeline.server.restore_music_review_queue import (
    restore_music_review_queue,
)


class RestoreMusicReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        output = self.root / "outputs"
        self.paths = RuntimePaths(
            project_root=self.root,
            douk_download_root=self.root / "download",
            douk_data_excel=self.root / "data.xlsx",
            output_dir=output,
            full_music_dir=output / "full_music",
            full_songs_dir=output / "full_songs",
            acr_tracking_excel=output / "tracking.xlsx",
            master_excel=output / "master.xlsx",
            server_db=output / "server.sqlite3",
            acr_config_file=self.root / "acr.json",
        )
        self.paths.douk_download_root.mkdir(parents=True)
        self.paths.full_songs_dir.mkdir(parents=True)
        init_db(self.paths.server_db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _insert_owner_row(
        self,
        video_id: str,
        *,
        song_path: str,
        emotion: str = "Happy",
        video_exists: bool = True,
    ) -> int:
        video_path = self.paths.douk_download_root / video_id
        if video_exists:
            video_path.write_bytes(b"video")
        with connect(self.paths.server_db) as conn:
            video_db_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, duration, row_json)
                VALUES (?, ?, 15, '{}')
                """,
                (video_id, str(self.root / "old" / video_id)),
            ).lastrowid
            existing_song = conn.execute(
                "SELECT id FROM songs WHERE full_song_path=?",
                (song_path,),
            ).fetchone()
            if existing_song:
                song_id = int(existing_song["id"])
            else:
                song_id = conn.execute(
                    """
                    INSERT INTO songs(title, artist, full_song_path, row_json)
                    VALUES ('Song', 'Artist', ?, '{}')
                    """,
                    (song_path,),
                ).lastrowid
            conn.execute(
                """
                INSERT INTO annotations(
                    video_id, song_id, annotator_id, sync_level,
                    music_start, music_end, emotion, style, usage_scene,
                    seg_scores_3, vocal_presence, genre, status, row_json
                )
                VALUES (?, ?, 'owner', 'No', 8.5, 23.5, ?, 'Film',
                        'Vlog', '4', 'Full', 'Pop', 'in_progress', '{}')
                """,
                (video_db_id, song_id, emotion),
            )
        return video_db_id

    def test_dry_run_and_apply_restore_only_safe_candidates(self) -> None:
        song = self.paths.full_songs_dir / "song.mp3"
        song.write_bytes(b"x" * 2048)
        ready_id = self._insert_owner_row(
            "ready.mp4",
            song_path=r"E:\old\full_songs\song.mp3",
        )
        self._insert_owner_row(
            "missing-label.mp4",
            song_path=str(song),
            emotion="",
        )
        self._insert_owner_row(
            "missing-song.mp4",
            song_path=r"E:\old\missing.mp3",
        )
        self._insert_owner_row(
            "missing-video.mp4",
            song_path=str(song),
            video_exists=False,
        )

        dry_run = restore_music_review_queue(
            self.paths.server_db,
            self.paths,
        )
        self.assertEqual(dry_run["mode"], "dry-run")
        self.assertEqual(dry_run["ready"], 1)
        self.assertEqual(dry_run["missing_labels"], 1)
        self.assertEqual(dry_run["missing_song_file"], 1)
        self.assertEqual(dry_run["missing_video_file"], 1)
        with connect(self.paths.server_db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM music_preparations").fetchone()[0],
                0,
            )

        applied = restore_music_review_queue(
            self.paths.server_db,
            self.paths,
            apply=True,
        )
        self.assertEqual(applied["restored"], 1)
        with connect(self.paths.server_db) as conn:
            row = conn.execute(
                """
                SELECT status, song_offset, aligned_duration, full_song_path
                FROM music_preparations
                WHERE video_id=?
                """,
                (ready_id,),
            ).fetchone()
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(row["song_offset"], 8.5)
        self.assertEqual(row["aligned_duration"], 15.0)
        self.assertEqual(Path(row["full_song_path"]), song.resolve())

    def test_delete_missing_videos_removes_only_orphan_song_files(self) -> None:
        shared_song = self.paths.full_songs_dir / "shared.mp3"
        orphan_song = self.paths.full_songs_dir / "orphan.mp3"
        shared_song.write_bytes(b"shared")
        orphan_song.write_bytes(b"orphan")

        self._insert_owner_row(
            "existing.mp4",
            song_path=str(shared_song),
        )
        self._insert_owner_row(
            "missing-shared.mp4",
            song_path=str(shared_song),
            video_exists=False,
        )
        self._insert_owner_row(
            "missing-orphan.mp4",
            song_path=r"E:\old\full_songs\orphan.mp3",
            video_exists=False,
        )

        applied = restore_music_review_queue(
            self.paths.server_db,
            self.paths,
            apply=True,
            delete_missing_videos=True,
        )

        self.assertEqual(applied["deleted_missing_videos"], 2)
        self.assertEqual(applied["deleted_orphan_songs"], 1)
        self.assertEqual(applied["deleted_orphan_song_files"], 1)
        self.assertTrue(shared_song.is_file())
        self.assertFalse(orphan_song.exists())
        with connect(self.paths.server_db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM songs WHERE full_song_path=?",
                    (str(shared_song),),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM songs WHERE full_song_path=?",
                    (r"E:\old\full_songs\orphan.mp3",),
                ).fetchone()[0],
                0,
            )

    def test_ambiguous_video_candidates_are_not_deleted(self) -> None:
        song = self.paths.full_songs_dir / "song.mp3"
        song.write_bytes(b"song")
        self._insert_owner_row(
            "ambiguous.mp4",
            song_path=str(song),
            video_exists=False,
        )
        first = self.paths.douk_download_root / "first" / "ambiguous.mp4"
        second = self.paths.douk_download_root / "second" / "ambiguous.mp4"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first")
        second.write_bytes(b"different-size")

        applied = restore_music_review_queue(
            self.paths.server_db,
            self.paths,
            apply=True,
            delete_missing_videos=True,
        )

        self.assertEqual(applied["ambiguous_video_file"], 1)
        self.assertEqual(applied["missing_video_file"], 0)
        self.assertEqual(applied["deleted_missing_videos"], 0)
        with connect(self.paths.server_db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
