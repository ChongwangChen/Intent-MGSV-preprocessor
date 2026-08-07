from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, quote, urlparse

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
SHARED_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
MODAL_ID_PATTERN = re.compile(r"^\d{10,}$")
DURATION_PATTERN = re.compile(r"(?<!\d)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?!\d)")
NON_TARGET_PATTERNS = (
    (
        re.compile(r"(?:^|[#\s])(清唱|无伴奏|纯人声)(?:$|[#\s，。！？])"),
        "明确标注为清唱/无伴奏",
    ),
    (
        re.compile(r"(?:^|[#\s])(翻唱|现场演唱|唱歌给你听)(?:$|[#\s，。！？])"),
        "明确标注为纯唱歌/翻唱",
    ),
    (
        re.compile(
            r"(钢琴|吉他|古筝|架子鼓|小提琴|萨克斯|二胡|琵琶|笛子)"
            r".{0,8}(演奏|弹奏|独奏|弹唱|翻奏)"
        ),
        "明确标注为乐器演奏",
    ),
    (
        re.compile(
            r"(演奏|弹奏|独奏|弹唱|翻奏).{0,8}"
            r"(钢琴|吉他|古筝|架子鼓|小提琴|萨克斯|二胡|琵琶|笛子)"
        ),
        "明确标注为乐器演奏",
    ),
)


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
    candidates = SHARED_URL_PATTERN.findall(text) or [text]
    for candidate in candidates:
        candidate = candidate.rstrip(".,;!?，。；！？)]}）】")
        parsed = urlparse(
            candidate if "://" in candidate else f"https://www.douyin.com{candidate}"
        )
        match = LINK_PATTERN.search(parsed.path)
        if not match:
            continue
        kind, work_id = match.groups()
        content_type = "video" if kind == "video" else "image"
        return (
            f"https://www.douyin.com/{kind}/{work_id}",
            work_id,
            content_type,
        )
    return None


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


def _duration_seconds_from_card_text(text: Any) -> int | None:
    match = DURATION_PATTERN.search(str(text or ""))
    if not match:
        return None
    hours_text, minutes_text, seconds_text = match.groups()
    hours = int(hours_text or 0)
    minutes = int(minutes_text)
    seconds = int(seconds_text)
    if minutes >= 60 and not hours_text:
        return None
    if seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _non_target_reason(text: Any, max_duration_seconds: int) -> str:
    card_text = str(text or "").strip()
    duration = _duration_seconds_from_card_text(card_text)
    if duration is not None and duration > max_duration_seconds:
        return f"时长 {duration // 60}:{duration % 60:02d} 超过限制"
    for pattern, reason in NON_TARGET_PATTERNS:
        if pattern.search(card_text):
            return reason
    return ""


