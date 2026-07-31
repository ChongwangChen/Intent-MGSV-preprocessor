from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import requests

from intent_mgsv_pipeline.runtime_config import PATHS


AUDIO_EXTENSIONS = (".mp3", ".m4a", ".flac", ".opus", ".webm")
MAX_SONG_DURATION = 600


@dataclass(frozen=True)
class QQMusicCandidate:
    song_mid: str
    title: str
    artist: str
    album: str
    duration: int
    title_score: float
    artist_score: float
    match_score: float


def normalize_song_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(official|audio|lyrics?|mv|live|version|remaster(?:ed)?)\b",
        "",
        text,
    )
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_song_text(left)
    right_norm = normalize_song_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm))
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def score_candidate(
    title: str,
    artist: str,
    candidate_title: str,
    candidate_artist: str,
) -> tuple[float, float, float]:
    title_score = text_similarity(title, candidate_title)
    artist_score = text_similarity(artist, candidate_artist) if artist else 0.0
    if artist:
        combined = title_score * 0.72 + artist_score * 0.28
    else:
        combined = title_score
    return title_score, artist_score, combined


def candidate_is_acceptable(candidate: QQMusicCandidate, artist_required: bool) -> bool:
    if candidate.title_score < 0.84:
        return False
    if artist_required and candidate.artist_score < 0.42:
        return candidate.title_score >= 0.97 and candidate.match_score >= 0.78
    return candidate.match_score >= 0.72


def search_qqmusic(
    title: str,
    artist: str,
    *,
    session: requests.Session | None = None,
    attempts: int = 3,
) -> list[QQMusicCandidate]:
    session = session or requests.Session()
    endpoint = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
    params = {
        "w": f"{title} {artist}".strip(),
        "format": "json",
        "n": 12,
        "p": 1,
        "cr": 1,
    }
    headers = {
        "Referer": "https://y.qq.com/",
        "User-Agent": "Mozilla/5.0",
    }
    last_error: Exception | None = None
    payload: dict[str, Any] = {}
    for attempt in range(attempts):
        try:
            response = session.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"QQ Music search failed: {last_error}") from last_error

    candidates: list[QQMusicCandidate] = []
    for item in payload.get("data", {}).get("song", {}).get("list", []):
        duration = int(item.get("interval", 0) or 0)
        if duration <= 0 or duration > MAX_SONG_DURATION:
            continue
        candidate_artist = "/".join(
            str(singer.get("name", "")).strip()
            for singer in item.get("singer", []) or []
            if singer.get("name")
        )
        candidate_title = str(item.get("songname", "")).strip()
        title_score, artist_score, match_score = score_candidate(
            title,
            artist,
            candidate_title,
            candidate_artist,
        )
        candidates.append(
            QQMusicCandidate(
                song_mid=str(item.get("songmid", "")).strip(),
                title=candidate_title,
                artist=candidate_artist,
                album=str(item.get("albumname", "")).strip(),
                duration=duration,
                title_score=title_score,
                artist_score=artist_score,
                match_score=match_score,
            )
        )
    return sorted(candidates, key=lambda item: item.match_score, reverse=True)


def safe_file_stem(title: str, artist: str) -> str:
    value = f"{title}_{artist}".strip("_ ")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "unknown_song")[:120]


def find_downloaded(base: Path) -> Path | None:
    for extension in AUDIO_EXTENSIONS:
        candidate = base.with_suffix(extension)
        if candidate.exists() and candidate.stat().st_size > 1024:
            return candidate
    matches = [
        path
        for path in base.parent.glob(f"{base.name}*")
        if path.suffix.lower() in AUDIO_EXTENSIONS and path.stat().st_size > 1024
    ]
    return matches[0] if matches else None


def _yt_dlp_command(
    source: str,
    output_base: Path,
    *,
    cookie_file: Path | None,
    aria2c: str | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "320K",
        "--no-playlist",
        "--socket-timeout",
        "25",
        "--retries",
        "4",
        "--fragment-retries",
        "4",
        "-o",
        str(output_base) + ".%(ext)s",
    ]
    if cookie_file:
        command.extend(["--cookies", str(cookie_file)])
    if aria2c:
        command.extend(
            [
                "--downloader",
                aria2c,
                "--downloader-args",
                "aria2c:-x 8 -s 8 -k 1M --max-tries=5 --retry-wait=2",
            ]
        )
    command.append(source)
    return command


