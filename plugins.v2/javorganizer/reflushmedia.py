
from typing import Optional

try:
    from app.log import logger as mp_logger  # type: ignore
except Exception:  # pragma: no cover
    import logging
    mp_logger = logging.getLogger("javorganizer.reflushmedia")

def _refresh_library(
    self,
    *,
    file_meta=None,
    mediainfo=None,
    transferinfo=None,
    dest_path: Optional[str] = None,
) -> None:
    """
    触发 MoviePilot 媒体库刷新。

    推荐方式：
    1. 如果已有 MoviePilot TransferInfo，则直接广播 TransferComplete；
    2. 如果是插件自己移动/归档的文件，则构造一个最小 TransferInfo；
    3. 真正刷新 Emby/Jellyfin/Plex 的动作交给“媒体库服务器刷新”插件处理。
    """
    try:
        from pathlib import Path

        from app.core.event import eventmanager
        from app.schemas.types import EventType
        from app.schemas import TransferInfo
        from app.schemas.file import FileItem
    except Exception as exc:
        mp_logger.warning(f"刷新媒体库动作失败：导入 MoviePilot 模块失败：{exc}")
        return

    try:
        if not mediainfo:
            # 允许插件仅凭 transferinfo 触发刷新插件处理
            mediainfo = {"title": "JavOrganizer", "source": "plugin"}

        # 如果你的主流程已经拿到了 TransferInfo，优先直接使用
        if not transferinfo:
            if not dest_path:
                mp_logger.warning("刷新媒体库动作跳过：缺少 transferinfo 且 dest_path 为空")
                return

            dest = Path(dest_path)
            target_item = FileItem(
                path=dest.as_posix(),
                name=dest.name,
                basename=dest.stem,
                extension=dest.suffix[1:] if dest.suffix.startswith(".") else dest.suffix,
                type="file",
                storage="local",
                size=dest.stat().st_size if dest.exists() and dest.is_file() else 0,
                modify_time=dest.stat().st_mtime if dest.exists() else None,
            )

            # 注意：媒体库刷新插件主要检查 target_diritem.path
            # 这里 target_diritem 可以给目标文件所在目录，也可以给目标文件。
            target_dir = dest.parent if dest.suffix else dest
            target_diritem = FileItem(
                path=target_dir.as_posix(),
                name=target_dir.name,
                basename=target_dir.name,
                extension="",
                type="dir",
                storage="local",
                size=0,
                modify_time=target_dir.stat().st_mtime if target_dir.exists() else None,
            )

            transferinfo = TransferInfo(
                success=True,
                target_item=target_item,
                target_diritem=target_diritem,
                transfer_type="move",
                file_count=1,
                file_list_new=[target_item.model_dump()],
                total_size=target_item.size or 0,
            )

        eventmanager.send_event(
            EventType.TransferComplete,
            {
                "meta": file_meta or {},
                "mediainfo": mediainfo,
                "transferinfo": transferinfo,
            },
        )

        target_hint = dest_path or getattr(transferinfo, "target_diritem", None)
        target_path = ""
        if target_hint:
            target_path = target_hint if isinstance(target_hint, str) else getattr(target_hint, "path", "")
        border = "!" * 60
        mp_logger.warning(border)
        mp_logger.warning("【媒体库刷新】TransferComplete 事件已发送")
        mp_logger.warning(f"  >> 目标路径：{target_path or dest_path or '见 transferinfo'}")
        mp_logger.warning("  >> 等待「媒体库服务器刷新」插件执行 Emby/Jellyfin/Plex 刷新")
        mp_logger.warning(border)

    except Exception as exc:
        mp_logger.warning(f"刷新媒体库动作失败：{exc}")
