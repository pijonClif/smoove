from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator, Dict
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import init_db
from app.routers import slack, wa

import sys

# Configure logging with force=True so uvicorn doesn't swallow application logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger("wa-slack-bridge")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Initializing SQLite database tables...")
    init_db()
    logger.info("wa-slack-bridge service started successfully.")
    yield
    logger.info("wa-slack-bridge service shutting down.")


settings = get_settings()

app = FastAPI(
    title="wa-slack-bridge",
    description="FastAPI bridge between Twilio WhatsApp and Slack with SQLite persistence and LLM ticket extraction.",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.DEBUG,
)

app.include_router(wa.router)
app.include_router(slack.router)


@app.get("/health", response_class=JSONResponse, tags=["health"])
async def health_check() -> Dict[str, str]:
    return {
        "status": "healthy",
        "service": "wa-slack-bridge",
        "version": "1.0.0",
    }


@app.get("/", response_class=JSONResponse, tags=["root"])
async def root() -> Dict[str, Any]:
    # just so it's not a bare 404 if someone hits the root url
    return {
        "message": "wa-slack-bridge is running",
        "docs_url": "/docs",
        "health_url": "/health",
        "webhooks": {
            "whatsapp": "/webhook/wa",
            "slack": "/webhook/slack",
        },
    }
