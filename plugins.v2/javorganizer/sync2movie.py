from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def _path_size(path: Optional[str]) -> int:
    """
    获取文件或目录大小，单位：字节。

    注意：
    - 文件：返回文件大小
    - 目录：递归统计目录下所有文件大小
    - 路径不存在：返回 0
    """
    if not path:
        return 0

    try:
        p = Path(path)

        if not p.exists():
            return 0

        if p.is_file():
            return int(p.stat().st_size)

        if p.is_dir():
            total = 0
            for root, _, files in os.walk(p):
                for filename in files:
                    file_path = Path(root) / filename
                    try:
                        if file_path.is_file():
                            total += int(file_path.stat().st_size)
                    except Exception:
                        continue
            return total

        return 0
    except Exception:
        return 0


def _path_mtime(path: Optional[str]) -> Optional[float]:
    """
    获取文件修改时间。
    """
    if not path:
        return None

    try:
        p = Path(path)
        if not p.exists():
            return None
        return float(p.stat().st_mtime)
    except Exception:
        return None


def _build_fileitem_dict(
    path: Optional[str],
    storage: str = "local",
    size: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """
    构造 MoviePilot TransferHistory 中使用的 FileItem-like dict。

    MoviePilot 官方整理记录中 src_fileitem / dest_fileitem 是 JSON 字段，
    这里补充 size、basename、extension、modify_time 等常用字段。
    """
    if not path:
        return None

    p = Path(path)
    suffix = p.suffix
    real_size = _path_size(path) if size is None else int(size)

    item_type = "file"
    try:
        if p.exists() and p.is_dir():
            item_type = "dir"
    except Exception:
        item_type = "file"

    return {
        "path": p.as_posix(),
        "name": p.name,
        "basename": p.stem if suffix else p.name,
        "extension": suffix[1:] if suffix.startswith(".") else suffix,
        "type": item_type,
        "storage": storage,
        "size": real_size,
        "modify_time": _path_mtime(path),
        "children": [],
        "fileid": None,
        "parent_fileid": None,
        "thumbnail": None,
        "pickcode": None,
        "drive_id": None,
        "url": None,
    }


def sync_transfer_history(
    *,
    src_path: str,
    dest_path: Optional[str],
    title: str,
    year: Optional[str] = None,
    media_type: str = "电影",
    mode: str = "move",
    success: bool = True,
    errmsg: Optional[str] = None,
    tmdbid: Optional[int] = None,
    imdbid: Optional[str] = None,
    doubanid: Optional[str] = None,
    image: Optional[str] = None,
    season: Optional[str] = None,
    episode: Optional[str] = None,
    downloader: str = "JavOrganizer",
) -> bool:
    """
    Best-effort sync to MoviePilot transfer history.

    返回：
    - True：同步成功
    - False：MoviePilot API 不可用或同步失败
    """
    try:
        from app.db.transferhistory_oper import TransferHistoryOper
        from app.log import logger as mp_logger  # type: ignore
    except Exception:
        return False

    try:
        src = Path(src_path).as_posix()
        dest = Path(dest_path).as_posix() if dest_path else None

        src_size = _path_size(src)
        dest_size = _path_size(dest) if dest else 0

        # move 场景下，src 可能已经不存在，所以优先用目标文件大小。
        # 如果目标不存在，则退回源文件大小。
        transfer_size = dest_size or src_size

        # 如果 move 后源文件已不存在，src_size 可能是 0。
        # 这里仍然给 src_fileitem 补上 transfer_size，避免整理记录里文件大小显示为空或 0。
        src_fileitem = _build_fileitem_dict(
            src,
            "local",
            size=src_size or transfer_size,
        )

        dest_fileitem = _build_fileitem_dict(
            dest,
            "local",
            size=dest_size or transfer_size,
        ) if dest else None

        transfer_history = TransferHistoryOper()

        transfer_history.add_force(
            src=src,
            src_storage="local",
            src_fileitem=src_fileitem,

            dest=dest,
            dest_storage="local" if dest else None,
            dest_fileitem=dest_fileitem,

            mode=mode,
            type=media_type,
            category=None,
            title=title,
            year=str(year) if year else None,
            tmdbid=tmdbid,
            imdbid=imdbid,
            tvdbid=None,
            doubanid=doubanid,
            seasons=season,
            episodes=episode,
            image=image,
            downloader=downloader,
            download_hash=None,
            status=bool(success),
            errmsg=errmsg,

            # 不要在 add_force 顶层传 size。
            # MoviePilot TransferHistory 没有顶层 size 字段，
            # 文件大小放在 src_fileitem、dest_fileitem、files JSON 中。
            files=[
                {
                    "src": src,
                    "src_storage": "local",
                    "src_fileitem": src_fileitem,

                    "dest": dest,
                    "dest_storage": "local" if dest else None,
                    "dest_fileitem": dest_fileitem,

                    "mode": mode,
                    "size": transfer_size,
                    "src_size": src_size or transfer_size,
                    "dest_size": dest_size or transfer_size if dest else 0,

                    "success": bool(success),
                    "errmsg": errmsg,
                }
            ],
        )

        mp_logger.info(
            f"{downloader}: 已同步整理记录到 MoviePilot："
            f"{src} -> {dest}，size={transfer_size}"
        )
        return True

    except Exception as exc:
        try:
            mp_logger.error(
                f"{downloader}: 同步 MoviePilot 整理记录失败：{exc}"
            )  # type: ignore[name-defined]
        except Exception:
            pass
        return False
