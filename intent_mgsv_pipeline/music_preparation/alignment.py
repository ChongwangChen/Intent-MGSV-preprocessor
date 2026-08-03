from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.signal import stft


SAMPLE_RATE = 11025
HOP_LENGTH = 1024
READY_SCORE = 0.45
REVIEW_SCORE = 0.30


@dataclass(frozen=True)
class WindowMatch:
    video_start: float
    relation: float
    score: float


@dataclass(frozen=True)
class AlignmentResult:
    song_offset: float | None
    video_audio_start: float
    score: float | None
    votes: int
    status: str
    error: str = ""


def probe_media_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=True,
        )
        duration = float(result.stdout.strip())
        return duration if duration > 0 else None
    except (FileNotFoundError, ValueError, subprocess.SubprocessError):
        return None


def _decode_audio(
    path: Path,
    *,
    duration: float,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[np.ndarray, int]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed or not on PATH")
    handle, temp_name = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    Path(temp_name).unlink(missing_ok=True)
    try:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "wav",
            temp_name,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=max(60, int(duration * 1.5)),
        )
        if result.returncode != 0 or not Path(temp_name).exists():
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"ffmpeg could not decode {path.name}")
        with wave.open(temp_name, "rb") as audio_file:
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            rate = audio_file.getframerate()
            raw = audio_file.readframes(audio_file.getnframes())
        if sample_width == 2:
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sample_width == 4:
            audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        if audio.size < sample_rate * 3:
            raise RuntimeError(f"decoded audio is too short: {path.name}")
        return audio, rate
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _normalize_chroma(chroma: np.ndarray) -> np.ndarray:
    chroma = chroma - chroma.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms[norms < 1e-8] = 1.0
    return chroma / norms


def chroma_features(
    audio: np.ndarray,
    sample_rate: int,
    *,
    n_fft: int = 4096,
    hop_length: int = HOP_LENGTH,
) -> np.ndarray:
    if len(audio) < n_fft:
        return np.empty((12, 0), dtype=np.float32)
    _, _, spectrum = stft(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop_length,
        nfft=n_fft,
        boundary=None,
        padded=False,
    )
    magnitude = np.abs(spectrum).astype(np.float32)
    frequencies = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    valid = (frequencies >= 65.0) & (frequencies <= 2100.0)
    frequencies = frequencies[valid]
    magnitude = magnitude[valid]
    if not magnitude.size:
        return np.empty((12, 0), dtype=np.float32)
    midi = np.rint(69 + 12 * np.log2(frequencies / 440.0)).astype(int)
    chroma_index = np.mod(midi, 12)
    chroma = np.zeros((12, magnitude.shape[1]), dtype=np.float32)
    for index in range(12):
        mask = chroma_index == index
        if np.any(mask):
            chroma[index] = magnitude[mask].sum(axis=0)
    return _normalize_chroma(chroma)


def chroma_xcorr(song: np.ndarray, clip: np.ndarray) -> tuple[int, float]:
    candidates = chroma_xcorr_candidates(song, clip)
    return candidates[0] if candidates else (0, 0.0)


def chroma_xcorr_candidates(
    song: np.ndarray,
    clip: np.ndarray,
    *,
    max_candidates: int = 1,
    minimum_separation_frames: int = 1,
) -> list[tuple[int, float]]:
    song_frames = song.shape[1]
    clip_frames = clip.shape[1]
    if song_frames < clip_frames or clip_frames < 4:
        return []
    correlations = np.zeros(song_frames - clip_frames + 1, dtype=np.float32)
    for index in range(12):
        correlations += np.correlate(song[index], clip[index], mode="valid")
    correlations /= clip_frames
    candidates: list[tuple[int, float]] = []
    available = correlations.copy()
    separation = max(1, int(minimum_separation_frames))
    for _ in range(max(1, int(max_candidates))):
        lag = int(available.argmax())
        score = float(available[lag])
        if not np.isfinite(score):
            break
        candidates.append((lag, score))
        first = max(0, lag - separation)
        last = min(len(available), lag + separation + 1)
        available[first:last] = -np.inf
    return candidates


def build_alignment_windows(
    duration: float,
    *,
    window_duration: float = 15.0,
    max_windows: int = 5,
) -> list[tuple[float, float]]:
    if duration <= 3:
        return []
    if duration <= window_duration:
        return [(0.0, duration)]
    latest = duration - window_duration
    starts: list[float] = []
    if max_windows <= 5:
        candidates = [duration * value for value in (0.0, 0.20, 0.42, 0.64, 0.82)]
    else:
        candidates = np.linspace(0.0, latest, max_windows).tolist()
    for candidate in candidates:
        start = min(latest, max(0.0, candidate))
        if not any(abs(start - old) < window_duration * 0.45 for old in starts):
            starts.append(start)
        if len(starts) >= max_windows:
            break
    return [(round(start, 3), window_duration) for start in starts]


