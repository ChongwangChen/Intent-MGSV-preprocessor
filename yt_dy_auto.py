from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import scipy.io.wavfile as wav_io

from intent_mgsv_pipeline.runtime_config import (
    PATHS,
    RuntimePaths,
    load_acrcloud_config,
)


RECOGNITION_VERSION = "multi_window_v1"
DEFAULT_CONFIDENCE_THRESHOLD = 75
DEFAULT_STRONG_CONFIDENCE = 90
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


@dataclass(frozen=True)
class RecognitionCandidate:
    title: str
    artist: str
    score: int
    sample_start: float

    @property
    def key(self) -> tuple[str, str]:
        return normalize_song_text(self.title), normalize_song_text(self.artist)


def normalize_song_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def safe_file_part(value: Any, max_length: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._")
    return (text or "unknown")[:max_length]


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        return max(0.0, float(result.stdout.strip()))
    except (FileNotFoundError, ValueError, subprocess.SubprocessError):
        return 0.0


def build_sample_starts(
    duration: float,
    sample_duration: float = 15.0,
    max_samples: int = 4,
) -> list[float]:
    if duration <= sample_duration:
        return [0.0]
    latest = max(0.0, duration - sample_duration)
    fractions = (0.08, 0.30, 0.55, 0.78, 0.92)
    starts: list[float] = []
    for fraction in fractions:
        start = min(latest, max(0.0, duration * fraction))
        if not any(abs(start - existing) < sample_duration * 0.4 for existing in starts):
            starts.append(round(start, 3))
        if len(starts) >= max_samples:
            break
    return starts or [0.0]


def extract_wav_bytes(
    media_path: Path,
    start_seconds: float,
    duration: float,
) -> bytes:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(media_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "wav",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=30,
            check=True,
        )
        if len(result.stdout) <= 44:
            raise RuntimeError("decoded sample is empty")
        return result.stdout
    except FileNotFoundError:
        return extract_wav_bytes_librosa(media_path, start_seconds, duration)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "ffmpeg failed to decode sample") from exc


def extract_wav_bytes_librosa(
    media_path: Path,
    start_seconds: float,
    duration: float,
) -> bytes:
    import librosa

    audio, sample_rate = librosa.load(
        str(media_path),
        sr=8000,
        offset=start_seconds,
        duration=duration,
        mono=True,
    )
    if audio.size == 0:
        raise RuntimeError("decoded sample is empty")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    wav_io.write(buffer, sample_rate, pcm)
    return buffer.getvalue()


