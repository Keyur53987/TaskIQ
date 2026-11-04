"""Authentication, workspace authorization, and shared atomic rate limits."""
import hashlib
import secrets
import time
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
import config
from database import get_db, LoginSession, User, Membership, Workspace, Project, RateBucket


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return int(time.time())


@dataclass
class Identity:
    user: User
    session: LoginSession


def identity(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(config.SESSION_COOKIE, "")
    session = db.get(LoginSession, digest(token)) if token else None
    if session is None or session.expires_at <= now():
        raise HTTPException(401, "Sign in to continue")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("origin") != config.APP_URL:
            raise HTTPException(403, "Invalid request origin")
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "Invalid CSRF token")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Sign in to continue")
    return Identity(user, session)


def membership(db, workspace_id, user_id, roles=None, lock=False):
    if lock:
        db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).scalar_one_or_none()
    member = db.get(Membership, (workspace_id, user_id), populate_existing=True)
    if member is None:
        raise HTTPException(404, "Workspace not found")
    if roles and member.role not in roles:
        raise HTTPException(403, "Your workspace role does not allow this action")
    return member


def project_access(db, project_id, user_id, write=False):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    member = membership(db, project.workspace_id, user_id, {"Admin", "Member"} if write else None)
    return project, member


def rate_limit(db, key, limit, seconds):
    """Conditional upsert is atomic across workers and replicas."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    window = now() // seconds
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    stmt = insert(RateBucket).values(key=key, window=window, count=1, expires_at=(window + 2) * seconds)
    stmt = stmt.on_conflict_do_update(index_elements=[RateBucket.key, RateBucket.window], set_={"count": RateBucket.count + 1}, where=RateBucket.count < limit).returning(RateBucket.count)
    allowed = db.execute(stmt).scalar_one_or_none()
    db.commit()
    if allowed is None:
        raise HTTPException(429, "Too many requests; try again later", headers={"Retry-After": str(seconds - now() % seconds)})


def mutation_limit(db, user_id):
    rate_limit(db, "write:" + user_id, 60, 60)
