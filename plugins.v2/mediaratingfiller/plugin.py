from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import threading

from .models import RecordFilters, ScanConfig
from .nfo import write_rating_to_nfo
from .scanner import RatingFillerProcessor
from .storage import RatingStorage
from .utils import DEFAULT_EXCLUDE_DIRS, LOG_PREFIX, now_iso, parse_exclude_dirs, parse_path_list

try:
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # pragma: no cover
    CronTrigger = None

try:
    from app.log import logger
    from app.plugins import _PluginBase
except Exception:  # pragma: no cover
    class _FallbackLogger:
        def info(self, msg): print(msg)
        def warning(self, msg): print(msg)
        def error(self, msg): print(msg)

    logger = _FallbackLogger()

    class _PluginBase:
        pass


plugin_logger = logger

STATUS_LABELS = {
    "scanned": "已扫描",
    "skipped_existing": "已有分级",
    "queued": "待处理",
    "updated_omdb": "OMDb写入",
    "updated_tmdb": "TMDb写入",
    "fallback_mainland": "大陆兜底",
    "fallback_other": "其他兜底",
    "no_imdbid_no_tmdbid": "无ID",
    "api_limit": "API限额",
    "api_error": "API失败",
    "parse_error": "解析失败",
    "write_error": "写入失败",
    "manual_updated": "手动修改",
    "manual_failed": "手动失败",
}

PAGE_FILTERS_KEY = "page_filters"
PAGE_EDIT_RECORD_KEY = "page_edit_record_id"
PAGE_RECORD_LIMIT = 100
PLUGIN_API_PREFIX = "plugin/MediaRatingFiller"

FILTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("country", "国家地区"),
    ("new_rating", "新分级"),
    ("status", "处理状态"),
    ("year", "年份"),
    ("media_type", "类型"),
)

COMMON_RATINGS: tuple[str, ...] = (
    "G",
    "PG",
    "PG-13",
    "R",
    "NC-17",
    "TV-G",
    "TV-PG",
    "TV-14",
    "TV-MA",
    "NR",
    "Not Rated",
    "PG12",
    "15",
    "18",
)


