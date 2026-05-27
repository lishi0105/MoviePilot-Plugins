from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .models import ApiBudget, NfoMetadata, ScanConfig, ScanSummary
from .nfo import identify_movie_nfo, identify_tvshow_nfo, parse_nfo, write_rating_to_nfo
from .omdb import OmdbClient
from .storage import RatingStorage, StorageError
from .tmdb import TmdbClient
from .utils import (
    LOG_PREFIX,
    ProgressLogger,
    is_excluded_path,
    is_mainland_region,
    is_under_tvshow_tree,
    is_valid_rating,
    now_iso,
)

VERBOSE_PROCESS_THRESHOLD = 100


class RatingFillerProcessor:
    def __init__(self, config: ScanConfig, storage: RatingStorage, logger=None):
        self.config = config
        self.storage = storage
        self.log = logger
        self._run_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_summary = ScanSummary()
        self._omdb = OmdbClient(
            config.omdb_api_key,
            storage,
            request_interval=config.request_interval,
            on_api_call=self._persist_api_usage,
            log_fn=self._log,
        )
        self._tmdb = TmdbClient(
            config.tmdb_api_key,
            storage,
            request_interval=config.request_interval,
            on_api_call=self._persist_api_usage,
            log_fn=self._log,
        )

    def is_busy(self) -> bool:
        return self._running

    def submit_run(self, *, wait: bool = False) -> tuple[bool, ScanSummary]:
        if self._stop_event.is_set():
            return False, self.last_summary
        if not self._run_lock.acquire(blocking=False):
            return False, self.last_summary

        self._running = True

        def _work() -> None:
            try:
                if not self._stop_event.is_set():
                    self.last_summary = self._execute_round()
            except StorageError as exc:
                self._log("error", f"{LOG_PREFIX}数据库异常，安全退出：{exc}")
            except Exception as exc:
                self._log("error", f"{LOG_PREFIX}任务执行异常：{exc}")
            finally:
                self._running = False
                self._run_lock.release()

        if wait:
            _work()
            return True, self.last_summary

        self._thread = threading.Thread(target=_work, name="media-rating-filler", daemon=True)
        self._thread.start()
        return True, ScanSummary()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=600)

    def _execute_round(self) -> ScanSummary:
        summary = ScanSummary()
        self._log("info", f"{LOG_PREFIX}===== 本轮任务开始 =====")

        nfo_refs = self._phase_scan(summary)
        if self._should_abort():
            return summary

        identified, queue = self._phase_identify(nfo_refs, summary)
        if self._should_abort():
            return summary

        self._phase_process(queue, summary)
        self._log_summary(summary)
        self._log("info", f"{LOG_PREFIX}===== 本轮任务结束 =====")
        return summary

    def _phase_scan(self, summary: ScanSummary) -> list[tuple[Path, Path, str]]:
        paths = self.config.library_paths
        self._log("info", f"{LOG_PREFIX}开始扫描媒体库，媒体库路径数量：{len(paths)}")
        started = time.time()
        progress = ProgressLogger(self._log, self.config.progress_interval_seconds)
        discovered: list[tuple[Path, Path, str]] = []
        seen_dirs: set[str] = set()

        for root in paths:
            if self._should_abort():
                break
            if not root.exists():
                self._log("warning", f"{LOG_PREFIX}媒体库路径不存在：{root}")
                continue
            for dirpath, dirnames, _filenames in os.walk(root):
                if self._should_abort():
                    break
                current = Path(dirpath)
                if is_excluded_path(current, self.config.exclude_dirs):
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not is_excluded_path(current / name, self.config.exclude_dirs)
                ]
                key = str(current)
                if key in seen_dirs:
                    continue
                seen_dirs.add(key)
                summary.dirs_scanned += 1

                tvshow_nfo = identify_tvshow_nfo(current)
                if tvshow_nfo:
                    discovered.append((tvshow_nfo, current, "tvshow"))
                elif not is_under_tvshow_tree(current):
                    movie_nfo = identify_movie_nfo(current)
                    if movie_nfo:
                        discovered.append((movie_nfo, current, "movie"))

                progress.maybe_log(
                    "info",
                    f"{LOG_PREFIX}扫描中，已扫描目录：{summary.dirs_scanned}，已发现 NFO：{len(discovered)}",
                )

        summary.total_nfo = len(discovered)
        elapsed = time.time() - started
        self._log(
            "info",
            f"{LOG_PREFIX}媒体库扫描完成，总 NFO 文件数：{summary.total_nfo}，耗时：{elapsed:.1f} 秒",
        )
        return discovered

    def _phase_identify(
        self,
        nfo_refs: list[tuple[Path, Path, str]],
        summary: ScanSummary,
    ) -> tuple[list[NfoMetadata], list[NfoMetadata]]:
        total = len(nfo_refs)
        self._log("info", f"{LOG_PREFIX}开始识别 NFO 元数据，总数：{total}")
        started = time.time()
        progress = ProgressLogger(self._log, self.config.progress_interval_seconds)
        identified: list[NfoMetadata] = []
        queue: list[NfoMetadata] = []

        for index, (nfo_path, media_path, media_type) in enumerate(nfo_refs, start=1):
            if self._should_abort():
                break
            try:
                meta = parse_nfo(nfo_path, media_path, media_type)
                identified.append(meta)
                if is_valid_rating(meta.existing_rating):
                    summary.existing_rating += 1
                    self._save_record(
                        meta,
                        status="skipped_existing",
                        old_rating=meta.existing_rating,
                        new_rating=meta.existing_rating,
                        rating_source="existing",
                    )
                elif not meta.imdbid and not meta.tmdbid:
                    summary.no_id_error += 1
                    self._save_record(
                        meta,
                        status="no_imdbid_no_tmdbid",
                        error="无 imdbid 且无 tmdbid",
                    )
                else:
                    summary.queued += 1
                    queue.append(meta)
                    self._save_record(meta, status="queued")
            except Exception as exc:
                summary.parse_error += 1
                self._save_record_raw(
                    nfo_path=nfo_path,
                    media_path=media_path,
                    media_type=media_type,
                    status="parse_error",
                    error=str(exc),
                )

            progress.maybe_log(
                "info",
                f"{LOG_PREFIX}NFO 识别中，已识别：{index}/{total}，"
                f"已有分级：{summary.existing_rating}，无ID错误：{summary.no_id_error}，待处理：{summary.queued}",
            )

        elapsed = time.time() - started
        self._log(
            "info",
            f"{LOG_PREFIX}NFO 识别完成，总数：{total}，已有分级：{summary.existing_rating}，"
            f"无ID错误：{summary.no_id_error}，待处理：{summary.queued}，"
            f"解析失败：{summary.parse_error}，耗时：{elapsed:.1f} 秒",
        )
        return identified, queue

    def _phase_process(self, queue: list[NfoMetadata], summary: ScanSummary) -> None:
        total = len(queue)
        self._log("info", f"{LOG_PREFIX}开始补充分级，待处理 NFO：{total}")
        if not total:
            self._log("info", f"{LOG_PREFIX}分级补全完成，总数：0，耗时：0.0 秒")
            return

        started = time.time()
        verbose = total < VERBOSE_PROCESS_THRESHOLD
        progress = ProgressLogger(self._log, self.config.progress_interval_seconds)
        budget = ApiBudget(
            run_limit=max(0, self.config.api_call_limit_per_run),
            daily_limit=max(0, self.config.api_call_limit_per_day),
            daily_used=self.storage.get_daily_usage(),
        )
        processed = 0

        for meta in queue:
            if self._should_abort():
                break
            processed += 1
            if verbose:
                self._log(
                    "info",
                    f"{LOG_PREFIX}开始处理：标题={meta.title}，imdbid={meta.imdbid or '-'}，tmdbid={meta.tmdbid or '-'}",
                )
            result = self._process_one(meta, budget, verbose=verbose)
            self._apply_process_result(meta, result, summary)
            if not verbose:
                progress.maybe_log(
                    "info",
                    f"{LOG_PREFIX}分级处理中，成功：{self._success_count(summary)}，"
                    f"失败：{summary.failed}，跳过：{summary.skipped}，已处理：{processed}/{total}",
                )

        elapsed = time.time() - started
        self._log(
            "info",
            f"{LOG_PREFIX}分级补全完成，总数：{total}，OMDb成功：{summary.omdb_success}，"
            f"TMDb成功：{summary.tmdb_success}，大陆兜底：{summary.fallback_mainland}，"
            f"其他兜底：{summary.fallback_other}，失败：{summary.failed}，耗时：{elapsed:.1f} 秒",
        )

    def _process_one(self, meta: NfoMetadata, budget: ApiBudget, *, verbose: bool) -> dict:
        rating = ""
        source = ""
        status = ""
        error = ""

        if meta.imdbid:
            if verbose:
                self._log("info", f"{LOG_PREFIX}OMDb 查询：{meta.imdbid}")
            lookup = self._omdb.lookup(meta.imdbid, budget)
            if lookup.error == "API 调用达到限额":
                return {"status": "api_limit", "error": lookup.error}
            if lookup.rating:
                rating, source = lookup.rating, "omdb"
                status = "updated_omdb"
            elif lookup.error and not lookup.from_cache:
                error = lookup.error

        if not rating and meta.tmdbid:
            if verbose:
                self._log(
                    "info",
                    f"{LOG_PREFIX}TMDb 查询：类型={meta.media_type}，tmdbid={meta.tmdbid}",
                )
            lookup = self._tmdb.lookup(meta.media_type, meta.tmdbid, budget)
            if lookup.error == "API 调用达到限额":
                return {"status": "api_limit", "error": lookup.error}
            if lookup.rating:
                rating, source = lookup.rating, "tmdb"
                status = "updated_tmdb"
            elif lookup.error and not rating:
                error = lookup.error or error

        if not rating:
            if is_mainland_region(meta.country, meta.media_path):
                rating = self.config.fallback_mainland
                source = "fallback"
                status = "fallback_mainland"
            else:
                rating = self.config.fallback_other
                source = "fallback"
                status = "fallback_other"

        if not rating:
            return {"status": "api_error", "error": error or "无法获取分级"}

        try:
            write_rating_to_nfo(meta.nfo_path, rating, backup=True)
        except Exception as exc:
            return {"status": "write_error", "error": str(exc), "rating": rating, "source": source}

        if verbose:
            self._log(
                "info",
                f"{LOG_PREFIX}处理成功：标题={meta.title}，分级={rating}，来源={source}",
            )
        return {"status": status, "rating": rating, "source": source}

    def _apply_process_result(self, meta: NfoMetadata, result: dict, summary: ScanSummary) -> None:
        status = result.get("status", "")
        rating = result.get("rating", "")
        source = result.get("source", "")
        error = result.get("error", "")

        if status == "updated_omdb":
            summary.omdb_success += 1
        elif status == "updated_tmdb":
            summary.tmdb_success += 1
        elif status == "fallback_mainland":
            summary.fallback_mainland += 1
        elif status == "fallback_other":
            summary.fallback_other += 1
        elif status in {"api_limit", "api_error", "write_error"}:
            summary.failed += 1
            if status == "api_limit":
                summary.api_limit += 1
            self._log("warning", f"{LOG_PREFIX}处理失败：标题={meta.title}，原因={error or status}")
        else:
            summary.skipped += 1

        self._save_record(
            meta,
            status=status,
            old_rating=meta.existing_rating,
            new_rating=rating,
            rating_source=source,
            error=error,
        )

    @staticmethod
    def _success_count(summary: ScanSummary) -> int:
        return summary.omdb_success + summary.tmdb_success + summary.fallback_mainland + summary.fallback_other

    def _save_record(
        self,
        meta: NfoMetadata,
        *,
        status: str,
        old_rating: str = "",
        new_rating: str = "",
        rating_source: str = "",
        error: str = "",
    ) -> None:
        now = now_iso()
        self.storage.upsert_record(
            {
                "media_path": str(meta.media_path),
                "nfo_path": str(meta.nfo_path),
                "media_type": meta.media_type,
                "title": meta.title,
                "year": meta.year,
                "imdbid": meta.imdbid,
                "tmdbid": meta.tmdbid,
                "country": meta.country,
                "old_rating": old_rating,
                "new_rating": new_rating,
                "rating_source": rating_source,
                "status": status,
                "error": error,
                "nfo_mtime": meta.nfo_mtime,
                "nfo_size": meta.nfo_size,
                "updated_at": now,
                "last_scan_at": now,
            }
        )

    def _save_record_raw(
        self,
        *,
        nfo_path: Path,
        media_path: Path,
        media_type: str,
        status: str,
        error: str = "",
    ) -> None:
        now = now_iso()
        try:
            stat = nfo_path.stat()
            nfo_mtime = float(stat.st_mtime)
            nfo_size = int(stat.st_size)
        except OSError:
            nfo_mtime = 0.0
            nfo_size = 0
        self.storage.upsert_record(
            {
                "media_path": str(media_path),
                "nfo_path": str(nfo_path),
                "media_type": media_type,
                "title": nfo_path.stem,
                "year": "",
                "imdbid": "",
                "tmdbid": "",
                "country": "",
                "old_rating": "",
                "new_rating": "",
                "rating_source": "",
                "status": status,
                "error": error,
                "nfo_mtime": nfo_mtime,
                "nfo_size": nfo_size,
                "updated_at": now,
                "last_scan_at": now,
            }
        )

    def _persist_api_usage(self) -> None:
        self.storage.increment_api_usage()

    def _log_summary(self, summary: ScanSummary) -> None:
        self._log(
            "info",
            f"{LOG_PREFIX}本轮汇总：总NFO={summary.total_nfo}，已有分级={summary.existing_rating}，"
            f"待处理={summary.queued}，OMDb={summary.omdb_success}，TMDb={summary.tmdb_success}，"
            f"大陆兜底={summary.fallback_mainland}，其他兜底={summary.fallback_other}，失败={summary.failed}",
        )

    def _should_abort(self) -> bool:
        return self._stop_event.is_set()

    def _log(self, level: str, message: str) -> None:
        if self.log and hasattr(self.log, level):
            getattr(self.log, level)(message)
