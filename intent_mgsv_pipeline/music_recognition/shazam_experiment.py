from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Awaitable, Callable

import pandas as pd

from intent_mgsv_pipeline.runtime_config import RuntimePaths
from yt_dy_auto import find_clean_music_for_video, find_video_files, probe_duration


SUCCESS_STATUSES = {"recognized", "downloaded", "reused", "verified"}


@dataclass(frozen=True)
class ShazamMatch:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    shazam_key: str = ""
    url: str = ""

    @property
    def recognized(self) -> bool:
        return bool(self.title)


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def matches_reference(
    expected_title: Any,
    expected_artist: Any,
    actual_title: Any,
    actual_artist: Any,
) -> tuple[bool, float, float]:
    title_score = text_similarity(expected_title, actual_title)
    artist_score = text_similarity(expected_artist, actual_artist)
    artist_required = bool(normalize_text(expected_artist))
    matched = title_score >= 0.85 and (
        not artist_required or artist_score >= 0.5
    )
    return matched, title_score, artist_score


def parse_shazam_response(payload: Any) -> ShazamMatch:
    if not isinstance(payload, dict):
        return ShazamMatch()
    track = payload.get("track")
    if not isinstance(track, dict):
        return ShazamMatch()
    genres = track.get("genres")
    genre = ""
    if isinstance(genres, dict):
        genre = str(genres.get("primary", "") or "").strip()
    album = ""
    sections = track.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            metadata = section.get("metadata")
            if not isinstance(metadata, list):
                continue
            for item in metadata:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("title", "") or "").strip().casefold()
                if label in {"album", "专辑"}:
                    album = str(item.get("text", "") or "").strip()
                    break
            if album:
                break
    share = track.get("share")
    url = str(share.get("href", "") or "").strip() if isinstance(share, dict) else ""
    return ShazamMatch(
        title=str(track.get("title", "") or "").strip(),
        artist=str(track.get("subtitle", "") or "").strip(),
        album=album,
        genre=genre,
        shazam_key=str(track.get("key", "") or "").strip(),
        url=url,
    )


def select_experiment_rows(
    tracking: pd.DataFrame,
    *,
    success_samples: int,
    failed_samples: int,
) -> list[dict[str, Any]]:
    if tracking.empty or "video_id" not in tracking.columns:
        return []
    data = tracking.copy()
    for column in ("status", "song_title", "song_artist"):
        if column not in data.columns:
            data[column] = ""
        data[column] = data[column].fillna("").astype(str)
    data["video_id"] = data["video_id"].fillna("").astype(str)
    data = data[data["video_id"].str.strip().ne("")]
    data = data.sort_values("video_id", kind="stable")

    successful = data[
        data["status"].isin(SUCCESS_STATUSES)
        & data["song_title"].str.strip().ne("")
    ].head(max(0, success_samples))
    failed = data[
        ~data["status"].isin(SUCCESS_STATUSES)
        & data["song_title"].str.strip().eq("")
    ].head(max(0, failed_samples))

    records: list[dict[str, Any]] = []
    for group, frame in (("control_success", successful), ("acr_failed", failed)):
        for record in frame.to_dict("records"):
            record["experiment_group"] = group
            records.append(record)
    return records


def build_video_index(root: Path) -> dict[str, Path]:
    return {path.name: path for path in find_video_files(root)}


def resolve_video_path(record: dict[str, Any], index: dict[str, Path]) -> Path | None:
    raw_path = str(record.get("video_path", "") or "").strip()
    if raw_path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    return index.get(str(record.get("video_id", "") or "").strip())


