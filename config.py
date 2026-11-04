"""Validated deployment settings; fail closed on unsafe production configuration."""
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()
ENV = os.getenv("APP_ENV", "development")
PRODUCTION = ENV == "production"
APP_URL = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
SESSION_COOKIE = "__Host-taskiq" if PRODUCTION else "taskiq_session"
FLOW_COOKIE = "__Host-taskiq-oauth" if PRODUCTION else "taskiq_oauth"
SESSION_SECONDS = 60 * 60 * 24 * 7
AI_ENABLED = os.getenv("AI_ENABLED", "false" if PRODUCTION else "true").lower() == "true"
ALLOWED_HOSTS = [urlparse(APP_URL).hostname or "localhost"]
if not PRODUCTION:
    ALLOWED_HOSTS += ["127.0.0.1", "testserver"]


def validate():
    if ENV not in {"development", "production", "test"}:
        raise RuntimeError("APP_ENV must be development, test, or production")
    if not PRODUCTION:
        return
    origin = urlparse(APP_URL)
    if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise RuntimeError("Production APP_URL must be an HTTPS origin without a path")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth credentials are required in production")
    if not os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Production requires PostgreSQL DATABASE_URL")
