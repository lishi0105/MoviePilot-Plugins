from __future__ import annotations

import enum
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .ffmpeg import create_screenshot, video_summary
from .nfo import MovieMetadata, fallback_metadata, write_movie_nfo
from .parser import extract_jav_code, is_video_file
from .scraper import JavScraper
from .storage import OrganizerStorage, StorageError


@dataclass(frozen=True)
class DirectoryMapping:
    src_dir: Path
    dst_dir: Path


@dataclass
class ScanConfig:
    monitor_dirs: list[DirectoryMapping]
    fallback_dir: Path
    exclude_keywords: Optional[list[str]] = None
    save_history: bool = True
    stable_wait_seconds: int = 30
    stable_check_interval_seconds: int = 30
    stable_check_count: int = 2
    scan_progress_interval_seconds: int = 10
    screenshot_position: str = "10%"
    scraper_sources: Optional[list[str]] = None
    proxy: str = ""
    scrape_fail_policy: str = "fallback"
    exists_policy: str = "skip"
    move_mode: str = "move"
    rename_unrecognized: bool = True
    retry_count: int = 0
    moviepilot_sync_func: Optional[Callable[..., bool]] = None
    refresh_callback: Optional[Callable[[Optional[str]], None]] = None
    refresh_cooldown_seconds: int = 60


VERBOSE_CANDIDATE_THRESHOLD = 50


class RunSource(str, enum.Enum):
    SCHEDULED = "scheduled"
    IMMEDIATE = "immediate"


class CandidateStatus(str, enum.Enum):
    PENDING = "pending"
    STABLE = "stable"
    MISSING = "missing"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class ScanResult:
    seen: int = 0
    candidates: int = 0
    skipped: int = 0
    abnormal: int = 0
    stable: int = 0
    missing: int = 0
    processed: int = 0
    success: int = 0
    failed: int = 0
    history_saved: int = 0


@dataclass
class RoundCandidate:
    path: Path
    dst_dir: Path
    size: int
    mtime: float
    first_seen: float
    last_check_at: float = 0.0
    baseline_size: int = -1
    baseline_mtime: float = -1.0
    stable_hits: int = 0
    status: CandidateStatus = CandidateStatus.PENDING
    error: str = ""