def _cluster_matches(
    matches: Iterable[WindowMatch],
    *,
    tolerance: float = 1.5,
) -> list[WindowMatch]:
    ordered = sorted(matches, key=lambda item: item.score, reverse=True)
    best_cluster: list[WindowMatch] = []
    best_value = -math.inf
    for anchor in ordered:
        nearby = [
            item
            for item in ordered
            if abs(item.relation - anchor.relation) <= tolerance
        ]
        # Multiple lag candidates from one video window must count as one vote.
        cluster_by_window: dict[float, WindowMatch] = {}
        for item in nearby:
            old = cluster_by_window.get(item.video_start)
            if old is None or item.score > old.score:
                cluster_by_window[item.video_start] = item
        cluster = list(cluster_by_window.values())
        value = len(cluster) * 0.25 + sum(max(0.0, item.score) for item in cluster)
        if value > best_value:
            best_cluster = cluster
            best_value = value
    return best_cluster


def summarize_matches(
    matches: Iterable[WindowMatch],
    *,
    minimum_window_score: float = 0.18,
    cluster_tolerance: float = 1.5,
) -> AlignmentResult:
    valid = [item for item in matches if item.score >= minimum_window_score]
    if not valid:
        return AlignmentResult(None, 0.0, None, 0, "alignment_failed", "no reliable window match")
    cluster = _cluster_matches(valid, tolerance=cluster_tolerance)
    weights = np.array([max(item.score, 0.05) for item in cluster], dtype=np.float64)
    relations = np.array([item.relation for item in cluster], dtype=np.float64)
    relation = float(np.average(relations, weights=weights))
    base_score = float(np.average([item.score for item in cluster], weights=weights))
    agreement_bonus = min(0.16, max(0, len(cluster) - 1) * 0.08)
    score = min(1.0, base_score + agreement_bonus)
    if score >= READY_SCORE and (len(cluster) >= 2 or base_score >= 0.52):
        status = "ready_for_review"
    elif score >= REVIEW_SCORE:
        status = "needs_review"
    else:
        status = "alignment_failed"
    song_offset = max(0.0, relation)
    video_audio_start = max(0.0, -relation)
    return AlignmentResult(
        round(song_offset, 3),
        round(video_audio_start, 3),
        round(score, 3),
        len(cluster),
        status,
    )


def align_video_to_song(
    video_path: Path,
    song_path: Path,
    *,
    max_video_duration: float = 90.0,
    max_song_duration: float = 600.0,
    window_duration: float = 15.0,
    max_windows: int = 5,
    sample_rate: int = SAMPLE_RATE,
    hop_length: int = HOP_LENGTH,
    candidate_count: int = 1,
    candidate_separation: float = 4.0,
    minimum_window_score: float = 0.18,
    cluster_tolerance: float = 1.5,
) -> AlignmentResult:
    try:
        video_audio, video_rate = _decode_audio(
            video_path,
            duration=max_video_duration,
            sample_rate=sample_rate,
        )
        song_audio, song_rate = _decode_audio(
            song_path,
            duration=max_song_duration,
            sample_rate=sample_rate,
        )
        if video_rate != song_rate:
            raise RuntimeError("decoded sample rates do not match")
        song_chroma = chroma_features(
            song_audio,
            song_rate,
            hop_length=hop_length,
        )
        video_duration = len(video_audio) / float(video_rate)
        matches: list[WindowMatch] = []
        for start, duration in build_alignment_windows(
            video_duration,
            window_duration=window_duration,
            max_windows=max_windows,
        ):
            first = int(start * video_rate)
            last = min(len(video_audio), int((start + duration) * video_rate))
            window = video_audio[first:last]
            if len(window) < video_rate * 3:
                continue
            clip_chroma = chroma_features(
                window,
                video_rate,
                hop_length=hop_length,
            )
            separation_frames = max(
                1,
                int(candidate_separation * video_rate / hop_length),
            )
            for lag, score in chroma_xcorr_candidates(
                song_chroma,
                clip_chroma,
                max_candidates=candidate_count,
                minimum_separation_frames=separation_frames,
            ):
                relation = lag * hop_length / float(video_rate) - start
                matches.append(WindowMatch(start, relation, score))
        return summarize_matches(
            matches,
            minimum_window_score=minimum_window_score,
            cluster_tolerance=cluster_tolerance,
        )
    except Exception as exc:
        return AlignmentResult(
            None,
            0.0,
            None,
            0,
            "alignment_failed",
            f"{type(exc).__name__}: {exc}",
        )
