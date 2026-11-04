"""Tests use real cookies, CSRF checks, migrations and database permission checks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secrets
import os
import uuid
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
import database
import main
import config
from security import digest, now


@pytest.fixture
def store(tmp_path, monkeypatch):
    postgres_url = os.getenv("TASKIQ_TEST_DATABASE_URL")
    schema = "test_" + uuid.uuid4().hex
    if postgres_url:
        # Only a dedicated disposable test database is permitted. Each test gets its own schema.
        from sqlalchemy import event
        from sqlalchemy.schema import CreateSchema, DropSchema
        admin_engine = database.build_engine(postgres_url)
        with admin_engine.begin() as conn:
            conn.execute(CreateSchema(schema))
        engine = database.build_engine(postgres_url)
        @event.listens_for(engine, "connect")
        def search_path(connection, _):
            previous = connection.autocommit
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO "' + schema + '"')
            connection.autocommit = previous
    else:
        engine = database.build_engine("sqlite:///" + str(tmp_path / "test.db"))
    with engine.connect() as conn:
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(main, "engine", engine)
    yield factory
    engine.dispose()
    if postgres_url:
        with admin_engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


@pytest.fixture
def client(store):
    with TestClient(main.app, base_url=config.APP_URL) as client:
        yield client


@pytest.fixture
def account(store, client):
    def create(email="admin@example.com", login=True):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with store.begin() as db:
            user = database.User(google_sub=secrets.token_hex(16), email=email, name=email.split("@")[0])
            db.add(user)
            db.flush()
            db.add(database.LoginSession(token_hash=digest(token), user_id=user.id, csrf=csrf, expires_at=now() + 3600))
        if login:
            client.cookies.set(config.SESSION_COOKIE, token)
            client.headers.update({"Origin": config.APP_URL, "X-CSRF-Token": csrf})
        return {"id": user.id, "token": token, "csrf": csrf, "email": email}
    return create


@pytest.fixture
def project(client, account):
    user = account()
    workspace = client.post("/api/workspaces", json={"name": "Team"}).json()
    project = client.post(f"/api/workspaces/{workspace['id']}/projects", json={"name": "Launch"}).json()
    return user, workspace, project
