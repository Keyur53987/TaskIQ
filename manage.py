"""Operator commands. No HTTP route can claim legacy data or create login sessions."""
import argparse
import sqlite3
from pathlib import Path
from sqlalchemy import select, delete, func
import database
from database import LoginSession, OAuthFlow, Invitation, RateBucket, Project, Workspace, Membership, User, Task
from security import now


def cleanup():
    with database.SessionLocal.begin() as db:
        for model in (LoginSession, OAuthFlow, Invitation, RateBucket):
            result = db.execute(delete(model).where(model.expires_at <= now()))
            print(f"{model.__tablename__}: removed {result.rowcount} expired rows")


def import_legacy(source, project_id, admin_email):
    """Read legacy SQLite without modifying it; require an explicit known owner."""
    source_path = Path(source).resolve(strict=True)
    with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        rows = legacy.execute("SELECT description, due_date, owner, priority, status FROM tasks ORDER BY id").fetchall()
    from routers.tasks import TaskFields
    values = [TaskFields.model_validate({k: row[k] or "" for k in row.keys()}).model_dump() for row in rows]
    with database.SessionLocal.begin() as db:
        project = db.get(Project, project_id)
        if project:
            db.execute(select(Workspace).where(Workspace.id == project.workspace_id).with_for_update()).scalar_one()
        user = db.scalar(select(User).where(User.email == admin_email.strip().lower()))
        member = db.get(Membership, (project.workspace_id, user.id)) if project and user else None
        if not member or member.role != "Admin":
            raise ValueError("Target project must belong to the specified signed-in workspace Admin")
        if db.scalar(select(func.count()).select_from(Task).where(Task.project_id == project_id)):
            raise ValueError("Import requires an empty project; refusing possible duplicates")
        db.add_all([Task(project_id=project_id, **value) for value in values])
    print(f"Imported {len(values)} tasks. Original SQLite file was not modified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("cleanup")
    legacy = sub.add_parser("import-legacy")
    legacy.add_argument("--source", required=True)
    legacy.add_argument("--project-id", required=True)
    legacy.add_argument("--admin-email", required=True)
    args = parser.parse_args()
    if args.command == "cleanup":
        cleanup()
    else:
        import_legacy(args.source, args.project_id, args.admin_email)
