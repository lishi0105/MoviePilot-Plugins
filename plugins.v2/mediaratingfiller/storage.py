from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .models import RecordFilters, RecordStats
from .utils import now_iso


class StorageError(Exception):
    """数据库关键错误。"""


class RatingStorage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise StorageError(f"无法连接数据库：{exc}") from exc

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rating_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_path TEXT,
                    nfo_path TEXT NOT NULL UNIQUE,
                    media_type TEXT,
                    title TEXT,
                    year TEXT,
                    imdbid TEXT,
                    tmdbid TEXT,
                    country TEXT,
                    old_rating TEXT,
                    new_rating TEXT,
                    rating_source TEXT,
                    status TEXT,
                    error TEXT,
                    nfo_mtime REAL,
                    nfo_size INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    last_scan_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rating_api_cache (
                    cache_key TEXT PRIMARY KEY,
                    source TEXT,
                    media_type TEXT,
                    imdbid TEXT,
                    tmdbid TEXT,
                    rating TEXT,
                    response_json TEXT,
                    success INTEGER,
                    error TEXT,
                    fetched_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    day TEXT PRIMARY KEY,
                    used_count INTEGER DEFAULT 0,
                    updated_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rating_records_status ON rating_records(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rating_records_updated_at ON rating_records(updated_at)")

    @staticmethod
    def _today_key() -> str:
        return time.strftime("%Y-%m-%d")

    def get_daily_usage(self) -> int:
        day = self._today_key()
        with self._connect() as conn:
            row = conn.execute("SELECT used_count FROM api_usage WHERE day = ?", (day,)).fetchone()
        return int(row["used_count"]) if row else 0

    def increment_api_usage(self) -> int:
        day = self._today_key()
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_usage(day, used_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(day) DO UPDATE SET
                    used_count = used_count + 1,
                    updated_at = excluded.updated_at
                """,
                (day, now),
            )
            row = conn.execute("SELECT used_count FROM api_usage WHERE day = ?", (day,)).fetchone()
        return int(row["used_count"]) if row else 0

    def get_cache(self, cache_key: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rating_api_cache WHERE cache_key = ?", (cache_key,)).fetchone()
        return dict(row) if row else None

    def set_cache(
        self,
        *,
        cache_key: str,
        source: str,
        media_type: str,
        imdbid: str,
        tmdbid: str,
        rating: str,
        response_json: str,
        success: bool,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rating_api_cache(
                    cache_key, source, media_type, imdbid, tmdbid, rating,
                    response_json, success, error, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    source = excluded.source,
                    media_type = excluded.media_type,
                    imdbid = excluded.imdbid,
                    tmdbid = excluded.tmdbid,
                    rating = excluded.rating,
                    response_json = excluded.response_json,
                    success = excluded.success,
                    error = excluded.error,
                    fetched_at = excluded.fetched_at
                """,
                (
                    cache_key,
                    source,
                    media_type,
                    imdbid,
                    tmdbid,
                    rating,
                    response_json,
                    1 if success else 0,
                    error,
                    now_iso(),
                ),
            )

    def upsert_record(self, data: dict[str, Any]) -> None:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM rating_records WHERE nfo_path = ?",
                (data["nfo_path"],),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO rating_records(
                    media_path, nfo_path, media_type, title, year, imdbid, tmdbid, country,
                    old_rating, new_rating, rating_source, status, error,
                    nfo_mtime, nfo_size, created_at, updated_at, last_scan_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(nfo_path) DO UPDATE SET
                    media_path = excluded.media_path,
                    media_type = excluded.media_type,
                    title = excluded.title,
                    year = excluded.year,
                    imdbid = excluded.imdbid,
                    tmdbid = excluded.tmdbid,
                    country = excluded.country,
                    old_rating = excluded.old_rating,
                    new_rating = excluded.new_rating,
                    rating_source = excluded.rating_source,
                    status = excluded.status,
                    error = excluded.error,
                    nfo_mtime = excluded.nfo_mtime,
                    nfo_size = excluded.nfo_size,
                    updated_at = excluded.updated_at,
                    last_scan_at = excluded.last_scan_at
                """,
                (
                    data.get("media_path", ""),
                    data["nfo_path"],
                    data.get("media_type", ""),
                    data.get("title", ""),
                    data.get("year", ""),
                    data.get("imdbid", ""),
                    data.get("tmdbid", ""),
                    data.get("country", ""),
                    data.get("old_rating", ""),
                    data.get("new_rating", ""),
                    data.get("rating_source", ""),
                    data.get("status", ""),
                    data.get("error", ""),
                    data.get("nfo_mtime", 0.0),
                    data.get("nfo_size", 0),
                    created_at,
                    data.get("updated_at", now),
                    data.get("last_scan_at", now),
                ),
            )

    def get_record(self, record_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rating_records WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None

    def get_record_by_nfo(self, nfo_path: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rating_records WHERE nfo_path = ?", (nfo_path,)).fetchone()
        return dict(row) if row else None

    def update_manual_rating(self, record_id: int, new_rating: str, *, success: bool, error: str = "") -> None:
        record = self.get_record(record_id)
        if not record:
            raise StorageError("记录不存在")
        now = now_iso()
        old_rating = record.get("new_rating") or record.get("old_rating") or ""
        status = "manual_updated" if success else "manual_failed"
        source = "manual" if success else record.get("rating_source", "")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE rating_records
                SET old_rating = ?, new_rating = ?, rating_source = ?, status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (old_rating, new_rating if success else record.get("new_rating", ""), source, status, error, now, record_id),
            )

    def list_records(self, filters: Optional[RecordFilters] = None) -> list[dict[str, Any]]:
        filters = filters or RecordFilters()
        where, params = self._build_filter_clause(filters)
        sql = f"""
            SELECT * FROM rating_records
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([filters.limit, filters.offset])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def count_records(self, filters: Optional[RecordFilters] = None) -> int:
        filters = filters or RecordFilters()
        where, params = self._build_filter_clause(filters)
        sql = f"SELECT COUNT(*) AS cnt FROM rating_records {where}"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["cnt"]) if row else 0

    def stats(self, filters: Optional[RecordFilters] = None) -> RecordStats:
        filters = filters or RecordFilters()
        filtered_count = self.count_records(filters)
        total_count = self.count_records(RecordFilters(limit=1_000_000))
        success_statuses = (
            "updated_omdb",
            "updated_tmdb",
            "fallback_mainland",
            "fallback_other",
            "manual_updated",
            "skipped_existing",
        )
        failed_statuses = (
            "no_imdbid_no_tmdbid",
            "api_limit",
            "api_error",
            "parse_error",
            "write_error",
            "manual_failed",
        )
        fallback_statuses = ("fallback_mainland", "fallback_other")
        return RecordStats(
            filtered_count=filtered_count,
            total_count=total_count,
            success_count=self._count_by_statuses(filters, success_statuses),
            failed_count=self._count_by_statuses(filters, failed_statuses),
            fallback_count=self._count_by_statuses(filters, fallback_statuses),
            manual_count=self._count_by_statuses(filters, ("manual_updated",)),
        )

    def _count_by_statuses(self, filters: RecordFilters, statuses: tuple[str, ...]) -> int:
        where, params = self._build_filter_clause(filters)
        placeholders = ", ".join("?" for _ in statuses)
        sql = f"SELECT COUNT(*) AS cnt FROM rating_records {where}"
        if where:
            sql += f" AND status IN ({placeholders})"
        else:
            sql += f" WHERE status IN ({placeholders})"
        params.extend(list(statuses))
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["cnt"]) if row else 0

    @staticmethod
    def _build_filter_clause(filters: RecordFilters) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        mapping = {
            "country": filters.country,
            "new_rating": filters.new_rating,
            "status": filters.status,
            "year": filters.year,
            "media_type": filters.media_type,
        }
        for column, value in mapping.items():
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params

    def clear_history(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM rating_records").fetchone()
            conn.execute("DELETE FROM rating_records")
        return int(row["cnt"]) if row else 0

    def status_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM rating_records GROUP BY status ORDER BY cnt DESC"
            ).fetchall()
        return {row["status"]: int(row["cnt"]) for row in rows}

    def list_distinct_values(self, column: str, limit: int = 12) -> list[str]:
        allowed = {"country", "new_rating", "status", "year", "media_type"}
        if column not in allowed:
            return []
        sql = f"""
            SELECT {column} AS value, COUNT(*) AS cnt
            FROM rating_records
            WHERE {column} IS NOT NULL AND TRIM({column}) != ''
            GROUP BY {column}
            ORDER BY cnt DESC, value ASC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (max(1, limit),)).fetchall()
        return [str(row["value"]) for row in rows if row["value"] is not None]

    def dump_cache_json(self, cache_key: str) -> dict[str, Any]:
        row = self.get_cache(cache_key)
        if not row or not row.get("response_json"):
            return {}
        try:
            return json.loads(row["response_json"])
        except json.JSONDecodeError:
            return {}
