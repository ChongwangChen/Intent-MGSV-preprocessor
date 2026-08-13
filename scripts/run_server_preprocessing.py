from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_step(command: list[str], *, cwd: Path, log) -> None:
    printable = " ".join(command)
    print(f"\n>>> {printable}", flush=True)
    log.write(f"\n>>> {printable}\n")
    log.flush()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        log.write(line)
        log.flush()
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"Step failed ({return_code}): {printable}")


def acquire_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        owner = path.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(f"Preprocessing is already locked: {path} ({owner})") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} started={datetime.now().isoformat()}\n")


def backup_sqlite(source_path: Path, output_path: Path) -> None:
    with closing(sqlite3.connect(source_path)) as source:
        with closing(sqlite3.connect(output_path)) as target:
            source.backup(target)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Database backup failed: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run recognition, auto.py, append-only DB import and service restart."
    )
    parser.add_argument("--skip-music", action="store_true")
    parser.add_argument("--skip-auto", action="store_true")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument("--music-limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(os.environ.get("MGSV_ROOT", PROJECT_ROOT)).expanduser().resolve()
    output_dir = Path(os.environ.get("MGSV_OUTPUT_DIR", root / "outputs")).resolve()
    excel = Path(os.environ.get("MGSV_EXCEL", output_dir / "MGSV_Master_Dataset.xlsx")).resolve()
    db = Path(os.environ.get("MGSV_DB", output_dir / "server" / "intent_mgsv.sqlite3")).resolve()
    preprocess_python = os.environ.get("MGSV_PREPROCESS_PYTHON", "").strip()
    data_python = sys.executable
    if not args.skip_auto and not preprocess_python and not args.dry_run:
        raise SystemExit("MGSV_PREPROCESS_PYTHON is not configured; source config/server.env")
    if not preprocess_python:
        preprocess_python = "$MGSV_PREPROCESS_PYTHON"

    lock_path = output_dir / "server" / "locks" / "preprocessing.lock"
    log_dir = output_dir / "server" / "preprocessing_logs"
    backup_dir = output_dir / "server" / "backups"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"preprocess_{timestamp}.log"
    summary_path = log_dir / f"preprocess_{timestamp}.json"
    log_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    commands: list[list[str]] = []
    if not args.skip_music:
        music_command = [data_python, "yt_dy_auto.py", "--retry-failed", "--download"]
        if args.music_limit > 0:
            music_command.extend(["--limit", str(args.music_limit)])
        commands.append(music_command)
    if not args.skip_auto:
        commands.append([preprocess_python, "auto.py"])
    if not args.skip_import:
        commands.append(
            [
                data_python,
                "-m",
                "intent_mgsv_pipeline.server.import_excel_to_db",
                "--input",
                str(excel),
                "--db",
                str(db),
                "--annotator-id",
                os.environ.get("MGSV_OWNER_ID", "owner"),
            ]
        )
        commands.append(
            [
                data_python,
                "-m",
                "intent_mgsv_pipeline.server.repair_video_paths",
                "--db",
                str(db),
            ]
        )
    if not args.skip_restart:
        commands.append(
            ["bash", "intent_mgsv_pipeline/server/manage_annotation_services.sh", "restart"]
        )
        commands.append(
            ["bash", "intent_mgsv_pipeline/server/manage_annotation_services.sh", "status"]
        )

    plan = {
        "root": str(root),
        "excel": str(excel),
        "database": str(db),
        "log": str(log_path),
        "commands": commands,
        "append_only_import": True,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    acquire_lock(lock_path)
    started = time.time()
    try:
        if db.is_file():
            backup_path = backup_dir / f"{db.stem}_{timestamp}.sqlite3"
            backup_sqlite(db, backup_path)
            print(f"Database backup: {backup_path}")
        if excel.is_file():
            excel_backup = backup_dir / f"{excel.stem}_{timestamp}.xlsx"
            shutil.copy2(excel, excel_backup)
            print(f"Excel backup: {excel_backup}")

        with log_path.open("w", encoding="utf-8") as log:
            for command in commands:
                run_step(command, cwd=root, log=log)
        plan["status"] = "completed"
    except Exception as exc:
        plan["status"] = "failed"
        plan["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        plan["elapsed_seconds"] = round(time.time() - started, 2)
        summary_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lock_path.unlink(missing_ok=True)
        print(f"Pipeline report: {summary_path}")


if __name__ == "__main__":
    main()
