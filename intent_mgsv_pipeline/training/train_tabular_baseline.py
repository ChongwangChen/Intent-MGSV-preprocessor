from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from intent_mgsv_pipeline.features.tabular_features import TabularFeatureEncoder, load_split_csv
from intent_mgsv_pipeline.metrics.evaluate_intent_mgsv_metrics import evaluate
from intent_mgsv_pipeline.models.tabular_baseline import TabularGroundingMLP, interval_loss


DEFAULT_SPLIT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "experiments" / "tabular_baseline"


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def make_loader(x: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool) -> DataLoader:
    dataset = TensorDataset(torch.from_numpy(x), torch.from_numpy(y))
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


def run_epoch(model, loader, optimizer=None, device="cpu") -> float:
    train = optimizer is not None
    model.train(train)
    losses = []
    for x, y in loader:
        x = x.to(device)
        y = y.to(device)
        with torch.set_grad_enabled(train):
            pred = model(x)
            loss = interval_loss(pred, y)
        if train:
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        losses.append(float(loss.detach().cpu()))
    return float(np.mean(losses)) if losses else 0.0


def predict_dataframe(model, encoder: TabularFeatureEncoder, df: pd.DataFrame, device="cpu") -> pd.DataFrame:
    model.eval()
    x = torch.from_numpy(encoder.transform(df)).to(device)
    preds = []
    with torch.no_grad():
        for start in range(0, len(x), 256):
            preds.append(model(x[start:start + 256]).cpu().numpy())
    pred = np.concatenate(preds, axis=0) if preds else np.zeros((0, 2), dtype=np.float32)
    pred_start, pred_end = encoder.denormalize(pred)
    return pd.DataFrame({
        "video_id": df["video_id"].astype(str).tolist(),
        "pred_music_start": np.round(pred_start, 3),
        "pred_music_end": np.round(pred_end, 3),
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--max-m-duration", type=float, default=400.0)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    set_seed(args.seed)
    split_dir = Path(args.split_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_df = load_split_csv(split_dir / "train.csv")
    val_df = load_split_csv(split_dir / "val.csv")
    test_df = load_split_csv(split_dir / "test.csv")

    encoder = TabularFeatureEncoder.fit(train_df, max_m_duration=args.max_m_duration)
    encoder.config.save(out_dir / "feature_config.json")

    x_train = encoder.transform(train_df)
    y_train = encoder.targets(train_df)
    x_val = encoder.transform(val_df)
    y_val = encoder.targets(val_df)

    device = torch.device(args.device)
    model = TabularGroundingMLP(encoder.dim, hidden_dim=args.hidden_dim).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    train_loader = make_loader(x_train, y_train, args.batch_size, shuffle=True)
    val_loader = make_loader(x_val, y_val, args.batch_size, shuffle=False)

    history = []
    best = {"epoch": -1, "val_loss": float("inf")}
    best_path = out_dir / "best_model.pt"
    for epoch in range(1, args.epochs + 1):
        train_loss = run_epoch(model, train_loader, optimizer=optimizer, device=device)
        val_loss = run_epoch(model, val_loader, optimizer=None, device=device)
        row = {"epoch": epoch, "train_loss": round(train_loss, 6), "val_loss": round(val_loss, 6)}
        history.append(row)
        if val_loss < best["val_loss"]:
            best = {"epoch": epoch, "val_loss": val_loss}
            torch.save({
                "model": model.state_dict(),
                "input_dim": encoder.dim,
                "hidden_dim": args.hidden_dim,
                "feature_config": str(out_dir / "feature_config.json"),
            }, best_path)
        if epoch == 1 or epoch % 10 == 0 or epoch == args.epochs:
            print(json.dumps(row, ensure_ascii=False))

    pd.DataFrame(history).to_csv(out_dir / "history.csv", index=False, encoding="utf-8-sig")

    checkpoint = torch.load(best_path, map_location=device)
    model.load_state_dict(checkpoint["model"])

    summaries = {"best": {"epoch": best["epoch"], "val_loss": round(float(best["val_loss"]), 6)}}
    for split_name, df in [("val", val_df), ("test", test_df)]:
        pred = predict_dataframe(model, encoder, df, device=device)
        pred_path = out_dir / f"{split_name}_predictions.csv"
        pred.to_csv(pred_path, index=False, encoding="utf-8-sig")
        metrics_path = out_dir / f"{split_name}_metrics.csv"
        _, summary = evaluate(split_dir / f"{split_name}.csv", str(pred_path), tolerance=0.15)
        with open(metrics_path.with_suffix(".summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        summaries[split_name] = summary

    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summaries, f, ensure_ascii=False, indent=2)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"experiment_dir: {out_dir}")


if __name__ == "__main__":
    main()
