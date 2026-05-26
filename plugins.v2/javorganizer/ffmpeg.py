from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe_video(path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {}
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def video_summary(path: Path) -> dict[str, str]:
    data = probe_video(path)
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    duration = _safe_float(fmt.get("duration") or video_stream.get("duration"))
    return {
        "duration": str(int(duration)) if duration else "",
        "width": str(video_stream.get("width") or ""),
        "height": str(video_stream.get("height") or ""),
        "codec": str(video_stream.get("codec_name") or ""),
        "size": str(fmt.get("size") or path.stat().st_size),
    }


def create_screenshot(input_path: Path, output_path: Path, position: str = "10%") -> bool:
    if not shutil.which("ffmpeg"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seek = _resolve_seek(input_path, position)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        seek,
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def _resolve_seek(path: Path, position: str) -> str:
    text = str(position or "10%").strip()
    if text.endswith("%"):
        percent = max(1.0, min(90.0, _safe_float(text[:-1]) or 10.0))
        duration = _safe_float((probe_video(path).get("format") or {}).get("duration"))
        if duration:
            return str(int(duration * percent / 100))
        return "300"
    return text


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
