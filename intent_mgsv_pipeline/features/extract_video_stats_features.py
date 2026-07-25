from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits" / "all.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "features_fast" / "video"


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


def frame_stats(frame_rgb: np.ndarray, prev_gray: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    frame_rgb = cv2.resize(frame_rgb, (160, 160), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    feats = []
    for channel, bins, limit in [(0, 8, 180), (1, 8, 256), (2, 8, 256)]:
        hist = cv2.calcHist([hsv], [channel], None, [bins], [0, limit]).flatten()
        hist = hist / max(float(hist.sum()), 1.0)
        feats.extend(hist.tolist())
    feats.extend([
        float(gray.mean()) / 255.0,
        float(gray.std()) / 255.0,
        float(cv2.Laplacian(gray, cv2.CV_32F).var()) / 10000.0,
    ])
    if prev_gray is None:
        motion = 0.0
    else:
        motion = float(np.mean(np.abs(gray.astype("float32") - prev_gray.astype("float32")))) / 255.0
    feats.append(motion)
    return np.asarray(feats, dtype="float32"), gray


def extract_video(path: Path, num_frames: int) -> dict[str, np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if frame_count <= 0 or fps <= 0:
        raise ValueError(f"bad video metadata: {path}")
    indices = np.linspace(0, max(frame_count - 1, 0), num_frames).round().astype(int)
    wanted = {int(x) for x in indices.tolist()}
    last = max(wanted)
    feats = []
    times = []
    prev_gray = None
    idx = 0
    while idx <= last:
        if idx in wanted:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            feat, prev_gray = frame_stats(frame, prev_gray)
            feats.append(feat)
            times.append(float(idx) / fps)
        else:
            ok = cap.grab()
            if not ok:
                break
        idx += 1
    cap.release()
    if not feats:
        raise ValueError(f"no frames decoded: {path}")
    return {
        "feats": np.stack(feats, axis=0).astype("float32"),
        "times": np.asarray(times, dtype="float32"),
        "duration": np.asarray([frame_count / fps], dtype="float32"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default=str(DEFAULT_SPLIT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--num-frames", type=int, default=32)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    df = pd.read_csv(args.split, keep_default_na=False)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
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
                data = extract_video(video_path, args.num_frames)
                np.savez_compressed(out_path, **data)
                ok += 1
            except Exception as e:
                failed += 1
                rows.append({"feature_id": fid, "path": key_source, "status": "failed", "error": str(e)})
                print(f"video stats feature failed: {key_source} -> {e}")
                continue
        rows.append({"feature_id": fid, "path": key_source, "feature_path": str(out_path), "status": "ok"})

    manifest = pd.DataFrame(rows)
    manifest.to_csv(out_dir / "manifest.csv", index=False, encoding="utf-8-sig")
    summary = {"input": str(args.split), "out_dir": str(out_dir), "ok": ok, "failed": failed, "rows": len(manifest)}
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
