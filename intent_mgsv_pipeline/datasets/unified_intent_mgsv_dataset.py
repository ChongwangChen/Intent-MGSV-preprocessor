from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from intent_mgsv_pipeline.datasets.intent_mgsv_dataset import (
    parse_multi_label,
    parse_points,
    parse_scores,
)
from intent_mgsv_pipeline.schema.unified_sample_schema import (
    ValidationIssue,
    canonicalize_record,
    validate_record,
)


class DatasetValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        preview = "; ".join(
            f"{issue.sample_id}:{issue.field}:{issue.code}"
            for issue in issues[:8]
        )
        suffix = "" if len(issues) <= 8 else f"; ... ({len(issues)} issues)"
        super().__init__(preview + suffix)


def _as_paths(
    manifests: str | Path | Iterable[str | Path],
) -> list[Path]:
    if isinstance(manifests, (str, Path)):
        return [Path(manifests)]
    return [Path(path) for path in manifests]


def _load_manifest(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() in {".xlsx", ".xls"}:
        return pd.read_excel(path, keep_default_na=False)
    return pd.read_csv(path, keep_default_na=False)


def _resolve_path(
    raw_path: str,
    *,
    manifest_path: Path,
    data_root: Path | None,
) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    candidates = []
    if data_root is not None:
        candidates.append(data_root / path)
    candidates.append(manifest_path.parent / path)
    candidates.append(path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "yes",
        "true",
        "1",
        "confirmed",
        "\u662f",
        "\u5df2\u786e\u8ba4",
    }


class UnifiedIntentMGSVRowDataset:
    """
    Semantic row-level dataloader for video, image, and text music grounding.

    It validates manifests and normalizes labels but deliberately does not load
    pixels, frames, text embeddings, or audio features.
    """

    def __init__(
        self,
        manifests: str | Path | Iterable[str | Path],
        *,
        max_m_duration: float = 400.0,
        strict: bool = True,
        require_labels: bool = True,
        validate_files: bool = False,
        drop_invalid: bool = False,
        data_root: str | Path | None = None,
    ):
        self.manifest_paths = _as_paths(manifests)
        self.max_m_duration = float(max_m_duration)
        self.strict = bool(strict)
        self.require_labels = bool(require_labels)
        self.validate_files = bool(validate_files)
        self.drop_invalid = bool(drop_invalid)
        self.data_root = Path(data_root) if data_root is not None else None
        self.records: list[dict[str, Any]] = []
        self.issues: list[ValidationIssue] = []

        if self.max_m_duration <= 0:
            raise ValueError("max_m_duration must be positive")
        for manifest_path in self.manifest_paths:
            if not manifest_path.is_file():
                raise FileNotFoundError(manifest_path)
            frame = _load_manifest(manifest_path)
            for _, row in frame.iterrows():
                record = canonicalize_record(row.to_dict())
                record["source_manifest"] = str(manifest_path)
                record["_manifest_path"] = manifest_path
                issues = validate_record(
                    record,
                    require_labels=self.require_labels,
                )
                music_end = record.get("music_end")
                if (
                    isinstance(music_end, (int, float))
                    and music_end > self.max_m_duration
                ):
                    issues.append(
                        ValidationIssue(
                            record["sample_id"] or "<missing>",
                            "music_end",
                            "music_end_exceeds_max",
                            (
                                f"music_end={music_end} exceeds "
                                f"max_m_duration={self.max_m_duration}."
                            ),
                        )
                    )
                if self.validate_files:
                    issues.extend(self._file_issues(record, manifest_path))
                self.issues.extend(issues)
                if issues and self.drop_invalid:
                    continue
                record["validation_issues"] = issues
                self.records.append(record)

        if self.strict and self.issues:
            raise DatasetValidationError(self.issues)

    def _file_issues(
        self,
        record: dict[str, Any],
        manifest_path: Path,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        sample_id = record["sample_id"] or "<missing>"
        if record["content_type"] in {"video", "image"}:
            content_path = _resolve_path(
                record["content_path"],
                manifest_path=manifest_path,
                data_root=self.data_root,
            )
            if content_path is None or not content_path.is_file():
                issues.append(
                    ValidationIssue(
                        sample_id,
                        "content_path",
                        "content_file_missing",
                        f"Content file not found: {content_path}",
                    )
                )
        song_path = _resolve_path(
            record["full_song_path"],
            manifest_path=manifest_path,
            data_root=self.data_root,
        )
        if song_path is None or not song_path.is_file():
            issues.append(
                ValidationIssue(
                    sample_id,
                    "full_song_path",
                    "song_file_missing",
                    f"Song file not found: {song_path}",
                )
            )
        return issues

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.records[idx]
        manifest_path = row["_manifest_path"]
        music_start = float(row["music_start"])
        music_end = float(row["music_end"])
        center = ((music_start + music_end) / 2.0) / self.max_m_duration
        width = (music_end - music_start) / self.max_m_duration
        content_path = _resolve_path(
            row["content_path"],
            manifest_path=manifest_path,
            data_root=self.data_root,
        )
        song_path = _resolve_path(
            row["full_song_path"],
            manifest_path=manifest_path,
            data_root=self.data_root,
        )
        return {
            "ids": {
                "sample_id": row["sample_id"],
                "content_id": row["content_id"],
                "music_id": row["music_id"],
                "video_id": row["video_id"],
                "image_id": row["image_id"],
                "text_id": row["text_id"],
                "group_id": row["group_id"],
            },
            "modality": row["content_type"],
            "content": {
                "type": row["content_type"],
                "path": str(content_path) if content_path else "",
                "text": row["content_text"],
                "duration": float(row["content_duration"]),
            },
            "music": {
                "path": str(song_path) if song_path else "",
                "title": row["song_title"],
                "artist": row["song_artist"],
                "genre": row["genre"],
                "vocal_presence": row["vocal_presence"],
            },
            "grounding": {
                "music_start": music_start,
                "music_end": music_end,
                "target_center_width": [center, width],
            },
            "rhythm": {
                "sync_level": int(row["sync_level"]),
                "shot_points": parse_points(row["shot_points"]),
                "shot_points_3": parse_points(row["shot_points_3"]),
                "shot_points_5": parse_points(row["shot_points_5"]),
                "seg_scores_3": parse_scores(row["seg_scores_3"]),
                "seg_scores_5": parse_scores(row["seg_scores_5"]),
            },
            "intent": {
                "emotion": parse_multi_label(row["emotion"]),
                "style": parse_multi_label(row["style"]),
                "usage_scene": parse_multi_label(row["usage_scene"]),
            },
            "compatibility": {
                "overall_score": row["overall_score"],
                "pair_verified": _is_yes(row["pair_verified"]),
            },
            "provenance": {
                "source": row["source"],
                "source_url": row["source_url"],
                "annotator_id": row["annotator_id"],
                "annotation_status": row["annotation_status"],
                "source_manifest": row["source_manifest"],
                "split": row["split"],
            },
            "validation_issues": [
                {
                    "field": issue.field,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in row["validation_issues"]
            ],
        }


def unified_row_collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sample_ids": [item["ids"]["sample_id"] for item in batch],
        "modalities": [item["modality"] for item in batch],
        "items": batch,
    }
