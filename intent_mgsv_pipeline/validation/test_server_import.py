from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.server.db import connect
from intent_mgsv_pipeline.server.backup_database import backup_database
from intent_mgsv_pipeline.server.import_excel_to_db import import_excel
from intent_mgsv_pipeline.server.db import init_db
from intent_mgsv_pipeline.server.media_paths import (
    browser_safe_audio_path,
    browser_safe_video_path,
)
from intent_mgsv_pipeline.server.repair_video_paths import repair_video_paths


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

    def test_chinese_song_confirmation_imports_as_completed(self) -> None:
        self._write_source(emotion="Happy", music_start=1.0)
        frame = pd.read_excel(self.excel_path, keep_default_na=False)
        frame["song_verified"] = "\u662f"
        frame.to_excel(self.excel_path, index=False)

        import_excel(self.excel_path, self.db_path, "owner")

        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT song_verified, status
                FROM annotations
                WHERE annotator_id='owner'
                """
            ).fetchone()
        self.assertEqual(row["song_verified"], "\u662f")
        self.assertEqual(row["status"], "completed")

    def test_incremental_import_preserves_existing_video_path(self) -> None:
        init_db(self.db_path)
        valid_path = self.root / "video-1.mp4"
        valid_path.write_bytes(b"video")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO videos(video_id, video_path, row_json)
                VALUES ('video-1.mp4', ?, '{}')
                """,
                (str(valid_path),),
            )
        self._write_source(emotion="欢乐", music_start=1.0)
        frame = pd.read_excel(self.excel_path, keep_default_na=False)
        frame["video_path"] = ""
        frame.to_excel(self.excel_path, index=False)

        import_excel(self.excel_path, self.db_path, "owner")

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT video_path FROM videos WHERE video_id='video-1.mp4'"
            ).fetchone()
        self.assertEqual(row["video_path"], str(valid_path))

    def test_repair_video_paths_matches_video_id_filename(self) -> None:
        init_db(self.db_path)
        scan_root = self.root / "downloads"
        video = scan_root / "account" / "video-1.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO videos(video_id, video_path, row_json)
                VALUES ('video-1.mp4', '', '{}')
                """
            )

        counts = repair_video_paths(self.db_path, scan_root)

        self.assertEqual(counts["repaired"], 1)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT video_path FROM videos WHERE video_id='video-1.mp4'"
            ).fetchone()
        self.assertEqual(Path(row["video_path"]), video.resolve())

    def test_browser_safe_video_path_avoids_url_reserved_filename(self) -> None:
        source = self.root / "视频 #卡点.mp4"
        source.write_bytes(b"video")
        alias = browser_safe_video_path(
            source,
            cache_dir=self.root / "aliases",
        )

        self.assertTrue(alias.is_file())
        self.assertNotIn("#", alias.name)
        self.assertTrue(alias.name.isascii())
        self.assertEqual(alias.read_bytes(), source.read_bytes())

    def test_browser_safe_audio_path_preserves_full_song(self) -> None:
        source = self.root / "完整歌曲 #1.mp3"
        source.write_bytes(b"audio")
        alias = browser_safe_audio_path(
            source,
            cache_dir=self.root / "audio-aliases",
        )

        self.assertTrue(alias.is_file())
        self.assertTrue(alias.name.isascii())
        self.assertEqual(alias.suffix, ".mp3")
        self.assertEqual(alias.read_bytes(), source.read_bytes())

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
