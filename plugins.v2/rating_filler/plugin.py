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


class MediaRatingFiller(_PluginBase):
    plugin_name = "影视分级补全"
    plugin_desc = "扫描已整理媒体库，补全 NFO 中缺失的分级信息（OMDb/TMDb/地区兜底）。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/class.png"
    plugin_version = "1.0.0"
    plugin_author = "lishi0105"
    author_url = ""
    plugin_config_prefix = "media_rating_filler_"
    plugin_order = 67
    auth_level = 1
    DEFAULT_DB_PATH = "/config/plugins/media_rating_filler/state.sqlite"

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
                "summary": "历史记录（支持筛选）",
            },
            {
                "path": "/records/update",
                "endpoint": self.api_update_rating,
                "methods": ["POST"],
                "summary": "手动修改分级",
            },
            {
                "path": "/history/clear",
                "endpoint": self.api_clear_history,
                "methods": ["POST", "GET"],
                "summary": "清空历史记录",
            },
        ]

    def get_service(self) -> list[dict[str, Any]]:
        return []

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
            storage = self._ensure_storage()
            stats = storage.stats()
            rows = storage.list_records(RecordFilters(limit=100))
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}加载数据页失败：{exc}")
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "error", "variant": "tonal", "text": f"加载失败：{exc}"},
                }
            ]

        items = [self._format_record_row(row) for row in rows]
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": (
                        f"总记录 {stats.total_count} 条 | 当前展示 {len(items)} 条 | "
                        f"成功 {stats.success_count} | 失败 {stats.failed_count} | "
                        f"兜底 {stats.fallback_count} | 手动 {stats.manual_count}。"
                        "筛选与手动修改请调用插件 API：/records、/records/update。"
                    ),
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
                                    {"component": "th", "text": "标题"},
                                    {"component": "th", "text": "类型"},
                                    {"component": "th", "text": "年份"},
                                    {"component": "th", "text": "原分级"},
                                    {"component": "th", "text": "新分级"},
                                    {"component": "th", "text": "来源"},
                                    {"component": "th", "text": "状态"},
                                    {"component": "th", "text": "更新时间"},
                                    {"component": "th", "text": "错误"},
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
                                    {"component": "td", "text": item["rating_source"] or "-"},
                                    {"component": "td", "text": item["status_label"]},
                                    {"component": "td", "text": item["updated_at"]},
                                    {"component": "td", "text": item["error"] or "-"},
                                ],
                            }
                            for item in items
                        ],
                    },
                ],
            },
        ]

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "onlyonce": False,
            "clear_history": False,
            "library_paths": "/volume1/media/电影\n/volume1/media/剧集",
            "exclude_dirs": "\n".join(DEFAULT_EXCLUDE_DIRS),
            "omdb_api_key": "",
            "tmdb_api_key": "",
            "api_call_limit_per_run": 5,
            "api_call_limit_per_day": 800,
            "request_interval": 0.2,
            "fallback_mainland": "PG-13",
            "fallback_other": "R",
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
            return {"success": True, "message": "手动修改分级成功"}
        except Exception as exc:
            plugin_logger.error(f"{LOG_PREFIX}手动修改分级失败：{exc}")
            return {"success": False, "message": str(exc)}

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
