"""Upgrade a populated Phase 1 schema in an isolated test DB, never production."""
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from database import Task, Membership


def test_phase1_task_and_membership_survive_upgrade(store):
    engine = store.kw["bind"]
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0001_phase1")
        connection.execute(text("INSERT INTO users (id,google_sub,email,name,created_at) VALUES ('migration-user','migration-sub','migration@example.com','Admin',1)"))
        connection.execute(text("INSERT INTO workspaces (id,name) VALUES ('migration-workspace','Existing team')"))
        connection.execute(text("INSERT INTO memberships (workspace_id,user_id,role) VALUES ('migration-workspace','migration-user','Admin')"))
        connection.execute(text("INSERT INTO projects (id,workspace_id,name) VALUES ('migration-project','migration-workspace','Existing project')"))
        connection.execute(text("INSERT INTO project_tasks (id,project_id,description,due_date,owner,assignee_id,priority,status) VALUES (7,'migration-project','Existing private task','2026-11-06','Admin','migration-user','High','In Progress')"))
        command.upgrade(cfg, "head")
    with store() as db:
        task = db.get(Task, 7)
        assert task.description == "Existing private task"
        assert task.status == "In Progress" and task.priority == "High"
        assert task.assignee_id == "migration-user"
        assert task.source_proposal_id is None and task.source_proposal is None
        assert db.get(Membership, ("migration-workspace", "migration-user")).role == "Admin"
