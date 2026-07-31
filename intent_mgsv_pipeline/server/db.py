from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from intent_mgsv_pipeline.runtime_config import PATHS


PROJECT_ROOT = PATHS.project_root
DEFAULT_DB = PATHS.server_db
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB) -> Path:
    path = Path(db_path)
    with connect(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return path


def dumps_json(value: Mapping[str, Any] | list[Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def loads_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def scalar(value: Any) -> Any:
    if value is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except Exception:
        pass
    return value


def text(value: Any) -> str:
    value = scalar(value)
    return "" if value is None else str(value).strip()


def number(value: Any) -> float | None:
    value = scalar(value)
    if value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    n = number(value)
    if n is None:
        return None
    return int(n)


def log_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO events(actor, event_type, target_type, target_id, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor, event_type, target_type, "" if target_id is None else str(target_id), dumps_json(payload)),
    )
