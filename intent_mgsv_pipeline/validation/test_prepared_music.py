from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from intent_mgsv_pipeline.preprocessing.prepared_music import (
    load_prepared_music_index,
)


class PreparedMusicTests(unittest.TestCase):
    def test_prefers_completed_owner_offset_and_excludes_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite3"
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE videos (
                        id INTEGER PRIMARY KEY, video_id TEXT, deleted_at TEXT
                    );
                    CREATE TABLE songs (
                        id INTEGER PRIMARY KEY, title TEXT, artist TEXT,
                        full_song_path TEXT
                    );
                    CREATE TABLE music_preparations (
                        video_id INTEGER, song_id INTEGER, status TEXT,
                        song_offset REAL, video_audio_start REAL,
                        aligned_duration REAL, match_score REAL,
                        download_source TEXT, qq_song_mid TEXT,
                        full_song_path TEXT, recognized_title TEXT,
                        recognized_artist TEXT
                    );
                    CREATE TABLE song_reviews (
                        video_id INTEGER, reviewer_id TEXT,
                        corrected_offset REAL, final_genre TEXT, status TEXT
                    );
                    INSERT INTO videos VALUES (1, 'verified.mp4', NULL);
                    INSERT INTO videos VALUES (2, 'rejected.mp4', NULL);
                    INSERT INTO songs VALUES (1, 'Song', 'Artist', 'song.mp3');
                    INSERT INTO music_preparations VALUES
                        (1, 1, 'verified', 10.0, 0.0, 20.0, 0.9,
                         'reused', 'mid-1', '', 'Song', 'Artist');
                    INSERT INTO music_preparations VALUES
                        (2, 1, 'needs_manual', 3.0, 0.0, 20.0, 0.2,
                         'reused', 'mid-2', '', 'Wrong', 'Artist');
                    INSERT INTO song_reviews VALUES
                        (1, 'owner', 12.5, 'Pop', 'completed');
                    """
                )
                conn.commit()

            index = load_prepared_music_index(db)
            self.assertEqual(set(index), {"verified.mp4"})
            self.assertEqual(index["verified.mp4"]["effective_offset"], 12.5)
            self.assertEqual(
                index["verified.mp4"]["alignment_source"], "human_verified"
            )
            self.assertEqual(index["verified.mp4"]["full_song_path"], "song.mp3")


if __name__ == "__main__":
    unittest.main()
