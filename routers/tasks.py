"""All task operations require an explicit project and current workspace membership."""
import logging
from contextlib import contextmanager
from threading import BoundedSemaphore
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
import llm
import config
from database import get_db, Task, User, Membership
from security import Identity, identity, project_access, membership, mutation_limit, rate_limit

router = APIRouter(prefix="/api/projects/{project_id}/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)
AI_SLOTS = BoundedSemaphore(2)


@contextmanager
def ai_slot():
    if not config.AI_ENABLED:
        raise HTTPException(503, "AI features are not enabled on this installation")
    if not AI_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "AI processing is busy; please retry shortly", headers={"Retry-After": "20"})
    try:
        yield
    finally:
        AI_SLOTS.release()


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    due_date: str = Field(default="", max_length=100)
    owner: str = Field(default="", max_length=120)
    assignee_id: str | None = Field(default=None, max_length=36)
    priority: Literal["High", "Medium", "Low"] = "Medium"
    status: Literal["To Do", "In Progress", "Done"] = "To Do"


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, min_length=1, max_length=4000, pattern=r"\S")
    due_date: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=120)
    assignee_id: str | None = Field(default=None, max_length=36)
    priority: Literal["High", "Medium", "Low"] | None = None
    status: Literal["To Do", "In Progress", "Done"] | None = None


class ExtractRequest(BaseModel):
    text: str = Field(min_length=10, max_length=20000)


def task_json(t):
    return {k: getattr(t, k) for k in ("id", "project_id", "description", "due_date", "owner", "assignee_id", "priority", "status")}


def assignment(db, workspace_id, data):
    assignee_id = data.get("assignee_id")
    if assignee_id:
        if not db.get(Membership, (workspace_id, assignee_id)):
            raise HTTPException(422, "Assignee must be a current workspace member")
        data["owner"] = db.get(User, assignee_id).name
    elif "assignee_id" in data and "owner" not in data:
        data["owner"] = ""


def writable(db, project_id, user_id):
    project, _ = project_access(db, project_id, user_id, write=True)
    # Serialize task writes with assignment/membership changes for integrity.
    membership(db, project.workspace_id, user_id, {"Admin", "Member"}, lock=True)
    return project


@router.get("")
def list_tasks(project_id: str, owner: str = Query("", max_length=120), status: Literal["To Do", "In Progress", "Done"] | None = None, priority: Literal["High", "Medium", "Low"] | None = None, limit: int = Query(100, ge=1, le=100), before: int | None = Query(None, ge=1), auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    project_access(db, project_id, auth.user.id)
    query = select(Task).where(Task.project_id == project_id)
    if owner:
        query = query.where(Task.owner.icontains(owner, autoescape=True))
    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if before:
        query = query.where(Task.id < before)
    rows = list(db.scalars(query.order_by(Task.id.desc()).limit(limit + 1)))
    return {"tasks": [task_json(t) for t in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.post("", status_code=201)
def create_task(project_id: str, body: TaskFields, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    data = body.model_dump()
    assignment(db, project.workspace_id, data)
    task = Task(project_id=project_id, **data)
    db.add(task)
    db.commit()
    return task_json(task)


def ai_budget(db, project_id, user_id):
    project_access(db, project_id, user_id, write=True)
    rate_limit(db, "ai-user:" + user_id, 5, 60)
    project, _ = project_access(db, project_id, user_id, write=True)
    rate_limit(db, "ai-workspace:" + project.workspace_id, 100, 86400)
    # Release read transactions/connections before network I/O.
    db.commit()


@router.post("/extract")
def extract(project_id: str, body: ExtractRequest, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    ai_budget(db, project_id, auth.user.id)
    with ai_slot():
        try:
            raw = llm.extract_tasks_from_text(body.text)
            if len(raw) > 100:
                raise ValueError("Too many extracted tasks")
            # Validate model output locally; ignore the legacy explanation field.
            values = [TaskFields.model_validate({k: v for k, v in t.items() if k != "reasoning"}).model_dump() for t in raw]
        except Exception:
            logger.warning("Task extraction failed")
            raise HTTPException(502, "Task extraction is unavailable; please try again later")
    writable(db, project_id, auth.user.id)  # Re-check after potentially long external work.
    tasks = [Task(project_id=project_id, **v) for v in values]
    db.add_all(tasks)
    db.commit()
    return {"tasks": [task_json(t) for t in tasks]}


@router.post("/summary")
def summary(project_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    ai_budget(db, project_id, auth.user.id)
    if db.scalar(select(func.count()).select_from(Task).where(Task.project_id == project_id)) > 500:
        raise HTTPException(422, "Summary currently supports projects with up to 500 tasks")
    tasks = [task_json(t) for t in db.scalars(select(Task).where(Task.project_id == project_id).limit(500))]
    db.commit()
    with ai_slot():
        try:
            text = llm.summarize_project_status(tasks)
        except Exception:
            logger.warning("Project summary failed")
            raise HTTPException(502, "Summary is unavailable; please try again later")
    project_access(db, project_id, auth.user.id, write=True)
    return {"summary": text}


def find_task(db, project_id, task_id):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/{task_id}")
def get_task(project_id: str, task_id: int, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    project_access(db, project_id, auth.user.id)
    return task_json(find_task(db, project_id, task_id))


@router.put("/{task_id}")
def update_task(project_id: str, task_id: int, body: TaskUpdate, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    task = find_task(db, project_id, task_id)
    data = body.model_dump(exclude_unset=True)
    if not data or any(v is None for k, v in data.items() if k != "assignee_id"):
        raise HTTPException(422, "Provide valid task fields")
    if "owner" in data and task.assignee_id and "assignee_id" not in data:
        data["assignee_id"] = None
    assignment(db, project.workspace_id, data)
    for key, value in data.items():
        setattr(task, key, value)
    db.commit()
    return task_json(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(project_id: str, task_id: int, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    writable(db, project_id, auth.user.id)
    db.delete(find_task(db, project_id, task_id))
    db.commit()