class MediaRatingFiller(_PluginBase):
    plugin_name = "影视分级补全"
    plugin_desc = "扫描已整理媒体库，补全 NFO 中缺失的分级信息（OMDb/TMDb/地区兜底）。"
    plugin_icon = "https://raw.githubusercontent.com/lishi0105/MoviePilot-Plugins/main/icons/rating-filler.png"
    plugin_version = "1.0.0"
    plugin_author = "lishi0105"
    author_url = ""
    plugin_config_prefix = "media_rating_filler_"
    plugin_order = 67
    auth_level = 1
    DEFAULT_DB_PATH = "/config/plugins/media_rating_filler/state.sqlite"
    DEFAULT_CRON = "0 1 * * *"

    def init_plugin(self, config: Optional[dict] = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._clear_history = bool(config.get("clear_history", False))
        self._library_paths = config.get("library_paths") or ""
        self._exclude_dirs = config.get("exclude_dirs") or "\n".join(DEFAULT_EXCLUDE_DIRS)
        self._omdb_api_key = config.get("omdb_api_key") or ""
        self._tmdb_api_key = config.get("tmdb_api_key") or ""
        self._api_call_limit_per_run = self._safe_int(config.get("api_call_limit_per_run"), 5)
        self._api_call_limit_per_day = self._safe_int(config.get("api_call_limit_per_day"), 800)
        self._request_interval = self._safe_float(config.get("request_interval"), 0.2)
        self._fallback_mainland = config.get("fallback_mainland") or "PG-13"
        self._fallback_other = config.get("fallback_other") or "R"
        self._schedule_enabled = bool(config.get("schedule_enabled", True))
        self._cron = (config.get("cron") or self.DEFAULT_CRON).strip()
        self._db_path = Path(self.DEFAULT_DB_PATH)
        self._storage = RatingStorage(self._db_path)
        self._processor: Optional[RatingFillerProcessor] = None

        if self._clear_history:
            cleared = self._storage.clear_history()
            plugin_logger.info(f"{LOG_PREFIX}已清空历史记录：{cleared} 条（未删除 API 缓存）")
            self._clear_history = False
            self._save_config()

        if self._enabled or self._onlyonce:
            self._start_processor()

        if self._onlyonce:
            self._onlyonce = False
            self._save_config()
            threading.Thread(
                target=self._run_onlyonce_scan,
                name="media-rating-filler-onlyonce",
                daemon=True,
            ).start()

    def _run_onlyonce_scan(self) -> None:
        try:
            self.scan_now(wait=True)
        finally:
            if not self._enabled:
                self.stop_service()

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> list[dict[str, Any]]:
        return [
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["POST", "GET"],
                "summary": "立即扫描媒体库",
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "插件状态统计",
            },
            {
                "path": "/records",
                "endpoint": self.api_records,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "历史记录（支持筛选）",
            },
            {
                "path": "/records/update",
                "endpoint": self.api_update_rating,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "手动修改分级",
            },
            {
                "path": "/page/filters/set",
                "endpoint": self.api_page_filter_set,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "设置数据页筛选条件",
            },
            {
                "path": "/page/filters/clear",
                "endpoint": self.api_page_filter_clear,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "清空数据页筛选条件",
            },
            {
                "path": "/page/edit/select",
                "endpoint": self.api_page_edit_select,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "选择待手动修改分级的记录",
            },
            {
                "path": "/page/edit/clear",
                "endpoint": self.api_page_edit_clear,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "取消手动修改分级",
            },
            {
                "path": "/history/clear",
                "endpoint": self.api_clear_history,
                "methods": ["POST", "GET"],
                "summary": "清空历史记录",
            },
        ]

    def get_service(self) -> list[dict[str, Any]]:
        if not self._enabled or not self._schedule_enabled or not self._cron:
            return []
        if CronTrigger is None:
            plugin_logger.warning(f"{LOG_PREFIX}APScheduler 不可用，定时任务未注册")
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}Cron 表达式无效（{self._cron}）：{exc}")
            return []
        return [
            {
                "id": "MediaRatingFillerScan",
                "name": "影视分级补全定时扫描",
                "trigger": trigger,
                "func": self.scheduled_scan,
                "kwargs": {},
            }
        ]

    def scheduled_scan(self) -> None:
        plugin_logger.info(f"{LOG_PREFIX}定时任务触发，开始扫描...")
        if not self._enabled:
            plugin_logger.info(f"{LOG_PREFIX}定时任务跳过：插件未启用")
            return
        try:
            payload = self.scan_now(wait=True)
            if payload.get("busy"):
                plugin_logger.info(f"{LOG_PREFIX}定时任务跳过：已有任务在运行")
                return
            plugin_logger.info(f"{LOG_PREFIX}定时任务完成：{payload.get('summary')}")
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}定时任务失败：{exc}")

    @staticmethod
    def get_render_mode() -> tuple[str, Optional[str]]:
        return "vuetify", None

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            return self._build_form_schema(), self.get_default_config()
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}生成配置表单失败：{exc}")
            return self._fallback_form_schema(), self.get_default_config()

    def get_page(self) -> list[dict[str, Any]]:
        try:
            return self._build_history_page()
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}加载数据页失败：{exc}")
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "error", "variant": "tonal", "text": f"加载失败：{exc}"},
                }
            ]

    def _get_page_filters(self) -> dict[str, str]:
        raw = self.get_data(PAGE_FILTERS_KEY) if hasattr(self, "get_data") else None
        if not isinstance(raw, dict):
            return {}
        allowed = {field for field, _ in FILTER_FIELDS}
        return {
            key: str(value).strip()
            for key, value in raw.items()
            if key in allowed and str(value).strip()
        }

    def _filters_from_page_state(self) -> RecordFilters:
        page_filters = self._get_page_filters()
        return RecordFilters(
            country=page_filters.get("country", ""),
            new_rating=page_filters.get("new_rating", ""),
            status=page_filters.get("status", ""),
            year=page_filters.get("year", ""),
            media_type=page_filters.get("media_type", ""),
            limit=PAGE_RECORD_LIMIT,
        )

    @classmethod
    def _page_api_event(cls, path: str, *, method: str = "post", params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return {
            "api": f"{PLUGIN_API_PREFIX}/{path.lstrip('/')}",
            "method": method,
            "params": params or {},
        }

    @classmethod
    def _page_action_btn(
        cls,
        text: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        variant: str = "outlined",
        size: str = "small",
        color: Optional[str] = None,
    ) -> dict[str, Any]:
        props: dict[str, Any] = {"text": text, "size": size, "variant": variant}
        if color:
            props["color"] = color
        return {
            "component": "VBtn",
            "props": props,
            "events": {"click": cls._page_api_event(path, params=params)},
        }

    def _filter_option_values(self, storage: RatingStorage, field: str) -> list[str]:
        if field == "media_type":
            values = storage.list_distinct_values("media_type", limit=20)
            return values or ["movie", "tvshow"]
        if field == "status":
            values = storage.list_distinct_values("status", limit=20)
            ordered = list(STATUS_LABELS.keys())
            merged: list[str] = []
            for item in ordered + values:
                if item and item not in merged:
                    merged.append(item)
            return merged[:20]
        return storage.list_distinct_values(field, limit=12)

    @staticmethod
    def _filter_display_value(field: str, value: str) -> str:
        if field == "status":
            return STATUS_LABELS.get(value, value)
        return value

    def _build_filter_chip(self, field: str, value: str, active: bool) -> dict[str, Any]:
        return self._page_action_btn(
            self._filter_display_value(field, value),
            "page/filters/set",
            params={field: value},
            variant="tonal" if active else "outlined",
            color="primary" if active else None,
        )

    def _build_filter_section(self, storage: RatingStorage, page_filters: dict[str, str]) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = [
            {
                "component": "div",
                "props": {"class": "text-subtitle-2 mb-2"},
                "text": "组合筛选（点击标签筛选，再次点击取消）",
            }
        ]
        for field, label in FILTER_FIELDS:
            options = self._filter_option_values(storage, field)
            if not options:
                continue
            sections.append(
                {
                    "component": "div",
                    "props": {"class": "mb-3"},
                    "content": [
                        {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis mb-1"},
                            "text": label,
                        },
                        {
                            "component": "div",
                            "props": {"class": "d-flex flex-wrap ga-1"},
                            "content": [
                                self._build_filter_chip(field, value, page_filters.get(field) == value)
                                for value in options
                            ],
                        },
                    ],
                }
            )
        sections.append(
            {
                "component": "div",
                "props": {"class": "d-flex flex-wrap ga-2 mb-2"},
                "content": [
                    self._page_action_btn("清空筛选", "page/filters/clear", variant="outlined"),
                ],
            }
        )
        return sections

    def _build_edit_panel(self, storage: RatingStorage, edit_record_id: int) -> Optional[dict[str, Any]]:
        record = storage.get_record(edit_record_id)
        if not record:
            self.del_data(PAGE_EDIT_RECORD_KEY)
            return None
        title = record.get("title") or f"ID {edit_record_id}"
        current = record.get("new_rating") or record.get("old_rating") or "-"
        return {
            "component": "VAlert",
            "props": {
                "type": "warning",
                "variant": "tonal",
                "class": "mb-4",
            },
            "content": [
                {
                    "component": "div",
                    "props": {"class": "mb-2"},
                    "text": f"正在修改分级：{title}（当前：{current}）",
                },
                {
                    "component": "div",
                    "props": {"class": "d-flex flex-wrap ga-1 mb-2"},
                    "content": [
                        self._page_action_btn(
                            rating,
                            "records/update",
                            params={"id": edit_record_id, "rating": rating},
                            variant="tonal",
                            color="primary",
                        )
                        for rating in COMMON_RATINGS
                    ],
                },
                self._page_action_btn("取消修改", "page/edit/clear", variant="text"),
            ],
        }

    def _build_history_table(self, items: list[dict[str, Any]], edit_record_id: int) -> dict[str, Any]:
        return {
            "component": "VTable",
            "props": {"density": "compact"},
            "content": [
                {
                    "component": "thead",
                    "content": [
                        {
                            "component": "tr",
                            "content": [
                                {"component": "th", "text": "标题"},
                                {"component": "th", "text": "类型"},
                                {"component": "th", "text": "年份"},
                                {"component": "th", "text": "原分级"},
                                {"component": "th", "text": "新分级"},
                                {"component": "th", "text": "状态"},
                                {"component": "th", "text": "更新时间"},
                                {"component": "th", "text": "错误"},
                                {"component": "th", "text": "操作"},
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
                                {"component": "td", "text": item["title"]},
                                {"component": "td", "text": item["media_type"]},
                                {"component": "td", "text": item["year"] or "-"},
                                {"component": "td", "text": item["old_rating"] or "-"},
                                {"component": "td", "text": item["new_rating"] or "-"},
                                {"component": "td", "text": item["status_label"]},
                                {"component": "td", "text": item["updated_at"]},
                                {"component": "td", "text": item["error"] or "-"},
                                {
                                    "component": "td",
                                    "content": [
                                        self._page_action_btn(
                                            "修改分级",
                                            "page/edit/select",
                                            params={"id": item["id"]},
                                            variant="text",
                                            color="primary" if item["id"] == edit_record_id else None,
                                        )
                                    ],
                                },
                            ],
                        }
                        for item in items
                    ],
                },
            ],
        }

    def _build_history_page(self) -> list[dict[str, Any]]:
        storage = self._ensure_storage()
        page_filters = self._get_page_filters()
        filters = self._filters_from_page_state()
        stats = storage.stats(filters)
        rows = storage.list_records(filters)
        items = [self._format_record_row(row) for row in rows]
        edit_record_id = self._safe_int(self.get_data(PAGE_EDIT_RECORD_KEY) if hasattr(self, "get_data") else 0, 0)

        active_filter_text = " | ".join(
            f"{label}={self._filter_display_value(field, value)}"
            for field, label in FILTER_FIELDS
            if (value := page_filters.get(field))
        ) or "无"

        page: list[dict[str, Any]] = [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "class": "mb-4",
                    "text": (
                        f"筛选结果 {stats.filtered_count} 条 | 总记录 {stats.total_count} 条 | "
                        f"成功 {stats.success_count} | 失败 {stats.failed_count} | "
                        f"兜底 {stats.fallback_count} | 手动 {stats.manual_count} | "
                        f"当前展示 {len(items)} 条（最多 {PAGE_RECORD_LIMIT} 条）"
                    ),
                },
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-4 pa-4"},
                "content": self._build_filter_section(storage, page_filters)
                + [
                    {
                        "component": "div",
                        "props": {"class": "text-caption text-medium-emphasis"},
                        "text": f"当前筛选：{active_filter_text}",
                    }
                ],
            },
        ]

        edit_panel = self._build_edit_panel(storage, edit_record_id) if edit_record_id else None
        if edit_panel:
            page.append(edit_panel)

        if not items:
            page.append(
                {
                    "component": "VAlert",
                    "props": {"type": "warning", "variant": "tonal", "text": "没有符合筛选条件的历史记录。"},
                }
            )
            return page

        page.append(self._build_history_table(items, edit_record_id))
        return page

    @staticmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "onlyonce": False,
            "clear_history": False,
            "library_paths": "",
            "exclude_dirs": "\n".join(DEFAULT_EXCLUDE_DIRS),
            "omdb_api_key": "",
            "tmdb_api_key": "",
            "api_call_limit_per_run": 5,
            "api_call_limit_per_day": 800,
            "request_interval": 0.2,
            "fallback_mainland": "PG-13",
            "fallback_other": "R",
            "schedule_enabled": True,
            "cron": cls.DEFAULT_CRON,
        }

    def scan_now(self, *, wait: bool = False) -> dict[str, Any]:
        self._start_processor()
        if not self._processor:
            return {"busy": False, "summary": {}}
        accepted, summary = self._processor.submit_run(wait=wait)
        if not accepted:
            return {"busy": True, "summary": self._summary_dict(summary)}
        return {"busy": False, "summary": self._summary_dict(summary)}

    def api_scan(self) -> dict[str, Any]:
        payload = self.scan_now()
        return {"success": not payload.get("busy"), "data": payload}

    def api_status(self) -> dict[str, Any]:
        storage = self._ensure_storage()
        return {
            "success": True,
            "data": {
                "busy": bool(self._processor and self._processor.is_busy()),
                "status_counts": storage.status_counts(),
                "daily_api_usage": storage.get_daily_usage(),
            },
        }

    def api_records(self, **kwargs) -> dict[str, Any]:
        filters = RecordFilters(
            country=(kwargs.get("country") or "").strip(),
            new_rating=(kwargs.get("new_rating") or "").strip(),
            status=(kwargs.get("status") or "").strip(),
            year=(kwargs.get("year") or "").strip(),
            media_type=(kwargs.get("media_type") or "").strip(),
            limit=self._safe_int(kwargs.get("limit"), 200),
            offset=self._safe_int(kwargs.get("offset"), 0),
        )
        storage = self._ensure_storage()
        rows = storage.list_records(filters)
        stats = storage.stats(filters)
        return {
            "success": True,
            "data": {
                "items": [self._format_record_row(row) for row in rows],
                "stats": stats.__dict__,
            },
        }

    def api_update_rating(self, **kwargs) -> dict[str, Any]:
        record_id = self._safe_int(kwargs.get("id"), 0)
        rating = (kwargs.get("rating") or kwargs.get("new_rating") or "").strip()
        if not record_id:
            return {"success": False, "message": "缺少记录 ID"}
        if not rating:
            return {"success": False, "message": "分级不能为空"}
        try:
            self.manual_update_rating(record_id, rating)
            if hasattr(self, "get_data") and self._safe_int(self.get_data(PAGE_EDIT_RECORD_KEY), 0) == record_id:
                self.del_data(PAGE_EDIT_RECORD_KEY)
            return {"success": True, "message": "手动修改分级成功"}
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}手动修改分级失败：{exc}")
            return {"success": False, "message": str(exc)}

    def api_page_filter_set(self, **kwargs) -> dict[str, Any]:
        filters = dict(self._get_page_filters())
        for field, _ in FILTER_FIELDS:
            if field not in kwargs:
                continue
            value = str(kwargs.get(field) or "").strip()
            if value and filters.get(field) == value:
                filters.pop(field, None)
            elif value:
                filters[field] = value
            else:
                filters.pop(field, None)
        if hasattr(self, "save_data"):
            self.save_data(PAGE_FILTERS_KEY, filters)
        return {"success": True, "data": filters}

    def api_page_filter_clear(self, **kwargs) -> dict[str, Any]:
        if hasattr(self, "del_data"):
            self.del_data(PAGE_FILTERS_KEY)
        return {"success": True, "message": "已清空筛选条件"}

    def api_page_edit_select(self, **kwargs) -> dict[str, Any]:
        record_id = self._safe_int(kwargs.get("id"), 0)
        if not record_id:
            return {"success": False, "message": "缺少记录 ID"}
        if not self._ensure_storage().get_record(record_id):
            return {"success": False, "message": "记录不存在"}
        if hasattr(self, "save_data"):
            self.save_data(PAGE_EDIT_RECORD_KEY, record_id)
        return {"success": True, "message": "请选择新的分级"}

    def api_page_edit_clear(self, **kwargs) -> dict[str, Any]:
        if hasattr(self, "del_data"):
            self.del_data(PAGE_EDIT_RECORD_KEY)
        return {"success": True, "message": "已取消修改"}

    def api_clear_history(self) -> dict[str, Any]:
        cleared = self._ensure_storage().clear_history()
        plugin_logger.info(f"{LOG_PREFIX}已通过 API 清空历史记录：{cleared} 条")
        return {"success": True, "message": f"已清空 {cleared} 条历史记录（API 缓存保留）"}

    def manual_update_rating(self, record_id: int, rating: str) -> None:
        storage = self._ensure_storage()
        record = storage.get_record(record_id)
        if not record:
            raise ValueError("记录不存在")
        nfo_path = Path(record["nfo_path"])
        if not nfo_path.exists():
            storage.update_manual_rating(record_id, rating, success=False, error="NFO 文件不存在")
            raise FileNotFoundError(f"NFO 文件不存在：{nfo_path}")
        try:
            write_rating_to_nfo(nfo_path, rating, backup=True)
            storage.update_manual_rating(record_id, rating, success=True)
            plugin_logger.info(f"{LOG_PREFIX}手动修改分级成功：{record.get('title')} -> {rating}")
        except Exception as exc:
            storage.update_manual_rating(record_id, rating, success=False, error=str(exc))
            raise

    def stop_service(self) -> None:
        if self._processor:
            self._processor.stop()
            self._processor = None

    def _ensure_storage(self) -> RatingStorage:
        storage = getattr(self, "_storage", None)
        if storage is not None:
            return storage
        self._db_path = Path(self.DEFAULT_DB_PATH)
        self._storage = RatingStorage(self._db_path)
        return self._storage

    def _start_processor(self) -> None:
        if self._processor:
            return
        self._processor = RatingFillerProcessor(
            self._build_scan_config(),
            self._storage,
            plugin_logger,
        )

    def _build_scan_config(self) -> ScanConfig:
        return ScanConfig(
            library_paths=parse_path_list(self._library_paths),
            exclude_dirs=parse_exclude_dirs(self._exclude_dirs),
            omdb_api_key=self._omdb_api_key,
            tmdb_api_key=self._tmdb_api_key,
            api_call_limit_per_run=max(0, self._api_call_limit_per_run),
            api_call_limit_per_day=max(0, self._api_call_limit_per_day),
            request_interval=max(0.0, self._request_interval),
            fallback_mainland=self._fallback_mainland,
            fallback_other=self._fallback_other,
        )

    def _save_config(self) -> None:
        if hasattr(self, "update_config"):
            self.update_config(
                {
                    "enabled": self._enabled,
                    "onlyonce": self._onlyonce,
                    "clear_history": self._clear_history,
                    "library_paths": self._library_paths,
                    "exclude_dirs": self._exclude_dirs,
                    "omdb_api_key": self._omdb_api_key,
                    "tmdb_api_key": self._tmdb_api_key,
                    "api_call_limit_per_run": self._api_call_limit_per_run,
                    "api_call_limit_per_day": self._api_call_limit_per_day,
                    "request_interval": self._request_interval,
                    "fallback_mainland": self._fallback_mainland,
                    "fallback_other": self._fallback_other,
                    "schedule_enabled": self._schedule_enabled,
                    "cron": self._cron,
                }
            )

    @classmethod
    def _build_form_schema(cls) -> list[dict[str, Any]]:
        switch_col = {"cols": 12, "md": 4}
        api_key_col = {"cols": 12, "md": 6}
        param_col = {"cols": 12, "class": "flex-grow-1", "style": "flex: 1 1 0; min-width: 0;"}
        full_col = {"cols": 12}
        row_props = {"class": "mb-4"}
        outlined = {"variant": "outlined"}
        switch = {"color": "primary", "hideDetails": True}

        def _text_field(model: str, label: str, **extra: Any) -> dict[str, Any]:
            return {
                "component": "VTextField",
                "props": {"model": model, "label": label, **outlined, **extra},
            }

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "props": row_props,
                        "content": [
                            {
                                "component": "VCol",
                                "props": switch_col,
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件", **switch},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": switch_col,
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "onlyonce", "label": "立即扫描一次", **switch},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": switch_col,
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "clear_history",
                                            "label": "清空历史记录（保存后生效）",
                                            **switch,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": row_props,
                        "content": [
                            {
                                "component": "VCol",
                                "props": switch_col,
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "schedule_enabled",
                                            "label": "启用定时扫描",
                                            **switch,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    _text_field(
                                        "cron",
                                        "定时 Cron 表达式（5 位，默认每天 01:00）",
                                        placeholder="0 1 * * *",
                                    )
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": row_props,
                        "content": [
                            {
                                "component": "VCol",
                                "props": api_key_col,
                                "content": [_text_field("omdb_api_key", "OMDb API Key")],
                            },
                            {
                                "component": "VCol",
                                "props": api_key_col,
                                "content": [_text_field("tmdb_api_key", "TMDb API Key")],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {**row_props, "class": "mb-4 d-flex flex-wrap ga-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": param_col,
                                "content": [
                                    _text_field(
                                        "api_call_limit_per_run",
                                        "单次 API 调用限额",
                                        type="number",
                                    )
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": param_col,
                                "content": [
                                    _text_field(
                                        "api_call_limit_per_day",
                                        "每日 API 调用限额",
                                        type="number",
                                    )
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": param_col,
                                "content": [
                                    _text_field("request_interval", "请求间隔（秒）", type="number")
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": param_col,
                                "content": [_text_field("fallback_mainland", "大陆地区兜底分级")],
                            },
                            {
                                "component": "VCol",
                                "props": param_col,
                                "content": [_text_field("fallback_other", "其他地区兜底分级")],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": row_props,
                        "content": [
                            {
                                "component": "VCol",
                                "props": full_col,
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "library_paths",
                                            "label": "媒体库路径（换行或英文逗号分隔）",
                                            **outlined,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": row_props,
                        "content": [
                            {
                                "component": "VCol",
                                "props": full_col,
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "exclude_dirs",
                                            "label": "排除目录（换行或英文逗号分隔）",
                                            **outlined,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ]

    @classmethod
    def _fallback_form_schema(cls) -> list[dict[str, Any]]:
        return [
            {
                "component": "VAlert",
                "props": {"type": "error", "variant": "tonal", "text": "配置表单加载失败，请查看日志。"},
            }
        ]

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _summary_dict(summary) -> dict[str, int]:
        return {
            "总NFO": summary.total_nfo,
            "已有分级": summary.existing_rating,
            "无ID错误": summary.no_id_error,
            "待处理": summary.queued,
            "解析失败": summary.parse_error,
            "OMDb成功": summary.omdb_success,
            "TMDb成功": summary.tmdb_success,
            "大陆兜底": summary.fallback_mainland,
            "其他兜底": summary.fallback_other,
            "API限额": summary.api_limit,
            "失败": summary.failed,
        }

    @classmethod
    def _format_record_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        status = row.get("status") or ""
        return {
            "id": row.get("id"),
            "title": row.get("title") or "",
            "media_type": row.get("media_type") or "",
            "year": row.get("year") or "",
            "imdbid": row.get("imdbid") or "",
            "tmdbid": row.get("tmdbid") or "",
            "country": row.get("country") or "",
            "old_rating": row.get("old_rating") or "",
            "new_rating": row.get("new_rating") or "",
            "rating_source": row.get("rating_source") or "",
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "error": row.get("error") or "",
            "updated_at": row.get("updated_at") or "",
            "nfo_path": row.get("nfo_path") or "",
        }
