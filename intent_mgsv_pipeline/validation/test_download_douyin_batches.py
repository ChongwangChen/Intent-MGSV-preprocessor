from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from scripts.download_douyin_batches import (
    build_douk_commands,
    extract_work_id,
    read_downloaded_ids,
    read_links,
    read_original_batch_files,
    summarize_log,
)


class DownloadDouyinBatchesTests(unittest.TestCase):
    def test_builds_one_douk_session_for_multiple_batches(self) -> None:
        commands = build_douk_commands(
            [Path("batch_01.txt"), Path("batch_02.txt")]
        ).splitlines()
        self.assertEqual(commands[0], "3")
        self.assertEqual(commands[1:3], ["2", "2"])
        self.assertTrue(commands[3].endswith("batch_01.txt"))
        self.assertEqual(commands[4:6], ["2", "2"])
        self.assertTrue(commands[6].endswith("batch_02.txt"))
        self.assertEqual(commands[7], "q")

    def test_extracts_video_and_note_ids(self) -> None:
        self.assertEqual(
            extract_work_id("https://www.douyin.com/video/123456?x=1"), "123456"
        )
        self.assertEqual(
            extract_work_id("https://www.douyin.com/note/987654"), "987654"
        )
        self.assertIsNone(extract_work_id("https://v.douyin.com/example"))

    def test_reads_merged_file_and_removes_duplicate_work_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / "douyin_links_merged.txt").write_text(
                "https://www.douyin.com/video/1\n"
                "https://www.douyin.com/video/1?duplicate=1\n"
                "https://www.douyin.com/note/2\n"
                "not-a-work-link\n",
                encoding="utf-8",
            )
            links, malformed, duplicates = read_links(package)
            self.assertEqual([item.work_id for item in links], ["1", "2"])
            self.assertEqual(malformed, 1)
            self.assertEqual(duplicates, 1)

    def test_reads_douk_downloaded_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            metadata = Path(temp) / "Download.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["作品类型", "作品ID", "作品链接"])
            sheet.append(["视频", "123", "https://www.douyin.com/video/123"])
            sheet.append(["图集", 456, "https://www.douyin.com/note/456"])
            workbook.save(metadata)
            workbook.close()

            self.assertEqual(read_downloaded_ids(metadata), {"123", "456"})

    def test_reads_original_batches_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / "dydownload_02.txt").write_text("two\n", encoding="utf-8")
            (package / "dydownload_01.txt").write_text("one\n", encoding="utf-8")
            self.assertEqual(
                [path.name for path in read_original_batch_files(package)],
                ["dydownload_01.txt", "dydownload_02.txt"],
            )

    def test_summarizes_supplemental_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "batch.log"
            log.write_text(
                "【视频】a 文件下载成功\n"
                "【图集】b_1 文件下载成功\n"
                "【Music】a 文件下载成功\n"
                "【视频】c 存在下载记录或文件已存在，跳过下载\n"
                "HTTP Error 403 Forbidden\n"
                "获取作品数据失败\n"
                "[SSL: UNEXPECTED_EOF_WHILE_READING]\n",
                encoding="utf-8",
            )
            summary = summarize_log(log)
            self.assertEqual(summary["video_download_success"], 1)
            self.assertEqual(summary["gallery_download_success"], 1)
            self.assertEqual(summary["music_download_success"], 1)
            self.assertEqual(summary["existing_skipped"], 1)
            self.assertEqual(summary["http_403"], 1)
            self.assertEqual(summary["detail_fetch_failed"], 1)
            self.assertEqual(summary["tls_eof"], 1)


if __name__ == "__main__":
    unittest.main()
