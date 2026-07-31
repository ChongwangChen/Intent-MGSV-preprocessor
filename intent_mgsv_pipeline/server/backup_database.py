from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from intent_mgsv_pipeline.server.db import DEFAULT_DB


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"database does not exist: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = backup_dir / f"{db_path.stem}_{timestamp}.sqlite3"
    with sqlite3.connect(db_path) as source:
        with sqlite3.connect(output) as target:
            source.backup(target)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"database backup failed: {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a transactionally consistent SQLite backup."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--out-dir",
        default="outputs/server/backups",
    )
    args = parser.parse_args()
    output = backup_database(Path(args.db), Path(args.out_dir))
    print(f"Database backup: {output.resolve()}")


if __name__ == "__main__":
    main()
