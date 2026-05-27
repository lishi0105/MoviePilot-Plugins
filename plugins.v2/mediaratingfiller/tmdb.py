from __future__ import annotations

import json
import time
from typing import Callable, Optional
from urllib.request import Request, urlopen

from .models import ApiBudget, RatingLookupResult
from .storage import RatingStorage
from .utils import is_valid_rating, normalize_rating


class TmdbClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(
        self,
        api_key: str,
        storage: RatingStorage,
        *,
        request_interval: float = 0.2,
        on_api_call: Optional[Callable[[], None]] = None,
        log_fn: Optional[Callable[[str, str], None]] = None,
    ):
        self.api_key = api_key.strip()
        self.storage = storage
        self.request_interval = max(0.0, request_interval)
        self._on_api_call = on_api_call
        self._log = log_fn or (lambda _level, _msg: None)
        self._last_request_at = 0.0

    def lookup(self, media_type: str, tmdbid: str, budget: ApiBudget) -> RatingLookupResult:
        tmdbid = tmdbid.strip()
        if not tmdbid:
            return RatingLookupResult(error="缺少 tmdbid")
        if not self.api_key:
            return RatingLookupResult(error="未配置 TMDb API Key")

        normalized_type = "tv" if media_type == "tvshow" else "movie"
        cache_key = f"tmdb:{normalized_type}:{tmdbid}"
        cached = self.storage.get_cache(cache_key)
        if cached is not None:
            rating = normalize_rating(cached.get("rating"))
            if cached.get("success") and is_valid_rating(rating):
                return RatingLookupResult(rating=rating, source="tmdb", from_cache=True)
            if cached.get("success") and not is_valid_rating(rating):
                return RatingLookupResult(source="tmdb", from_cache=True, error="缓存无有效分级")
            return RatingLookupResult(source="tmdb", from_cache=True, error=cached.get("error") or "缓存无有效分级")

        if not budget.can_call():
            return RatingLookupResult(error="API 调用达到限额", source="tmdb")

        self._wait_interval()
        if normalized_type == "tv":
            url = f"{self.BASE_URL}/tv/{tmdbid}/content_ratings?api_key={self.api_key}"
        else:
            url = f"{self.BASE_URL}/movie/{tmdbid}/release_dates?api_key={self.api_key}"
        try:
            payload = self._http_get(url)
            budget.consume()
            if self._on_api_call:
                self._on_api_call()
            rating = self._extract_rating(payload, normalized_type)
            success = is_valid_rating(rating)
            self.storage.set_cache(
                cache_key=cache_key,
                source="tmdb",
                media_type=normalized_type,
                imdbid="",
                tmdbid=tmdbid,
                rating=rating,
                response_json=json.dumps(payload, ensure_ascii=False),
                success=success,
                error="" if success else "无有效分级",
            )
            if success:
                return RatingLookupResult(rating=rating, source="tmdb")
            return RatingLookupResult(source="tmdb", error="TMDb 无有效分级")
        except Exception as exc:
            self.storage.set_cache(
                cache_key=cache_key,
                source="tmdb",
                media_type=normalized_type,
                imdbid="",
                tmdbid=tmdbid,
                rating="",
                response_json="{}",
                success=False,
                error=str(exc),
            )
            return RatingLookupResult(source="tmdb", error=str(exc))

    def _extract_rating(self, payload: dict, media_type: str) -> str:
        results = payload.get("results") or []
        us_rating = self._pick_us_rating(results, media_type)
        if is_valid_rating(us_rating):
            return normalize_rating(us_rating)
        for item in results:
            if media_type == "tv":
                rating = normalize_rating(str(item.get("rating") or ""))
            else:
                rating = self._first_certification(item.get("release_dates") or [])
            if is_valid_rating(rating):
                return rating
        return ""

    @staticmethod
    def _pick_us_rating(results: list, media_type: str) -> str:
        for item in results:
            if (item.get("iso_3166_1") or "").upper() != "US":
                continue
            if media_type == "tv":
                return normalize_rating(str(item.get("rating") or ""))
            return TmdbClient._first_certification(item.get("release_dates") or [])
        return ""

    @staticmethod
    def _first_certification(release_dates: list) -> str:
        for entry in release_dates:
            cert = normalize_rating(str(entry.get("certification") or ""))
            if is_valid_rating(cert):
                return cert
        return ""

    def _wait_interval(self) -> None:
        if self.request_interval <= 0:
            return
        now = time.time()
        elapsed = now - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.time()

    @staticmethod
    def _http_get(url: str) -> dict:
        req = Request(url, headers={"User-Agent": "MoviePilot-MediaRatingFiller/1.0"})
        with urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        return json.loads(data)
