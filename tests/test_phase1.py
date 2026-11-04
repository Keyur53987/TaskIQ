import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
import pytest
from sqlalchemy import select, update
from database import Membership, User, Task, LoginSession, Invitation, OAuthFlow, RateBucket
from security import digest, now, rate_limit
import config
from routers import auth as auth_routes


def path(project):
    return f"/api/projects/{project['id']}/tasks"


def test_public_shell_and_protected_data(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/projects/unknown/tasks").status_code == 401
    assert client.get("/api/tasks").status_code == 404


def test_workspace_project_and_manual_task(client, project):
    user, workspace, p = project
    created = client.post(path(p), json={"description": "Ship", "assignee_id": user["id"]})
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["owner"] == "admin"
    assert client.get(path(p)).json()["tasks"][0]["description"] == "Ship"
    assert client.put(f"{path(p)}/{task['id']}", json={"status": "Done"}).json()["status"] == "Done"
    assert client.delete(f"{path(p)}/{task['id']}").status_code == 204


def test_csrf_and_origin(client, project):
    _, _, p = project
    assert client.post(path(p), json={"description": "x"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post(path(p), json={"description": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    del client.headers["Origin"]
    assert client.post(path(p), json={"description": "x"}).status_code == 403


def test_cross_workspace_access_every_task_operation(client, project, account, monkeypatch):
    _, workspace, p = project
    task = client.post(path(p), json={"description": "Private"}).json()
    account("outsider@example.com")
    assert client.get(path(p)).status_code == 404
    assert client.get(f"{path(p)}/{task['id']}").status_code == 404
    assert client.put(f"{path(p)}/{task['id']}", json={"status": "Done"}).status_code == 404
    assert client.delete(f"{path(p)}/{task['id']}").status_code == 404
    assert client.post(path(p), json={"description": "Injected"}).status_code == 404
    assert client.post(path(p) + "/extract", json={"text": "Some sufficiently long notes"}).status_code == 404
    assert client.post(path(p) + "/summary").status_code == 404
    assert client.get(f"/api/workspaces/{workspace['id']}/members").status_code == 404
    assert client.get(f"/api/workspaces/{workspace['id']}/projects").status_code == 404


@pytest.mark.parametrize("role", ["Member", "Viewer"])
def test_role_matrix(client, project, account, store, role):
    _, w, p = project
    member = account(role.lower() + "@example.com")
    with store.begin() as db:
        db.add(Membership(workspace_id=w["id"], user_id=member["id"], role=role))
    assert client.get(path(p)).status_code == 200
    assert client.post(f"/api/workspaces/{w['id']}/projects", json={"name": "Forbidden"}).status_code == 403
    assert client.post(f"/api/workspaces/{w['id']}/invitations", json={"email": "new@example.com"}).status_code == 403
    result = client.post(path(p), json={"description": "Write"})
    assert result.status_code == (201 if role == "Member" else 403)
    if role == "Viewer":
        assert client.post(path(p) + "/summary").status_code == 403
        assert client.post(path(p) + "/extract", json={"text": "Some long notes for extract"}).status_code == 403


def test_cross_project_idor(client, project):
    _, w, p = project
    other = client.post(f"/api/workspaces/{w['id']}/projects", json={"name": "Other"}).json()
    task = client.post(path(p), json={"description": "Only here"}).json()
    for method, kwargs in [("get", {}), ("put", {"json": {"status": "Done"}}), ("delete", {})]:
        assert getattr(client, method)(f"{path(other)}/{task['id']}", **kwargs).status_code == 404


def test_assignment_validation_and_unassign(client, project, account):
    _, _, p = project
    outsider = account("elsewhere@example.com", login=False)
    assert client.post(path(p), json={"description": "x", "assignee_id": outsider["id"]}).status_code == 422
    own = client.get("/api/auth/me").json()["user"]["id"]
    task = client.post(path(p), json={"description": "x", "assignee_id": own}).json()
    result = client.put(f"{path(p)}/{task['id']}", json={"assignee_id": None}).json()
    assert result["assignee_id"] is None and result["owner"] == ""


def test_last_admin_cannot_be_removed_or_demoted(client, project):
    user, w, _ = project
    endpoint = f"/api/workspaces/{w['id']}/members/{user['id']}"
    assert client.patch(endpoint, json={"role": "Viewer"}).status_code == 409
    assert client.delete(endpoint).status_code == 409


def test_invitation_email_binding_single_use_and_roles(client, project, account):
    _, w, _ = project
    result = client.post(f"/api/workspaces/{w['id']}/invitations", json={"email": "joiner@example.com", "role": "Viewer"})
    assert result.status_code == 201, result.text
    token = result.json()["invite_url"].split("#invite=")[1]
    account("wrong@example.com")
    assert client.post("/api/invitations/accept", json={"token": token}).status_code == 403
    account("joiner@example.com")
    assert client.post("/api/invitations/accept", json={"token": token}).status_code == 200
    assert client.post("/api/invitations/accept", json={"token": token}).status_code == 404
    assert client.get("/api/workspaces").json()[0]["role"] == "Viewer"


@pytest.mark.parametrize("action", ["expire", "revoke", "replace"])
def test_expired_revoked_replaced_invites(client, project, account, store, action):
    _, w, _ = project
    endpoint = f"/api/workspaces/{w['id']}/invitations"
    result = client.post(endpoint, json={"email": "joiner@example.com"}).json()
    token = result["invite_url"].split("#invite=")[1]
    if action == "expire":
        with store.begin() as db:
            db.execute(update(Invitation).values(expires_at=now() - 1))
    elif action == "revoke":
        assert client.delete(endpoint + "/" + result["id"]).status_code == 204
    else:
        client.post(endpoint, json={"email": "joiner@example.com"})
    account("joiner@example.com")
    assert client.post("/api/invitations/accept", json={"token": token}).status_code == 404


def test_membership_removal_revokes_access_and_clears_assignment(client, project, account, store):
    _, w, p = project
    person = account("member@example.com", login=False)
    with store.begin() as db:
        db.add(Membership(workspace_id=w["id"], user_id=person["id"], role="Member"))
    task = client.post(path(p), json={"description": "x", "assignee_id": person["id"]}).json()
    assert client.delete(f"/api/workspaces/{w['id']}/members/{person['id']}").status_code == 204
    assert client.get(f"{path(p)}/{task['id']}").json()["assignee_id"] is None
    client.cookies.set(config.SESSION_COOKIE, person["token"])
    assert client.get(path(p)).status_code == 404


def test_logout_and_expiration(client, project, store):
    user, _, _ = project
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(config.SESSION_COOKIE, user["token"])
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session(client, account, store):
    account()
    with store.begin() as db:
        db.execute(update(LoginSession).values(expires_at=now() - 1))
    assert client.get("/api/auth/me").status_code == 401


def test_logout_all_devices(client, account, store):
    user = account()
    other = secrets.token_urlsafe(32)
    with store.begin() as db:
        db.add(LoginSession(token_hash=digest(other), user_id=user["id"], csrf="x", expires_at=now() + 1000))
    assert client.post("/api/auth/logout-all").status_code == 204
    client.cookies.set(config.SESSION_COOKIE, other)
    assert client.get("/api/auth/me").status_code == 401


def test_profile_validation_and_update(client, account):
    account()
    assert client.patch("/api/auth/me", json={"name": "  "}).status_code == 422
    assert client.patch("/api/auth/me", json={"name": "New name"}).json()["name"] == "New name"


def test_pagination_and_filter_literal_wildcards(client, project):
    _, _, p = project
    for i in range(5):
        client.post(path(p), json={"description": f"Task {i}", "owner": "%literal" if i == 0 else "Bob"})
    first = client.get(path(p), params={"limit": 2}).json()
    second = client.get(path(p), params={"limit": 2, "before": first["next_cursor"]}).json()
    assert len(first["tasks"]) == len(second["tasks"]) == 2
    assert not {t["id"] for t in first["tasks"]} & {t["id"] for t in second["tasks"]}
    assert len(client.get(path(p), params={"owner": "%"}).json()["tasks"]) == 1


def test_extraction_and_summary_are_scoped(client, project, monkeypatch):
    _, w, p = project
    other = client.post(f"/api/workspaces/{w['id']}/projects", json={"name": "Other"}).json()
    client.post(path(other), json={"description": "Secret other project"})
    assert client.post(path(p) + "/extract", json={"text": "Sufficiently long meeting notes"}).status_code == 410
    client.post(path(p), json={"description": "Project task"})
    seen = []
    def summary(tasks):
        seen.extend(tasks)
        return "<script>untrusted</script>"
    monkeypatch.setattr("llm.summarize_project_status", summary)
    result = client.post(path(p) + "/summary")
    assert result.status_code == 200
    assert [t["description"] for t in seen] == ["Project task"]


def test_retired_extraction_route_cannot_create_tasks(client, project, monkeypatch, store):
    user, w, p = project
    def extract(text):
        raise AssertionError("Retired endpoint must not invoke the provider")
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    assert client.post(path(p) + "/extract", json={"text": "Sufficiently long meeting notes"}).status_code == 410
    with store() as db:
        assert list(db.scalars(select(Task))) == []


def test_limits_and_errors_do_not_expose_provider_secrets(client, project, monkeypatch):
    _, _, p = project
    assert client.post(path(p) + "/extract", json={"text": "x" * 20001}).status_code == 422
    def broken(text):
        raise RuntimeError("secret-provider-key")
    monkeypatch.setattr("llm.extract_meeting_proposals", broken)
    response = client.post(path(p) + "/extract", json={"text": "Sufficiently long notes"})
    assert response.status_code == 410 and "secret-provider-key" not in response.text
    assert client.post(path(p), content=b"x" * 131073).status_code == 413


def test_shared_rate_limit_atomic_under_concurrency(store):
    def attempt(_):
        from fastapi import HTTPException
        with store() as db:
            try:
                rate_limit(db, "parallel", 3, 60)
                return True
            except HTTPException as error:
                assert error.status_code == 429
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 3


def test_security_headers_and_host(client):
    response = client.get("/api/auth/config")
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health/ready").status_code == 200


def start_oauth(client, store, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    response = client.get("/api/auth/google", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    with store() as db:
        flow = db.get(OAuthFlow, digest(query["state"][0]))
    return query, flow


def test_oauth_success_session_rotation_and_replay(client, account, store, monkeypatch):
    previous = account("old@example.com")
    query, flow = start_oauth(client, store, monkeypatch)
    monkeypatch.setattr(auth_routes.httpx, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id_token": "verified-below"}))
    monkeypatch.setattr(auth_routes, "verify_google_token", lambda token: {"nonce": flow.nonce, "email_verified": True, "sub": "provider-subject", "email": "new@example.com", "name": "New"})
    response = client.get("/api/auth/google/callback", params={"state": query["state"][0], "code": "code"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["user"]["email"] == "new@example.com"
    with store() as db:
        assert db.get(LoginSession, digest(previous["token"])) is None
    assert client.get("/api/auth/google/callback", params={"state": query["state"][0], "code": "code"}).status_code == 400


@pytest.mark.parametrize("failure", ["nonce", "unverified", "provider", "browser", "expired", "state"])
def test_oauth_rejects_invalid_flows(client, store, monkeypatch, failure):
    query, flow = start_oauth(client, store, monkeypatch)
    monkeypatch.setattr(auth_routes.httpx, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id_token": "token"}))
    claims = {"nonce": flow.nonce, "email_verified": True, "sub": "abc", "email": "a@example.com"}
    if failure == "nonce": claims["nonce"] = "wrong"
    if failure == "unverified": claims["email_verified"] = False
    if failure == "provider":
        monkeypatch.setattr(auth_routes, "verify_google_token", lambda token: (_ for _ in ()).throw(ValueError("bad signature")))
    else:
        monkeypatch.setattr(auth_routes, "verify_google_token", lambda token: claims)
    if failure == "browser": client.cookies.clear()
    if failure == "expired":
        with store.begin() as db:
            db.execute(update(OAuthFlow).values(expires_at=now() - 1))
    result = client.get("/api/auth/google/callback", params={"state": "wrong" if failure == "state" else query["state"][0], "code": "code"}, follow_redirects=False)
    assert result.status_code in {400, 303}
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("invalid", [None, "audience", "expired", "signature", "issuer"])
def test_google_id_token_cryptographic_validation(monkeypatch, invalid):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from google.auth import crypt, jwt
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = crypt.RSASigner.from_string(pem, key_id="test")
    claims = {"iss": "https://accounts.google.com", "sub": "abc", "aud": "client", "iat": now() - 120, "exp": now() + 3600}
    if invalid == "audience": claims["aud"] = "attacker"
    if invalid == "expired": claims["exp"] = now() - 60
    if invalid == "issuer": claims["iss"] = "https://attacker.example"
    encoded = jwt.encode(signer, claims).decode()
    if invalid == "signature":
        parts = encoded.split(".")
        parts[-1] = ("A" if parts[-1][0] != "A" else "B") + parts[-1][1:]
        encoded = ".".join(parts)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "client")
    monkeypatch.setattr(auth_routes, "BoundedGoogleRequest", lambda **kw: lambda *a, **kw: SimpleNamespace(status=200, data=json.dumps({"test": public}).encode()))
    if invalid:
        with pytest.raises(Exception): auth_routes.verify_google_token(encoded)
    else:
        assert auth_routes.verify_google_token(encoded)["sub"] == "abc"
