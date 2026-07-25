from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from intent_mgsv_pipeline.datasets.feature_dataset import IntentMGSVFeatureDataset, feature_collate
from intent_mgsv_pipeline.metrics.evaluate_intent_mgsv_metrics import evaluate
from intent_mgsv_pipeline.models.multimodal_grounding_baseline import (
    MultimodalGroundingBaseline,
    grounding_loss,
)


DEFAULT_SPLIT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits"
DEFAULT_FEATURE_ROOT = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "features"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "experiments" / "multimodal_baseline"


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def to_device(batch: dict, device: torch.device) -> dict:
    out = {}
    for key, value in batch.items():
        out[key] = value.to(device) if torch.is_tensor(value) else value
    return out


def run_epoch(model, loader, optimizer, device, max_m_duration: float) -> float:
    train = optimizer is not None
    model.train(train)
    losses = []
    for batch in loader:
        batch = to_device(batch, device)
        with torch.set_grad_enabled(train):
            out = model(
                batch["video_feats"],
                batch["video_mask"],
                batch["audio_feats"],
                batch["audio_mask"],
                batch["audio_times"],
                max_m_duration=max_m_duration,
            )
            loss = grounding_loss(out["pred"], batch["target"])
        if train:
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        losses.append(float(loss.detach().cpu()))
    return float(np.mean(losses)) if losses else 0.0


def predict(model, loader, device, max_m_duration: float) -> pd.DataFrame:
    model.eval()
    rows = []
    with torch.no_grad():
        for batch in loader:
            batch = to_device(batch, device)
            out = model(
                batch["video_feats"],
                batch["video_mask"],
                batch["audio_feats"],
                batch["audio_mask"],
                batch["audio_times"],
                max_m_duration=max_m_duration,
            )
            pred = out["pred"].detach().cpu().numpy()
            center = np.clip(pred[:, 0], 0.0, 1.0)
            width = np.clip(pred[:, 1], 0.001, 1.0)
            start = np.clip((center - width / 2.0) * max_m_duration, 0.0, max_m_duration)
            end = np.clip((center + width / 2.0) * max_m_duration, start + 0.001, max_m_duration)
            for video_id, s, e in zip(batch["video_id"], start, end):
                rows.append({
                    "video_id": video_id,
                    "pred_music_start": round(float(s), 3),
                    "pred_music_end": round(float(e), 3),
                })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--feature-root", default=str(DEFAULT_FEATURE_ROOT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--max-audio-steps", type=int, default=256)
    parser.add_argument("--max-video-steps", type=int, default=32)
    parser.add_argument("--max-m-duration", type=float, default=400.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    set_seed(args.seed)
    split_dir = Path(args.split_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)

    common = {
        "feature_root": args.feature_root,
        "max_audio_steps": args.max_audio_steps,
        "max_video_steps": args.max_video_steps,
        "max_m_duration": args.max_m_duration,
    }
    train_set = IntentMGSVFeatureDataset(split_dir / "train.csv", **common)
    val_set = IntentMGSVFeatureDataset(split_dir / "val.csv", **common)
    test_set = IntentMGSVFeatureDataset(split_dir / "test.csv", **common)

    sample = train_set[0]
    model = MultimodalGroundingBaseline(
        video_dim=sample["video_feats"].shape[-1],
        audio_dim=sample["audio_feats"].shape[-1],
        hidden_dim=args.hidden_dim,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True, collate_fn=feature_collate)
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False, collate_fn=feature_collate)
    test_loader = DataLoader(test_set, batch_size=args.batch_size, shuffle=False, collate_fn=feature_collate)

    best = {"epoch": -1, "val_loss": float("inf")}
    history = []
    best_path = out_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        train_loss = run_epoch(model, train_loader, optimizer, device, args.max_m_duration)
        val_loss = run_epoch(model, val_loader, None, device, args.max_m_duration)
        row = {"epoch": epoch, "train_loss": round(train_loss, 6), "val_loss": round(val_loss, 6)}
        history.append(row)
        if val_loss < best["val_loss"]:
            best = {"epoch": epoch, "val_loss": val_loss}
            torch.save({"model": model.state_dict(), "args": vars(args)}, best_path)
        if epoch == 1 or epoch % 10 == 0 or epoch == args.epochs:
            print(json.dumps(row, ensure_ascii=False))

    pd.DataFrame(history).to_csv(out_dir / "history.csv", index=False, encoding="utf-8-sig")
    ckpt = torch.load(best_path, map_location=device)
    model.load_state_dict(ckpt["model"])

    summaries = {"best": {"epoch": best["epoch"], "val_loss": round(float(best["val_loss"]), 6)}}
    for split_name, loader in [("val", val_loader), ("test", test_loader)]:
        pred = predict(model, loader, device, args.max_m_duration)
        pred_path = out_dir / f"{split_name}_predictions.csv"
        pred.to_csv(pred_path, index=False, encoding="utf-8-sig")
        _, summary = evaluate(split_dir / f"{split_name}.csv", str(pred_path), tolerance=0.15)
        summaries[split_name] = summary
        with open(out_dir / f"{split_name}_metrics.summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summaries, f, ensure_ascii=False, indent=2)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"experiment_dir: {out_dir}")


if __name__ == "__main__":
    main()
