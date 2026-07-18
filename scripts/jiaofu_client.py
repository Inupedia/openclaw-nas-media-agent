"""Jiaofu.com Quark discovery via Playwright (login storage state)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from urllib.parse import quote


class JiaofuError(RuntimeError):
    pass


_BASE = "https://www.xn--wcv59z.com"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def title_matches_query(query: str, title: str) -> bool:
    """Loose relevance: query without trailing sequel digits must appear in title."""
    q = _normalize_text(query)
    t = _normalize_text(title)
    if not q or not t:
        return False
    if q in t:
        return True
    # 幼女战记2 -> also accept titles containing 幼女战记
    base = re.sub(r"\d{1,2}$", "", q)
    if base and len(base) >= 2 and base in t:
        return True
    return False


class JiaofuClient:
    def __init__(
        self,
        storage_state: str | Path,
        *,
        base_url: str = _BASE,
        max_candidates: int = 20,
        timeout_ms: int = 90_000,
        headless: bool = True,
        browser_factory: Callable | None = None,
    ):
        path = Path(storage_state)
        if not path.is_file():
            raise JiaofuError("jiaofu storage state missing")
        self._storage_state = path
        self._base_url = str(base_url).rstrip("/")
        self.max_candidates = (
            int(max_candidates) if 1 <= int(max_candidates) <= 50 else 20
        )
        self.timeout_ms = int(timeout_ms)
        self.headless = bool(headless)
        self._browser_factory = browser_factory

    def __repr__(self) -> str:
        return (
            "JiaofuClient(storage_state='***', "
            f"max_candidates={self.max_candidates!r})"
        )

    def search(self, query: str) -> list[dict]:
        q = str(query or "").strip()
        if not q:
            return []
        try:
            raw = self._crawl(q)
        except JiaofuError:
            raise
        except Exception as error:
            raise JiaofuError(f"jiaofu crawl failed: {error}") from None

        results: list[dict] = []
        seen: set[str] = set()
        for item in raw:
            title = str(item.get("title") or "").strip()
            share = str(item.get("shareurl") or "").strip()
            if not share.startswith("https://pan.quark.cn/s/"):
                continue
            if share in seen:
                continue
            if not title_matches_query(q, title):
                continue
            seen.add(share)
            results.append(
                {
                    "taskname": title or "Jiaofu candidate",
                    "shareurl": share,
                }
            )
            if len(results) >= self.max_candidates:
                break
        return results

    def _crawl(self, query: str) -> list[dict]:
        if self._browser_factory is not None:
            return list(self._browser_factory(query) or [])

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise JiaofuError("playwright not installed") from error

        url = (
            f"{self._base_url}/search?q={quote(query)}"
            f"&type=5&mode=1"
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            try:
                context = browser.new_context(
                    storage_state=str(self._storage_state)
                )
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                title = page.title()
                html = page.content()
                if "未登录" in html or "访问受限" in title:
                    raise JiaofuError("jiaofu login required")
                if "验证" in title:
                    page.wait_for_timeout(8000)
                    title = page.title()
                    if "验证" in title:
                        raise JiaofuError("jiaofu security challenge")
                items = page.eval_on_selector_all(
                    'a[href*="pan.quark.cn/s/"]',
                    """els => {
                      const seen = new Set();
                      const out = [];
                      for (const a of els) {
                        const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                        if (!/^https:\\/\\/pan\\.quark\\.cn\\/s\\/[A-Za-z0-9]+$/.test(href)) continue;
                        if (seen.has(href)) continue;
                        seen.add(href);
                        out.push({
                          title: (a.textContent || '').replace(/\\s+/g, ' ').trim(),
                          shareurl: href,
                        });
                      }
                      return out;
                    }""",
                )
                return list(items or [])
            finally:
                browser.close()
