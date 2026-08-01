from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from intent_mgsv_pipeline.runtime_config import PATHS


def browser_safe_media_path(
    source: Path,
    *,
    cache_dir: Path | None = None,
    default_suffix: str = ".bin",
) -> Path:
    """Return an ASCII-only alias so URL-reserved filename characters are safe."""
    source = source.resolve()
    if not source.is_file():
        return source

    stat = source.stat()
    fingerprint = (
        f"{source}\0{stat.st_size}\0{stat.st_mtime_ns}".encode(
            "utf-8",
            errors="surrogatepass",
        )
    )
    digest = hashlib.sha256(fingerprint).hexdigest()[:24]
    suffix = source.suffix.lower() if source.suffix else default_suffix
    cache_dir = cache_dir or PATHS.output_dir / "server" / "media_aliases"
    cache_dir.mkdir(parents=True, exist_ok=True)
    alias = cache_dir / f"{digest}{suffix}"

    if alias.is_file() and alias.stat().st_size == stat.st_size:
        return alias
    alias.unlink(missing_ok=True)

    try:
        os.link(source, alias)
    except OSError:
        try:
            alias.symlink_to(source)
        except OSError:
            # This is only reached when source and output are on different
            # filesystems and symlinks are unavailable.
            shutil.copy2(source, alias)
    return alias


def browser_safe_video_path(
    source: Path,
    *,
    cache_dir: Path | None = None,
) -> Path:
    return browser_safe_media_path(
        source,
        cache_dir=cache_dir or PATHS.output_dir / "server" / "video_aliases",
        default_suffix=".mp4",
    )


def browser_safe_audio_path(
    source: Path,
    *,
    cache_dir: Path | None = None,
) -> Path:
    return browser_safe_media_path(
        source,
        cache_dir=cache_dir or PATHS.output_dir / "server" / "audio_aliases",
        default_suffix=".mp3",
    )
