from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = (
    PROJECT_ROOT
    / "DouK-Source"
    / "src"
    / "application"
    / "main_terminal.py"
)

ORIGINAL = """        detail_data = [
            await self.handle_detail_single(
                processor,
                cookie,
                proxy,
                i,
            )
            for i in ids
        ]
"""

PATCHED = """        effective_proxy = proxy
        if effective_proxy is None:
            effective_proxy = (
                self.parameter.proxy_tiktok
                if tiktok
                else self.parameter.proxy
            )
        detail_data = [
            await self.handle_detail_single(
                processor,
                cookie,
                effective_proxy,
                i,
            )
            for i in ids
        ]
"""


def patch_source(source: str) -> tuple[str, str]:
    if PATCHED in source:
        return source, "already_patched"
    if ORIGINAL not in source:
        raise RuntimeError(
            "Expected DouK detail routing block was not found; "
            "the upstream source may have changed."
        )
    return source.replace(ORIGINAL, PATCHED, 1), "patched"


def patch_file(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    updated, status = patch_source(source)
    if status == "patched":
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(updated, encoding="utf-8")
        temp_path.replace(path)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Make DouK pass its configured proxy explicitly to detail requests."
        )
    )
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    args = parser.parse_args()
    target = Path(args.target)
    status = patch_file(target)
    print(f"{status}: {target.resolve()}")


if __name__ == "__main__":
    main()
