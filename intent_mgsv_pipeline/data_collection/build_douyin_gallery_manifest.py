from __future__ import annotations

import argparse
import hashlib
import json
import re
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import pandas as pd

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.schema.unified_sample_schema import UNIFIED_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_METADATA = (
    PROJECT_ROOT / "DouK-Source" / "Volume" / "Data" / "Download.xlsx"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "outputs"
    / "intent_mgsv_dataset"
    / "pilot"
    / "douyin_gallery_pilot.csv"
)
GALLERY_MARKER = "-\u56fe\u96c6-"
IMAGE_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}

COL_TYPE = "\u4f5c\u54c1\u7c7b\u578b"
COL_POST_ID = "\u4f5c\u54c1ID"
COL_DESCRIPTION = "\u4f5c\u54c1\u63cf\u8ff0"
COL_POST_URL = "\u4f5c\u54c1\u94fe\u63a5"
COL_PUBLISHED_AT = "\u53d1\u5e03\u65f6\u95f4"
COL_NICKNAME = "\u8d26\u53f7\u6635\u79f0"
COL_MUSIC_ARTIST = "\u97f3\u4e50\u4f5c\u8005"
COL_MUSIC_TITLE = "\u97f3\u4e50\u6807\u9898"
GALLERY_TYPE = "\u56fe\u96c6"


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"nan", "none", "null"} else text


def _post_id(value: Any) -> str:
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        return str(int(value)) if float(value).is_integer() else str(value)
    text = _text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return text
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _post_id_from_url(value: Any) -> str:
    match = re.search(r"/(?:note|video)/(\d+)", _text(value))
    return match.group(1) if match else ""


def _natural_key(path: Path) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    )


def _gallery_directories(download_root: Path) -> list[Path]:
    if not download_root.is_dir():
        return []
    return sorted(
        (
            path
            for path in download_root.iterdir()
            if path.is_dir() and GALLERY_MARKER in path.name
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _load_gallery_metadata(metadata_path: Path) -> list[dict[str, Any]]:
    if not metadata_path.is_file():
        return []
    frame = pd.read_excel(metadata_path, keep_default_na=False)
    if COL_TYPE not in frame.columns:
        return []
    rows = frame[frame[COL_TYPE].astype(str) == GALLERY_TYPE]
    return rows.to_dict(orient="records")


def _metadata_prefix(row: dict[str, Any]) -> str:
    published_at = pd.to_datetime(
        row.get(COL_PUBLISHED_AT),
        errors="coerce",
    )
    if pd.isna(published_at):
        return ""
    nickname = _text(row.get(COL_NICKNAME))
    return (
        f"{published_at.strftime('%Y-%m-%d %H.%M.%S')}"
        f"{GALLERY_MARKER}{nickname}-"
    )


def _match_metadata(
    folder: Path,
    metadata_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    matches = [
        row
        for row in metadata_rows
        if (prefix := _metadata_prefix(row))
        and folder.name.startswith(prefix)
    ]
    return matches[0] if len(matches) == 1 else None


def _fallback_id(folder: Path) -> str:
    digest = hashlib.sha1(
        folder.name.encode("utf-8")
    ).hexdigest()[:12]
    return f"gallery_{digest}"


def build_douyin_gallery_manifest(
    download_root: Path,
    metadata_path: Path,
    output: Path,
    *,
    limit: int = 0,
) -> dict[str, Any]:
    metadata_rows = _load_gallery_metadata(metadata_path)
    folders = _gallery_directories(download_root)
    if limit > 0:
        folders = folders[:limit]

    records: list[dict[str, Any]] = []
    total_images = 0
    metadata_matches = 0
    with_source_audio = 0
    missing_images: list[str] = []
    missing_source_audio: list[str] = []
    for folder in folders:
        images = sorted(
            (
                path.resolve()
                for path in folder.iterdir()
                if path.is_file()
                and path.suffix.casefold() in IMAGE_SUFFIXES
            ),
            key=_natural_key,
        )
        audio_files = sorted(
            (
                path.resolve()
                for path in folder.iterdir()
                if path.is_file()
                and path.suffix.casefold() in AUDIO_SUFFIXES
            ),
            key=_natural_key,
        )
        metadata = _match_metadata(folder, metadata_rows)
        post_id = (
            _post_id_from_url(
                metadata.get(COL_POST_URL) if metadata else ""
            )
            or _post_id(
                metadata.get(COL_POST_ID) if metadata else ""
            )
        )
        sample_id = (
            f"douyin_note_{post_id}" if post_id else _fallback_id(folder)
        )
        if not images:
            missing_images.append(sample_id)
            continue
        total_images += len(images)
        if metadata:
            metadata_matches += 1
        if audio_files:
            with_source_audio += 1
        else:
            missing_source_audio.append(sample_id)

        record = {column: "" for column in UNIFIED_COLUMNS}
        record.update(
            {
                "sample_id": sample_id,
                "content_type": "image",
                "content_id": sample_id,
                "content_path": str(images[0]),
                "content_paths": json.dumps(
                    [str(path) for path in images],
                    ensure_ascii=False,
                ),
                "content_text": _text(
                    metadata.get(COL_DESCRIPTION) if metadata else folder.name
                ),
                "content_duration": 0,
                "source_audio_path": (
                    str(audio_files[0]) if audio_files else ""
                ),
                "song_title": _text(
                    metadata.get(COL_MUSIC_TITLE) if metadata else ""
                ),
                "song_artist": _text(
                    metadata.get(COL_MUSIC_ARTIST) if metadata else ""
                ),
                "sync_level": 0,
                "source": "douyin_gallery",
                "source_url": _text(
                    metadata.get(COL_POST_URL) if metadata else ""
                ),
                "annotator_id": "owner",
                "annotation_status": "pending",
                "group_id": sample_id,
            }
        )
        records.append(record)

    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records, columns=UNIFIED_COLUMNS).to_csv(
        output,
        index=False,
        encoding="utf-8-sig",
    )
    return {
        "download_root": str(download_root),
        "metadata": str(metadata_path),
        "output": str(output),
        "collections": len(records),
        "images": total_images,
        "metadata_matches": metadata_matches,
        "with_source_audio": with_source_audio,
        "without_source_audio": len(missing_source_audio),
        "missing_source_audio_ids": missing_source_audio,
        "missing_image_ids": missing_images,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build one-row-per-gallery annotation workspace from DouK files."
        )
    )
    parser.add_argument(
        "--download-root",
        default=str(PATHS.douk_download_root),
    )
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Use the most recently modified N galleries; 0 means all.",
    )
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    result = build_douyin_gallery_manifest(
        Path(args.download_root),
        Path(args.metadata),
        Path(args.output),
        limit=args.limit,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        print(f"Report: {report}")


if __name__ == "__main__":
    main()
