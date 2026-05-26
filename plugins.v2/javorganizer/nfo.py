from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MovieMetadata:
    code: str
    title: str
    plot: str = ""
    actors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    premiered: str = ""
    runtime: str = ""
    poster_url: str = ""
    fanart_url: str = ""
    source: str = ""


def write_movie_nfo(path: Path, metadata: MovieMetadata, original_path: str = "", video_info: Optional[dict[str, str]] = None) -> None:
    video_info = video_info or {}
    movie = ET.Element("movie")
    _text(movie, "title", metadata.title or metadata.code)
    _text(movie, "originaltitle", metadata.code)
    _text(movie, "sorttitle", metadata.code)
    _text(movie, "plot", metadata.plot)
    _text(movie, "premiered", metadata.premiered)
    _text(movie, "runtime", metadata.runtime or _minutes(video_info.get("duration", "")))
    if metadata.code:
        uniqueid = ET.SubElement(movie, "uniqueid", {"type": "video_code", "default": "true"})
        uniqueid.text = metadata.code
    for tag in metadata.tags:
        _text(movie, "tag", tag)
        _text(movie, "genre", tag)
    for actor_name in metadata.actors:
        actor = ET.SubElement(movie, "actor")
        _text(actor, "name", actor_name)
    if original_path:
        _text(movie, "originalpath", original_path)
    for key in ("size", "width", "height", "codec"):
        if video_info.get(key):
            _text(movie, key, video_info[key])
    if metadata.source:
        _text(movie, "source", metadata.source)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(movie).write(path, encoding="utf-8", xml_declaration=True)


def fallback_metadata(input_path: Path, video_info: dict[str, str]) -> MovieMetadata:
    return MovieMetadata(
        code="",
        title=input_path.stem,
        plot=f"Fallback metadata generated from {input_path}",
        tags=["未识别"],
        runtime=_minutes(video_info.get("duration", "")),
    )


def _text(parent: ET.Element, name: str, value: str) -> None:
    if value is None:
        value = ""
    child = ET.SubElement(parent, name)
    child.text = str(value)


def _minutes(seconds: str) -> str:
    try:
        return str(int(float(seconds) // 60))
    except (TypeError, ValueError):
        return ""
