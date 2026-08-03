from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intent_mgsv_pipeline.runtime_config import RuntimePaths
from intent_mgsv_pipeline.server.collect_diagnostics import build_diagnostic_report
from intent_mgsv_pipeline.server.db import connect, init_db


class ServerDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        output = self.root / "outputs"
        download = self.root / "download"
        download.mkdir()
        self.paths = RuntimePaths(
            project_root=self.root,
            douk_download_root=download,
            douk_data_excel=self.root / "data.xlsx",
            output_dir=output,
            full_music_dir=output / "full_music",
            full_songs_dir=output / "full_songs",
            acr_tracking_excel=output / "tracking.xlsx",
            master_excel=output / "master.xlsx",
            server_db=output / "server.sqlite3",
            acr_config_file=self.root / "acr.json",
        )
        init_db(self.paths.server_db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_report_lists_recent_files_and_pipeline_status(self) -> None:
        pending = self.paths.douk_download_root / "pending.mp4"
        missing = self.paths.douk_download_root / "not-imported.mp4"
        pending.write_bytes(b"video")
        missing.write_bytes(b"video")
        with connect(self.paths.server_db) as conn:
            video_id = conn.execute(
                """
                INSERT INTO videos(video_id, video_path, row_json)
                VALUES ('pending.mp4', ?, '{}')
                """,
                (str(pending),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO music_preparations(video_id, status, error)
                VALUES (?, 'alignment_failed', 'no reliable match')
                """,
                (video_id,),
            )

        report = build_diagnostic_report(
            self.paths.server_db,
            self.paths,
            recent=20,
            log_lines=5,
        )

        self.assertIn("alignment_failed: 1", report)
        self.assertIn("pending.mp4", report)
        self.assertIn("error: no reliable match", report)
        self.assertIn("not-imported.mp4", report)
        self.assertIn("pipeline_stage: not_in_database", report)
        self.assertIn("--- mgsv-owner.log ---", report)


if __name__ == "__main__":
    unittest.main()
