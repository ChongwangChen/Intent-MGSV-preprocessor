from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

from intent_mgsv_pipeline.data_collection.merge_douyin_collection import (
    load_collection_records,
    write_server_upload_package,
)


class MergeDouyinCollectionTests(unittest.TestCase):
    def test_deduplicates_sessions_and_excludes_historical_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, links in (
                ("session_a", [("101", "video"), ("202", "image")]),
                ("session_b", [("101", "video"), ("303", "video")]),
            ):
                session = root / name
                session.mkdir()
                with (session / "collected_links.csv").open(
                    "w",
                    encoding="utf-8-sig",
                    newline="",
                ) as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[
                            "url",
                            "work_id",
                            "content_type",
                            "keyword",
                            "collected_at",
                        ],
                    )
                    writer.writeheader()
                    for work_id, content_type in links:
                        kind = "video" if content_type == "video" else "note"
                        writer.writerow(
                            {
                                "url": f"https://www.douyin.com/{kind}/{work_id}",
                                "work_id": work_id,
                                "content_type": content_type,
                                "keyword": "test",
                                "collected_at": "now",
                            }
                        )

            records, counters = load_collection_records(
                root,
                historical_links={"https://www.douyin.com/note/202"},
            )
            self.assertEqual([record["work_id"] for record in records], ["101", "303"])
            self.assertEqual(counters["duplicates_removed"], 1)
            self.assertEqual(counters["historical_douk_removed"], 1)

    def test_writes_upload_zip_with_merged_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = [
                {
                    "url": f"https://www.douyin.com/video/{work_id}",
                    "work_id": work_id,
                    "content_type": "video",
                    "keyword": "test",
                    "collected_at": "now",
                    "source_session": "session_a",
                }
                for work_id in ("101", "202", "303")
            ]
            summary = write_server_upload_package(
                records,
                root,
                package_name="package",
                batch_size=2,
            )
            self.assertEqual(summary["total_links"], 3)
            self.assertEqual(
                summary["batch_files"],
                ["dydownload_01.txt", "dydownload_02.txt"],
            )
            self.assertEqual(
                (root / "package" / "dydownload_01.txt")
                .read_text(encoding="utf-8")
                .splitlines(),
                [
                    "https://www.douyin.com/video/101",
                    "https://www.douyin.com/video/202",
                ],
            )
            zip_path = Path(summary["zip_path"])
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "douyin_links_merged.txt",
                        "dydownload_01.txt",
                        "dydownload_02.txt",
                        "douyin_links_manifest.csv",
                        "summary.json",
                        "README_server_download_zh.md",
                    },
                )


if __name__ == "__main__":
    unittest.main()
