"""Point d'entrée FastAPI VIGIE."""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from agent.config import ALERT_INTERVAL_MINUTES, APP_VERSION, DATA_DIR
from agent.db.session import init_db
from agent.mcp.server import build_mcp_server
from agent.routes.alerts import router as alerts_router
from agent.routes.ask import router as ask_router
from agent.routes.discover import router as discover_router
from agent.routes.health import router as health_router
from agent.routes.metrics import router as metrics_router
from agent.routes.report import router as report_router
from agent.services.alerting import run_alert_cycle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vigie")

scheduler = AsyncIOScheduler()


async def _alert_job():
    try:
        n = await run_alert_cycle()
        logger.info("Cycle alerting: %s alerte(s)", n)
    except Exception as e:
        logger.exception("Erreur cycle alerting: %s", e)


async def _mcp_asgi(scope, receive, send):
    await scope["app"].state.mcp_app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    scheduler.add_job(
        _alert_job,
        "interval",
        minutes=ALERT_INTERVAL_MINUTES,
        id="alert_cycle",
    )
    scheduler.start()
    logger.info("VIGIE agent v%s démarré", APP_VERSION)
    mcp_server = build_mcp_server()
    app.state.mcp_app = mcp_server.streamable_http_app()
    async with mcp_server.session_manager.run():
        yield
    scheduler.shutdown()


app = FastAPI(title="VIGIE Agent", version=APP_VERSION, lifespan=lifespan)

app.include_router(health_router)
app.include_router(ask_router)
app.include_router(report_router)
app.include_router(metrics_router)
app.include_router(discover_router)
app.include_router(alerts_router)
app.mount("/mcp", _mcp_asgi)
