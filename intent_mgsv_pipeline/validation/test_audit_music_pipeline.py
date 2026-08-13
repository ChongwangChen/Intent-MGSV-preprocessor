from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from intent_mgsv_pipeline.server.db import connect, init_db
from scripts.audit_music_pipeline import audit_music_pipeline


class AuditMusicPipelineTests(unittest.TestCase):
    def test_reports_status_and_next_action_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "audit.sqlite3"
            init_db(db)
            with closing(connect(db)) as conn:
                video_id = conn.execute(
                    "INSERT INTO videos(video_id) VALUES ('video.mp4')"
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO music_preparations(video_id, status, error)
                    VALUES (?, 'needs_realign', 'offset rejected')
                    """,
                    (video_id,),
                )
                conn.commit()
            report = audit_music_pipeline(db)
            self.assertEqual(report["status_counts"], {"needs_realign": 1})
            self.assertIn(
                "retry preparation",
                report["pending_preview"][0]["next_action"],
            )
            self.assertIn(
                "--include-existing-dataset",
                report["pending_preview"][0]["next_action"],
            )


if __name__ == "__main__":
    unittest.main()
