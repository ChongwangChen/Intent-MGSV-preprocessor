from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    douk_download_root: Path
    douk_data_excel: Path
    output_dir: Path
    full_music_dir: Path
    full_songs_dir: Path
    acr_tracking_excel: Path
    master_excel: Path
    server_db: Path
    acr_config_file: Path


def load_runtime_paths() -> RuntimePaths:
    default_root = Path(__file__).resolve().parents[1]
    project_root = _path_from_env("MGSV_ROOT", default_root)
    output_dir = _path_from_env("MGSV_OUTPUT_DIR", project_root / "outputs")
    douk_volume = _path_from_env(
        "MGSV_DOUK_VOLUME",
        project_root / "DouK-Source" / "Volume",
    )
    return RuntimePaths(
        project_root=project_root,
        douk_download_root=_path_from_env(
            "MGSV_SCAN_ROOT",
            douk_volume / "Download",
        ),
        douk_data_excel=_path_from_env(
            "MGSV_DOUK_DATA_EXCEL",
            douk_volume / "Data" / "Download.xlsx",
        ),
        output_dir=output_dir,
        full_music_dir=_path_from_env(
            "MGSV_FULL_MUSIC_DIR",
            output_dir / "full_music",
        ),
        full_songs_dir=_path_from_env(
            "MGSV_FULL_SONGS_DIR",
            output_dir / "full_songs",
        ),
        acr_tracking_excel=_path_from_env(
            "MGSV_ACR_TRACKING",
            output_dir / "acrcloud_tracking.xlsx",
        ),
        master_excel=_path_from_env(
            "MGSV_EXCEL",
            output_dir / "MGSV_Master_Dataset.xlsx",
        ),
        server_db=_path_from_env(
            "MGSV_DB",
            output_dir / "server" / "intent_mgsv.sqlite3",
        ),
        acr_config_file=_path_from_env(
            "ACRCLOUD_CONFIG_FILE",
            project_root / "acrcloud_config.json",
        ),
    )


def load_acrcloud_config(paths: RuntimePaths | None = None) -> dict[str, Any]:
    paths = paths or load_runtime_paths()
    config: dict[str, Any] = {}
    if paths.acr_config_file.exists():
        value = json.loads(paths.acr_config_file.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            config.update(value)

    env_mapping = {
        "host": "ACRCLOUD_HOST",
        "access_key": "ACRCLOUD_ACCESS_KEY",
        "access_secret": "ACRCLOUD_ACCESS_SECRET",
        "timeout": "ACRCLOUD_TIMEOUT",
    }
    for key, env_name in env_mapping.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            config[key] = value

    config.setdefault("host", "identify-ap-southeast-1.acrcloud.com")
    config.setdefault("timeout", 15)
    try:
        config["timeout"] = float(config["timeout"])
    except (TypeError, ValueError):
        config["timeout"] = 15

    missing = [key for key in ("access_key", "access_secret") if not config.get(key)]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"ACRCloud config is missing: {joined}. "
            f"Set environment variables or create {paths.acr_config_file}."
        )
    return config


PATHS = load_runtime_paths()
