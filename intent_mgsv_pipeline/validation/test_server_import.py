from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.server.db import connect
from intent_mgsv_pipeline.server.backup_database import backup_database
from intent_mgsv_pipeline.server.import_excel_to_db import import_excel


class ServerImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "server.sqlite3"
        self.excel_path = self.root / "source.xlsx"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_source(self, *, emotion: str, music_start: float) -> None:
        pd.DataFrame(
            [
                {
                    "video_id": "video-1.mp4",
                    "video_path": "/data/video-1.mp4",
                    "song_title": "Song",
                    "song_artist": "Artist",
                    "full_song_path": "/data/song.mp3",
                    "music_start": music_start,
                    "music_end": music_start + 10,
                    "emotion": emotion,
                    "style": "卡点",
                    "usage_scene": "舞蹈",
                    "vocal_presence": "Full",
                    "genre": "Pop",
                    "song_verified": "Yes",
                }
            ]
        ).to_excel(self.excel_path, index=False)

    def test_default_reimport_does_not_overwrite_database_annotation(self) -> None:
        self._write_source(emotion="欢乐", music_start=1.0)
        import_excel(self.excel_path, self.db_path, "owner")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE annotations
                SET emotion='人工修正', music_start=8.5
                WHERE annotator_id='owner'
                """
            )

        self._write_source(emotion="旧Excel", music_start=2.0)
        import_excel(self.excel_path, self.db_path, "owner")
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT emotion, music_start
                FROM annotations
                WHERE annotator_id='owner'
                """
            ).fetchone()
        self.assertEqual(row["emotion"], "人工修正")
        self.assertEqual(row["music_start"], 8.5)

    def test_update_existing_requires_explicit_flag(self) -> None:
        self._write_source(emotion="初始", music_start=1.0)
        import_excel(self.excel_path, self.db_path, "owner")
        self._write_source(emotion="显式更新", music_start=3.0)
        import_excel(
            self.excel_path,
            self.db_path,
            "owner",
            update_existing=True,
        )
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT emotion, music_start
                FROM annotations
                WHERE annotator_id='owner'
                """
            ).fetchone()
        self.assertEqual(row["emotion"], "显式更新")
        self.assertEqual(row["music_start"], 3.0)

    def test_sqlite_backup_is_readable(self) -> None:
        self._write_source(emotion="欢乐", music_start=1.0)
        import_excel(self.excel_path, self.db_path, "owner")
        backup = backup_database(self.db_path, self.root / "backups")
        self.assertTrue(backup.exists())
        with connect(backup) as conn:
            count = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
