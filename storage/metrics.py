"""
LCO — Metrics Storage
Persists per-request metrics to a local SQLite database via aiosqlite.
"""

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
import aiosqlite

from ..config import settings

# Internal paths that must never appear in the metrics table
INTERNAL_PATHS = {"/health", "/lco/status", "/lco/recent",
                  "/lco/dashboard", "/lco/docs", "/openapi.json"}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS requests (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                    REAL    NOT NULL,
    provider              TEXT    NOT NULL,
    model                 TEXT    NOT NULL,
    path                  TEXT    NOT NULL,
    streaming             INTEGER NOT NULL DEFAULT 0,
    safe_zone_hit         INTEGER NOT NULL DEFAULT 0,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    total_tokens          INTEGER,
    input_tokens_saved    INTEGER NOT NULL DEFAULT 0,
    output_tokens_saved   INTEGER NOT NULL DEFAULT 0,
    latency_ms            REAL,
    quality_score         REAL,
    compression_mode      TEXT,
    status_code           INTEGER
);
"""

# Migration: add new columns to existing DBs that were created before Phase 2
_MIGRATIONS = [
    "ALTER TABLE requests ADD COLUMN input_tokens_saved  INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE requests ADD COLUMN output_tokens_saved INTEGER NOT NULL DEFAULT 0",
]


@dataclass
class RequestRecord:
    provider: str
    model: str
    path: str
    streaming: bool = False
    safe_zone_hit: bool = False
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    quality_score: Optional[float] = None
    compression_mode: str = "passthrough"
    status_code: int = 200
    input_tokens_saved_est: int = 0
    output_tokens_saved_est: int = 0
    ts: float = field(default_factory=time.time)

    @property
    def is_internal(self) -> bool:
        """True for health/status/dashboard routes — never record these."""
        return self.path in INTERNAL_PATHS or self.path.startswith("/lco/")


class MetricsDB:
    _instance: Optional["MetricsDB"] = None
    _lock = asyncio.Lock()

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    @classmethod
    async def get(cls) -> "MetricsDB":
        async with cls._lock:
            if cls._instance is None:
                inst = cls(settings.db_path)
                await inst._init()
                cls._instance = inst
            return cls._instance

    async def _init(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        # Run migrations safely (ignore if column already exists)
        for migration in _MIGRATIONS:
            try:
                await self._db.execute(migration)
                await self._db.commit()
            except Exception:
                pass  # Column already exists — fine

    async def record(self, r: RequestRecord) -> None:
        """Record a request. Internal routes are silently skipped."""
        if self._db is None or r.is_internal:
            return
        await self._db.execute(
            """INSERT INTO requests
               (ts, provider, model, path, streaming, safe_zone_hit,
                input_tokens, output_tokens, total_tokens,
                input_tokens_saved, output_tokens_saved,
                latency_ms, quality_score, compression_mode, status_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.ts, r.provider, r.model, r.path,
                int(r.streaming), int(r.safe_zone_hit),
                r.input_tokens, r.output_tokens, r.total_tokens,
                r.input_tokens_saved_est, r.output_tokens_saved_est,
                r.latency_ms, r.quality_score,
                r.compression_mode, r.status_code,
            ),
        )
        await self._db.commit()

    async def summary(self, last_n: int = 1000) -> dict:
        if self._db is None:
            return {}
        async with self._db.execute(
            """SELECT
                COUNT(*)                                          AS total_requests,
                SUM(input_tokens)                                 AS total_input_tokens,
                SUM(output_tokens)                                AS total_output_tokens,
                SUM(input_tokens_saved)                           AS total_input_saved,
                SUM(output_tokens_saved)                          AS total_output_saved,
                SUM(input_tokens_saved + output_tokens_saved)     AS total_tokens_saved,
                AVG(latency_ms)                                   AS avg_latency_ms,
                MIN(latency_ms)                                   AS min_latency_ms,
                MAX(latency_ms)                                   AS max_latency_ms,
                AVG(quality_score)                                AS avg_quality_score,
                SUM(safe_zone_hit)                                AS safe_zone_hits,
                SUM(CASE WHEN streaming=1 THEN 1 ELSE 0 END)     AS streaming_requests
               FROM (SELECT * FROM requests ORDER BY id DESC LIMIT ?)""",
            (last_n,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return {}
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    async def recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent real (non-internal) requests."""
        if self._db is None:
            return []
        async with self._db.execute(
            """SELECT ts, provider, model, path, streaming, safe_zone_hit,
                      input_tokens, output_tokens, total_tokens,
                      input_tokens_saved, output_tokens_saved,
                      latency_ms, quality_score, compression_mode, status_code
               FROM requests
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in rows]

    async def close(self) -> None:
        if self._db:
            await self._db.close()