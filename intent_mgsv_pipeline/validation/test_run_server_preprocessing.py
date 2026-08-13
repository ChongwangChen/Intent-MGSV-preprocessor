from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_server_preprocessing import (
    acquire_lock,
    backup_sqlite,
    build_parser,
)


class RunServerPreprocessingTests(unittest.TestCase):
    def test_music_limit_has_an_explicit_full_pipeline_override(self) -> None:
        parser = build_parser()
        limited = parser.parse_args(["--music-limit", "5"])
        forced = parser.parse_args(
            ["--music-limit", "5", "--continue-after-music-limit"]
        )
        self.assertEqual(limited.music_limit, 5)
        self.assertFalse(limited.continue_after_music_limit)
        self.assertTrue(forced.continue_after_music_limit)

    def test_sqlite_backup_is_readable_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.sqlite3"
            output = root / "backup.sqlite3"
            with sqlite3.connect(source) as conn:
                conn.execute("CREATE TABLE sample(value TEXT)")
                conn.execute("INSERT INTO sample VALUES ('kept')")
            backup_sqlite(source, output)
            conn = sqlite3.connect(output)
            try:
                value = conn.execute("SELECT value FROM sample").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(value, "kept")

    def test_lock_rejects_a_second_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "preprocessing.lock"
            acquire_lock(lock)
            self.assertTrue(lock.is_file())
            with self.assertRaises(RuntimeError):
                acquire_lock(lock)


if __name__ == "__main__":
    unittest.main()