def download_with_ytdlp(
    source: str,
    output_base: Path,
    *,
    cookie_file: Path | None = None,
    prefer_aria2c: bool = True,
    timeout: int = 900,
) -> tuple[Path | None, str]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    aria2c = (
        os.environ.get("ARIA2C_EXE", "").strip()
        or shutil.which("aria2c")
        or ""
    )
    attempts: list[tuple[bool, str]] = []
    if prefer_aria2c and aria2c:
        attempts.append((True, "aria2c"))
    attempts.append((False, "native"))

    cookie_copy: Path | None = None
    if cookie_file and cookie_file.exists():
        handle, temp_name = tempfile.mkstemp(
            prefix="qqmusic_cookies_",
            suffix=".txt",
            dir=str(output_base.parent),
        )
        os.close(handle)
        cookie_copy = Path(temp_name)
        shutil.copy2(cookie_file, cookie_copy)

    errors: list[str] = []
    try:
        for use_aria, label in attempts:
            command = _yt_dlp_command(
                source,
                output_base,
                cookie_file=cookie_copy,
                aria2c=aria2c if use_aria else None,
            )
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                errors.append(f"{label}: timeout after {timeout}s")
                continue
            downloaded = find_downloaded(output_base)
            if result.returncode == 0 and downloaded:
                return downloaded, label
            message = (result.stderr or result.stdout or "download failed").strip()
            errors.append(f"{label}: {message[-500:]}")
    finally:
        if cookie_copy:
            cookie_copy.unlink(missing_ok=True)
        for path in output_base.parent.glob(f"{output_base.name}*.part"):
            path.unlink(missing_ok=True)
        for path in output_base.parent.glob(f"{output_base.name}*.ytdl"):
            path.unlink(missing_ok=True)
    return None, " | ".join(errors)


def download_qq_candidate(
    candidate: QQMusicCandidate,
    *,
    output_dir: Path | None = None,
    cookie_file: Path | None = None,
) -> tuple[Path | None, str]:
    output_dir = output_dir or PATHS.full_songs_dir
    if cookie_file is None:
        cookie_file = Path(
            os.environ.get(
                "QQMUSIC_COOKIES_FILE",
                str(PATHS.project_root / "qqmusic_cookies.txt"),
            )
        )
    final_base = output_dir / safe_file_stem(candidate.title, candidate.artist)
    existing = find_downloaded(final_base)
    if existing:
        return existing, "existing_file"

    temp_base = output_dir / f"._qq_{candidate.song_mid}"
    downloaded, method = download_with_ytdlp(
        f"https://y.qq.com/n/ryqq/songDetail/{candidate.song_mid}",
        temp_base,
        cookie_file=cookie_file if cookie_file.exists() else None,
    )
    if not downloaded:
        return None, method
    final_path = final_base.with_suffix(downloaded.suffix.lower())
    if final_path.exists():
        downloaded.unlink(missing_ok=True)
        return final_path, "existing_file"
    downloaded.replace(final_path)
    return final_path, f"qqmusic_{method}"


def download_fallback(
    title: str,
    artist: str,
    *,
    output_dir: Path | None = None,
    sources: Iterable[str] = ("youtube", "bilibili"),
) -> tuple[Path | None, str, str]:
    output_dir = output_dir or PATHS.full_songs_dir
    final_base = output_dir / safe_file_stem(title, artist)
    existing = find_downloaded(final_base)
    if existing:
        return existing, "existing_file", ""

    query = f"{title} {artist}".strip()
    for source in sources:
        source = source.strip().casefold()
        if source == "youtube":
            target = f"ytsearch1:{query} official audio"
        elif source == "bilibili":
            target = f"bilisearch1:{query}"
        else:
            continue
        temp_base = output_dir / f"._fallback_{safe_file_stem(title, artist)}"
        downloaded, detail = download_with_ytdlp(
            target,
            temp_base,
            cookie_file=None,
            prefer_aria2c=False,
            timeout=300,
        )
        if downloaded:
            final_path = final_base.with_suffix(downloaded.suffix.lower())
            if final_path.exists():
                downloaded.unlink(missing_ok=True)
                return final_path, "existing_file", ""
            downloaded.replace(final_path)
            return final_path, source, ""
        error = detail
    return None, "", error if "error" in locals() else "no fallback source enabled"