def identify_sample(
    audio_bytes: bytes,
    config: dict[str, Any],
    sample_start: float,
) -> list[RecognitionCandidate]:
    host = str(config["host"])
    access_key = str(config["access_key"])
    access_secret = str(config["access_secret"])
    timestamp = str(int(time.time()))
    uri = "/v1/identify"
    string_to_sign = "\n".join(
        ["POST", uri, access_key, "audio", "1", timestamp]
    )
    signature = base64.b64encode(
        hmac.new(
            access_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")
    response = requests.post(
        f"https://{host}{uri}",
        files={"sample": ("audio.wav", audio_bytes, "audio/wav")},
        data={
            "access_key": access_key,
            "data_type": "audio",
            "signature_version": "1",
            "signature": signature,
            "sample_bytes": len(audio_bytes),
            "timestamp": timestamp,
        },
        timeout=float(config.get("timeout", 15)),
    )
    response.raise_for_status()
    result = response.json()
    status = result.get("status", {})
    if status.get("code", -1) != 0:
        message = status.get("msg", "unknown ACRCloud error")
        raise RuntimeError(f"ACRCloud code={status.get('code')}: {message}")

    candidates: list[RecognitionCandidate] = []
    for item in result.get("metadata", {}).get("music", [])[:3]:
        title = str(item.get("title", "")).strip()
        artists = item.get("artists") or []
        artist = "/".join(
            str(artist_item.get("name", "")).strip()
            for artist_item in artists
            if artist_item.get("name")
        )
        if title:
            candidates.append(
                RecognitionCandidate(
                    title=title,
                    artist=artist,
                    score=int(item.get("score", 0) or 0),
                    sample_start=sample_start,
                )
            )
    return candidates


def choose_candidate(
    candidates: Iterable[RecognitionCandidate],
    confidence_threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[RecognitionCandidate | None, int, str]:
    grouped: dict[tuple[str, str], list[RecognitionCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.title:
            grouped[candidate.key].append(candidate)
    if not grouped:
        return None, 0, ""

    ranked = sorted(
        grouped.values(),
        key=lambda group: (
            len({round(item.sample_start, 3) for item in group}),
            max(item.score for item in group),
            sum(item.score for item in group),
        ),
        reverse=True,
    )
    best_group = ranked[0]
    best = max(best_group, key=lambda item: item.score)
    votes = len({round(item.sample_start, 3) for item in best_group})
    accepted = best.score >= confidence_threshold or (
        votes >= 2 and best.score >= max(60, confidence_threshold - 15)
    )
    sample_summary = ",".join(
        f"{item.sample_start:.2f}s:{item.score}"
        for item in sorted(best_group, key=lambda item: item.sample_start)
    )
    return (best if accepted else None), votes, sample_summary


def recognize_music_multi_window(
    video_path: Path,
    config: dict[str, Any],
    *,
    sample_duration: float = 15.0,
    max_samples: int = 4,
    confidence_threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
    strong_confidence: int = DEFAULT_STRONG_CONFIDENCE,
    request_interval: float = 1.0,
) -> dict[str, Any]:
    duration = probe_duration(video_path)
    starts = build_sample_starts(duration, sample_duration, max_samples)
    all_candidates: list[RecognitionCandidate] = []
    errors: list[str] = []
    attempted: list[float] = []

    for start in starts:
        attempted.append(start)
        try:
            sample = extract_wav_bytes(video_path, start, sample_duration)
            current = identify_sample(sample, config, start)
            all_candidates.extend(current)
            current_best, votes, _ = choose_candidate(
                all_candidates,
                confidence_threshold,
            )
            if current_best and (
                current_best.score >= strong_confidence or votes >= 2
            ):
                break
        except Exception as exc:
            errors.append(f"{start:.2f}s: {type(exc).__name__}: {exc}")
        if request_interval > 0:
            time.sleep(request_interval)

    best, votes, sample_summary = choose_candidate(
        all_candidates,
        confidence_threshold,
    )
    return {
        "title": best.title if best else "",
        "artist": best.artist if best else "",
        "confidence": best.score if best else 0,
        "votes": votes,
        "sample_starts": ",".join(f"{value:.3f}" for value in attempted),
        "sample_summary": sample_summary,
        "recognition_error": " | ".join(errors),
        "status": "recognized" if best else "recognition_failed",
    }


def find_video_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"scan root does not exist: {root}")
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def load_tracking(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_excel(path)


def should_process(
    previous: dict[str, Any] | None,
    *,
    retry_failed: bool,
) -> bool:
    if not previous:
        return True
    status = str(previous.get("status", "") or "")
    version = str(previous.get("recognition_version", "") or "")
    if status in {"recognized", "downloaded", "reused", "verified"}:
        return False
    return retry_failed or version != RECOGNITION_VERSION


def save_tracking(
    tracking_path: Path,
    old_df: pd.DataFrame,
    new_records: list[dict[str, Any]],
) -> None:
    if not new_records:
        return
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(new_records)
    if not old_df.empty and "video_id" in old_df.columns:
        replaced = set(new_df["video_id"].astype(str))
        old_df = old_df[~old_df["video_id"].astype(str).isin(replaced)]
    final_df = pd.concat([old_df, new_df], ignore_index=True)
    final_df.to_excel(tracking_path, index=False)


def download_full_music(title: str, artist: str, output_path: Path) -> Path | None:
    if output_path.exists():
        return output_path
    artist_part = f" {artist}" if artist else ""
    queries = [
        f"ytsearch1:{title}{artist_part} official audio",
        f"ytsearch1:{title}{artist_part}",
        f"bilisearch1:{title}{artist_part}",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for query in queries:
        template = str(output_path.with_suffix(".%(ext)s"))
        command = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "m4a",
            "--audio-quality",
            "0",
            "--no-playlist",
            "--match-filter",
            "duration > 60",
            "-o",
            template,
            "--quiet",
            "--no-warnings",
            query,
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=180, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        for suffix in (".m4a", ".webm", ".opus", ".mp3"):
            candidate = output_path.with_suffix(suffix)
            if candidate.exists():
                return candidate
    return None


def process_videos(
    paths: RuntimePaths,
    *,
    retry_failed: bool = False,
    recognition_only: bool = True,
    max_samples: int = 4,
    sample_duration: float = 15.0,
    confidence_threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
    limit: int = 0,
) -> dict[str, int]:
    config = load_acrcloud_config(paths)
    tracking_df = load_tracking(paths.acr_tracking_excel)
    previous_by_video: dict[str, dict[str, Any]] = {}
    if not tracking_df.empty and "video_id" in tracking_df.columns:
        for record in tracking_df.to_dict("records"):
            previous_by_video[str(record.get("video_id", ""))] = record

    new_records: list[dict[str, Any]] = []
    counts = {"scanned": 0, "processed": 0, "recognized": 0, "failed": 0}
    for video_path in find_video_files(paths.douk_download_root):
        counts["scanned"] += 1
        video_id = video_path.name
        if not should_process(
            previous_by_video.get(video_id),
            retry_failed=retry_failed,
        ):
            continue
        if limit and counts["processed"] >= limit:
            break

        print(f"\n[{counts['processed'] + 1}] Recognizing: {video_id}")
        result = recognize_music_multi_window(
            video_path,
            config,
            sample_duration=sample_duration,
            max_samples=max_samples,
            confidence_threshold=confidence_threshold,
        )
        counts["processed"] += 1
        title = result["title"]
        artist = result["artist"]
        full_music_path = ""
        final_status = result["status"]

        if title:
            counts["recognized"] += 1
            print(
                f"  recognized: {title} - {artist} "
                f"(score={result['confidence']}, votes={result['votes']})"
            )
            if not recognition_only:
                filename = (
                    f"{safe_file_part(title)}_{safe_file_part(artist)}.m4a"
                )
                downloaded = download_full_music(
                    title,
                    artist,
                    paths.full_music_dir / filename,
                )
                if downloaded:
                    full_music_path = str(downloaded)
                    final_status = "downloaded"
        else:
            counts["failed"] += 1
            print(f"  recognition failed: {result['recognition_error'] or 'no match'}")

        new_records.append(
            {
                "video_id": video_id,
                "video_path": str(video_path),
                "song_title": title,
                "song_artist": artist,
                "acr_confidence": result["confidence"],
                "recognition_votes": result["votes"],
                "recognition_samples": result["sample_starts"],
                "recognition_sample_summary": result["sample_summary"],
                "recognition_error": result["recognition_error"],
                "recognition_version": RECOGNITION_VERSION,
                "full_music_path": full_music_path,
                "status": final_status,
            }
        )
        save_tracking(paths.acr_tracking_excel, tracking_df, new_records)

    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incremental multi-window music recognition for Douyin videos."
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--max-samples", type=int, default=4)
    parser.add_argument("--sample-duration", type=float, default=15.0)
    parser.add_argument(
        "--confidence-threshold",
        type=int,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    parser.add_argument("--limit", type=int, default=0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(f"Scan root: {PATHS.douk_download_root}")
    print(f"Tracking file: {PATHS.acr_tracking_excel}")
    counts = process_videos(
        PATHS,
        retry_failed=args.retry_failed,
        recognition_only=not args.download,
        max_samples=max(1, args.max_samples),
        sample_duration=max(5.0, args.sample_duration),
        confidence_threshold=max(0, min(100, args.confidence_threshold)),
        limit=max(0, args.limit),
    )
    print(
        "Done: "
        f"scanned={counts['scanned']}, "
        f"processed={counts['processed']}, "
        f"recognized={counts['recognized']}, "
        f"failed={counts['failed']}"
    )


if __name__ == "__main__":
    main()
