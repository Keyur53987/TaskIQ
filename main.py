"""Same-origin web application. Schema changes run explicitly through Alembic."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text
import config
from database import engine
from routers import auth, workspaces, tasks, meetings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("taskiq")
ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    config.validate()
    startup_engine = engine
    # Fail before serving traffic when migrations have not been applied.
    with startup_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if version != "0002_meetings":
            raise RuntimeError("Apply database migrations: alembic upgrade head")
    yield
    startup_engine.dispose()


app = FastAPI(title="TaskIQ", version="2.1.0", lifespan=lifespan, docs_url=None if config.PRODUCTION else "/docs", redoc_url=None, openapi_url=None if config.PRODUCTION else "/openapi.json")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)


class BodyLimitMiddleware:
    """Bound chunked as well as Content-Length bodies before JSON parsing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > 131072:
                return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    started = time.monotonic()
    request_id = uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        # No query strings, request bodies, tokens, or provider exceptions in logs.
        logger.error("request_failed id=%s", request_id)
        response = JSONResponse({"detail": "An unexpected error occurred", "request_id": request_id}, status_code=500)
    response.headers.update({"X-Request-ID": request_id, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
    if request.url.path.startswith("/api/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    if config.PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    logger.info("request id=%s method=%s status=%d duration_ms=%d", request_id, request.method, response.status_code, (time.monotonic() - started) * 1000)
    return response


app.add_middleware(BodyLimitMiddleware)
app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(tasks.router)
app.include_router(meetings.router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def ready():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
