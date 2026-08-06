from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEYWORDS = PROJECT_ROOT / "config" / "douyin_collection_keywords.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "douyin_link_collection"
DEFAULT_PROFILE_DIR = (
    PROJECT_ROOT / "outputs" / "browser_profiles" / "douyin_collector"
)
DEFAULT_DOUK_METADATA = (
    PROJECT_ROOT / "DouK-Source" / "Volume" / "Data" / "Download.xlsx"
)
LINK_PATTERN = re.compile(r"/(video|note)/(\d+)")


@dataclass(frozen=True)
class KeywordTask:
    keyword: str
    content_type: str
    quota: int


@dataclass(frozen=True)
class CollectedLink:
    url: str
    work_id: str
    content_type: str
    keyword: str
    collected_at: str


def _console(text: Any) -> None:
    value = str(text)
    try:
        print(value, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(
            value.encode(encoding, errors="backslashreplace").decode(encoding),
            flush=True,
        )


def normalize_content_type(value: Any) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "video": "video",
        "videos": "video",
        "\u89c6\u9891": "video",
        "image": "image",
        "images": "image",
        "gallery": "image",
        "note": "image",
        "photo": "image",
        "\u56fe\u7247": "image",
        "\u56fe\u6587": "image",
        "\u56fe\u96c6": "image",
        "all": "all",
        "both": "all",
        "\u5168\u90e8": "all",
        "\u4e0d\u9650": "all",
    }
    return aliases.get(text, "")


def normalize_douyin_url(value: Any) -> tuple[str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text if "://" in text else f"https://www.douyin.com{text}")
    match = LINK_PATTERN.search(parsed.path)
    if not match:
        return None
    kind, work_id = match.groups()
    content_type = "video" if kind == "video" else "image"
    return (
        f"https://www.douyin.com/{kind}/{work_id}",
        work_id,
        content_type,
    )


