from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class StorageError(Exception):
    """数据库关键错误，需安全退出当前任务。"""


@dataclass(frozen=True)
class FileState:
    path: str
    size: int
    mtime: float
    status: str
    jav_code: str = ""
    last_scan: float = 0
    error: str = ""
    retry_count: int = 0


class OrganizerStorage:
    SUCCESS_STATUS = "SUCCESS"

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
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    status TEXT NOT NULL,
                    jav_code TEXT NOT NULL DEFAULT '',
                    last_scan REAL NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)")
            self._ensure_column(conn, "files", "retry_count", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    jav_code TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {row[1] for row in rows}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def get(self, path: Path) -> Optional[FileState]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE path = ?", (str(path),)).fetchone()
        return self._row_to_state(row)

    def is_success_version(self, path: Path, size: int, mtime: float) -> bool:
        """按 path + size + mtime 判断是否已成功处理过相同文件版本。"""
        state = self.get(path)
        if not state:
            return False
        return (
            state.status.upper() == self.SUCCESS_STATUS
            and state.size == size
            and abs(state.mtime - mtime) < 0.001
        )

    def upsert_pending(self, path: Path, size: int, mtime: float) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files(path, size, mtime, status, jav_code, last_scan, error, retry_count)
                VALUES (?, ?, ?, 'pending', '', ?, '', 0)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    status = 'pending',
                    last_scan = excluded.last_scan,
                    error = ''
                """,
                (str(path), size, mtime, now),
            )

    def mark_status(
        self,
        path: Path,
        status: str,
        *,
        size: int,
        mtime: float,
        jav_code: str = "",
        error: str = "",
        retry_count: Optional[int] = None,
    ) -> None:
        now = time.time()
        current = self.get(path)
        retries = current.retry_count if current and retry_count is None else (retry_count or 0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files(path, size, mtime, status, jav_code, last_scan, error, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    status = excluded.status,
                    jav_code = excluded.jav_code,
                    last_scan = excluded.last_scan,
                    error = excluded.error,
                    retry_count = excluded.retry_count
                """,
                (str(path), size, mtime, status, jav_code, now, error, retries),
            )

    def mark_success(self, path: Path, size: int, mtime: float, jav_code: str = "") -> None:
        self.mark_status(path, self.SUCCESS_STATUS, size=size, mtime=mtime, jav_code=jav_code, error="", retry_count=0)

    def mark_failed(self, path: Path, size: int, mtime: float, error: str) -> int:
        current = self.get(path)
        retry_count = (current.retry_count if current else 0) + 1
        self.mark_status(
            path,
            "failed",
            size=size,
            mtime=mtime,
            error=error,
            retry_count=retry_count,
        )
        return retry_count

    def mark_missing(self, path: Path, size: int, mtime: float) -> None:
        self.mark_status(path, "missing", size=size, mtime=mtime, error="文件已消失")

    def status_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, count(*) AS total FROM files GROUP BY status").fetchall()
        return {row["status"]: row["total"] for row in rows}

    def append_history(
        self,
        source_path: Path,
        target_path: Path,
        status: str,
        jav_code: str = "",
        error: str = "",
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO history(source_path, target_path, status, jav_code, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(source_path), str(target_path), status, jav_code, error, now),
            )

    def list_history(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(2000, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source_path, target_path, status, jav_code, error, created_at
                FROM history
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "source_path": row["source_path"],
                "target_path": row["target_path"],
                "status": row["status"],
                "jav_code": row["jav_code"] or "",
                "error": row["error"] or "",
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def clear_history(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM history").fetchone()
            total = int(row["total"]) if row else 0
            conn.execute("DELETE FROM history")
        return total

    @staticmethod
    def _row_to_state(row: Any) -> Optional[FileState]:
        if not row:
            return None
        keys = row.keys() if hasattr(row, "keys") else []
        retry_count = int(row["retry_count"]) if "retry_count" in keys else 0
        return FileState(
            path=row["path"],
            size=int(row["size"]),
            mtime=float(row["mtime"]),
            status=row["status"],
            jav_code=row["jav_code"] or "",
            last_scan=float(row["last_scan"]),
            error=row["error"] or "",
            retry_count=retry_count,
        )
