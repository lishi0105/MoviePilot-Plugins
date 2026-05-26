from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


VIDEO_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".rmvb",
    ".ts",
    ".webm",
    ".wmv",
}

NOISE_PATTERNS = (
    r"\b(?:720P|1080P|2160P|4K|8K|UHD|FHD|HD)\b",
    r"\b(?:H\.?264|H\.?265|HEVC|AVC|X264|X265|AAC|DDP?5\.1)\b",
    r"\b(?:CHS|CHT|SUB|字幕|中字|中文|UNCENSORED|无码|有码)\b",
    r"\[[^\]]+\]",
    r"\([^\)]*\)",
)

CODE_PATTERNS = (
    re.compile(r"\bFC2[-_\s]?(?:PPV[-_\s]?)?(\d{5,8})\b", re.IGNORECASE),
    re.compile(r"\bHEYZO[-_\s]?(\d{3,6})\b", re.IGNORECASE),
    re.compile(r"\b([A-Z]{2,10})[-_\s]?(\d{2,6})(?:[-_\s]?[A-Z])?\b", re.IGNORECASE),
)

IGNORED_PREFIXES = {"H264", "H265", "X264", "X265", "AAC", "HEVC", "AVC", "FHD", "UHD"}


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def clean_name(name: str) -> str:
    text = Path(name).stem.upper()
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_jav_code(name: str) -> Optional[str]:
    text = clean_name(name)
    for pattern in CODE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if pattern.pattern.startswith("\\bFC2"):
            return f"FC2-PPV-{match.group(1)}"
        if pattern.pattern.startswith("\\bHEYZO"):
            return f"HEYZO-{match.group(1)}"
        prefix, number = match.group(1).upper(), match.group(2)
        if prefix in IGNORED_PREFIXES:
            continue
        return f"{prefix}-{number}"
    return None
