from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.server.dataset_growth_report import (
    build_growth_report,
)
from intent_mgsv_pipeline.server.db import connect, init_db


class DatasetGrowthReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "server.sqlite3"
        self.scan_root = self.root / "download"
        self.scan_root.mkdir()
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reports_incremental_pipeline_gaps(self) -> None:
        registered = self.scan_root / "registered.mp4"
        unregistered = self.scan_root / "unregistered.mp4"
        song = self.root / "song.mp3"
        registered.write_bytes(b"video")
        unregistered.write_bytes(b"video")
        song.write_bytes(b"song")
        with connect(self.db_path) as conn:
            registered_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, row_json)
                VALUES ('registered.mp4', ?, '{}')
                """,
                (str(registered),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO videos(video_id, video_path, row_json)
                VALUES ('missing.mp4', '/old/missing.mp4', '{}')
                """
            )
            song_id = conn.execute(
                """
                INSERT INTO songs(title, full_song_path, row_json)
                VALUES ('Song', ?, '{}')
                """,
                (str(song),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO music_preparations(
                    video_id, song_id, status, raw_json
                )
                VALUES (?, ?, 'verified', '{}')
                """,
                (registered_id, song_id),
            )
            conn.execute(
                """
                INSERT INTO annotations(
                    video_id, song_id, annotator_id, song_verified,
                    status, row_json
                )
                VALUES (?, ?, 'owner', 'Yes', 'completed', '{}')
                """,
                (registered_id, song_id),
            )

        report = build_growth_report(self.db_path, self.scan_root)

        self.assertEqual(report["disk"]["video_files"], 2)
        self.assertEqual(report["disk"]["unregistered_files"], 1)
        self.assertEqual(report["database"]["active_videos"], 2)
        self.assertEqual(report["database"]["missing_video_files"], 1)
        self.assertEqual(
            report["pipeline"]["videos_without_music_preparation"],
            1,
        )
        self.assertEqual(report["pipeline"]["owner_song_verified"], 1)
        self.assertEqual(report["pipeline"]["peer_eligible"], 1)

    def test_song_path_can_be_recovered_by_filename(self) -> None:
        song_root = self.root / "songs"
        song_root.mkdir()
        song = song_root / "same_song.mp3"
        song.write_bytes(b"song")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO songs(title, full_song_path, row_json)
                VALUES ('Same Song', '/old/server/same_song.mp3', '{}')
                """
            )

        report = build_growth_report(
            self.db_path,
            self.scan_root,
            song_roots=(song_root,),
        )

        self.assertEqual(report["database"]["missing_song_files"], 0)


if __name__ == "__main__":
    unittest.main()
