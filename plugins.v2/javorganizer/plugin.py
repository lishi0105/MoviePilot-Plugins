from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import threading
import time

from .scanner import JavOrganizerProcessor, RunSource, ScanConfig, parse_monitor_dirs
from .storage import OrganizerStorage
from .sync2movie import sync_transfer_history
from .reflushmedia import _refresh_library as refresh_media_event

try:
    from app.core.event import eventmanager
    from app.log import logger
    from app.plugins import _PluginBase
    from app.schemas import Event
    from app.schemas.types import EventType
except Exception:  # pragma: no cover - allows local syntax checks outside MoviePilot.
    eventmanager = None
    Event = Any
    EventType = None

    class _FallbackLogger:
        def info(self, msg): print(msg)
        def warning(self, msg): print(msg)
        def error(self, msg): print(msg)

    logger = _FallbackLogger()

    class _PluginBase:
        pass


plugin_logger = logger


def _plugin_action_handler(func):
    if eventmanager and EventType:
        return eventmanager.register(EventType.PluginAction)(func)
    return func


class JavOrganizer(_PluginBase):
    plugin_name = "私密影片整理"
    plugin_desc = "扫描指定目录，识别影片编码，生成NFO和图片，并整理到指定媒体库。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/organize.png"
    plugin_version = "1.0.0"
    plugin_author = "lishi0105"
    author_url = ""
    plugin_config_prefix = "javorganizer_"
    plugin_order = 66
    auth_level = 1

    # 配置校验下限（须严格大于对应阈值）
    MIN_SCAN_INTERVAL_MINUTES = 2          # > 1 分钟
    MIN_STABLE_CHECK_COUNT = 2             # > 1 次
    MIN_STABLE_CHECK_INTERVAL_SECONDS = 2  # > 1 秒
    MIN_SCAN_PROGRESS_INTERVAL_SECONDS = 1
    MAX_SCAN_PROGRESS_INTERVAL_SECONDS = 60
    DEFAULT_SCAN_PROGRESS_INTERVAL_SECONDS = 10

    def init_plugin(self, config: Optional[dict] = None):
        config = config or {}
        config_changed = False
        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._refresh_mediaserver = bool(config.get("refresh_mediaserver", False))
        self._save_history = bool(config.get("save_history", True))
        self._clear_history = bool(config.get("clear_history", False))
        self._rename_unrecognized = bool(config.get("rename_unrecognized", True))
        self._scan_interval = self._safe_int(config.get("scan_interval"), 10)
        self._monitor_dirs = config.get("monitor_dirs") or ""
        self._fallback_dir = config.get("fallback_dir") or "/media/未识别影片"
        self._exclude_keywords = config.get("exclude_keywords") or ""
        self._stable_wait_seconds = self._safe_int(config.get("stable_wait_seconds"), 0)
        if self._stable_wait_seconds <= 0:
            # Backward compatibility: old config used minutes.
            legacy_minutes = self._safe_int(config.get("stable_minutes"), 0)
            self._stable_wait_seconds = legacy_minutes * 60 if legacy_minutes > 0 else 30
            config_changed = True
        self._stable_check_interval_seconds = self._safe_int(config.get("stable_check_interval_seconds"), 30)
        self._stable_check_count = self._safe_int(config.get("stable_check_count"), 2)
        self._scan_progress_interval_seconds = self._safe_int(
            config.get("scan_progress_interval_seconds"),
            self.DEFAULT_SCAN_PROGRESS_INTERVAL_SECONDS,
        )
        if not config.get("stable_check_interval_seconds"):
            config_changed = True
        if not config.get("stable_check_count"):
            config_changed = True
        if self._validate_config_values():
            config_changed = True
        self._screenshot_position = config.get("screenshot_position") or "10%"
        self._scraper_sources = config.get("scraper_sources") or "site_a"
        self._proxy = config.get("proxy") or ""
        self._retry_count = int(config.get("retry_count") or 0)
        self._scrape_fail_policy = config.get("scrape_fail_policy") or "fallback"
        self._exists_policy = config.get("exists_policy") or "skip"
        self._move_mode = config.get("move_mode") or "move"
        self._db_path = Path(config.get("db_path") or "/config/plugins/javorganizer/state.sqlite")
        self._storage = OrganizerStorage(self._db_path)
        self._processor: Optional[JavOrganizerProcessor] = None
        self._schedule_thread: Optional[threading.Thread] = None
        self._schedule_stop = threading.Event()
        if config_changed:
            self._save_config()
        if self._clear_history:
            cleared = self._storage.clear_history()
            plugin_logger.info(f"已清空历史记录：{cleared} 条")
            self._clear_history = False
            self._save_config()
        if self._enabled or self._onlyonce:
            self._start_processor()
        if self._enabled and not self._onlyonce:
            self._start_schedule_loop()
        if self._onlyonce:
            self._onlyonce = False
            self._save_config()
            threading.Thread(
                target=self._run_onlyonce_scan,
                name="javorganizer-onlyonce",
                daemon=True,
            ).start()
            if not self._enabled:
                pass  # 仅单次扫描时不启动调度，扫描线程结束后由 _run_onlyonce_scan 清理
            else:
                self._start_schedule_loop()

    def _run_onlyonce_scan(self) -> None:
        try:
            self.scan_now(wait=True)
        finally:
            if not self._enabled:
                self.stop_service()

    def get_state(self) -> bool:
        return self._enabled

    def get_command(self) -> list[dict[str, Any]]:
        if EventType is None:
            return []
        return [{
            "cmd": "/video_archive",
            "event": EventType.PluginAction,
            "desc": "立即归档影片",
            "category": "插件",
            "data": {"action": "video_archive"},
        }]

    def get_api(self) -> list[dict[str, Any]]:
        return [
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["POST", "GET"],
                "summary": "立即扫描影片目录",
                "description": "触发一次影片自动归档扫描",
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "影片归档状态",
                "description": "返回SQLite记录中的状态计数",
            },
            {
                "path": "/history",
                "endpoint": self.api_history,
                "methods": ["GET"],
                "summary": "影片归档历史",
                "description": "返回影片归档历史记录",
            },
            {
                "path": "/history/clear",
                "endpoint": self.api_clear_history,
                "methods": ["POST", "GET"],
                "summary": "清空归档历史",
                "description": "清空影片归档历史记录",
            },
        ]

    def get_service(self) -> list[dict[str, Any]]:
        # Use internal scheduler loop instead of MoviePilot interval trigger.
        return []

    @staticmethod
    def get_render_mode() -> tuple[str, Optional[str]]:
        return "vuetify", None

    def _ensure_storage(self) -> OrganizerStorage:
        """确保存储可用（配置页/数据页可能在 init_plugin 之前访问）。"""
        storage = getattr(self, "_storage", None)
        if storage is not None:
            return storage
        db_path = getattr(self, "_db_path", None)
        if db_path is None:
            config = {}
            try:
                config = self.get_config() or {}
            except Exception:
                config = {}
            db_path = Path(config.get("db_path") or "/config/plugins/javorganizer/state.sqlite")
        self._db_path = Path(db_path)
        self._storage = OrganizerStorage(self._db_path)
        return self._storage

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            return self._build_form_schema(), self.get_default_config()
        except Exception as exc:
            plugin_logger.error(f"生成插件配置表单失败：{exc}")
            return self._fallback_form_schema(), self.get_default_config()

    @classmethod
    def _fallback_form_schema(cls) -> list[dict[str, Any]]:
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "error",
                    "variant": "tonal",
                    "text": "配置表单加载失败，请查看 MoviePilot 日志。",
                },
            }
        ]

    @classmethod
    def _build_form_schema(cls) -> list[dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "props": {"class": "mb-4"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即扫描一次"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "refresh_mediaserver", "label": "刷新媒体库"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "save_history", "label": "存储历史记录"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "clear_history", "label": "清除历史记录（保存后生效）"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "rename_unrecognized", "label": "未识别视频重命名"}}],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"class": "mb-4"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4, "class": "pb-3"},
                                "content": [{"component": "VTextField", "props": {"model": "scan_interval", "label": "扫描周期（分钟，须>1）", "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4, "class": "pb-3"},
                                "content": [{"component": "VTextField", "props": {"model": "stable_wait_seconds", "label": "稳定等待（秒）", "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4, "class": "pb-3"},
                                "content": [{"component": "VTextField", "props": {"model": "stable_check_interval_seconds", "label": "稳定检测间隔（秒，须>1）", "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VTextField", "props": {"model": "stable_check_count", "label": "稳定检测次数（须>1）", "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VTextField", "props": {"model": "scan_progress_interval_seconds", "label": "进度日志间隔（秒，1-60）", "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VTextField", "props": {"model": "screenshot_position", "label": "截图位置"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "scrape_fail_policy",
                                            "label": "刮削失败策略",
                                            "items": [
                                                {"title": "保底整理", "value": "fallback"},
                                                {"title": "跳过处理", "value": "skip"},
                                                {"title": "等待重试", "value": "retry"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "exists_policy",
                                            "label": "目标已存在策略",
                                            "items": [
                                                {"title": "自动重命名", "value": "rename"},
                                                {"title": "跳过处理", "value": "skip"},
                                                {"title": "覆盖目标", "value": "overwrite"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "move_mode",
                                            "label": "转移方式",
                                            "items": [
                                                {"title": "移动", "value": "move"},
                                                {"title": "复制", "value": "copy"},
                                                {"title": "硬链接", "value": "hardlink"},
                                                {"title": "软链接", "value": "softlink"},
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {"component": "VTextarea", "props": {"model": "monitor_dirs", "label": "目录映射，每行 src:dst", "class": "mt-3"}},
                    {"component": "VTextField", "props": {"model": "fallback_dir", "label": "识别失败目录（保底目录）", "class": "mt-3"}},
                    {"component": "VTextarea", "props": {"model": "exclude_keywords", "label": "排除关键词（逗号或换行分隔）", "class": "mt-3"}},
                    {"component": "VTextField", "props": {"model": "scraper_sources", "label": "刮削源，逗号分隔", "class": "mt-3"}},
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "proxy",
                            "label": "代理",
                            "class": "mt-3",
                        },
                    },
                    {"component": "VTextField", "props": {"model": "db_path", "label": "状态数据库路径", "class": "mt-3"}},
                ],
            }
        ]

    def get_page(self) -> list[dict[str, Any]]:
        try:
            rows = self._ensure_storage().list_history(limit=200)
        except Exception as exc:
            plugin_logger.error(f"加载插件数据页失败：{exc}")
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "error",
                        "variant": "tonal",
                        "text": f"加载历史记录失败：{exc}",
                    },
                }
            ]
        items = [self._format_history_row(row) for row in rows]
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": f"历史记录共 {len(items)} 条（最多展示最近 200 条）",
                },
            },
            {
                "component": "VTable",
                "props": {"density": "compact"},
                "content": [
                    {
                        "component": "thead",
                        "content": [
                            {
                                "component": "tr",
                                "content": [
                                    {"component": "th", "text": "时间"},
                                    {"component": "th", "text": "状态"},
                                    {"component": "th", "text": "番号"},
                                    {"component": "th", "text": "源路径"},
                                    {"component": "th", "text": "目标路径"},
                                    {"component": "th", "text": "备注"},
                                ],
                            }
                        ],
                    },
                    {
                        "component": "tbody",
                        "content": [
                            {
                                "component": "tr",
                                "content": [
                                    {"component": "td", "text": item["time"]},
                                    {"component": "td", "text": item["status"]},
                                    {"component": "td", "text": item["jav_code"] or "-"},
                                    {"component": "td", "text": item["source_path"]},
                                    {"component": "td", "text": item["target_path"]},
                                    {"component": "td", "text": item["error"] or "-"},
                                ],
                            }
                            for item in items
                        ],
                    },
                ],
            },
        ]

    @staticmethod
    def get_default_config() -> dict[str, Any]:
        return {
            "enabled": False,
            "onlyonce": False,
            "refresh_mediaserver": False,
            "save_history": True,
            "clear_history": False,
            "rename_unrecognized": True,
            "scan_interval": 10,
            "monitor_dirs": "/downloads/videos:/media/Videos",
            "fallback_dir": "/media/未识别影片",
            "exclude_keywords": "@eaDir\n#recycle\n.recycle\n.!qB\n.!qb\n.torrent\nparts\ntmp\ntemp\nincomplete",
            "stable_wait_seconds": 30,
            "stable_check_interval_seconds": 30,
            "stable_check_count": 2,
            "scan_progress_interval_seconds": 10,
            "screenshot_position": "10%",
            "scraper_sources": "site_a",
            "proxy": "",
            "retry_count": 0,
            "scrape_fail_policy": "fallback",
            "exists_policy": "skip",
            "move_mode": "move",
            "db_path": "/config/plugins/javorganizer/state.sqlite",
        }

    def scan_now(self, *, wait: bool = False) -> dict[str, Any]:
        self._start_processor()
        if not self._processor:
            return {"busy": False, "summary": self._empty_summary()}
        accepted, _ = self._processor.submit_run(RunSource.IMMEDIATE, wait=wait)
        if not accepted:
            return {"busy": True, "summary": self._summary_cn(self._processor.last_result.__dict__)}
        summary = self._summary_cn(self._processor.last_result.__dict__)
        if wait:
            plugin_logger.info(f"立即扫描完成：{summary}")
        else:
            plugin_logger.info(f"立即扫描已提交，后台处理中：{summary}")
        return {"busy": False, "summary": summary}

    def api_scan(self) -> dict[str, Any]:
        payload = self.scan_now()
        return {"success": not payload.get("busy"), "data": payload}

    def api_status(self) -> dict[str, Any]:
        return {"success": True, "data": self._ensure_storage().status_counts()}

    def api_history(self) -> dict[str, Any]:
        rows = self._ensure_storage().list_history(limit=200)
        return {"success": True, "data": [self._format_history_row(row) for row in rows]}

    def api_clear_history(self) -> dict[str, Any]:
        cleared = self._ensure_storage().clear_history()
        plugin_logger.info(f"已通过 API 清空历史记录：{cleared} 条")
        return {"success": True, "message": f"已清空 {cleared} 条历史记录"}

    @_plugin_action_handler
    def command_action(self, event: Event) -> None:
        event_data = getattr(event, "event_data", None)
        if not event_data or event_data.get("action") != "video_archive":
            return
        self.scan_now(wait=False)

    def stop_service(self):
        self._schedule_stop.set()
        if self._schedule_thread and self._schedule_thread.is_alive():
            self._schedule_thread.join(timeout=3)
            self._schedule_thread = None
        if self._processor:
            self._processor.stop()
            self._processor = None

    def _build_scan_config(self) -> ScanConfig:
        sources = [item.strip() for item in self._scraper_sources.split(",") if item.strip()]
        return ScanConfig(
            monitor_dirs=parse_monitor_dirs(self._monitor_dirs),
            fallback_dir=Path(self._fallback_dir),
            exclude_keywords=self._parse_exclude_keywords(self._exclude_keywords),
            save_history=self._save_history,
            stable_wait_seconds=max(1, self._stable_wait_seconds),
            stable_check_interval_seconds=max(self.MIN_STABLE_CHECK_INTERVAL_SECONDS, self._stable_check_interval_seconds),
            stable_check_count=max(self.MIN_STABLE_CHECK_COUNT, self._stable_check_count),
            scan_progress_interval_seconds=self._clamp_scan_progress_interval(self._scan_progress_interval_seconds),
            screenshot_position=self._screenshot_position,
            scraper_sources=sources,
            proxy=self._proxy,
            scrape_fail_policy=self._scrape_fail_policy,
            exists_policy=self._exists_policy,
            move_mode=self._move_mode,
            rename_unrecognized=self._rename_unrecognized,
            retry_count=self._retry_count,
            moviepilot_sync_func=self._sync_history_to_moviepilot_record,
            refresh_callback=self._refresh_library if self._refresh_mediaserver else None,
            refresh_cooldown_seconds=60,
        )

    def _save_config(self) -> None:
        if hasattr(self, "update_config"):
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": self._onlyonce,
                "refresh_mediaserver": self._refresh_mediaserver,
                "save_history": self._save_history,
                "clear_history": self._clear_history,
                "rename_unrecognized": self._rename_unrecognized,
                "scan_interval": self._scan_interval,
                "monitor_dirs": self._monitor_dirs,
                "fallback_dir": self._fallback_dir,
                "exclude_keywords": self._exclude_keywords,
                "stable_wait_seconds": self._stable_wait_seconds,
                "stable_check_interval_seconds": self._stable_check_interval_seconds,
                "stable_check_count": self._stable_check_count,
                "scan_progress_interval_seconds": self._scan_progress_interval_seconds,
                "screenshot_position": self._screenshot_position,
                "scraper_sources": self._scraper_sources,
                "proxy": self._proxy,
                "retry_count": self._retry_count,
                "scrape_fail_policy": self._scrape_fail_policy,
                "exists_policy": self._exists_policy,
                "move_mode": self._move_mode,
                "db_path": str(self._db_path),
            })

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _validate_config_values(self) -> bool:
        """校验关键参数，非法值自动修正并写回配置。"""
        changed = False
        if self._scan_interval <= 1:
            plugin_logger.warning(
                f"扫描周期无效（{self._scan_interval} 分钟），必须大于 1 分钟，"
                f"已修正为 {self.MIN_SCAN_INTERVAL_MINUTES} 分钟。"
            )
            self._scan_interval = self.MIN_SCAN_INTERVAL_MINUTES
            changed = True
        if self._stable_check_count <= 1:
            plugin_logger.warning(
                f"稳定检测次数无效（{self._stable_check_count}），必须大于 1，"
                f"已修正为 {self.MIN_STABLE_CHECK_COUNT}。"
            )
            self._stable_check_count = self.MIN_STABLE_CHECK_COUNT
            changed = True
        if self._stable_check_interval_seconds <= 1:
            plugin_logger.warning(
                f"稳定检测间隔无效（{self._stable_check_interval_seconds} 秒），必须大于 1 秒，"
                f"已修正为 {self.MIN_STABLE_CHECK_INTERVAL_SECONDS} 秒。"
            )
            self._stable_check_interval_seconds = self.MIN_STABLE_CHECK_INTERVAL_SECONDS
            changed = True
        clamped_progress = self._clamp_scan_progress_interval(self._scan_progress_interval_seconds)
        if clamped_progress != self._scan_progress_interval_seconds:
            plugin_logger.warning(
                f"进度日志间隔无效（{self._scan_progress_interval_seconds} 秒），"
                f"有效范围 {self.MIN_SCAN_PROGRESS_INTERVAL_SECONDS}-{self.MAX_SCAN_PROGRESS_INTERVAL_SECONDS} 秒，"
                f"已修正为 {clamped_progress} 秒。"
            )
            self._scan_progress_interval_seconds = clamped_progress
            changed = True
        return changed

    @classmethod
    def _clamp_scan_progress_interval(cls, value: int) -> int:
        return max(
            cls.MIN_SCAN_PROGRESS_INTERVAL_SECONDS,
            min(cls.MAX_SCAN_PROGRESS_INTERVAL_SECONDS, int(value)),
        )

    @staticmethod
    def _parse_exclude_keywords(value: str) -> list[str]:
        text = (value or "").replace("\n", ",")
        return [item.strip() for item in text.split(",") if item.strip()]

    def _refresh_library(self, dest_path: Optional[str] = None) -> None:
        try:
            refresh_media_event(self, dest_path=dest_path)
        except Exception as exc:
            plugin_logger.warning("!" * 60)
            plugin_logger.warning(f"【媒体库刷新】执行失败：{exc}")
            plugin_logger.warning("!" * 60)

    def _sync_history_to_moviepilot(self) -> None:
        if self._save_history:
            plugin_logger.info("JavOrganizer: 历史记录已写入插件SQLite。")

    def _sync_history_to_moviepilot_record(
        self,
        *,
        src_path: str,
        dest_path: Optional[str],
        title: str,
        media_type: str,
        mode: str,
        success: bool,
        errmsg: Optional[str] = None,
    ) -> bool:
        if not self._save_history:
            return False
        return sync_transfer_history(
            src_path=src_path,
            dest_path=dest_path,
            title=title,
            media_type=media_type,
            mode=mode,
            success=success,
            errmsg=errmsg,
            downloader="JavOrganizer",
        )

    def _start_processor(self) -> None:
        if self._processor:
            return
        self._processor = JavOrganizerProcessor(self._build_scan_config(), self._storage, plugin_logger)
        self._processor.start()

    def _start_schedule_loop(self) -> None:
        if self._schedule_thread and self._schedule_thread.is_alive():
            return
        self._schedule_stop.clear()
        self._schedule_thread = threading.Thread(target=self._schedule_loop, name="javorganizer-schedule", daemon=True)
        self._schedule_thread.start()
        plugin_logger.info("已启动插件内部定时轮询线程。")

    def _schedule_loop(self) -> None:
        interval_seconds = max(
            self.MIN_SCAN_INTERVAL_MINUTES * 60,
            int(self._scan_interval) * 60,
        )
        while not self._schedule_stop.is_set():
            if not self._processor:
                plugin_logger.warning("调度线程检测到处理器未就绪，等待重试。")
                if self._schedule_stop.wait(5):
                    break
                continue
            plugin_logger.info("定时轮询触发：尝试启动本轮扫描任务。")
            accepted, _ = self._processor.submit_run(RunSource.SCHEDULED)
            if not accepted:
                plugin_logger.info("定时轮询跳过：当前已有任务在运行，等待下一周期。")
            else:
                plugin_logger.info("本轮任务已启动，等待处理完成。")
                while not self._schedule_stop.is_set() and self._processor and self._processor.is_busy():
                    if self._schedule_stop.wait(2):
                        break
                if self._processor and not self._schedule_stop.is_set():
                    summary = self._summary_cn(self._processor.last_result.__dict__)
                    success = int(self._processor.last_result.success)
                    if success > 0:
                        plugin_logger.warning("=" * 60)
                        plugin_logger.warning(f"【定时调度】本轮处理完成，成功入库 {success} 个文件")
                        plugin_logger.warning(f"  >> 汇总：{summary}")
                        plugin_logger.warning("=" * 60)
                    else:
                        plugin_logger.info(f"【定时调度】本轮处理完成：{summary}")
            plugin_logger.info(f"等待 {interval_seconds} 秒后进入下一周期。")
            if self._schedule_stop.wait(interval_seconds):
                break

    @staticmethod
    def _summary_cn(data: dict[str, int]) -> dict[str, int]:
        return {
            "扫描数量": int(data.get("seen", 0)),
            "候选数量": int(data.get("candidates", data.get("pending", 0))),
            "已跳过": int(data.get("skipped", 0)),
            "异常数量": int(data.get("abnormal", 0)),
            "稳定数量": int(data.get("stable", 0)),
            "消失数量": int(data.get("missing", 0)),
            "已处理": int(data.get("processed", 0)),
            "成功数量": int(data.get("success", data.get("processed", 0))),
            "失败数量": int(data.get("failed", 0)),
            "历史记录写入": int(data.get("history_saved", 0)),
        }

    @classmethod
    def _empty_summary(cls) -> dict[str, int]:
        return cls._summary_cn({})

    @staticmethod
    def _format_history_row(row: dict[str, Any]) -> dict[str, Any]:
        created_at = float(row.get("created_at") or 0)
        time_text = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S") if created_at else ""
        return {
            "id": row.get("id"),
            "time": time_text,
            "status": row.get("status", ""),
            "jav_code": row.get("jav_code", ""),
            "source_path": row.get("source_path", ""),
            "target_path": row.get("target_path", ""),
            "error": row.get("error", ""),
        }
