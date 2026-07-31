from __future__ import annotations

import argparse
from pathlib import Path

from intent_mgsv_pipeline.music_preparation.pipeline import prepare_recognized_music
from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.server.db import DEFAULT_DB


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search, download, reuse and align recognized full songs."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--fallback-sources",
        default=None,
        help="Comma-separated fallback sources: youtube,bilibili. Empty disables fallback.",
    )
    args = parser.parse_args()
    fallback_sources = None
    if args.fallback_sources is not None:
        fallback_sources = tuple(
            value.strip()
            for value in args.fallback_sources.split(",")
            if value.strip()
        )
    counts = prepare_recognized_music(
        paths=PATHS,
        db_path=Path(args.db),
        retry_failed=args.retry_failed,
        limit=max(0, args.limit),
        fallback_sources=fallback_sources,
    )
    print(
        "Done: "
        + ", ".join(f"{key}={value}" for key, value in counts.items())
    )


if __name__ == "__main__":
    main()
