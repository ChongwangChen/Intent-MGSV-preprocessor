from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = PROJECT_ROOT / "DouK-Source" / "Volume" / "settings.json"
DEFAULT_PROXY = "socks5://127.0.0.1:10808"
ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h"}


def validate_proxy(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            "Proxy scheme must be http, https, socks5, or socks5h"
        )
    if not parsed.hostname or parsed.port is None:
        raise ValueError("Proxy must include a host and port")
    return value


def update_proxy(
    settings_path: Path,
    *,
    proxy: str | None,
    apply: bool,
) -> dict[str, object]:
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    previous = str(settings.get("proxy", "") or "")
    current = previous
    if apply:
        current = validate_proxy(proxy) if proxy else ""
        settings["proxy"] = current
        temp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(settings_path)
    return {
        "settings": str(settings_path.resolve()),
        "previous_proxy": previous,
        "proxy": current,
        "enabled": bool(current),
        "changed": apply and previous != current,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enable, disable, or inspect the DouK proxy setting."
    )
    parser.add_argument(
        "action",
        choices=("enable", "disable", "status"),
    )
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    proxy = args.proxy if args.action == "enable" else None
    result = update_proxy(
        Path(args.settings),
        proxy=proxy,
        apply=args.action != "status",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