def find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    python_dir = Path(sys.executable).resolve().parent
    candidates = (
        python_dir / "Library" / "bin" / "ffmpeg.exe",
        python_dir / "ffmpeg.exe",
        python_dir / "ffmpeg",
        python_dir.parent / "bin" / "ffmpeg",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def prepare_shazam_sample(
    media_path: Path,
    output_path: Path,
    *,
    sample_duration: float = 20.0,
) -> tuple[Path, str]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return media_path, "ffmpeg not found; used original media"
    duration = probe_duration(media_path)
    start = max(0.0, duration * 0.5 - sample_duration * 0.5)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(media_path),
        "-t",
        f"{sample_duration:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
        if output_path.is_file() and output_path.stat().st_size > 1024:
            return output_path, ""
    except (OSError, subprocess.SubprocessError) as exc:
        return media_path, f"sample conversion failed: {type(exc).__name__}: {exc}"
    return media_path, "sample conversion produced no audio; used original media"


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    controls = [row for row in results if row["experiment_group"] == "control_success"]
    failures = [row for row in results if row["experiment_group"] == "acr_failed"]

    def count(rows: list[dict[str, Any]], key: str) -> int:
        return sum(bool(row.get(key)) for row in rows)

    control_hits = count(controls, "shazam_recognized")
    control_agreements = count(controls, "agrees_with_acr")
    rescued = count(failures, "shazam_recognized")
    errors = count(results, "error")
    control_total = len(controls)
    failed_total = len(failures)
    total = len(results)

    control_hit_rate = control_hits / control_total if control_total else 0.0
    control_agreement_rate = (
        control_agreements / control_total if control_total else 0.0
    )
    rescue_rate = rescued / failed_total if failed_total else 0.0
    error_rate = errors / total if total else 0.0

    if (
        control_total >= 5
        and failed_total >= 5
        and control_hit_rate >= 0.8
        and control_agreement_rate >= 0.8
        and rescue_rate >= 0.2
        and error_rate <= 0.2
    ):
        recommendation = "recommend_fallback_integration"
    elif rescued > 0 and control_agreement_rate >= 0.6 and error_rate <= 0.4:
        recommendation = "promising_manual_review_only"
    else:
        recommendation = "do_not_integrate_yet"

    return {
        "total": total,
        "control_total": control_total,
        "control_hits": control_hits,
        "control_agreements": control_agreements,
        "control_hit_rate": round(control_hit_rate, 4),
        "control_agreement_rate": round(control_agreement_rate, 4),
        "acr_failed_total": failed_total,
        "acr_failed_rescued": rescued,
        "rescue_rate": round(rescue_rate, 4),
        "errors": errors,
        "error_rate": round(error_rate, 4),
        "recommendation": recommendation,
        "manual_review_required_for_new_matches": True,
        "writes_official_tracking": False,
    }


async def recognize_rows(
    records: list[dict[str, Any]],
    *,
    video_index: dict[str, Path],
    recognize: Callable[[str], Awaitable[Any]],
    timeout_seconds: float,
    request_interval: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mgsv_shazam_") as temp_name:
        temp_root = Path(temp_name)
        for position, record in enumerate(records, start=1):
            video_id = str(record.get("video_id", "") or "").strip()
            video_path = resolve_video_path(record, video_index)
            media_path: Path | None = None
            media_source = ""
            if video_path is not None:
                clean_music = find_clean_music_for_video(video_path)
                media_path = clean_music or video_path
                media_source = "douk_music" if clean_music else "video_audio"

            print(f"\n[{position}/{len(records)}] Shazam: {video_id}", flush=True)
            started = time.monotonic()
            match = ShazamMatch()
            error = ""
            conversion_warning = ""
            submitted_path: Path | None = None
            if media_path is None:
                error = "video file not found"
            else:
                submitted_path, conversion_warning = prepare_shazam_sample(
                    media_path,
                    temp_root / f"sample_{position:04d}.wav",
                )
                try:
                    payload = await asyncio.wait_for(
                        recognize(str(submitted_path)),
                        timeout=max(1.0, timeout_seconds),
                    )
                    match = parse_shazam_response(payload)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"

            agrees = False
            title_score = 0.0
            artist_score = 0.0
            if record["experiment_group"] == "control_success" and match.recognized:
                agrees, title_score, artist_score = matches_reference(
                    record.get("song_title", ""),
                    record.get("song_artist", ""),
                    match.title,
                    match.artist,
                )
            needs_manual_review = match.recognized and (
                record["experiment_group"] == "acr_failed" or not agrees
            )
            elapsed = round(time.monotonic() - started, 3)
            output = {
                "experiment_group": record["experiment_group"],
                "video_id": video_id,
                "video_path": str(video_path or ""),
                "recognition_media_path": str(media_path or ""),
                "recognition_media_source": media_source,
                "recognition_input": (
                    "normalized_wav"
                    if submitted_path is not None and submitted_path != media_path
                    else "original_media"
                ),
                "sample_conversion_warning": conversion_warning,
                "acr_title": str(record.get("song_title", "") or ""),
                "acr_artist": str(record.get("song_artist", "") or ""),
                "shazam_title": match.title,
                "shazam_artist": match.artist,
                "shazam_album": match.album,
                "shazam_genre": match.genre,
                "shazam_key": match.shazam_key,
                "shazam_url": match.url,
                "shazam_recognized": match.recognized,
                "agrees_with_acr": agrees,
                "needs_manual_review": needs_manual_review,
                "title_similarity": round(title_score, 4),
                "artist_similarity": round(artist_score, 4),
                "elapsed_seconds": elapsed,
                "error": error,
            }
            results.append(output)
            if match.recognized:
                agreement = " agrees" if agrees else " (manual review)"
                print(f"  -> {match.title} - {match.artist}{agreement}", flush=True)
            else:
                print(f"  -> no match{': ' + error if error else ''}", flush=True)
            if request_interval > 0 and position < len(records):
                await asyncio.sleep(request_interval)
    return results


async def run_shazam_experiment(
    paths: RuntimePaths,
    *,
    success_samples: int = 10,
    failed_samples: int = 10,
    timeout_seconds: float = 45.0,
    request_interval: float = 2.0,
    output_dir: Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        ffmpeg_dir = str(Path(ffmpeg).parent)
        current_path = os.environ.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        if ffmpeg_dir not in path_parts:
            os.environ["PATH"] = os.pathsep.join([ffmpeg_dir, *path_parts])
    try:
        from shazamio import Shazam
    except ImportError as exc:
        raise RuntimeError(
            "ShazamIO is not installed. Run: "
            "python -m pip install -r requirements_music_experiment.txt"
        ) from exc

    if not paths.acr_tracking_excel.is_file():
        raise FileNotFoundError(f"tracking file not found: {paths.acr_tracking_excel}")
    tracking = pd.read_excel(paths.acr_tracking_excel)
    records = select_experiment_rows(
        tracking,
        success_samples=success_samples,
        failed_samples=failed_samples,
    )
    if not records:
        raise RuntimeError("No suitable control or failed rows were found in tracking data")

    shazam = Shazam()
    video_index = build_video_index(paths.douk_download_root)
    results = await recognize_rows(
        records,
        video_index=video_index,
        recognize=shazam.recognize,
        timeout_seconds=timeout_seconds,
        request_interval=request_interval,
    )
    summary = summarize_results(results)
    summary.update(
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "tracking_file": str(paths.acr_tracking_excel),
            "success_samples_requested": success_samples,
            "failed_samples_requested": failed_samples,
            "backend": "ShazamIO 0.8.x (unofficial Shazam client)",
        }
    )

    destination = output_dir or (
        paths.output_dir / "server" / "music_recognition_experiments"
    )
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = destination / f"shazam_ab_{timestamp}.csv"
    json_path = destination / f"shazam_ab_{timestamp}.json"
    pd.DataFrame(results).to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {"summary": summary, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return csv_path, json_path, summary
