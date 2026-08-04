from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.schema.unified_sample_schema import (
    UNIFIED_COLUMNS,
    canonicalize_record,
    validate_record,
)
from intent_mgsv_pipeline.server.repair_video_paths import VIDEO_SUFFIXES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIDEO_MANIFEST = (
    PROJECT_ROOT
    / "outputs"
    / "intent_mgsv_dataset"
    / "intent_mgsv_clean_ready.csv"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "outputs"
    / "intent_mgsv_dataset"
    / "unified"
    / "unified_video_manifest.csv"
)


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() in {".xlsx", ".xls"}:
        return pd.read_excel(path, keep_default_na=False)
    return pd.read_csv(path, keep_default_na=False)


def _video_index(video_root: Path | None) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    if video_root is None or not video_root.is_dir():
        return index
    for path in video_root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in VIDEO_SUFFIXES:
            index.setdefault(path.name.casefold(), []).append(path.resolve())
    return index


def _resolve_video_by_name(
    video_id: str,
    index: dict[str, list[Path]],
) -> Path | None:
    name = Path(str(video_id or "").replace("\\", "/")).name.casefold()
    matches = index.get(name, [])
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sizes = {path.stat().st_size for path in matches}
        if len(sizes) == 1:
            return sorted(matches, key=lambda path: (len(path.parts), str(path)))[0]
    return None


def build_unified_manifest(
    inputs: list[Path],
    output: Path,
    *,
    require_labels: bool = True,
    video_root: Path | None = None,
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    issue_counts: Counter[str] = Counter()
    invalid_rows = 0
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    video_index = _video_index(video_root)
    for input_path in inputs:
        frame = _read(input_path)
        for _, row in frame.iterrows():
            record = canonicalize_record(row.to_dict())
            if (
                record["content_type"] == "video"
                and not record["content_path"]
            ):
                resolved_video = _resolve_video_by_name(
                    str(record["video_id"] or record["content_id"]),
                    video_index,
                )
                if resolved_video is not None:
                    record["content_path"] = str(resolved_video)
            record["source"] = record["source"] or input_path.stem
            issues = validate_record(record, require_labels=require_labels)
            if issues:
                invalid_rows += 1
                issue_counts.update(issue.code for issue in issues)
            sample_id = str(record["sample_id"])
            if sample_id in seen_ids:
                duplicate_ids.append(sample_id)
            seen_ids.add(sample_id)
            records.append(
                {column: record.get(column, "") for column in UNIFIED_COLUMNS}
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records, columns=UNIFIED_COLUMNS).to_csv(
        output,
        index=False,
        encoding="utf-8-sig",
    )
    return {
        "inputs": [str(path) for path in inputs],
        "output": str(output),
        "rows": len(records),
        "invalid_rows": invalid_rows,
        "duplicate_sample_ids": sorted(set(duplicate_ids)),
        "issue_counts": dict(sorted(issue_counts.items())),
        "video_root": str(video_root) if video_root else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert video/image/text tables into one unified manifest."
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="CSV/XLSX input. Repeat to merge multiple modalities.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--allow-missing-labels", action="store_true")
    parser.add_argument(
        "--video-root",
        default=str(PATHS.douk_download_root),
        help="Resolve legacy video_id values against this media root.",
    )
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    inputs = [Path(path) for path in args.input] or [DEFAULT_VIDEO_MANIFEST]
    result = build_unified_manifest(
        inputs,
        Path(args.output),
        require_labels=not args.allow_missing_labels,
        video_root=Path(args.video_root) if args.video_root else None,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        print(f"Report: {report}")
    if result["invalid_rows"] or result["duplicate_sample_ids"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
