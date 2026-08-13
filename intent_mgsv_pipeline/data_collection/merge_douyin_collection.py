from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .collect_douyin_browser_links import (
    DEFAULT_DOUK_METADATA,
    DEFAULT_OUTPUT_DIR,
    normalize_douyin_url,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPLOAD_ROOT = PROJECT_ROOT / "outputs" / "douyin_server_upload"


def _historical_douk_work_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    frame = pd.read_excel(path, keep_default_na=False)
    column = "\u4f5c\u54c1\u94fe\u63a5"
    if column not in frame.columns:
        return set()
    work_ids: set[str] = set()
    for value in frame[column]:
        normalized = normalize_douyin_url(value)
        if normalized:
            work_ids.add(normalized[1])
    return work_ids


def resolve_session_dirs(
    collection_root: Path,
    session_names: list[str] | None = None,
) -> list[Path]:
    if (collection_root / "collected_links.csv").is_file():
        if session_names:
            raise ValueError("--session cannot be used when collection root is a session")
        return [collection_root]
    if session_names:
        session_dirs = [collection_root / name for name in session_names]
        missing = [str(path) for path in session_dirs if not path.is_dir()]
        if missing:
            raise FileNotFoundError("Collection sessions not found: " + ", ".join(missing))
        return session_dirs
    return sorted(path for path in collection_root.iterdir() if path.is_dir())


def load_collection_records(
    collection_root: Path,
    *,
    historical_work_ids: set[str] | None = None,
    session_names: list[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    historical_work_ids = historical_work_ids or set()
    records: list[dict[str, str]] = []
    seen_work_ids: set[str] = set()
    duplicate_count = 0
    historical_count = 0

    session_dirs = resolve_session_dirs(collection_root, session_names)
    for session_dir in session_dirs:
        manifest = session_dir / "collected_links.csv"
        if not manifest.is_file():
            continue
        with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                normalized = normalize_douyin_url(row.get("url", ""))
                if not normalized:
                    continue
                url, work_id, content_type = normalized
                if work_id in historical_work_ids:
                    historical_count += 1
                    continue
                if work_id in seen_work_ids:
                    duplicate_count += 1
                    continue
                seen_work_ids.add(work_id)
                records.append(
                    {
                        "url": url,
                        "work_id": work_id,
                        "content_type": content_type,
                        "keyword": str(row.get("keyword", "")).strip(),
                        "collected_at": str(row.get("collected_at", "")).strip(),
                        "source_session": session_dir.name,
                    }
                )
    return records, {
        "duplicates_removed": duplicate_count,
        "historical_douk_removed": historical_count,
        "sessions_merged": len(session_dirs),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_server_upload_package(
    records: list[dict[str, str]],
    output_root: Path,
    *,
    counters: dict[str, int] | None = None,
    package_name: str | None = None,
    batch_size: int = 20,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    package_name = package_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_root / package_name
    package_dir.mkdir(parents=True, exist_ok=True)

    links_path = package_dir / "douyin_links_merged.txt"
    links_path.write_text(
        "\n".join(record["url"] for record in records) + ("\n" if records else ""),
        encoding="utf-8",
    )

    batch_paths: list[Path] = []
    for start in range(0, len(records), batch_size):
        batch_path = package_dir / f"dydownload_{len(batch_paths) + 1:02d}.txt"
        batch = records[start : start + batch_size]
        batch_path.write_text(
            "\n".join(record["url"] for record in batch) + "\n",
            encoding="utf-8",
        )
        batch_paths.append(batch_path)

    manifest_path = package_dir / "douyin_links_manifest.csv"
    fieldnames = [
        "url",
        "work_id",
        "content_type",
        "keyword",
        "collected_at",
        "source_session",
    ]
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    type_counts = Counter(record["content_type"] for record in records)
    keyword_counts = Counter(record["keyword"] for record in records)
    summary: dict[str, Any] = {
        "package_dir": str(package_dir),
        "total_links": len(records),
        "videos": type_counts["video"],
        "images": type_counts["image"],
        "batch_size": batch_size,
        "batch_files": [path.name for path in batch_paths],
        "keyword_counts": dict(sorted(keyword_counts.items())),
        **(counters or {}),
        "links_sha256": _sha256(links_path),
        "manifest_sha256": _sha256(manifest_path),
    }
    summary_path = package_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readme_path = package_dir / "README_server_download_zh.md"
    readme_path.write_text(
        f"""# 抖音新数据下载包

## 文件

- `douyin_links_merged.txt`：全部去重后的新作品链接。
- `dydownload_01.txt` 等：每批 {batch_size} 条，适合本地分批下载。
- `douyin_links_manifest.csv`：链接、作品 ID、视频/图文类型、关键词和来源会话。
- `summary.json`：数量分布与 SHA-256 校验值。

## 当前推荐流程

服务器公网出口访问抖音详情接口若持续返回 HTTP 403，请停止服务器重试，
改在已登录抖音的本地电脑分批下载，再将下载结果传到服务器。

本地进入 `DouK-Source`，运行 `python main.py`，依次选择：

```text
3  终端交互模式
2  批量下载链接作品（抖音）
2  从文本文档读取待采集链接
```

第一次输入：

```text
E:\\MGSV_preprocessor\\outputs\\douyin_server_upload\\{package_name}\\dydownload_01.txt
```

每批完成后，把本批新生成的作品文件夹上传到服务器：

```text
/data/users/ccw/intent_mgsv/repo/MGSV_preprocessor/DouK-Source/Volume/Download/
```

确认服务器文件完整后再继续下一批。保留 `douyin_links_manifest.csv`，
后续可按作品 ID 核对全部链接是否下载成功。
""",
        encoding="utf-8",
    )

    zip_path = output_root / f"{package_name}.zip"
    output_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (
            links_path,
            *batch_paths,
            manifest_path,
            summary_path,
            readme_path,
        ):
            archive.write(path, arcname=path.name)
    summary["zip_path"] = str(zip_path)
    summary["zip_sha256"] = _sha256(zip_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge browser-collected Douyin links into a server upload package."
    )
    parser.add_argument("--collection-root", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        help="Session directory name to include; repeat for multiple sessions.",
    )
    parser.add_argument("--douk-metadata", default=str(DEFAULT_DOUK_METADATA))
    parser.add_argument("--output-root", default=str(DEFAULT_UPLOAD_ROOT))
    parser.add_argument("--package-name", default="")
    parser.add_argument("--batch-size", type=int, default=20)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    collection_root = Path(args.collection_root)
    if not collection_root.is_dir():
        raise SystemExit(f"Collection root not found: {collection_root}")
    historical_work_ids = _historical_douk_work_ids(Path(args.douk_metadata))
    records, counters = load_collection_records(
        collection_root,
        historical_work_ids=historical_work_ids,
        session_names=args.session or None,
    )
    summary = write_server_upload_package(
        records,
        Path(args.output_root),
        counters=counters,
        package_name=args.package_name or None,
        batch_size=args.batch_size,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
