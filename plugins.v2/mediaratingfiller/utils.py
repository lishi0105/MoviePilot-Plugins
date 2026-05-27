from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

LOG_PREFIX = "【影视分级补全】"

DEFAULT_EXCLUDE_DIRS = [
    "@eaDir",
    "#recycle",
    ".recycle",
    "downloads",
    "manual",
    "brush",
    "tmp",
    "temp",
    "incomplete",
]

INVALID_RATINGS = {
    "",
    "n/a",
    "unknown",
    "not rated",
    "unrated",
    "未分级",
}

MAINLAND_COUNTRY_KEYWORDS = ("中国", "中国大陆", "china")
MAINLAND_PATH_KEYWORDS = ("大陆", "国产")

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".wmv",
    ".m4v",
    ".ts",
    ".iso",
    ".mov",
    ".flv",
    ".webm",
}


def parse_path_list(value: str) -> list[Path]:
    text = (value or "").replace("\n", ",")
    paths: list[Path] = []
    for item in text.split(","):
        raw = item.strip()
        if raw:
            paths.append(Path(raw))
    return paths


def parse_exclude_dirs(value: str) -> list[str]:
    text = (value or "").replace("\n", ",")
    items = [item.strip() for item in text.split(",") if item.strip()]
    return items or list(DEFAULT_EXCLUDE_DIRS)


def is_under_tvshow_tree(directory: Path) -> bool:
    for parent in directory.parents:
        if (parent / "tvshow.nfo").is_file():
            return True
    return False


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def is_excluded_path(path: Path, exclude_dirs: list[str]) -> bool:
    lowered = {item.strip().lower() for item in exclude_dirs if item.strip()}
    if not lowered:
        return False
    for part in path.parts:
        if part.lower() in lowered:
            return True
    return False


def is_valid_rating(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in INVALID_RATINGS


def normalize_rating(value: Optional[str]) -> str:
    if value is None:
        return ""
    return value.strip()


def is_mainland_region(country: str, media_path: Path) -> bool:
    country_text = (country or "").lower()
    for keyword in MAINLAND_COUNTRY_KEYWORDS:
        if keyword.lower() in country_text:
            return True
    path_text = str(media_path).lower()
    for keyword in MAINLAND_PATH_KEYWORDS:
        if keyword.lower() in path_text:
            return True
    return False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ProgressLogger:
    """按固定间隔输出进度日志。"""

    def __init__(self, log_fn: Callable[[str, str], None], interval_seconds: float = 5.0):
        self._log = log_fn
        self._interval = max(1.0, interval_seconds)
        self._last_at = 0.0

    def maybe_log(self, level: str, message: str, *, force: bool = False) -> None:
        now = time.time()
        if force or now - self._last_at >= self._interval:
            self._log(level, message)
            self._last_at = now

    def reset(self) -> None:
        self._last_at = 0.0
