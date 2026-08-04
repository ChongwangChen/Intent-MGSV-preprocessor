from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.data_collection.build_douyin_gallery_manifest import (
    build_douyin_gallery_manifest,
)


class DouyinGalleryManifestTests(unittest.TestCase):
    def test_builds_one_ordered_sample_per_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            download_root = root / "Download"
            folder = download_root / (
                "2025-01-02 03.04.05-\u56fe\u96c6-Tester-Description"
            )
            folder.mkdir(parents=True)
            (folder / f"{folder.name}_2.jpeg").write_bytes(b"2")
            (folder / f"{folder.name}_1.jpeg").write_bytes(b"1")
            (folder / f"{folder.name}.mp3").write_bytes(b"audio")
            metadata = root / "Download.xlsx"
            metadata_row = {
                "\u4f5c\u54c1\u7c7b\u578b": "\u56fe\u96c6",
                "\u4f5c\u54c1ID": 7484280271425080635,
                "\u4f5c\u54c1\u63cf\u8ff0": "Description",
                "\u4f5c\u54c1\u94fe\u63a5": (
                    "https://www.douyin.com/note/7484280271425080635"
                ),
                "\u53d1\u5e03\u65f6\u95f4": "2025-01-02 03:04:05",
                "\u8d26\u53f7\u6635\u79f0": "Tester",
                "\u97f3\u4e50\u4f5c\u8005": "Artist",
                "\u97f3\u4e50\u6807\u9898": "Song",
            }
            pd.DataFrame(
                [metadata_row, metadata_row]
            ).to_excel(metadata, index=False)
            output = root / "gallery.csv"

            result = build_douyin_gallery_manifest(
                download_root,
                metadata,
                output,
            )

            self.assertEqual(result["collections"], 1)
            self.assertEqual(result["images"], 2)
            self.assertEqual(result["metadata_matches"], 1)
            self.assertEqual(result["with_source_audio"], 1)
            row = pd.read_csv(output, keep_default_na=False).iloc[0]
            self.assertEqual(
                row["sample_id"],
                "douyin_note_7484280271425080635",
            )
            self.assertEqual(row["song_title"], "Song")
            self.assertEqual(
                row["source_url"],
                "https://www.douyin.com/note/7484280271425080635",
            )
            image_paths = json.loads(row["content_paths"])
            self.assertTrue(image_paths[0].endswith("_1.jpeg"))
            self.assertTrue(image_paths[1].endswith("_2.jpeg"))
            self.assertTrue(row["source_audio_path"].endswith(".mp3"))


if __name__ == "__main__":
    unittest.main()
