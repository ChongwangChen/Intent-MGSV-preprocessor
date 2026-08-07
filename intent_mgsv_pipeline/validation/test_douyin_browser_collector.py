from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.data_collection.collect_douyin_browser_links import (
    CollectedLink,
    _canonical_from_modal,
    _duration_seconds_from_card_text,
    _modal_work_id,
    _non_target_reason,
    _same_search_page,
    load_existing_links,
    load_keyword_tasks,
    normalize_douyin_url,
    write_collection_outputs,
)


class DouyinBrowserCollectorTests(unittest.TestCase):
    def test_normalizes_video_and_note_links(self) -> None:
        self.assertEqual(
            normalize_douyin_url(
                "https://www.douyin.com/video/123?previous_page=search"
            ),
            ("https://www.douyin.com/video/123", "123", "video"),
        )
        self.assertEqual(
            normalize_douyin_url("/note/456"),
            ("https://www.douyin.com/note/456", "456", "image"),
        )
        self.assertIsNone(normalize_douyin_url("https://www.douyin.com/user/x"))

    def test_extracts_canonical_url_from_shared_copy(self) -> None:
        shared = (
            "6.52 复制打开抖音，看看【示例作品】 "
            "https://www.douyin.com/video/7540244096460688682?previous_page=search"
        )
        self.assertEqual(
            normalize_douyin_url(shared),
            (
                "https://www.douyin.com/video/7540244096460688682",
                "7540244096460688682",
                "video",
            ),
        )

    def test_uses_modal_id_as_copy_fallback(self) -> None:
        modal_url = (
            "https://www.douyin.com/search/test"
            "?modal_id=7540244096460688682&type=general"
        )
        self.assertEqual(_modal_work_id(modal_url), "7540244096460688682")
        self.assertEqual(
            _canonical_from_modal(modal_url, "image"),
            (
                "https://www.douyin.com/note/7540244096460688682",
                "7540244096460688682",
                "image",
            ),
        )

    def test_search_page_guard_rejects_blank_modal_and_other_keyword(self) -> None:
        expected = "https://www.douyin.com/search/test?type=video"
        self.assertTrue(
            _same_search_page(
                "https://www.douyin.com/search/test?type=video",
                expected,
            )
        )
        self.assertFalse(_same_search_page("about:blank", expected))
        self.assertFalse(
            _same_search_page(
                "https://www.douyin.com/search/test"
                "?modal_id=123456789012345&type=video",
                expected,
            )
        )
        self.assertFalse(
            _same_search_page(
                "https://www.douyin.com/search/other?type=video",
                expected,
            )
        )

    def test_parses_card_duration_and_filters_long_video(self) -> None:
        self.assertEqual(_duration_seconds_from_card_text("06:02\n1.1万"), 362)
        self.assertEqual(_duration_seconds_from_card_text("01:02:03"), 3723)
        self.assertIsNone(_duration_seconds_from_card_text("图文\n2087"))
        self.assertIn(
            "超过限制",
            _non_target_reason("06:02\n旅行记录", 300),
        )
        self.assertEqual(_non_target_reason("04:59\n旅行记录", 300), "")

    def test_content_filter_is_conservative(self) -> None:
        self.assertEqual(
            _non_target_reason("00:30 #钢琴演奏 #独奏", 300),
            "明确标注为乐器演奏",
        )
        self.assertEqual(
            _non_target_reason("00:20 #清唱 #翻唱", 300),
            "明确标注为清唱/无伴奏",
        )
        self.assertEqual(
            _non_target_reason("00:15 #舞蹈 #卡点 #纯音乐", 300),
            "",
        )

    def test_loads_keyword_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keywords.csv"
            pd.DataFrame(
                [
                    {"keyword": "travel", "content_type": "video", "quota": 10},
                    {"keyword": "photos", "content_type": "image", "quota": 5},
                ]
            ).to_csv(path, index=False, encoding="utf-8-sig")
            tasks = load_keyword_tasks(path)
            self.assertEqual([task.quota for task in tasks], [10, 5])
            self.assertEqual(tasks[1].content_type, "image")

    def test_writes_batches_of_twenty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = [
                CollectedLink(
                    url=f"https://www.douyin.com/video/{index}",
                    work_id=str(index),
                    content_type="video",
                    keyword="test",
                    collected_at="2026-08-06T00:00:00",
                )
                for index in range(45)
            ]
            summary = write_collection_outputs(
                records,
                Path(tmp),
                batch_size=20,
                session_name="test_session",
            )
            self.assertEqual(summary["collected"], 45)
            self.assertEqual(len(summary["batch_files"]), 3)
            sizes = [
                len(Path(path).read_text(encoding="utf-8").splitlines())
                for path in summary["batch_files"]
            ]
            self.assertEqual(sizes, [20, 20, 5])

    def test_existing_links_include_batches_and_douk_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "old.txt").write_text(
                "https://www.douyin.com/video/111\n",
                encoding="utf-8",
            )
            metadata = root / "Download.xlsx"
            pd.DataFrame(
                [{"\u4f5c\u54c1\u94fe\u63a5": "https://www.douyin.com/note/222"}]
            ).to_excel(metadata, index=False)
            links = load_existing_links(output_dir, metadata)
            self.assertEqual(
                links,
                {
                    "https://www.douyin.com/video/111",
                    "https://www.douyin.com/note/222",
                },
            )


if __name__ == "__main__":
    unittest.main()
