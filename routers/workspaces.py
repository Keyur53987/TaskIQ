"""Workspace-wide roles: Admin manages the team/projects; Member edits tasks; Viewer reads."""
import secrets
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
import config
from database import get_db, Workspace, Membership, Project, User, Invitation, Task
from security import Identity, identity, membership, mutation_limit, digest, now

router = APIRouter(prefix="/api", tags=["workspaces"])
Role = Literal["Admin", "Member", "Viewer"]


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")


class InviteBody(BaseModel):
    email: EmailStr = Field(max_length=254)
    role: Role = "Member"


class RoleBody(BaseModel):
    role: Role


class AcceptBody(BaseModel):
    token: str = Field(min_length=40, max_length=100)


@router.get("/workspaces")
def list_workspaces(auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, Membership.role).join(Membership).where(Membership.user_id == auth.user.id).order_by(Workspace.name).limit(100))
    return [{"id": w.id, "name": w.name, "role": role} for w, role in rows]


@router.post("/workspaces", status_code=201)
def create_workspace(body: NameBody, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    # Serialize per-user creation quotas across concurrent requests.
    db.execute(select(User).where(User.id == auth.user.id).with_for_update()).scalar_one()
    count = db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == auth.user.id))
    if count >= 50:
        raise HTTPException(409, "Workspace limit reached (50)")
    workspace = Workspace(name=body.name.strip())
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=auth.user.id, role="Admin"))
    db.commit()
    return {"id": workspace.id, "name": workspace.name, "role": "Admin"}


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(workspace_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    membership(db, workspace_id, auth.user.id)
    projects = db.scalars(select(Project).where(Project.workspace_id == workspace_id).order_by(Project.name).limit(200))
    return [{"id": p.id, "name": p.name, "workspace_id": p.workspace_id} for p in projects]


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
def create_project(workspace_id: str, body: NameBody, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    membership(db, workspace_id, auth.user.id, {"Admin"}, lock=True)
    if db.scalar(select(func.count()).select_from(Project).where(Project.workspace_id == workspace_id)) >= 200:
        raise HTTPException(409, "Project limit reached (200)")
    project = Project(workspace_id=workspace_id, name=body.name.strip())
    db.add(project)
    db.commit()
    return {"id": project.id, "name": project.name, "workspace_id": workspace_id}


@router.get("/workspaces/{workspace_id}/members")
def list_members(workspace_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    membership(db, workspace_id, auth.user.id)
    rows = db.execute(select(User, Membership.role).join(Membership).where(Membership.workspace_id == workspace_id).order_by(User.name))
    return [{"id": u.id, "name": u.name, "email": u.email, "role": role} for u, role in rows]


def protect_last_admin(db, workspace_id, target, new_role=None):
    if target.role == "Admin" and new_role != "Admin":
        admins = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id, Membership.role == "Admin"))
        if admins <= 1:
            raise HTTPException(409, "Keep at least one workspace Admin")


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
def change_role(workspace_id: str, user_id: str, body: RoleBody, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    membership(db, workspace_id, auth.user.id, {"Admin"}, lock=True)
    target = membership(db, workspace_id, user_id)
    protect_last_admin(db, workspace_id, target, body.role)
    target.role = body.role
    db.commit()
    return {"id": user_id, "role": target.role}


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(workspace_id: str, user_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    membership(db, workspace_id, auth.user.id, {"Admin"}, lock=True)
    target = membership(db, workspace_id, user_id)
    protect_last_admin(db, workspace_id, target)
    # Former assignees cannot remain active assignments in this workspace.
    from sqlalchemy import update
    db.execute(update(Task).where(Task.assignee_id == user_id, Task.project_id.in_(select(Project.id).where(Project.workspace_id == workspace_id))).values(assignee_id=None, owner=""))
    db.delete(target)
    db.commit()


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def invite(workspace_id: str, body: InviteBody, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    membership(db, workspace_id, auth.user.id, {"Admin"}, lock=True)
    if db.scalar(select(func.count()).select_from(Invitation).where(Invitation.workspace_id == workspace_id, Invitation.expires_at > now())) >= 200:
        raise HTTPException(409, "Pending invitation limit reached (200)")
    email = str(body.email).lower()
    existing = db.scalar(select(User.id).join(Membership).where(User.email == email, Membership.workspace_id == workspace_id))
    if existing:
        raise HTTPException(409, "This person is already a workspace member")
    db.execute(delete(Invitation).where(Invitation.workspace_id == workspace_id, Invitation.email == email))
    token = secrets.token_urlsafe(32)
    invitation = Invitation(workspace_id=workspace_id, email=email, role=body.role, token_hash=digest(token), expires_at=now() + 7 * 86400)
    db.add(invitation)
    db.commit()
    # Explicit copy/share flow: no external email service or implicit message sending.
    return {"id": invitation.id, "email": email, "role": body.role, "expires_at": invitation.expires_at, "invite_url": config.APP_URL + "/#invite=" + token}


@router.get("/workspaces/{workspace_id}/invitations")
def list_invites(workspace_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    membership(db, workspace_id, auth.user.id, {"Admin"})
    invites = db.scalars(select(Invitation).where(Invitation.workspace_id == workspace_id, Invitation.expires_at > now()).limit(200))
    return [{"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at} for i in invites]


@router.delete("/workspaces/{workspace_id}/invitations/{invite_id}", status_code=204)
def revoke_invite(workspace_id: str, invite_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    membership(db, workspace_id, auth.user.id, {"Admin"}, lock=True)
    db.execute(delete(Invitation).where(Invitation.id == invite_id, Invitation.workspace_id == workspace_id))
    db.commit()


@router.post("/invitations/accept")
def accept_invite(body: AcceptBody, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == digest(body.token)))
    if invitation is None:
        raise HTTPException(404, "Invitation is invalid or expired")
    # Same lock used by revoke/role changes; reload after waiting for the lock.
    db.execute(select(Workspace).where(Workspace.id == invitation.workspace_id).with_for_update()).scalar_one()
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation.id).execution_options(populate_existing=True))
    if invitation is None or invitation.expires_at <= now():
        raise HTTPException(404, "Invitation is invalid or expired")
    if invitation.email != auth.user.email:
        raise HTTPException(403, "Sign in with the email address this invitation was issued to")
    if db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == invitation.workspace_id)) >= 200:
        raise HTTPException(409, "Workspace member limit reached (200)")
    existing = db.get(Membership, (invitation.workspace_id, auth.user.id))
    if not existing:
        db.execute(select(User).where(User.id == auth.user.id).with_for_update()).scalar_one()
        if db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == auth.user.id)) >= 50:
            raise HTTPException(409, "Workspace membership limit reached (50)")
        db.add(Membership(workspace_id=invitation.workspace_id, user_id=auth.user.id, role=invitation.role))
    workspace_id = invitation.workspace_id
    db.delete(invitation)
    db.commit()
    return {"workspace_id": workspace_id}
