from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "splits" / "all.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "intent_mgsv_dataset" / "features" / "audio"


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


def extract_audio(path: Path, sr: int, hop_length: int, n_mels: int, max_duration: float) -> dict[str, np.ndarray]:
    y, sr = librosa.load(path, sr=sr, mono=True, duration=max_duration)
    if len(y) == 0:
        raise ValueError(f"empty audio: {path}")

    mel = librosa.feature.melspectrogram(
        y=y,
        sr=sr,
        n_mels=n_mels,
        n_fft=2048,
        hop_length=hop_length,
        power=2.0,
    )
    logmel = librosa.power_to_db(mel, ref=np.max).T.astype("float32")
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop_length).T.astype("float32")
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length).astype("float32")[:, None]
    rms = librosa.feature.rms(y=y, hop_length=hop_length).T.astype("float32")
    times = librosa.frames_to_time(np.arange(logmel.shape[0]), sr=sr, hop_length=hop_length).astype("float32")

    n = min(logmel.shape[0], chroma.shape[0], onset.shape[0], rms.shape[0], times.shape[0])
    feats = np.concatenate([logmel[:n], chroma[:n], onset[:n], rms[:n]], axis=1).astype("float32")
    return {
        "feats": feats,
        "times": times[:n],
        "duration": np.asarray([len(y) / float(sr)], dtype="float32"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default=str(DEFAULT_SPLIT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--sr", type=int, default=16000)
    parser.add_argument("--hop-length", type=int, default=512)
    parser.add_argument("--n-mels", type=int, default=64)
    parser.add_argument("--max-duration", type=float, default=400.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    df = pd.read_csv(args.split, keep_default_na=False)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    unique = df.drop_duplicates(subset=["full_song_path"]).reset_index(drop=True)
    if args.limit:
        unique = unique.head(args.limit)

    ok = 0
    failed = 0
    for _, row in unique.iterrows():
        song_path = resolve_path(row.get("full_song_path", ""))
        key_source = str(row.get("full_song_path", "")) or str(row.get("music_id", ""))
        fid = stable_id(key_source)
        out_path = out_dir / f"{fid}.npz"
        if out_path.exists() and not args.overwrite:
            ok += 1
        else:
            try:
                if song_path is None:
                    raise FileNotFoundError(key_source)
                data = extract_audio(song_path, args.sr, args.hop_length, args.n_mels, args.max_duration)
                np.savez_compressed(out_path, **data)
                ok += 1
            except Exception as e:
                failed += 1
                rows.append({"feature_id": fid, "path": key_source, "status": "failed", "error": str(e)})
                print(f"audio feature failed: {key_source} -> {e}")
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
