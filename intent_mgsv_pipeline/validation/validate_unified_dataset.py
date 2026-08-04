from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from intent_mgsv_pipeline.datasets.unified_intent_mgsv_dataset import (
    UnifiedIntentMGSVRowDataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_DIR = (
    PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "templates"
)


def validate_manifests(
    manifests: list[Path],
    *,
    max_m_duration: float = 400.0,
    require_labels: bool = True,
    validate_files: bool = False,
) -> dict[str, object]:
    dataset = UnifiedIntentMGSVRowDataset(
        manifests,
        max_m_duration=max_m_duration,
        strict=False,
        require_labels=require_labels,
        validate_files=validate_files,
    )
    modality_counts = Counter(
        record["content_type"] for record in dataset.records
    )
    issue_counts = Counter(issue.code for issue in dataset.issues)
    ready_rows = sum(
        not record["validation_issues"] for record in dataset.records
    )
    return {
        "manifests": [str(path) for path in manifests],
        "rows": len(dataset),
        "ready_rows": ready_rows,
        "invalid_rows": len(dataset) - ready_rows,
        "modalities": dict(sorted(modality_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "issues": [
            {
                "sample_id": issue.sample_id,
                "field": issue.field,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in dataset.issues
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate unified video/image/text music manifests."
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="CSV/XLSX manifest. Repeat to combine modalities.",
    )
    parser.add_argument("--max-m-duration", type=float, default=400.0)
    parser.add_argument("--validate-files", action="store_true")
    parser.add_argument("--allow-missing-labels", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    manifests = [Path(path) for path in args.manifest]
    if not manifests:
        manifests = [
            DEFAULT_TEMPLATE_DIR / "image_music_annotation_example.csv",
            DEFAULT_TEMPLATE_DIR / "text_music_annotation_example.csv",
        ]
    result = validate_manifests(
        manifests,
        max_m_duration=args.max_m_duration,
        require_labels=not args.allow_missing_labels,
        validate_files=args.validate_files,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        print(f"Report: {report}")
    if result["invalid_rows"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
