"""Preflight checks for the auto.py preprocessing environment."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FAILED = []


def ok(label: str, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    print(f"[OK] {label}{suffix}")


def fail(label: str, detail: str) -> None:
    FAILED.append(label)
    print(f"[FAIL] {label}: {detail}")


def package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def check_import(label: str, module: str, distribution: str | None = None) -> None:
    try:
        imported = importlib.import_module(module)
        version = getattr(imported, "__version__", "")
        if not version and distribution:
            version = package_version(distribution)
        ok(label, str(version) if version else "imported")
    except Exception as exc:
        fail(label, f"{type(exc).__name__}: {exc}")


def check_command(name: str) -> None:
    executable = shutil.which(name)
    if executable:
        ok(name, executable)
    else:
        fail(name, "system command not found on PATH")


def check_file(label: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size > 0:
        ok(label, str(path))
    else:
        fail(label, f"missing or empty: {path}")


def main() -> int:
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info[:2] == (3, 10):
        ok("Python version", "3.10")
    else:
        fail("Python version", "auto.py is validated with Python 3.10")

    required = [
        ("NumPy", "numpy", "numpy"),
        ("SciPy", "scipy", "scipy"),
        ("pandas", "pandas", "pandas"),
        ("openpyxl", "openpyxl", "openpyxl"),
        ("librosa", "librosa", "librosa"),
        ("OpenCV", "cv2", "opencv-python-headless"),
        ("requests", "requests", "requests"),
        ("ffmpeg-python", "ffmpeg", "ffmpeg-python"),
        ("TensorFlow", "tensorflow", "tensorflow"),
        ("PyTorch", "torch", "torch"),
        ("madmom", "madmom", "madmom"),
    ]
    for label, module, distribution in required:
        check_import(label, module, distribution)

    check_import(
        "BeatNet offline loader",
        "intent_mgsv_pipeline.preprocessing.beatnet_compat",
        "BeatNet",
    )

    check_command("ffmpeg")
    check_command("ffprobe")

    weights = ROOT / "transnetv2-weights"
    check_file("TransNetV2 SavedModel", weights / "saved_model.pb")
    check_file(
        "TransNetV2 variables index",
        weights / "variables" / "variables.index",
    )
    check_file(
        "TransNetV2 variables data",
        weights / "variables" / "variables.data-00000-of-00001",
    )

    if FAILED:
        print("\nPreflight failed: " + ", ".join(FAILED))
        print("Install requirements_auto.txt and restore the model weights, then retry.")
        return 1

    print("\nPreflight passed. auto.py has all mandatory runtime dependencies.")
    print(
        "transformers/open_clip_torch/Pillow are optional and are not needed by "
        "the current main preprocessing flow."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
