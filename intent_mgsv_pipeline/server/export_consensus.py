from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.annotation_collab.merge_annotators import (
    build_multi_consensus,
)
from intent_mgsv_pipeline.server.db import DEFAULT_DB
from intent_mgsv_pipeline.server.export_db_to_excel import (
    load_annotation_dataframe,
)


def export_consensus(
    db_path: Path,
    out_path: Path,
    *,
    owner_id: str = "owner",
    peer_id: str = "annotator_b",
    peer_ids: list[str] | None = None,
) -> dict[str, object]:
    selected_peer_ids = list(dict.fromkeys(peer_ids or [peer_id]))
    annotator_ids = [owner_id, *selected_peer_ids]
    frames: list[pd.DataFrame] = []
    for annotator_id in annotator_ids:
        frame = load_annotation_dataframe(db_path, annotator_id)
        if frame.empty:
            raise RuntimeError(f"Annotations are empty for {annotator_id}.")
        frame = frame[frame["annotation_status"] == "completed"].copy()
        frame["video_id"] = frame["video_id"].astype(str)
        frame = frame.drop_duplicates("video_id", keep="last")
        frames.append(frame)

    common_ids = list(frames[0]["video_id"])
    for frame in frames[1:]:
        available = set(frame["video_id"])
        common_ids = [video_id for video_id in common_ids if video_id in available]
    if not common_ids:
        raise RuntimeError("No videos are completed by every selected annotator.")

    aligned = [
        frame.set_index("video_id").loc[common_ids].reset_index()
        for frame in frames
    ]
    consensus, disagreements = build_multi_consensus(
        aligned,
        annotator_ids,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    consensus.to_excel(out_path, index=False)
    disagreement_path = out_path.with_name(
        f"{out_path.stem}.disagreements.xlsx"
    )
    disagreements.to_excel(disagreement_path, index=False)
    summary: dict[str, object] = {
        "owner_id": owner_id,
        "peer_ids": selected_peer_ids,
        "annotator_ids": annotator_ids,
        "completed_pairs": len(consensus),
        "disagreement_rows": len(disagreements),
        "consensus_path": str(out_path),
        "disagreement_path": str(disagreement_path),
        "rules": {
            "emotion/style/usage_scene": "union",
            "seg_scores_3/seg_scores_5": "per-segment mean across all annotators",
            "vocal_presence/genre": f"kept from {owner_id}",
        },
    }
    out_path.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export consensus annotations directly from the server database."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--out",
        default="outputs/server/MGSV_Master_Dataset.consensus.xlsx",
    )
    parser.add_argument("--owner-id", default="owner")
    parser.add_argument(
        "--peer-id",
        action="append",
        dest="peer_ids",
        help=(
            "Peer annotator ID. Repeat this option for multiple peers. "
            "Default: annotator_b"
        ),
    )
    args = parser.parse_args()
    summary = export_consensus(
        Path(args.db),
        Path(args.out),
        owner_id=args.owner_id,
        peer_ids=args.peer_ids or ["annotator_b"],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
