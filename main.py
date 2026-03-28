"""
LCO — FastAPI Application
Entry point. Wires together middleware, routers, and lifecycle hooks.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .version import __version__ as _VERSION
from .middleware.metrics import MetricsMiddleware
from .proxy.router import router, get_client
from .storage.metrics import MetricsDB

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lco")


def _unique_operation_id(route: APIRoute) -> str:
    """
    Generate a unique OpenAPI operation ID per route+method combination.
    Prevents the 'Duplicate Operation ID' warning when a single route
    handles multiple HTTP methods (e.g. the /v1/{path} catch-all).
    """
    methods = "_".join(sorted(route.methods or ["GET"]))
    return f"{route.name}_{methods}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LCO proxy starting on http://%s:%s", settings.host, settings.port)
    logger.info("Compression mode : %s", settings.compression_mode)
    logger.info("Quality gate     : %s", settings.quality_gate_enabled)
    await MetricsDB.get()
    get_client()
    yield
    db = await MetricsDB.get()
    await db.close()
    client = get_client()
    await client.aclose()
    logger.info("LCO proxy stopped.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LCO — LLM Context Optimizer",
        description="Local-first, OpenAI-compatible proxy that reduces LLM costs.",
        version=_VERSION,
        docs_url="/lco/docs",
        redoc_url=None,
        lifespan=lifespan,
        generate_unique_id_function=_unique_operation_id,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware)
    app.include_router(router)
    return app


app = create_app()