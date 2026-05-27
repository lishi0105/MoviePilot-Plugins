from __future__ import annotations

import json
import time
from typing import Callable, Optional
from urllib.request import Request, urlopen

from .models import ApiBudget, RatingLookupResult
from .storage import RatingStorage
from .utils import is_valid_rating, normalize_rating


class OmdbClient:
    BASE_URL = "https://www.omdbapi.com/"

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

    def lookup(self, imdbid: str, budget: ApiBudget) -> RatingLookupResult:
        imdbid = imdbid.strip()
        if not imdbid:
            return RatingLookupResult(error="缺少 imdbid")
        if not self.api_key:
            return RatingLookupResult(error="未配置 OMDb API Key")

        cache_key = f"omdb:{imdbid}"
        cached = self.storage.get_cache(cache_key)
        if cached is not None:
            rating = normalize_rating(cached.get("rating"))
            if cached.get("success") and is_valid_rating(rating):
                return RatingLookupResult(rating=rating, source="omdb", from_cache=True)
            if cached.get("success") and not is_valid_rating(rating):
                return RatingLookupResult(source="omdb", from_cache=True, error="缓存无有效分级")
            return RatingLookupResult(source="omdb", from_cache=True, error=cached.get("error") or "缓存无有效分级")

        if not budget.can_call():
            return RatingLookupResult(error="API 调用达到限额", source="omdb")

        self._wait_interval()
        url = f"{self.BASE_URL}?i={imdbid}&apikey={self.api_key}"
        try:
            payload = self._http_get(url)
            budget.consume()
            if self._on_api_call:
                self._on_api_call()
            rated = normalize_rating(str(payload.get("Rated") or ""))
            success = is_valid_rating(rated)
            self.storage.set_cache(
                cache_key=cache_key,
                source="omdb",
                media_type="",
                imdbid=imdbid,
                tmdbid="",
                rating=rated,
                response_json=json.dumps(payload, ensure_ascii=False),
                success=success,
                error="" if success else "无有效 Rated",
            )
            if success:
                return RatingLookupResult(rating=rated, source="omdb")
            return RatingLookupResult(source="omdb", error="OMDb 无有效 Rated")
        except Exception as exc:
            self.storage.set_cache(
                cache_key=cache_key,
                source="omdb",
                media_type="",
                imdbid=imdbid,
                tmdbid="",
                rating="",
                response_json="{}",
                success=False,
                error=str(exc),
            )
            return RatingLookupResult(source="omdb", error=str(exc))

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
        payload = json.loads(data)
        if payload.get("Response") == "False":
            raise RuntimeError(payload.get("Error") or "OMDb 请求失败")
        return payload