def parse_monitor_dirs(value: str) -> list[DirectoryMapping]:
    mappings: list[DirectoryMapping] = []
    for raw_line in (value or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            src, dst = line.split(":", 1)
        else:
            src, dst = line, line
        src_path = Path(src.strip())
        dst_path = Path(dst.strip()) if dst.strip() else src_path
        mappings.append(DirectoryMapping(src_path, dst_path))
    return mappings


class JavOrganizerProcessor:
    """
    串行扫描 → 串行稳定检测 → 串行处理。
    整轮任务由原子锁保护，同一时间仅允许一个任务运行。
    """

    def __init__(self, config: ScanConfig, storage: OrganizerStorage, logger=None):
        self.config = config
        self.storage = storage
        self.log = logger
        self.scraper = JavScraper(config.scraper_sources, config.proxy)
        self._run_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._round_thread: Optional[threading.Thread] = None
        self._last_refresh_at = 0.0
        self._last_refresh_dest_path: Optional[str] = None
        self._verbose_file_logs = False
        self.last_result = ScanResult()

    def start(self) -> None:
        """初始化处理器，不自动启动扫描（由调度或立即扫描触发）。"""
        self._log("info", "影片整理处理器已就绪，等待任务触发。")

    def stop(self) -> None:
        """设置停止标志；当前文件处理完成后不再继续后续步骤。"""
        self._stop_event.set()
        if self._round_thread and self._round_thread.is_alive():
            self._round_thread.join(timeout=600)
            self._round_thread = None
        self._log("info", "影片整理处理器已停止。")

    def is_busy(self) -> bool:
        return self._running

    def submit_run(
        self,
        source: RunSource = RunSource.SCHEDULED,
        *,
        wait: bool = False,
    ) -> tuple[bool, ScanResult]:
        """
        提交一轮任务。
        - 周期任务：已有任务运行时跳过（不排队）
        - 立即扫描：已有任务运行时直接返回 busy
        """
        if self._stop_event.is_set():
            return False, self.last_result
        if not self._run_lock.acquire(blocking=False):
            if source == RunSource.IMMEDIATE:
                self._log("warning", "立即扫描请求被拒绝：当前已有任务在运行。")
            else:
                self._log("info", "周期任务跳过：当前已有任务在运行。")
            return False, self.last_result

        self._running = True

        def _work() -> None:
            try:
                if not self._stop_event.is_set():
                    self.last_result = self._execute_round(source)
            except StorageError as exc:
                self._log("error", f"数据库异常，安全退出当前任务：{exc}")
            except Exception as exc:
                self._log("error", f"任务执行异常：{exc}")
            finally:
                self._running = False
                self._run_lock.release()

        if wait:
            _work()
            return True, self.last_result

        self._round_thread = threading.Thread(
            target=_work,
            name="javorganizer-round",
            daemon=True,
        )
        self._round_thread.start()
        return True, ScanResult()

    def _execute_round(self, source: RunSource) -> ScanResult:
        result = ScanResult()
        self._last_refresh_dest_path = None
        self._log("info", f"===== 本轮任务开始（来源={source.value}）=====")

        candidates = self._phase_scan(result)
        if self._should_abort_round():
            self._log_round_summary(result, aborted=True)
            return result

        self._phase_stability(candidates, result)
        if self._should_abort_round():
            self._log_round_summary(result, aborted=True)
            return result

        self._phase_process(candidates, result)
        self._finalize_round(result)
        self._log_round_summary(result, aborted=False)
        self._log("info", f"===== 本轮任务结束（来源={source.value}）=====")
        return result

    def _phase_scan(self, result: ScanResult) -> list[RoundCandidate]:
        self._log("info", "【扫描阶段】开始统计过滤后候选文件数量。")
        videos = self._collect_scan_videos(result)
        if self._should_abort_round():
            return []

        precount = self._count_scan_candidates(videos)
        self._apply_round_log_mode(precount)
        self._log(
            "info",
            f"【扫描阶段】过滤后候选={precount}，开始扫描 {len(videos)} 个视频文件。",
        )

        candidates: list[RoundCandidate] = []
        now = time.time()
        last_progress = now

        for path, mapping in videos:
            if self._should_abort_round():
                break

            result.seen += 1
            self._log_detail(f"扫描命中视频：{path}")

            is_candidate, stat_pair, skip_reason, error = self._evaluate_scan_file(path)
            if skip_reason == "excluded":
                result.skipped += 1
                self._log_detail(f"扫描跳过（排除规则）：{path}")
                last_progress = self._tick_scan_progress(result, last_progress)
                continue

            if skip_reason == "abnormal":
                result.abnormal += 1
                self._log("warning", f"扫描异常：{path} - {error}")
                last_progress = self._tick_scan_progress(result, last_progress)
                continue

            if stat_pair is None:
                last_progress = self._tick_scan_progress(result, last_progress)
                continue

            size, mtime = stat_pair

            if skip_reason == "success":
                result.skipped += 1
                self._log_detail(f"扫描跳过（已成功处理相同版本）：{path}")
                last_progress = self._tick_scan_progress(result, last_progress)
                continue

            if not is_candidate:
                last_progress = self._tick_scan_progress(result, last_progress)
                continue

            candidate = RoundCandidate(
                path=path,
                dst_dir=mapping.dst_dir,
                size=size,
                mtime=mtime,
                first_seen=now,
                last_check_at=0.0,
            )
            candidates.append(candidate)
            result.candidates += 1
            self._log_detail(f"扫描入队候选：{path}")
            try:
                self.storage.upsert_pending(path, size, mtime)
            except StorageError:
                raise

            last_progress = self._tick_scan_progress(result, last_progress)

        if not self._verbose_file_logs:
            self._log_scan_progress(result)
        self._log(
            "info",
            f"【扫描阶段】完成：已扫描={result.seen}, 候选={result.candidates}, "
            f"跳过={result.skipped}, 异常={result.abnormal}。"
            f"本轮候选快照已锁定，后续稳定检测与处理仅针对以上候选文件。",
        )
        return candidates

    def _collect_scan_videos(self, result: ScanResult) -> list[tuple[Path, DirectoryMapping]]:
        videos: list[tuple[Path, DirectoryMapping]] = []
        for mapping in self.config.monitor_dirs:
            if self._should_abort_round():
                break
            if not mapping.src_dir.exists():
                self._log("warning", f"扫描目录不存在：{mapping.src_dir}")
                continue
            try:
                paths = list(mapping.src_dir.rglob("*"))
            except OSError as exc:
                result.abnormal += 1
                self._log("warning", f"扫描目录异常：{mapping.src_dir} - {exc}")
                continue

            for path in paths:
                if self._should_abort_round():
                    break
                if path.is_file() and is_video_file(path):
                    videos.append((path, mapping))
        return videos

    def _count_scan_candidates(self, videos: list[tuple[Path, DirectoryMapping]]) -> int:
        count = 0
        for path, _mapping in videos:
            is_candidate, _stat_pair, skip_reason, _error = self._evaluate_scan_file(path)
            if is_candidate and not skip_reason:
                count += 1
        return count

    def _evaluate_scan_file(
        self,
        path: Path,
    ) -> tuple[bool, Optional[tuple[int, float]], str, str]:
        """
        评估单个视频文件在扫描阶段的处置。

        返回 (is_candidate, (size, mtime), skip_reason, error_message)。
        skip_reason: 空串=候选; excluded/success/abnormal=跳过原因。
        """
        if self._is_excluded(path):
            return False, None, "excluded", ""

        try:
            stat = path.stat()
        except PermissionError as exc:
            return False, None, "abnormal", str(exc)
        except OSError as exc:
            return False, None, "abnormal", str(exc)

        size = int(stat.st_size)
        mtime = float(stat.st_mtime)
        if self.storage.is_success_version(path, size, mtime):
            return False, (size, mtime), "success", ""
        return True, (size, mtime), "", ""

    def _phase_stability(self, candidates: list[RoundCandidate], result: ScanResult) -> None:
        if not candidates:
            self._log("info", "【稳定检测阶段】无候选文件，跳过。")
            return

        self._log(
            "info",
            f"【稳定检测阶段】开始，候选={len(candidates)}，"
            f"等待={self.config.stable_wait_seconds}s，"
            f"间隔={self.config.stable_check_interval_seconds}s，"
            f"连续次数={self.config.stable_check_count}",
        )
        phase_start = time.time()
        last_progress = phase_start
        required_hits = max(2, self.config.stable_check_count)

        while not self._should_abort_round():
            now = time.time()
            pending_left = 0
            newly_stable = 0

            for candidate in candidates:
                if candidate.status != CandidateStatus.PENDING:
                    continue

                if now < phase_start + self.config.stable_wait_seconds:
                    pending_left += 1
                    continue

                if candidate.last_check_at and now < candidate.last_check_at + self.config.stable_check_interval_seconds:
                    pending_left += 1
                    continue

                if not candidate.path.exists():
                    candidate.status = CandidateStatus.MISSING
                    result.missing += 1
                    try:
                        self.storage.mark_missing(candidate.path, candidate.size, candidate.mtime)
                    except StorageError:
                        raise
                    self._log_detail(f"稳定检测：文件消失 -> missing：{candidate.path}")
                    continue

                try:
                    stat = candidate.path.stat()
                except OSError as exc:
                    result.abnormal += 1
                    self._log("warning", f"稳定检测异常：{candidate.path} - {exc}")
                    candidate.last_check_at = now
                    pending_left += 1
                    continue

                current_size = int(stat.st_size)
                current_mtime = float(stat.st_mtime)
                candidate.last_check_at = now

                if candidate.baseline_size < 0:
                    candidate.baseline_size = current_size
                    candidate.baseline_mtime = current_mtime
                    candidate.stable_hits = 0
                    self._log_detail(f"稳定检测基线：{candidate.path} size={current_size}")
                    pending_left += 1
                    continue

                unchanged = (
                    current_size == candidate.baseline_size
                    and abs(current_mtime - candidate.baseline_mtime) < 0.001
                )
                if unchanged:
                    candidate.stable_hits += 1
                    self._log_detail(
                        f"稳定检测命中：{candidate.path} "
                        f"hits={candidate.stable_hits}/{required_hits}",
                    )
                else:
                    candidate.stable_hits = 0
                    candidate.baseline_size = current_size
                    candidate.baseline_mtime = current_mtime
                    candidate.size = current_size
                    candidate.mtime = current_mtime
                    self._log_detail(
                        f"稳定检测重置：{candidate.path} size={current_size}",
                    )

                if candidate.stable_hits >= required_hits:
                    candidate.status = CandidateStatus.STABLE
                    result.stable += 1
                    newly_stable += 1
                    self._log_detail(f"稳定检测通过 -> stable：{candidate.path}")
                else:
                    pending_left += 1

            if not self._verbose_file_logs:
                if newly_stable > 0:
                    self._log(
                        "info",
                        f"【稳定检测进度】本周期新增稳定={newly_stable}，累计稳定={result.stable}，"
                        f"待检测={pending_left}，消失={result.missing}，总数={len(candidates)}",
                    )
                    last_progress = now
                elif now - last_progress >= self.config.scan_progress_interval_seconds:
                    self._log(
                        "info",
                        f"【稳定检测进度】稳定={result.stable}, 待检测={pending_left}, "
                        f"消失={result.missing}, 总数={len(candidates)}",
                    )
                    last_progress = now

            if pending_left == 0:
                break
            time.sleep(0.2)

        self._log(
            "info",
            f"【稳定检测阶段】完成：稳定={result.stable}, 消失={result.missing}, "
            f"异常累计={result.abnormal}",
        )

    def _phase_process(self, candidates: list[RoundCandidate], result: ScanResult) -> None:
        stable_candidates = [c for c in candidates if c.status == CandidateStatus.STABLE]
        total = len(stable_candidates)
        if not total:
            self._log("info", "【处理阶段】无稳定文件，跳过。")
            return

        self._log("info", f"【处理阶段】开始，待处理={total}")
        last_progress = time.time()
        for index, candidate in enumerate(stable_candidates, start=1):
            if self._should_abort_round():
                self._log("info", "【处理阶段】收到停止信号，终止后续文件处理。")
                break

            self._log_detail(f"【处理】{index}/{total} 开始：{candidate.path}")
            candidate.status = CandidateStatus.PROCESSING
            try:
                self.storage.mark_status(
                    candidate.path,
                    "processing",
                    size=candidate.size,
                    mtime=candidate.mtime,
                )
            except StorageError:
                raise

            try:
                success, jav_code, target_path, error = self._process_file(candidate)
            except Exception as exc:
                success = False
                jav_code = ""
                target_path = None
                error = str(exc)

            result.processed += 1
            if success:
                candidate.status = CandidateStatus.SUCCESS
                result.success += 1
                try:
                    self.storage.mark_success(
                        candidate.path,
                        candidate.size,
                        candidate.mtime,
                        jav_code=jav_code,
                    )
                except StorageError:
                    raise
                if target_path:
                    self._save_history(
                        candidate.path,
                        target_path,
                        "SUCCESS",
                        jav_code,
                        "",
                        result,
                    )
                    self._last_refresh_dest_path = str(Path(target_path).parent)
                self._log_detail(f"【处理成功】{candidate.path} -> {target_path}")
            else:
                candidate.status = CandidateStatus.FAILED
                candidate.error = error
                result.failed += 1
                try:
                    retry_count = self.storage.mark_failed(
                        candidate.path,
                        candidate.size,
                        candidate.mtime,
                        error or "process_failed",
                    )
                except StorageError:
                    raise
                self._log(
                    "warning",
                    f"【处理失败】{candidate.path} error={error} retry_count={retry_count}",
                )

            if not self._verbose_file_logs:
                last_progress = self._tick_phase_progress(
                    "处理进度",
                    last_progress,
                    已完成=result.processed,
                    总数=total,
                    成功=result.success,
                    失败=result.failed,
                )

    def _process_file(self, candidate: RoundCandidate) -> tuple[bool, str, Optional[Path], str]:
        path = candidate.path
        dst_root = candidate.dst_dir

        if not path.exists() or not is_video_file(path):
            return False, "", None, "文件不存在或非视频"

        code = extract_jav_code(path.name)
        if not code:
            self._log_detail(f"未识别编号，进入保底：{path}")
            target_path = self._fallback(path, "未识别影片编码")
            return True, "", target_path, ""

        self._log_detail(f"识别编号：path={path}, code={code}")
        metadata = self.scraper.scrape(code)
        if not metadata:
            if self.config.scrape_fail_policy == "skip":
                return False, code, None, "刮削失败"
            if self.config.scrape_fail_policy == "retry":
                return False, code, None, "刮削失败，等待下轮重试"
            metadata = MovieMetadata(code=code, title=code, tags=["影片"])
            self._log("warning", f"刮削失败，使用基础元数据：{path}")

        target_dir = self._resolve_target_dir(dst_root / code)
        self._write_assets(path, target_dir, metadata)
        target_video = target_dir / f"{code}{path.suffix.lower()}"
        self._move_video(path, target_video)
        return True, code, target_video, ""

    def _fallback(self, path: Path, reason: str) -> Path:
        target_dir, target_video = self._fallback_target(path)
        self._log_detail(f"保底目标：dir={target_dir}, file={target_video.name}, reason={reason}")
        info = video_summary(path)
        metadata = fallback_metadata(path, info)
        write_movie_nfo(target_dir / "movie.nfo", metadata, str(path), info)
        create_screenshot(path, target_dir / "poster.jpg", self.config.screenshot_position)
        create_screenshot(path, target_dir / "fanart.jpg", self.config.screenshot_position)
        self._move_video(path, target_video)
        return target_video

    def _write_assets(self, source_video: Path, target_dir: Path, metadata: MovieMetadata) -> None:
        info = video_summary(source_video)
        write_movie_nfo(target_dir / "movie.nfo", metadata, str(source_video), info)
        if not self.scraper.download_image(metadata.poster_url, target_dir / "poster.jpg"):
            create_screenshot(source_video, target_dir / "poster.jpg", self.config.screenshot_position)
        if not self.scraper.download_image(metadata.fanart_url, target_dir / "fanart.jpg"):
            create_screenshot(source_video, target_dir / "fanart.jpg", self.config.screenshot_position)

    def _resolve_target_dir(self, target_dir: Path) -> Path:
        if self.config.exists_policy == "overwrite" or not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir
        if self.config.exists_policy == "skip":
            raise FileExistsError(f"目标目录已存在：{target_dir}")
        index = 1
        while True:
            candidate = target_dir.with_name(f"{target_dir.name}_{index}")
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            index += 1

    def _move_video(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.config.move_mode == "copy":
            self._log("debug", f"复制文件：{source} -> {target}")
            shutil.copy2(source, target)
        elif self.config.move_mode in {"softlink", "symlink"}:
            self._log("debug", f"软链接文件：{source} -> {target}")
            os.symlink(source, target)
        elif self.config.move_mode == "hardlink":
            self._log("debug", f"硬链接文件：{source} -> {target}")
            os.link(source, target)
        else:
            self._log("debug", f"移动文件：{source} -> {target}")
            shutil.move(str(source), str(target))

    def _fallback_target(self, source: Path) -> tuple[Path, Path]:
        if not self.config.rename_unrecognized:
            target_dir = self._resolve_target_dir(self.config.fallback_dir / source.stem)
            return target_dir, target_dir / source.name
        suffix = source.suffix.lower()
        base = self._timestamp_base_name(source)
        dir_candidate = self.config.fallback_dir / base
        file_candidate = dir_candidate / f"{base}{suffix}"
        if not dir_candidate.exists() and not file_candidate.exists():
            dir_candidate.mkdir(parents=True, exist_ok=True)
            return dir_candidate, file_candidate
        index = 1
        while True:
            alt_base = f"{base}_{index}"
            alt_dir = self.config.fallback_dir / alt_base
            alt_file = alt_dir / f"{alt_base}{suffix}"
            if not alt_dir.exists() and not alt_file.exists():
                alt_dir.mkdir(parents=True, exist_ok=True)
                return alt_dir, alt_file
            index += 1

    @staticmethod
    def _timestamp_base_name(source: Path) -> str:
        stat = source.stat()
        ts = _safe_ts(getattr(stat, "st_birthtime", None))
        if ts is None:
            ts = _safe_ts(getattr(stat, "st_ctime", None))
        if ts is None:
            ts = _safe_ts(getattr(stat, "st_mtime", None))
        if ts is None:
            ts = time.time()
        return time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))

    def _save_history(
        self,
        source_path: Path,
        target_path: Path,
        status: str,
        jav_code: str,
        error: str,
        result: ScanResult,
    ) -> None:
        if not self.config.save_history:
            return
        self.storage.append_history(
            source_path=source_path,
            target_path=target_path,
            status=status,
            jav_code=jav_code,
            error=error,
        )
        result.history_saved += 1
        self._log_detail(f"已写入历史：status={status}, src={source_path}, dest={target_path}")
        if self.config.moviepilot_sync_func:
            try:
                synced = bool(
                    self.config.moviepilot_sync_func(
                        src_path=str(source_path),
                        dest_path=str(target_path),
                        title=jav_code or source_path.stem,
                        media_type="电影",
                        mode=self.config.move_mode,
                        success=True,
                        errmsg=error or None,
                        verbose_log=self._verbose_file_logs,
                    )
                )
                self._log_detail(f"MoviePilot 记录同步：synced={synced}, src={source_path}")
            except Exception as exc:
                self._log("warning", f"同步 MoviePilot 整理记录失败：{exc}")

    def _finalize_round(self, result: ScanResult) -> None:
        if result.success <= 0:
            return
        if not self.config.refresh_callback:
            return
        refresh_path = self._resolve_refresh_dest_path()
        if not refresh_path:
            self._log("warning", "无法确定刷新目录，跳过媒体库刷新。")
            return
        now = time.time()
        if now - self._last_refresh_at < max(1, self.config.refresh_cooldown_seconds):
            self._log("info", "命中刷新冷却时间，跳过媒体库刷新。")
            return
        self._last_refresh_at = now
        try:
            self._log_prominent(
                "【媒体库刷新】>>> 正在触发 <<<",
                f"成功入库：{result.success} 个文件",
                f"刷新目录：{refresh_path}",
                "已广播 TransferComplete 事件，请留意媒体库服务器刷新插件日志",
                level="warning",
            )
            self.config.refresh_callback(refresh_path)
        except Exception as exc:
            self._log_prominent(
                "【媒体库刷新】触发失败",
                str(exc),
                level="error",
            )

    def _resolve_refresh_dest_path(self) -> Optional[str]:
        if self._last_refresh_dest_path:
            return self._last_refresh_dest_path
        if self.config.monitor_dirs:
            return str(self.config.monitor_dirs[0].dst_dir)
        return None

    def _should_abort_round(self) -> bool:
        return self._stop_event.is_set()

    def _apply_round_log_mode(self, candidate_count: int) -> None:
        self._verbose_file_logs = candidate_count < VERBOSE_CANDIDATE_THRESHOLD
        if self._verbose_file_logs:
            mode = "逐文件 INFO"
        else:
            mode = f"定期进度 INFO（间隔={self.config.scan_progress_interval_seconds}s，单文件 DEBUG）"
        self._log(
            "info",
            f"【日志模式】本轮过滤后候选={candidate_count}，"
            f"扫描/稳定检测/处理采用：{mode}",
        )

    def _detail_level(self) -> str:
        return "info" if self._verbose_file_logs else "debug"

    def _log_detail(self, message: str) -> None:
        self._log(self._detail_level(), message)

    def _tick_phase_progress(self, label: str, last_progress: float, **stats: int) -> float:
        now = time.time()
        if now - last_progress >= self.config.scan_progress_interval_seconds:
            parts = ", ".join(f"{key}={value}" for key, value in stats.items())
            self._log("info", f"【{label}】{parts}")
            return now
        return last_progress

    def _tick_scan_progress(self, result: ScanResult, last_progress: float) -> float:
        if self._verbose_file_logs:
            return last_progress
        now = time.time()
        if now - last_progress >= self.config.scan_progress_interval_seconds:
            self._log_scan_progress(result)
            return now
        return last_progress

    def _log_scan_progress(self, result: ScanResult) -> None:
        self._log(
            "info",
            f"【扫描进度】已扫描={result.seen}, 候选={result.candidates}, "
            f"跳过={result.skipped}, 异常={result.abnormal}",
        )

    def _log_round_summary(self, result: ScanResult, *, aborted: bool) -> None:
        if aborted:
            self._log(
                "warning",
                f"本轮任务中止汇总：已扫描={result.seen}, 候选={result.candidates}, "
                f"已处理={result.processed}, 成功={result.success}, 失败={result.failed}",
            )
            return

        title = "【本轮处理完成】汇总报告"
        if result.success > 0:
            title = f"【本轮处理完成】成功入库 {result.success} 个文件"
        self._log_prominent(
            title,
            f"已扫描={result.seen} | 候选={result.candidates} | 跳过={result.skipped} | 异常={result.abnormal}",
            f"稳定={result.stable} | 消失={result.missing} | 已处理={result.processed}",
            f"成功={result.success} | 失败={result.failed} | 历史写入={result.history_saved}",
            level="warning" if result.success > 0 else "info",
        )
        if result.success > 0 and not self.config.refresh_callback:
            self._log_prominent(
                "【提示】本轮有成功入库，但未开启「刷新媒体库」开关",
                "如需自动刷新 Emby/Jellyfin/Plex，请在插件配置中启用",
                level="info",
            )

    def _log_prominent(self, title: str, *lines: str, level: str = "info") -> None:
        border = "!" * 60 if level == "warning" else "=" * 60
        self._log(level, border)
        self._log(level, title)
        for line in lines:
            self._log(level, f"  >> {line}")
        self._log(level, border)

    def _is_excluded(self, path: Path) -> bool:
        keywords = [k.strip().lower() for k in (self.config.exclude_keywords or []) if k.strip()]
        if not keywords:
            return False
        haystack = str(path).lower()
        return any(keyword in haystack for keyword in keywords)

    def _log(self, level: str, message: str) -> None:
        if self.log and hasattr(self.log, level):
            getattr(self.log, level)(message)


def _safe_ts(value: Any) -> Optional[float]:
    try:
        ts = float(value)
        if ts <= 0:
            return None
        return ts
    except Exception:
        return None
