"""
routers/tasks.py - API endpoints for task management.

Provides RESTful endpoints for extracting, reading, updating, and deleting tasks,
as well as generating AI-powered project summaries.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field

import database
import llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# --- Request / Response Models ---

class ExtractRequest(BaseModel):
    """Request body for the task extraction endpoint."""
    text: str = Field(
        ...,
        min_length=10,
        description="The unstructured meeting notes or task descriptions to parse.",
        examples=["John needs to finalize the report by Friday. Sarah will handle the database migration — high priority."],
    )


class TaskResponse(BaseModel):
    """Standard response for a single task."""
    id: int
    description: str
    due_date: Optional[str] = None
    owner: Optional[str] = None
    priority: str = "Medium"
    status: str = "To Do"


class TaskUpdateRequest(BaseModel):
    """Request body for updating a task. All fields are optional."""
    description: Optional[str] = None
    due_date: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = Field(None, pattern="^(High|Medium|Low)$")
    status: Optional[str] = Field(None, pattern="^(To Do|In Progress|Done)$")


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str


class SummaryResponse(BaseModel):
    """Response body for the project summary endpoint."""
    summary: str


# --- Endpoints ---

@router.post("/extract", response_model=dict, summary="Extract tasks from text")
def extract_tasks(req: ExtractRequest) -> dict:
    """
    Accept unstructured text (meeting notes, emails, etc.), send it to the LLM
    for intelligent parsing, and save the extracted tasks to the database.
    """
    logger.info(f"Received extraction request ({len(req.text)} chars).")
    try:
        extracted = llm.extract_tasks_from_text(req.text)
        if not extracted:
            logger.warning("LLM returned zero tasks from the input.")
            return {"tasks": [], "message": "No actionable tasks were found in the input."}
        saved_tasks = database.create_tasks(extracted)
        return {"tasks": saved_tasks}
    except ValueError as e:
        logger.error(f"Configuration error during extraction: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        logger.error(f"LLM error during extraction: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error during extraction: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred during task extraction.")


@router.get("", response_model=list[TaskResponse], summary="List all tasks")
def get_all_tasks(
    owner: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve all tasks, optionally filtered by owner (partial match),
    status, or priority.
    """
    return database.get_tasks(owner=owner, status=status, priority=priority)


@router.get("/summary", response_model=SummaryResponse, summary="Get AI project summary")
def get_project_summary() -> dict:
    """
    Generate an AI-powered executive summary of the current project status.
    Analyzes all tasks and produces a concise stakeholder-ready report.
    """
    logger.info("Generating project summary.")
    try:
        all_tasks = database.get_tasks()
        summary = llm.summarize_project_status(all_tasks)
        return {"summary": summary}
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{task_id}", response_model=TaskResponse, summary="Get a single task")
def get_single_task(task_id: int) -> dict:
    """Retrieve a single task by its ID."""
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with id {task_id} not found.")
    return task


@router.put("/{task_id}", response_model=TaskResponse, summary="Update a task")
def update_task(task_id: int, task_update: TaskUpdateRequest) -> dict:
    """Update one or more fields of a task by its ID."""
    # Only send non-None fields to the database layer
    update_data = task_update.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided for update.")

    updated = database.update_task(task_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Task with id {task_id} not found.")
    return updated


@router.delete("/{task_id}", response_model=MessageResponse, summary="Delete a task")
def delete_task(task_id: int) -> dict:
    """Delete a task by its ID."""
    deleted = database.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task with id {task_id} not found.")
    return {"message": f"Task {task_id} deleted successfully."}
