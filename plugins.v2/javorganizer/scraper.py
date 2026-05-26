from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote
from typing import Optional

from .nfo import MovieMetadata


class JavScraper:
    def __init__(self, sources: Optional[list[str]] = None, proxy: str = "", timeout: int = 20):
        self.sources = [s.strip().lower() for s in (sources or ["site_a"]) if s.strip()]
        self.proxy = proxy.strip()
        self.timeout = timeout

    def scrape(self, code: str) -> Optional[MovieMetadata]:
        for source in self.sources:
            try:
                if source in {"site_a", "javdb"}:
                    item = self._scrape_javdb(code)
                else:
                    item = None
            except Exception:
                item = None
            if item:
                return item
        return None

    def download_image(self, url: str, output_path: Path) -> bool:
        if not url:
            return False
        try:
            requests = self._requests()
            resp = requests.get(url, headers=self._headers(), proxies=self._proxies(), timeout=self.timeout)
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception:
            return False

    def _scrape_javdb(self, code: str) -> Optional[MovieMetadata]:
        requests = self._requests()
        bs4 = self._bs4()
        search_url = f"https://javdb.com/search?q={quote(code)}&f=all"
        resp = requests.get(search_url, headers=self._headers(), proxies=self._proxies(), timeout=self.timeout)
        resp.raise_for_status()
        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        link = soup.select_one(".movie-list .item a[href]") or soup.select_one("a.box[href]")
        if not link:
            return None
        detail_url = "https://javdb.com" + link["href"] if link["href"].startswith("/") else link["href"]
        detail = requests.get(detail_url, headers=self._headers(), proxies=self._proxies(), timeout=self.timeout)
        detail.raise_for_status()
        soup = bs4.BeautifulSoup(detail.text, "html.parser")
        title = (soup.select_one("h2.title") or soup.select_one("title"))
        cover = soup.select_one("meta[property='og:image']")
        text = soup.get_text("\n", strip=True)
        actors = [node.get_text(strip=True) for node in soup.select(".panel-block .value a[href*='/actors/']")]
        tags = [node.get_text(strip=True) for node in soup.select(".panel-block .value a[href*='/tags/']")]
        return MovieMetadata(
            code=code,
            title=_clean_title(title.get_text(" ", strip=True) if title else code, code),
            plot="",
            actors=actors,
            tags=tags or ["影片"],
            premiered=_match_field(text, r"(?:Released Date|日期)[:：]?\s*(\d{4}-\d{2}-\d{2})"),
            runtime=_match_field(text, r"(?:Duration|時長|时长)[:：]?\s*(\d+)"),
            poster_url=cover.get("content", "") if cover else "",
            fanart_url=cover.get("content", "") if cover else "",
            source=detail_url,
        )

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 MoviePilot-VideoOrganizer/0.1"}

    def _proxies(self) -> Optional[dict[str, str]]:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    @staticmethod
    def _requests():
        import requests

        return requests

    @staticmethod
    def _bs4():
        import bs4

        return bs4


def _clean_title(title: str, code: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title or code


def _match_field(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""