def _modal_work_id(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("modal_id", [])
    if not values or not MODAL_ID_PATTERN.fullmatch(values[0]):
        return ""
    return values[0]


def _canonical_from_modal(url: str, content_type: str) -> tuple[str, str, str] | None:
    work_id = _modal_work_id(url)
    if not work_id or content_type not in {"video", "image"}:
        return None
    kind = "video" if content_type == "video" else "note"
    return f"https://www.douyin.com/{kind}/{work_id}", work_id, content_type


def _visible_card_images(
    page: Any,
    requested_type: str,
) -> list[dict[str, Any]]:
    return page.locator(".videoImage img").evaluate_all(
        """
        (elements, requestedType) => elements
          .map(element => {
            const rect = element.getBoundingClientRect();
            const card = element.closest(".videoImage");
            const cardText = String(card ? card.innerText : "");
            const contentType = cardText.includes("\u56fe\u6587") ? "image" : "video";
            return {
              src: element.currentSrc || element.src || "",
              contentType,
              text: cardText,
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
            };
          })
          .filter(item =>
            item.src &&
            (requestedType === "all" || item.contentType === requestedType) &&
            item.x >= 150 &&
            item.y >= 90 &&
            item.y + item.height / 2 < window.innerHeight - 10 &&
            item.width >= 150 &&
            item.height >= 100
          )
          .sort((left, right) => left.y - right.y || left.x - right.x)
        """,
        requested_type,
    )


def _dismiss_detail_guide(page: Any) -> None:
    try:
        button = page.get_by_text("\u6211\u77e5\u9053\u4e86", exact=True).first
        if button.is_visible(timeout=800):
            button.click(timeout=2000)
            page.wait_for_timeout(300)
    except Exception:
        pass


def _copy_detail_link(page: Any, timeout_ms: int) -> str:
    _dismiss_detail_guide(page)
    try:
        page.evaluate("navigator.clipboard.writeText('')")
    except Exception:
        pass

    share_buttons = page.locator('[data-e2e="video-player-share"]')
    share = None
    viewport = page.viewport_size
    if not viewport:
        try:
            viewport = page.evaluate(
                "() => ({width: window.innerWidth, height: window.innerHeight})"
            )
        except Exception:
            viewport = None
    viewport_height = viewport["height"] if viewport else 10000
    for index in range(share_buttons.count()):
        candidate = share_buttons.nth(index)
        try:
            box = candidate.bounding_box()
            if (
                candidate.is_visible(timeout=300)
                and box
                and box["y"] >= 0
                and box["y"] < viewport_height
            ):
                share = candidate
                break
        except Exception:
            continue
    if share is None:
        raise RuntimeError("detail share button not found")

    share.click(timeout=min(timeout_ms, 5000))
    copy_button = page.get_by_text("\u590d\u5236\u94fe\u63a5", exact=False).last
    copy_button.wait_for(state="visible", timeout=min(timeout_ms, 5000))
    copy_button.click(timeout=min(timeout_ms, 5000))
    page.wait_for_timeout(300)
    try:
        return str(page.evaluate("navigator.clipboard.readText()") or "").strip()
    except Exception as exc:
        raise RuntimeError(f"clipboard read failed: {exc}") from exc


def _captcha_visible(page: Any) -> bool:
    selectors = (
        "text=完成验证",
        "text=安全验证",
        "text=拖动滑块",
        "text=验证码",
        "text=点击两个形状相同的物体",
        "text=请完成下列验证",
        'iframe[src*="captcha"]',
    )
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def _select_top_search_tab(page: Any, label: str) -> bool:
    candidates = page.get_by_text(label, exact=True)
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        try:
            box = candidate.bounding_box()
            if (
                candidate.is_visible(timeout=300)
                and box
                and box["x"] >= 150
                and 50 <= box["y"] <= 180
            ):
                candidate.click(timeout=2500)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def _open_search(
    page: Any,
    keyword: str,
    content_type: str,
    timeout_ms: int,
) -> None:
    opened = False
    search_inputs = page.locator('input[data-e2e="searchbar-input"]')
    for index in range(search_inputs.count()):
        candidate = search_inputs.nth(index)
        try:
            if candidate.is_visible(timeout=300):
                candidate.click()
                candidate.fill(keyword)
                candidate.press("Enter")
                page.wait_for_timeout(1800)
                opened = True
                break
        except Exception:
            continue
    if not opened:
        page.goto(
            f"https://www.douyin.com/search/{quote(keyword)}?type=general",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        page.wait_for_timeout(1800)
    if content_type == "video":
        _select_top_search_tab(page, "\u89c6\u9891")
    else:
        _select_top_search_tab(page, "\u7efc\u5408")
        current_type = parse_qs(urlparse(page.url).query).get(
            "type",
            ["general"],
        )[0]
        if current_type != "general":
            _goto_search_with_retries(
                page,
                f"https://www.douyin.com/search/{quote(keyword)}?type=general",
                timeout_ms,
            )


def _same_search_page(current_url: str, expected_url: str) -> bool:
    current = urlparse(current_url)
    expected = urlparse(expected_url)
    if current.scheme not in {"http", "https"}:
        return False
    if current.netloc != expected.netloc or current.path != expected.path:
        return False
    current_query = parse_qs(current.query)
    expected_query = parse_qs(expected.query)
    return (
        not current_query.get("modal_id")
        and current_query.get("type", ["general"])
        == expected_query.get("type", ["general"])
    )


def _body_scroll_top(page: Any) -> int:
    try:
        return int(
            page.locator("body").evaluate(
                "element => Math.round(element.scrollTop)"
            )
        )
    except Exception:
        return 0


def _goto_search_with_retries(
    page: Any,
    search_url: str,
    timeout_ms: int,
    *,
    attempts: int = 3,
) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        except Exception as exc:
            if _same_search_page(page.url, search_url):
                return True
            _console(
                f"  search navigation retry {attempt}/{attempts}: "
                f"{type(exc).__name__}: {exc}"
            )
            page.wait_for_timeout(600 * attempt)
            continue
        if _same_search_page(page.url, search_url):
            return True
        page.wait_for_timeout(600 * attempt)
    return _same_search_page(page.url, search_url)


def _restore_search_scroll(page: Any, scroll_top: int) -> None:
    try:
        page.locator("body").evaluate(
            "(element, top) => element.scrollTo({top, left: 0, behavior: 'instant'})",
            scroll_top,
        )
    except Exception:
        pass


def _return_to_search(
    page: Any,
    search_url: str,
    timeout_ms: int,
    scroll_top: int,
) -> bool:
    # Closing the modal in place preserves the loaded result list and avoids a
    # full search-page navigation after every copied link.
    for _ in range(2):
        if _same_search_page(page.url, search_url):
            _restore_search_scroll(page, scroll_top)
            page.wait_for_timeout(350)
            return True
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(450)
        except Exception:
            break

    try:
        page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    if not _same_search_page(page.url, search_url):
        if not _goto_search_with_retries(page, search_url, timeout_ms):
            return False
    try:
        page.locator(".videoImage img").first.wait_for(
            state="visible",
            timeout=min(timeout_ms, 5000),
        )
    except Exception:
        pass
    _restore_search_scroll(page, scroll_top)
    page.wait_for_timeout(500)
    return True


def _search_scroll_state(page: Any) -> str:
    return str(
        page.evaluate(
            """
            () => {
              const cards = Array.from(document.querySelectorAll(".videoImage"));
              const lastCard = cards.length ? cards[cards.length - 1] : null;
              const lastRect = lastCard ? lastCard.getBoundingClientRect() : null;
              const root = document.scrollingElement || document.documentElement;
              const scrollables = Array.from(document.querySelectorAll("main, div"))
                .filter(element => {
                  const style = getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  return (
                    element.scrollHeight > element.clientHeight + 80 &&
                    ["auto", "scroll"].includes(style.overflowY) &&
                    rect.width > 500 &&
                    rect.height > 250 &&
                    element.getAttribute("data-e2e") !== "douyin-navigation"
                  );
                })
                .map(element => ({
                  top: Math.round(element.scrollTop),
                  height: element.scrollHeight,
                  cls: String(element.className || "").slice(0, 60),
                }));
              return JSON.stringify({
                windowY: Math.round(window.scrollY),
                rootTop: Math.round(root ? root.scrollTop : 0),
                bodyTop: Math.round(document.body ? document.body.scrollTop : 0),
                cardCount: cards.length,
                lastY: lastRect ? Math.round(lastRect.y) : null,
                scrollables,
              });
            }
            """
        )
    )


def _scroll_search_results(page: Any) -> tuple[str, str]:
    before = _search_scroll_state(page)
    cards = page.locator(".videoImage")
    if cards.count():
        try:
            cards.last.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            try:
                cards.last.evaluate(
                    "element => element.scrollIntoView({block: 'end', behavior: 'instant'})"
                )
            except Exception:
                pass

    viewport = page.viewport_size
    if viewport:
        try:
            page.mouse.move(
                viewport["width"] * 0.68,
                viewport["height"] * 0.72,
            )
            page.mouse.wheel(0, max(int(viewport["height"] * 0.85), 650))
        except Exception:
            pass

    try:
        page.evaluate(
            """
            () => {
              const root = document.scrollingElement || document.documentElement;
              const delta = Math.max(window.innerHeight * 0.82, 620);
              if (root) {
                root.scrollTop += delta;
              }
              if (document.body && document.body !== root) {
                document.body.scrollTop += delta;
              }
            }
            """
        )
    except Exception:
        pass
    page.wait_for_timeout(300)
    return before, _search_scroll_state(page)


def collect_task(
    page: Any,
    task: KeywordTask,
    excluded_urls: set[str],
    *,
    max_scrolls: int,
    scroll_pause_ms: int,
    timeout_ms: int,
    detail_dwell_ms: int,
    max_duration_seconds: int,
    content_filter_enabled: bool,
    on_record: Callable[[CollectedLink], None] | None = None,
) -> list[CollectedLink]:
    _console(
        f"Searching: {task.keyword} | type={task.content_type} | quota={task.quota}"
    )
    _open_search(page, task.keyword, task.content_type, timeout_ms)
    try:
        page.wait_for_function(
            """
            requestedType => Array.from(document.querySelectorAll(".videoImage"))
              .some(card => {
                const type = String(card.innerText || "").includes("\u56fe\u6587")
                  ? "image"
                  : "video";
                return requestedType === "all" || type === requestedType;
              })
            """,
            task.content_type,
            timeout=min(timeout_ms, 7000),
        )
    except Exception:
        pass
    page.wait_for_timeout(800)
    search_url = page.url
    initial_candidates = len(
        _visible_card_images(page, task.content_type)
    )
    _console(f"  search ready: {search_url} | visible targets={initial_candidates}")
    collected: list[CollectedLink] = []
    seen_cards: set[str] = set()
    stagnant_scrolls = 0

    for _ in range(max_scrolls):
        if _captcha_visible(page):
            _console(
                "Browser verification detected. Complete it manually, then press Enter."
            )
            input()
            page.wait_for_timeout(1500)

        screen_had_fresh_cards = False
        while len(collected) < task.quota:
            cards = _visible_card_images(page, task.content_type)
            fresh_cards = [card for card in cards if card["src"] not in seen_cards]
            if not fresh_cards:
                break
            screen_had_fresh_cards = True
            card = fresh_cards[0]
            seen_cards.add(card["src"])
            if content_filter_enabled:
                reason = _non_target_reason(
                    card.get("text", ""),
                    max_duration_seconds,
                )
                if reason:
                    preview = " ".join(
                        str(card.get("text", "")).split()
                    )[:80]
                    _console(f"  filtered before opening: {reason} | {preview}")
                    continue
            search_scroll_top = _body_scroll_top(page)
            try:
                page.mouse.click(
                    card["x"] + card["width"] / 2,
                    card["y"] + card["height"] / 2,
                )
                try:
                    page.wait_for_function(
                        """
                        () => {
                          const url = new URL(window.location.href);
                          return url.searchParams.has("modal_id") ||
                            /\\/(video|note)\\/\\d+/.test(url.pathname);
                        }
                        """,
                        timeout=min(timeout_ms, 4000),
                    )
                except Exception:
                    pass
            except Exception as exc:
                _console(f"  card click failed: {exc}")
                continue

            direct_detail = normalize_douyin_url(page.url)
            if not _modal_work_id(page.url) and not direct_detail:
                _console("  card did not open a work detail; skipped")
                continue
            page.wait_for_timeout(detail_dwell_ms)

            normalized = None
            try:
                copied_text = _copy_detail_link(page, timeout_ms)
                normalized = normalize_douyin_url(copied_text)
            except Exception as exc:
                _console(f"  share/copy unavailable, using detail ID: {exc}")
            if not normalized:
                normalized = direct_detail or _canonical_from_modal(
                    page.url,
                    card["contentType"],
                )

            returned = _return_to_search(
                page,
                search_url,
                timeout_ms,
                search_scroll_top,
            )
            if not returned:
                _console(
                    "  could not restore the current search page; "
                    "moving to the next keyword"
                )
                return collected
            if not normalized:
                _console("  could not determine a canonical work link")
                continue

            url, work_id, content_type = normalized
            if not _matches_type(content_type, task.content_type):
                _console(
                    f"  skipped {work_id}: actual type={content_type}, "
                    f"requested={task.content_type}"
                )
                continue
            if url in excluded_urls:
                continue

            excluded_urls.add(url)
            record = CollectedLink(
                url=url,
                work_id=work_id,
                content_type=content_type,
                keyword=task.keyword,
                collected_at=datetime.now().isoformat(timespec="seconds"),
            )
            collected.append(record)
            if on_record is not None:
                on_record(record)
            _console(f"  copied {content_type}: {url}")

        _console(f"  collected {len(collected)}/{task.quota}")
        if len(collected) >= task.quota:
            break
        before_scroll, after_scroll = _scroll_search_results(page)
        page.wait_for_timeout(scroll_pause_ms)
        reached_bottom = before_scroll == after_scroll
        stagnant_scrolls = (
            stagnant_scrolls + 1
            if reached_bottom and not screen_had_fresh_cards
            else 0
        )
        if stagnant_scrolls >= 3:
            _console("  reached the end of current search results")
            break
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


def _launch_browser(
    args: argparse.Namespace,
    tasks: list[KeywordTask],
    checkpoint_path: Path,
) -> list[CollectedLink]:
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
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with (
        checkpoint_path.open("a", encoding="utf-8") as checkpoint,
        sync_playwright() as playwright,
    ):
        def save_record(record: CollectedLink) -> None:
            all_records.append(record)
            checkpoint.write(record.url + "\n")
            checkpoint.flush()

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
        context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin="https://www.douyin.com",
        )
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
        try:
            for task in tasks:
                try:
                    collect_task(
                        page,
                        task,
                        excluded_urls,
                        max_scrolls=args.max_scrolls,
                        scroll_pause_ms=args.scroll_pause_ms,
                        timeout_ms=args.timeout_ms,
                        detail_dwell_ms=args.detail_dwell_ms,
                        max_duration_seconds=args.max_duration_seconds,
                        content_filter_enabled=not args.disable_content_filter,
                        on_record=save_record,
                    )
                except Exception as exc:
                    _console(
                        f"Task failed but saved links were kept: {task.keyword}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        except KeyboardInterrupt:
            _console(
                "Collection interrupted; finalizing links already copied in this session."
            )
        finally:
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
    parser.add_argument(
        "--detail-dwell-ms",
        type=int,
        default=3000,
        help="Minimum time to keep each opened work visible before copying.",
    )
    parser.add_argument(
        "--max-duration-seconds",
        type=int,
        default=300,
        help="Skip cards whose displayed duration exceeds this limit.",
    )
    parser.add_argument(
        "--disable-content-filter",
        action="store_true",
        help="Keep long videos and cards explicitly labeled as singing/instrument performance.",
    )
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
    if args.detail_dwell_ms < 3000:
        raise SystemExit("--detail-dwell-ms must be at least 3000")
    if args.max_duration_seconds <= 0:
        raise SystemExit("--max-duration-seconds must be positive")
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
    session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint_path = (
        Path(args.output_dir) / session_name / "_checkpoint_links.txt"
    )
    records = _launch_browser(args, tasks, checkpoint_path)
    summary = write_collection_outputs(
        records,
        Path(args.output_dir),
        batch_size=args.batch_size,
        session_name=session_name,
    )
    checkpoint_path.unlink(missing_ok=True)
    _console(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
