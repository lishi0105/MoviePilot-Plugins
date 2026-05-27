from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScanConfig:
    library_paths: list[Path]
    exclude_dirs: list[str]
    omdb_api_key: str
    tmdb_api_key: str
    api_call_limit_per_run: int = 5
    api_call_limit_per_day: int = 800
    request_interval: float = 0.2
    fallback_mainland: str = "PG-13"
    fallback_other: str = "R"
    progress_interval_seconds: float = 5.0


@dataclass
class NfoMetadata:
    nfo_path: Path
    media_path: Path
    media_type: str
    title: str = ""
    year: str = ""
    imdbid: str = ""
    tmdbid: str = ""
    country: str = ""
    existing_rating: str = ""
    nfo_mtime: float = 0.0
    nfo_size: int = 0


@dataclass
class ScanSummary:
    total_nfo: int = 0
    existing_rating: int = 0
    no_id_error: int = 0
    queued: int = 0
    parse_error: int = 0
    omdb_success: int = 0
    tmdb_success: int = 0
    fallback_mainland: int = 0
    fallback_other: int = 0
    failed: int = 0
    skipped: int = 0
    api_limit: int = 0
    api_limit: int = 0
    dirs_scanned: int = 0


@dataclass
class ApiBudget:
    run_limit: int
    daily_limit: int
    run_used: int = 0
    daily_used: int = 0

    def can_call(self) -> bool:
        return self.run_used < self.run_limit and self.daily_used < self.daily_limit

    def consume(self) -> None:
        self.run_used += 1
        self.daily_used += 1


@dataclass
class RecordFilters:
    country: str = ""
    new_rating: str = ""
    status: str = ""
    year: str = ""
    media_type: str = ""
    limit: int = 200
    offset: int = 0


@dataclass
class RecordStats:
    filtered_count: int = 0
    total_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    fallback_count: int = 0
    manual_count: int = 0


@dataclass
class RatingLookupResult:
    rating: str = ""
    source: str = ""
    from_cache: bool = False
    error: str = ""
