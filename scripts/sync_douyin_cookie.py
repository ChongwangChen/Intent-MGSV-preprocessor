from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = (
    PROJECT_ROOT / "outputs" / "browser_profiles" / "douyin_collector"
)
DEFAULT_SETTINGS = PROJECT_ROOT / "DouK-Source" / "Volume" / "settings.json"
DEFAULT_EXPORT = (
    PROJECT_ROOT
    / "outputs"
    / "douyin_server_upload"
    / "douyin_cookie_current.json"
)
REQUIRED_COOKIES = {
    "odin_tt",
    "passport_csrf_token",
    "sessionid",
    "sessionid_ss",
    "ttwid",
}


def sid_guard_expiry(cookie: dict[str, str]) -> datetime | None:
    value = unquote(str(cookie.get("sid_guard", "")))
    parts = value.split("|")
    if len(parts) < 3:
        return None
    try:
        issued = int(parts[1])
        lifetime = int(parts[2])
    except ValueError:
        return None
    return datetime.fromtimestamp(issued + lifetime, tz=timezone.utc)


def cookie_summary(cookie: dict[str, str]) -> dict[str, object]:
    expiry = sid_guard_expiry(cookie)
    now = datetime.now(timezone.utc)
    return {
        "cookie_count": len(cookie),
        "required_present": sorted(REQUIRED_COOKIES & set(cookie)),
        "required_missing": sorted(REQUIRED_COOKIES - set(cookie)),
        "sid_guard_expires_at": expiry.isoformat() if expiry else "",
        "sid_guard_expired": expiry <= now if expiry else None,
    }


def export_browser_cookie(
    profile_dir: Path,
    output_path: Path,
    *,
    chrome_path: str = "",
) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is required in the local environment."
        ) from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(profile_dir),
            "channel": "chrome",
            "headless": False,
            "no_viewport": True,
            "args": ["--start-maximized"],
        }
        if chrome_path:
            launch_kwargs.pop("channel", None)
            launch_kwargs["executable_path"] = chrome_path
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                "https://www.douyin.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1500)
            raw_cookies = context.cookies(
                ["https://www.douyin.com/", "https://www.douyin.com/jingxuan"]
            )
            cookie = {
                item["name"]: item["value"]
                for item in raw_cookies
                if str(item.get("domain", "")).endswith("douyin.com")
            }
            user_agent = str(
                page.evaluate("() => navigator.userAgent") or ""
            )
            platform = str(
                page.evaluate("() => navigator.platform") or "Win32"
            )
        finally:
            context.close()

    chrome_match = re.search(r"Chrome/([\d.]+)", user_agent)
    chrome_version = chrome_match.group(1) if chrome_match else ""
    payload: dict[str, object] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_profile": str(profile_dir),
        "cookie": cookie,
        "browser_info": {
            "User-Agent": user_agent,
            "browser_platform": platform,
            "browser_name": "Chrome",
            "browser_version": chrome_version,
            "engine_name": "Blink",
            "engine_version": chrome_version,
            "os_name": "Windows",
            "os_version": "10",
            "pc_libra_divert": "Windows",
            "browser_language": "zh-CN",
        },
        "summary": cookie_summary(cookie),
    }
    missing = payload["summary"]["required_missing"]
    if missing:
        raise RuntimeError(
            f"Browser is missing required Douyin login cookies: {missing}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload["summary"]


def apply_cookie_file(cookie_file: Path, settings_path: Path) -> dict[str, object]:
    payload = json.loads(cookie_file.read_text(encoding="utf-8"))
    cookie = payload.get("cookie")
    if not isinstance(cookie, dict) or not cookie:
        raise ValueError("Cookie payload is empty or invalid")
    summary = cookie_summary(
        {str(key): str(value) for key, value in cookie.items()}
    )
    if summary["required_missing"]:
        raise ValueError(
            f"Cookie payload is missing: {summary['required_missing']}"
        )
    if summary["sid_guard_expired"] is True:
        raise ValueError("Cookie payload sid_guard is already expired")

    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    settings["cookie"] = cookie
    browser_info = payload.get("browser_info")
    if isinstance(browser_info, dict):
        current = settings.get("browser_info")
        if not isinstance(current, dict):
            current = {}
        current.update(
            {
                str(key): str(value)
                for key, value in browser_info.items()
                if value
            }
        )
        settings["browser_info"] = current

    temp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(settings_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a logged-in Douyin browser cookie or apply it to DouK."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-browser")
    export_parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE))
    export_parser.add_argument("--out", default=str(DEFAULT_EXPORT))
    export_parser.add_argument("--chrome-path", default="")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--cookie-file", required=True)
    apply_parser.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "export-browser":
        summary = export_browser_cookie(
            Path(args.profile_dir),
            Path(args.out),
            chrome_path=args.chrome_path,
        )
    else:
        summary = apply_cookie_file(
            Path(args.cookie_file),
            Path(args.settings),
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
