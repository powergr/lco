"""
LCO — Metrics Middleware
Times each request and records a row in the metrics DB after it completes.
Attaches timing context to the request state so the proxy router can
add token counts before the record is finalised.
"""

from __future__ import annotations
import time
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ..storage.metrics import MetricsDB, RequestRecord

logger = logging.getLogger("lco.metrics")


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()

        # Attach a mutable record to request state so the router can fill it
        record = RequestRecord(
            provider="unknown",
            model="unknown",
            path=request.url.path,
        )
        request.state.metrics = record

        try:
            response = await call_next(request)
            record.status_code = response.status_code
        except Exception:
            record.status_code = 500
            raise
        finally:
            record.latency_ms = (time.perf_counter() - start) * 1000
            try:
                db = await MetricsDB.get()
                await db.record(record)
            except Exception as exc:
                logger.warning("Failed to record metrics: %s", exc)

        return response
