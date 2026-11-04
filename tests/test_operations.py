import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from alembic import command
from alembic.config import Config
from database import Membership, Workspace, Task, LoginSession, OAuthFlow, Invitation, RateBucket
from security import now, digest
import config
import main
import manage


def test_legacy_import_is_explicit_readonly_and_refuses_duplicates(client, project, tmp_path, store):
    user, _, p = project
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, description TEXT, due_date TEXT, owner TEXT, priority TEXT, status TEXT)")
        db.execute("INSERT INTO tasks VALUES (1, 'Original', NULL, NULL, 'High', 'To Do')")
    original = source.read_bytes()
    with pytest.raises(ValueError):
        manage.import_legacy(source, p["id"], "wrong@example.com")
    manage.import_legacy(source, p["id"], user["email"])
    assert source.read_bytes() == original
    with store() as db:
        assert db.scalar(select(Task).where(Task.project_id == p["id"])).description == "Original"
    with pytest.raises(ValueError):
        manage.import_legacy(source, p["id"], user["email"])


def test_cleanup_keeps_active_sessions(store, account):
    user = account()
    with store.begin() as db:
        db.add(LoginSession(token_hash=digest("expired"), user_id=user["id"], csrf="x", expires_at=now() - 1))
        db.add(OAuthFlow(state_hash=digest("state"), browser_hash="b", nonce="n", verifier="v", expires_at=now() - 1))
        db.add(RateBucket(key="expired", window=1, count=1, expires_at=now() - 1))
    manage.cleanup()
    with store() as db:
        assert db.get(LoginSession, digest("expired")) is None
        assert db.get(LoginSession, digest(user["token"])) is not None
        assert list(db.scalars(select(OAuthFlow))) == []


@pytest.mark.parametrize("failure", ["sqlite", "http", "credentials"])
def test_production_configuration_fails_closed(monkeypatch, failure):
    monkeypatch.setattr(config, "PRODUCTION", True)
    monkeypatch.setattr(config, "APP_URL", "https://taskiq.example")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/taskiq")
    if failure == "sqlite": monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    if failure == "http": monkeypatch.setattr(config, "APP_URL", "http://taskiq.example")
    if failure == "credentials": monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    with pytest.raises(RuntimeError): config.validate()


def test_readiness_reports_database_failure(client, monkeypatch):
    class Broken:
        def connect(self): raise ConnectionError("private-database-address")
    monkeypatch.setattr(main, "engine", Broken())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "private-database-address" not in response.text


def test_ai_can_be_disabled(client, project, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    _, _, p = project
    result = client.post(f"/api/projects/{p['id']}/tasks/summary")
    assert result.status_code == 503


def test_invitation_respects_workspace_membership_limit(client, project, account, store):
    _, w, _ = project
    invite = client.post(f"/api/workspaces/{w['id']}/invitations", json={"email": "many@example.com"}).json()
    user = account("many@example.com")
    with store.begin() as db:
        for i in range(50):
            workspace = Workspace(name=f"Workspace {i}")
            db.add(workspace)
            db.flush()
            db.add(Membership(workspace_id=workspace.id, user_id=user["id"], role="Member"))
    token = invite["invite_url"].split("#invite=")[1]
    assert client.post("/api/invitations/accept", json={"token": token}).status_code == 409
    assert client.post("/api/workspaces", json={"name": "Another"}).status_code == 409


@pytest.mark.skipif(not os.getenv("TASKIQ_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_concurrent_admin_demotions_keep_one_admin(client, project, account, store):
    first, w, _ = project
    second = account("other-admin@example.com", login=False)
    with store.begin() as db:
        db.add(Membership(workspace_id=w["id"], user_id=second["id"], role="Admin"))
    def demote(user):
        with TestClient(main.app, base_url=config.APP_URL) as own:
            own.cookies.set(config.SESSION_COOKIE, user["token"])
            return own.patch(f"/api/workspaces/{w['id']}/members/{user['id']}", json={"role": "Viewer"}, headers={"Origin": config.APP_URL, "X-CSRF-Token": user["csrf"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(demote, [first, second]))
    assert sorted(statuses) == [200, 409]
    with store() as db:
        assert len(list(db.scalars(select(Membership).where(Membership.workspace_id == w["id"], Membership.role == "Admin")))) == 1


@pytest.mark.skipif(not os.getenv("TASKIQ_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_concurrent_invitation_acceptance_is_single_use(client, project, account, store):
    _, w, _ = project
    invite = client.post(f"/api/workspaces/{w['id']}/invitations", json={"email": "joiner@example.com"}).json()
    token = invite["invite_url"].split("#invite=")[1]
    user = account("joiner@example.com", login=False)
    def accept(_):
        with TestClient(main.app, base_url=config.APP_URL) as own:
            own.cookies.set(config.SESSION_COOKIE, user["token"])
            return own.post("/api/invitations/accept", json={"token": token}, headers={"Origin": config.APP_URL, "X-CSRF-Token": user["csrf"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(accept, range(2)))
    assert sorted(statuses) == [200, 404]
