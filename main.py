"""
main.py - Entry point for the AI Project Manager Assistant.

Configures logging, initializes the database, and mounts the
FastAPI application with API routes and static file serving.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

import database
from routers import tasks

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Mini AI Project Manager Assistant",
    description=(
        "An intelligent tool that converts unstructured meeting notes "
        "into structured, actionable tasks using LLMs."
    ),
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Startup Events
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    """Initialize the database on application startup."""
    logger.info("Starting AI Project Manager Assistant...")
    database.init_db()
    logger.info("Application ready.")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(tasks.router)

# Serve frontend static assets
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def read_index() -> FileResponse:
    """Serve the frontend single-page application."""
    return FileResponse("static/index.html")
