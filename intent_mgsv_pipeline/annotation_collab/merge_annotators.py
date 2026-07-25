from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_A = PROJECT_ROOT / "outputs" / "MGSV_Master_Dataset.xlsx"
DEFAULT_B = PROJECT_ROOT / "outputs" / "inter_annotator" / "annotator_b_completed.xlsx"
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "inter_annotator" / "MGSV_Master_Dataset.consensus.xlsx"

UNION_LABEL_COLUMNS = ["emotion", "style", "usage_scene"]
SEG_SCORE_COLUMNS = ["seg_scores_3", "seg_scores_5"]


def _blank(value: Any) -> bool:
    text = str(value or "").strip()
    return text == "" or text.lower() in {"nan", "none", "null", "unmarked"}


def split_labels(value: Any) -> list[str]:
    if _blank(value):
        return []
    seen = []
    for part in str(value).replace(",", "/").split("/"):
        label = part.strip()
        if label and label not in seen:
            seen.append(label)
    return seen


def union_labels(a: Any, b: Any) -> str:
    labels = []
    for value in (a, b):
        for label in split_labels(value):
            if label not in labels:
                labels.append(label)
    return "/".join(labels)


def parse_scores(value: Any) -> list[float | None]:
    text = str(value or "").strip()
    if not text or text.upper() in {"NONE", "NAN"}:
        return []
    out = []
    for part in text.replace(",", "/").split("/"):
        part = part.strip()
        if not part or part == "-":
            out.append(None)
            continue
        try:
            out.append(float(part))
        except Exception:
            out.append(None)
    return out


def merge_scores(a: Any, b: Any) -> str:
    aa = parse_scores(a)
    bb = parse_scores(b)
    n = max(len(aa), len(bb))
    merged = []
    for i in range(n):
        vals = []
        if i < len(aa) and aa[i] is not None:
            vals.append(float(aa[i]))
        if i < len(bb) and bb[i] is not None:
            vals.append(float(bb[i]))
        if not vals:
            merged.append("-")
        else:
            avg = sum(vals) / len(vals)
            merged.append(str(round(avg, 2)).rstrip("0").rstrip("."))
    return "/".join(merged)


def read_pair(a_path: Path, b_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    a = pd.read_excel(a_path, keep_default_na=False)
    b = pd.read_excel(b_path, keep_default_na=False)
    if "source_row_id" in b.columns:
        b = b.sort_values("source_row_id").reset_index(drop=True)
    if len(a) != len(b):
        raise ValueError(f"Row count mismatch: annotator A={len(a)}, annotator B={len(b)}")
    return a, b


def build_consensus(a: pd.DataFrame, b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = a.copy()
    disagreements = []

    for idx in range(len(out)):
        row_diff = {"row": idx, "video_id": str(a.at[idx, "video_id"]) if "video_id" in a.columns else str(idx)}

        for col in UNION_LABEL_COLUMNS:
            if col not in out.columns or col not in b.columns:
                continue
            av = a.at[idx, col]
            bv = b.at[idx, col]
            merged = union_labels(av, bv)
            out.at[idx, col] = merged
            if set(split_labels(av)) != set(split_labels(bv)):
                row_diff[f"{col}_a"] = av
                row_diff[f"{col}_b"] = bv
                row_diff[f"{col}_consensus"] = merged

        for col in SEG_SCORE_COLUMNS:
            if col not in out.columns or col not in b.columns:
                continue
            av = a.at[idx, col]
            bv = b.at[idx, col]
            merged = merge_scores(av, bv)
            out.at[idx, col] = merged
            if parse_scores(av) != parse_scores(bv):
                row_diff[f"{col}_a"] = av
                row_diff[f"{col}_b"] = bv
                row_diff[f"{col}_consensus"] = merged

        if len(row_diff) > 2:
            disagreements.append(row_diff)

    report = pd.DataFrame(disagreements)
    out["annotator_merge_note"] = "emotion/style/usage_scene=union; segment_scores=mean; vocal_presence/genre kept from annotator A"
    return out, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotator-a", default=str(DEFAULT_A))
    parser.add_argument("--annotator-b", default=str(DEFAULT_B))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    a_path = Path(args.annotator_a)
    b_path = Path(args.annotator_b)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    a, b = read_pair(a_path, b_path)
    consensus, report = build_consensus(a, b)
    consensus.to_excel(out_path, index=False)
    report_path = out_path.with_name(out_path.stem + ".disagreements.xlsx")
    report.to_excel(report_path, index=False)

    summary = {
        "annotator_a": str(a_path),
        "annotator_b": str(b_path),
        "out": str(out_path),
        "rows": int(len(consensus)),
        "disagreement_rows": int(len(report)),
        "rules": {
            "emotion/style/usage_scene": "union",
            "vocal_presence/genre": "kept from annotator A",
            "seg_scores_3/seg_scores_5": "per-segment mean",
        },
    }
    summary_path = out_path.with_suffix(".summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
