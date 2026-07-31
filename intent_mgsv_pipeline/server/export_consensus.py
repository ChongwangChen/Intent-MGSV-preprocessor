from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from intent_mgsv_pipeline.annotation_collab.merge_annotators import (
    build_consensus,
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
) -> dict[str, object]:
    owner = load_annotation_dataframe(db_path, owner_id)
    peer = load_annotation_dataframe(db_path, peer_id)
    if owner.empty or peer.empty:
        raise RuntimeError("Owner or peer annotations are empty.")

    peer = peer[peer["annotation_status"] == "completed"].copy()
    owner = owner[owner["annotation_status"] == "completed"].copy()
    peer_ids = set(peer["video_id"].astype(str))
    common_ids = [
        video_id
        for video_id in owner["video_id"].astype(str)
        if video_id in peer_ids
    ]
    if not common_ids:
        raise RuntimeError("No videos are completed by both annotators.")

    owner = (
        owner.assign(video_id=owner["video_id"].astype(str))
        .drop_duplicates("video_id", keep="last")
        .set_index("video_id")
        .loc[common_ids]
        .reset_index()
    )
    peer = (
        peer.assign(video_id=peer["video_id"].astype(str))
        .drop_duplicates("video_id", keep="last")
        .set_index("video_id")
        .loc[common_ids]
        .reset_index()
    )
    consensus, disagreements = build_consensus(owner, peer)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    consensus.to_excel(out_path, index=False)
    disagreement_path = out_path.with_name(
        f"{out_path.stem}.disagreements.xlsx"
    )
    disagreements.to_excel(disagreement_path, index=False)
    summary: dict[str, object] = {
        "owner_id": owner_id,
        "peer_id": peer_id,
        "completed_pairs": len(consensus),
        "disagreement_rows": len(disagreements),
        "consensus_path": str(out_path),
        "disagreement_path": str(disagreement_path),
        "rules": {
            "emotion/style/usage_scene": "union",
            "seg_scores_3/seg_scores_5": "per-segment mean",
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
    parser.add_argument("--peer-id", default="annotator_b")
    args = parser.parse_args()
    summary = export_consensus(
        Path(args.db),
        Path(args.out),
        owner_id=args.owner_id,
        peer_id=args.peer_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
