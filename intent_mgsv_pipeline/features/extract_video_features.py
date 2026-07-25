from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import torch
from torchvision import models, transforms


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits" / "all.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "features" / "video"


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def resolve_path(value: str) -> Path | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    p = Path(text)
    if p.is_file():
        return p
    p = PROJECT_ROOT / text
    return p if p.is_file() else None


def build_model(device: torch.device) -> torch.nn.Module:
    model = models.resnet18(weights=None)
    model.fc = torch.nn.Identity()
    model.to(device)
    model.eval()
    return model


def sample_frames(video_path: Path, num_frames: int) -> tuple[list[np.ndarray], np.ndarray, float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if frame_count <= 0 or fps <= 0:
        raise ValueError(f"bad video metadata: {video_path}")
    indices = np.linspace(0, max(frame_count - 1, 0), num_frames).round().astype(int)
    frames: list[np.ndarray] = []
    times = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frames.append(frame)
        times.append(float(idx) / fps)
    cap.release()
    if not frames:
        raise ValueError(f"no frames decoded: {video_path}")
    duration = frame_count / fps
    return frames, np.asarray(times, dtype="float32"), duration


def extract_video(video_path: Path, model: torch.nn.Module, device: torch.device, num_frames: int, batch_size: int):
    tfm = transforms.Compose([
        transforms.ToPILImage(),
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    frames, times, duration = sample_frames(video_path, num_frames)
    tensors = torch.stack([tfm(frame) for frame in frames], dim=0)
    feats = []
    with torch.no_grad():
        for start in range(0, len(tensors), batch_size):
            batch = tensors[start:start + batch_size].to(device)
            feats.append(model(batch).cpu().numpy())
    feat = np.concatenate(feats, axis=0).astype("float32")
    return {
        "feats": feat,
        "times": times[: feat.shape[0]],
        "duration": np.asarray([duration], dtype="float32"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default=str(DEFAULT_SPLIT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--num-frames", type=int, default=32)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    df = pd.read_csv(args.split, keep_default_na=False)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)
    model = build_model(device)

    unique = df.drop_duplicates(subset=["video_path"]).reset_index(drop=True)
    if args.limit:
        unique = unique.head(args.limit)

    rows = []
    ok = 0
    failed = 0
    for _, row in unique.iterrows():
        video_path = resolve_path(row.get("video_path", ""))
        key_source = str(row.get("video_path", "")) or str(row.get("video_id", ""))
        fid = stable_id(key_source)
        out_path = out_dir / f"{fid}.npz"
        if out_path.exists() and not args.overwrite:
            ok += 1
        else:
            try:
                if video_path is None:
                    raise FileNotFoundError(key_source)
                data = extract_video(video_path, model, device, args.num_frames, args.batch_size)
                np.savez_compressed(out_path, **data)
                ok += 1
            except Exception as e:
                failed += 1
                rows.append({"feature_id": fid, "path": key_source, "status": "failed", "error": str(e)})
                print(f"video feature failed: {key_source} -> {e}")
                continue
        rows.append({"feature_id": fid, "path": key_source, "feature_path": str(out_path), "status": "ok"})

    manifest = pd.DataFrame(rows)
    manifest_path = out_dir / "manifest.csv"
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    summary = {"input": str(args.split), "out_dir": str(out_dir), "ok": ok, "failed": failed, "rows": len(manifest)}
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
