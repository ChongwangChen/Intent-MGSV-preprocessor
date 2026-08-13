from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from intent_mgsv_pipeline.music_recognition.shazam_experiment import (
    run_shazam_experiment,
)
from intent_mgsv_pipeline.runtime_config import PATHS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Shazam against ACRCloud controls and failed samples without "
            "modifying official tracking data."
        )
    )
    parser.add_argument("--success-samples", type=int, default=10)
    parser.add_argument("--failed-samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    csv_path, json_path, summary = asyncio.run(
        run_shazam_experiment(
            PATHS,
            success_samples=max(0, args.success_samples),
            failed_samples=max(0, args.failed_samples),
            timeout_seconds=max(1.0, args.timeout),
            request_interval=max(0.0, args.request_interval),
            output_dir=args.output_dir,
        )
    )
    print("\nExperiment summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"CSV report: {csv_path}")
    print(f"JSON report: {json_path}")
    print("Official tracking data was not modified.")


if __name__ == "__main__":
    main()
