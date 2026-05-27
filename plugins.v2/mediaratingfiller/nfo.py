from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .models import NfoMetadata
from .utils import is_valid_rating, is_video_file, normalize_rating


def identify_movie_nfo(directory: Path) -> Optional[Path]:
    movie_nfo = directory / "movie.nfo"
    if movie_nfo.is_file():
        return movie_nfo

    for item in directory.iterdir():
        if not is_video_file(item):
            continue
        companion = directory / f"{item.stem}.nfo"
        if companion.is_file():
            return companion

    nfos = [
        item
        for item in directory.iterdir()
        if item.is_file() and item.suffix.lower() == ".nfo" and item.name.lower() != "tvshow.nfo"
    ]
    if len(nfos) == 1:
        return nfos[0]
    return None


def identify_tvshow_nfo(directory: Path) -> Optional[Path]:
    tvshow = directory / "tvshow.nfo"
    if tvshow.is_file():
        return tvshow
    return None


def parse_nfo(nfo_path: Path, media_path: Path, media_type: str) -> NfoMetadata:
    stat = nfo_path.stat()
    tree = _parse_xml(nfo_path)
    root = tree.getroot()

    title = _first_text(root, "title") or _first_text(root, "name") or nfo_path.stem
    year = _first_text(root, "year")
    if not year:
        premiered = _first_text(root, "premiered")
        year = premiered[:4] if premiered else ""
    country = _first_text(root, "country") or _join_text(root, "country")
    imdbid = _read_imdbid(root)
    tmdbid = _read_tmdbid(root)
    existing_rating = _read_existing_rating(root)

    return NfoMetadata(
        nfo_path=nfo_path,
        media_path=media_path,
        media_type=media_type,
        title=title,
        year=year,
        imdbid=imdbid,
        tmdbid=tmdbid,
        country=country,
        existing_rating=existing_rating,
        nfo_mtime=float(stat.st_mtime),
        nfo_size=int(stat.st_size),
    )


def write_rating_to_nfo(nfo_path: Path, rating: str, *, backup: bool = True) -> None:
    rating = normalize_rating(rating)
    if not rating:
        raise ValueError("分级不能为空")

    if backup:
        backup_path = nfo_path.with_name(f"{nfo_path.name}.bak_rating")
        shutil.copy2(nfo_path, backup_path)

    tree = _parse_xml(nfo_path)
    root = tree.getroot()
    _upsert_text(root, "mpaa", rating)
    _upsert_text(root, "certification", rating)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)


def _parse_xml(nfo_path: Path) -> ET.ElementTree:
    try:
        return ET.parse(nfo_path)
    except ET.ParseError as exc:
        preview = _file_head_preview(nfo_path)
        raise ValueError(f"XML 解析失败：{nfo_path}，{exc}，文件开头：{preview}") from exc


def _file_head_preview(nfo_path: Path) -> str:
    try:
        data = nfo_path.read_bytes()[:80]
    except OSError as exc:
        return f"无法读取文件开头：{exc}"
    if not data:
        return "空文件"
    text = data.decode("utf-8", errors="replace")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text


def _read_existing_rating(root: ET.Element) -> str:
    for tag in ("mpaa", "certification", "contentrating"):
        value = _first_text(root, tag)
        if is_valid_rating(value):
            return normalize_rating(value)
    return ""


def _read_imdbid(root: ET.Element) -> str:
    direct = _first_text(root, "imdbid")
    if direct and direct.startswith("tt"):
        return direct.strip()
    for node in root.findall(".//uniqueid"):
        if (node.attrib.get("type") or "").lower() == "imdb" and node.text:
            text = node.text.strip()
            if text.startswith("tt"):
                return text
    return ""


def _read_tmdbid(root: ET.Element) -> str:
    direct = _first_text(root, "tmdbid")
    if direct and direct.isdigit():
        return direct.strip()
    for node in root.findall(".//uniqueid"):
        if (node.attrib.get("type") or "").lower() == "tmdb" and node.text:
            text = node.text.strip()
            if text.isdigit():
                return text
    return ""


def _first_text(root: ET.Element, tag: str) -> str:
    node = root.find(f"./{tag}")
    if node is not None and node.text:
        return node.text.strip()
    return ""


def _join_text(root: ET.Element, tag: str) -> str:
    values = [node.text.strip() for node in root.findall(f"./{tag}") if node.text and node.text.strip()]
    return ", ".join(values)


def _upsert_text(root: ET.Element, tag: str, value: str) -> None:
    node = root.find(f"./{tag}")
    if node is None:
        node = ET.SubElement(root, tag)
    node.text = value