def load_keyword_tasks(path: Path) -> list[KeywordTask]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Keyword file not found: {path}. Copy the .example.csv file first."
        )
    frame = pd.read_csv(path, keep_default_na=False, encoding="utf-8-sig")
    required = {"keyword", "content_type", "quota"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Keyword file missing columns: {sorted(missing)}")
    tasks: list[KeywordTask] = []
    for row_number, row in frame.iterrows():
        keyword = str(row["keyword"] or "").strip()
        content_type = normalize_content_type(row["content_type"])
        try:
            quota = int(row["quota"])
        except (TypeError, ValueError):
            quota = 0
        if not keyword:
            raise ValueError(f"Row {row_number + 2}: keyword is empty")
        if not content_type:
            raise ValueError(
                f"Row {row_number + 2}: content_type must be video/image/all"
            )
        if quota <= 0:
            raise ValueError(f"Row {row_number + 2}: quota must be positive")
        tasks.append(KeywordTask(keyword, content_type, quota))
    if not tasks:
        raise ValueError("Keyword file contains no tasks")
    return tasks


def _links_from_text_files(output_dir: Path) -> set[str]:
    links: set[str] = set()
    if not output_dir.is_dir():
        return links
    for path in output_dir.rglob("*.txt"):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            normalized = normalize_douyin_url(line)
            if normalized:
                links.add(normalized[0])
    return links


def _links_from_douk_metadata(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        frame = pd.read_excel(path, keep_default_na=False)
    except Exception as exc:
        _console(f"Warning: could not read DouK metadata: {exc}")
        return set()
    url_column = "\u4f5c\u54c1\u94fe\u63a5"
    if url_column not in frame.columns:
        return set()
    links: set[str] = set()
    for value in frame[url_column]:
        normalized = normalize_douyin_url(value)
        if normalized:
            links.add(normalized[0])
    return links


def load_existing_links(output_dir: Path, douk_metadata: Path) -> set[str]:
    return _links_from_text_files(output_dir) | _links_from_douk_metadata(
        douk_metadata
    )


def _matches_type(link_type: str, requested_type: str) -> bool:
    return requested_type == "all" or requested_type == link_type


def _page_links(page: Any) -> list[str]:
    return page.locator("a[href]").evaluate_all(
        "elements => elements.map(element => element.href).filter(Boolean)"
    )


def _captcha_visible(page: Any) -> bool:
    selectors = (
        "text=完成验证",
        "text=安全验证",
        "text=拖动滑块",
        "text=验证码",
    )
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def _open_search(page: Any, keyword: str, timeout_ms: int) -> None:
    search_inputs = (
        'input[data-e2e="searchbar-input"]',
        'input[placeholder*="搜索"]',
        'input[type="search"]',
    )
    for selector in search_inputs:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=1000):
                locator.click()
                locator.fill(keyword)
                locator.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                return
        except Exception:
            continue
    page.goto(
        f"https://www.douyin.com/search/{quote(keyword)}?type=general",
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )


def collect_task(
    page: Any,
    task: KeywordTask,
    excluded_urls: set[str],
    *,
    max_scrolls: int,
    scroll_pause_ms: int,
    timeout_ms: int,
) -> list[CollectedLink]:
    _console(
        f"Searching: {task.keyword} | type={task.content_type} | quota={task.quota}"
    )
    _open_search(page, task.keyword, timeout_ms)
    page.wait_for_timeout(2500)
    collected: list[CollectedLink] = []
    stagnant_scrolls = 0
    for _ in range(max_scrolls):
        if _captcha_visible(page):
            _console("Browser verification detected. Complete it manually, then press Enter.")
            input()
            page.wait_for_timeout(1500)
        before = len(collected)
        for href in _page_links(page):
            normalized = normalize_douyin_url(href)
            if not normalized:
                continue
            url, work_id, content_type = normalized
            if not _matches_type(content_type, task.content_type):
                continue
            if url in excluded_urls:
                continue
            excluded_urls.add(url)
            collected.append(
                CollectedLink(
                    url=url,
                    work_id=work_id,
                    content_type=content_type,
                    keyword=task.keyword,
                    collected_at=datetime.now().isoformat(timespec="seconds"),
                )
            )
            if len(collected) >= task.quota:
                break
        _console(f"  collected {len(collected)}/{task.quota}")
        if len(collected) >= task.quota:
            break
        stagnant_scrolls = stagnant_scrolls + 1 if len(collected) == before else 0
        if stagnant_scrolls >= 8:
            _console("  no new links after 8 scrolls; moving to the next keyword")
            break
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
        page.wait_for_timeout(scroll_pause_ms)
    return collected


def write_collection_outputs(
    records: Iterable[CollectedLink],
    output_dir: Path,
    *,
    batch_size: int,
    session_name: str | None = None,
) -> dict[str, Any]:
    records = list(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = output_dir / session
    session_dir.mkdir(parents=True, exist_ok=True)
    batch_files: list[str] = []
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        path = session_dir / f"dydownload_{start // batch_size + 1:02d}.txt"
        path.write_text(
            "\n".join(record.url for record in batch) + "\n",
            encoding="utf-8",
        )
        batch_files.append(str(path))

    session_csv = session_dir / "collected_links.csv"
    with session_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "url",
                "work_id",
                "content_type",
                "keyword",
                "collected_at",
            ],
        )
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)
    summary = {
        "session_dir": str(session_dir),
        "collected": len(records),
        "batch_size": batch_size,
        "batch_files": batch_files,
        "session_csv": str(session_csv),
        "videos": sum(record.content_type == "video" for record in records),
        "images": sum(record.content_type == "image" for record in records),
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _launch_browser(args: argparse.Namespace, tasks: list[KeywordTask]) -> list[CollectedLink]:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install -r "
            "requirements_collection.txt"
        ) from exc

    excluded_urls = load_existing_links(
        Path(args.output_dir),
        Path(args.douk_metadata),
    )
    _console(f"Existing/DouK links excluded: {len(excluded_urls)}")
    all_records: list[CollectedLink] = []
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "channel": "chrome",
            "headless": False,
            "no_viewport": True,
            "args": ["--start-maximized"],
        }
        if args.chrome_path:
            launch_kwargs.pop("channel", None)
            launch_kwargs["executable_path"] = args.chrome_path
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            "https://www.douyin.com/",
            wait_until="domcontentloaded",
            timeout=args.timeout_ms,
        )
        if not args.skip_login_wait:
            _console(
                "Log in to Douyin in the opened Chrome window. "
                "Complete any verification, then press Enter here."
            )
            input()
        for task in tasks:
            all_records.extend(
                collect_task(
                    page,
                    task,
                    excluded_urls,
                    max_scrolls=args.max_scrolls,
                    scroll_pause_ms=args.scroll_pause_ms,
                    timeout_ms=args.timeout_ms,
                )
            )
        context.close()
    return all_records


def browser_smoke_test(args: argparse.Namespace) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install -r "
            "requirements_collection.txt"
        ) from exc
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "channel": "chrome",
            "headless": False,
            "no_viewport": True,
            "args": ["--start-maximized"],
        }
        if args.chrome_path:
            launch_kwargs.pop("channel", None)
            launch_kwargs["executable_path"] = args.chrome_path
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("about:blank")
        page.wait_for_timeout(1500)
        version = context.browser.version if context.browser else "unknown"
        _console(f"Browser smoke test passed: Chrome {version}")
        context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect canonical Douyin video/note links from a visible, logged-in Chrome browser."
        )
    )
    parser.add_argument("--keywords", default=str(DEFAULT_KEYWORDS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--douk-metadata", default=str(DEFAULT_DOUK_METADATA))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-scrolls", type=int, default=60)
    parser.add_argument("--scroll-pause-ms", type=int, default=1600)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--chrome-path", default="")
    parser.add_argument("--skip-login-wait", action="store_true")
    parser.add_argument("--browser-smoke-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    tasks = load_keyword_tasks(Path(args.keywords))
    _console(
        json.dumps(
            {
                "tasks": [asdict(task) for task in tasks],
                "target": sum(task.quota for task in tasks),
                "batch_size": args.batch_size,
                "expected_batches": (
                    sum(task.quota for task in tasks) + args.batch_size - 1
                )
                // args.batch_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.browser_smoke_test:
        browser_smoke_test(args)
        return
    if args.dry_run:
        return
    records = _launch_browser(args, tasks)
    summary = write_collection_outputs(
        records,
        Path(args.output_dir),
        batch_size=args.batch_size,
    )
    _console(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
