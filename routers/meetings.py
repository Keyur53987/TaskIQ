"""Immutable sources, evidence-backed proposals, and explicit, versioned review."""
import hashlib
import json
import logging
import re
from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, func, update, or_, and_
from sqlalchemy.orm import Session

import llm
from database import get_db, Meeting, TaskProposal, Task, Project
from security import Identity, identity, project_access, mutation_limit, now
from routers.tasks import writable, assignment, ai_budget, ai_slot, task_json

router = APIRouter(prefix="/api/projects/{project_id}/meetings", tags=["meetings"])
logger = logging.getLogger(__name__)
EXTRACTION_LEASE = 120


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeetingCreate(StrictBody):
    request_id: UUID
    title: str = Field(min_length=1, max_length=160, pattern=r"\S")
    meeting_date: date
    timezone: str = Field(max_length=64)
    source_type: Literal["notes", "transcript"] = "notes"
    source_filename: str = Field(default="", max_length=200)
    source_text: str = Field(min_length=10, max_length=20000, pattern=r"\S")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone, such as Asia/Kolkata or UTC")
        return value

    @field_validator("source_text")
    @classmethod
    def text_only(cls, value):
        if "\x00" in value:
            raise ValueError("Source must be plain text without NUL bytes")
        return value  # Preserve exact source characters and offsets.

    @model_validator(mode="after")
    def valid_file(self):
        if self.source_filename:
            filename = self.source_filename
            if self.source_type != "transcript" or "/" in filename or "\\" in filename or not filename.lower().endswith((".txt", ".md", ".vtt", ".srt")):
                raise ValueError("Upload a plain-text .txt, .md, .vtt, or .srt transcript")
        return self


class ProposalFields(StrictBody):
    description: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    owner: str = Field(default="", max_length=120)
    due_date: str = Field(default="", max_length=100)
    priority: Literal["High", "Medium", "Low"] = "Medium"
    assignee_id: str | None = Field(default=None, max_length=36)


class ProposalCreate(ProposalFields):
    request_id: UUID
    source_quote: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ProposalEdits(StrictBody):
    description: str | None = Field(default=None, min_length=1, max_length=4000, pattern=r"\S")
    owner: str | None = Field(default=None, max_length=120)
    due_date: str | None = Field(default=None, max_length=100)
    priority: Literal["High", "Medium", "Low"] | None = None
    assignee_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def no_null_fields(self):
        if any(value is None for key, value in self.model_dump(exclude_unset=True).items() if key != "assignee_id"):
            raise ValueError("Only assignee_id may be null; use an empty string for missing dates or owners")
        return self


class EditRequest(StrictBody):
    version: int = Field(ge=1)
    edits: ProposalEdits


class ReviewRequest(StrictBody):
    version: int = Field(ge=1)
    action: Literal["accept", "reject"]
    edits: ProposalEdits | None = None

    @model_validator(mode="after")
    def reject_is_not_edit(self):
        if self.action == "reject" and self.edits is not None:
            raise ValueError("Save edits separately or accept them; rejection does not change proposal fields")
        return self


def find_meeting(db, project_id, meeting_id):
    meeting = db.scalar(select(Meeting).where(Meeting.id == meeting_id, Meeting.project_id == project_id).execution_options(populate_existing=True))
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    return meeting


def find_proposal(db, meeting_id, proposal_id):
    proposal = db.scalar(select(TaskProposal).where(TaskProposal.id == proposal_id, TaskProposal.meeting_id == meeting_id).execution_options(populate_existing=True))
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    return proposal


def warnings(proposal):
    result = []
    if not proposal.owner.strip() and not proposal.assignee_id:
        result.append("Missing owner")
    if not proposal.due_date.strip():
        result.append("Missing deadline")
    else:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", proposal.due_date):
                raise ValueError()
            date.fromisoformat(proposal.due_date)
        except ValueError:
            result.append("Deadline needs clarification")
    return result


def proposal_json(proposal, task_id=None):
    return {
        **{key: getattr(proposal, key) for key in ("id", "meeting_id", "description", "owner", "due_date", "priority", "assignee_id", "source_quote", "source_start", "source_end", "origin", "status", "version", "reviewed_by", "reviewed_at")},
        "task_id": task_id, "warnings": warnings(proposal),
    }


def meeting_json(meeting, include_source=False):
    result = {key: getattr(meeting, key) for key in ("id", "project_id", "title", "meeting_date", "timezone", "source_type", "source_filename", "created_at", "extraction_status")}
    result["retry_available"] = meeting.extraction_status == "Failed" or (meeting.extraction_status == "Extracting" and (meeting.extraction_started_at or 0) + EXTRACTION_LEASE < now())
    if include_source:
        result["source_text"] = meeting.source_text
    return result


def detail(db, meeting):
    proposals = list(db.scalars(select(TaskProposal).where(TaskProposal.meeting_id == meeting.id).order_by(TaskProposal.created_at, TaskProposal.id)))
    task_ids = dict(db.execute(select(Task.source_proposal_id, Task.id).where(Task.source_proposal_id.in_([p.id for p in proposals]))).all()) if proposals else {}
    return {"meeting": meeting_json(meeting, True), "proposals": [proposal_json(p, task_ids.get(p.id)) for p in proposals]}


def evidence(source, quote):
    start = source.find(quote)
    if not quote.strip() or start < 0:
        raise ValueError("Evidence must be an exact passage from the saved source")
    return start, start + len(quote)


@router.post("", status_code=201)
def create_meeting(project_id: str, body: MeetingCreate, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    values = body.model_dump(mode="json", exclude={"request_id"})
    key = fingerprint(values)
    existing = db.scalar(select(Meeting).where(Meeting.project_id == project_id, Meeting.request_id == str(body.request_id)))
    if existing:
        if existing.input_hash != key:
            raise HTTPException(409, "This save identifier was already used for different meeting content")
        return detail(db, existing)
    count = db.scalar(select(func.count()).select_from(Meeting).join(Project).where(Project.workspace_id == project.workspace_id))
    if count >= 500:
        raise HTTPException(409, "Workspace meeting limit reached (500)")
    if db.scalar(select(func.count()).select_from(Meeting).where(Meeting.project_id == project_id)) >= 200:
        raise HTTPException(409, "Project meeting limit reached (200)")
    meeting = Meeting(project_id=project_id, title=body.title.strip(), meeting_date=body.meeting_date, timezone=body.timezone, source_type=body.source_type, source_filename=body.source_filename, source_text=body.source_text, request_id=str(body.request_id), input_hash=key, created_by=auth.user.id)
    db.add(meeting)
    db.commit()
    return detail(db, meeting)


@router.get("")
def list_meetings(project_id: str, before: str = Query("", max_length=64), limit: int = Query(20, ge=1, le=50), auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    project_access(db, project_id, auth.user.id)
    query = select(Meeting).where(Meeting.project_id == project_id)
    if before:
        try:
            timestamp, meeting_id = before.split(":", 1)
            timestamp, meeting_id = int(timestamp), str(UUID(meeting_id))
            if timestamp < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Invalid meeting cursor")
        query = query.where(or_(Meeting.created_at < timestamp, and_(Meeting.created_at == timestamp, Meeting.id < meeting_id)))
    rows = list(db.scalars(query.order_by(Meeting.created_at.desc(), Meeting.id.desc()).limit(limit + 1)))
    page = rows[:limit]
    counts = {}
    if page:
        for meeting_id, status, count in db.execute(select(TaskProposal.meeting_id, TaskProposal.status, func.count()).where(TaskProposal.meeting_id.in_([m.id for m in page])).group_by(TaskProposal.meeting_id, TaskProposal.status)):
            counts.setdefault(meeting_id, {})[status] = count
    return {"meetings": [{**meeting_json(m), "proposal_counts": counts.get(m.id, {})} for m in page], "next_cursor": f"{page[-1].created_at}:{page[-1].id}" if len(rows) > limit else None}


@router.get("/{meeting_id}")
def get_meeting(project_id: str, meeting_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    project_access(db, project_id, auth.user.id)
    return detail(db, find_meeting(db, project_id, meeting_id))


@router.post("/{meeting_id}/proposals", status_code=201)
def manual_proposal(project_id: str, meeting_id: str, body: ProposalCreate, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    meeting = find_meeting(db, project_id, meeting_id)
    key = fingerprint(body.model_dump(mode="json", exclude={"request_id"}))
    existing = db.scalar(select(TaskProposal).where(TaskProposal.meeting_id == meeting_id, TaskProposal.request_id == str(body.request_id)))
    if existing:
        if existing.input_hash != key:
            raise HTTPException(409, "This proposal identifier was already used for different content")
        return proposal_json(existing, db.scalar(select(Task.id).where(Task.source_proposal_id == existing.id)))
    if meeting.extraction_status == "Extracting" and not meeting_json(meeting)["retry_available"]:
        raise HTTPException(409, "Extraction is in progress; refresh the meeting before adding proposals")
    if db.scalar(select(func.count()).select_from(TaskProposal).where(TaskProposal.meeting_id == meeting_id)) >= 100:
        raise HTTPException(409, "Meeting proposal limit reached (100)")
    try:
        start, end = evidence(meeting.source_text, body.source_quote)
    except ValueError as error:
        raise HTTPException(422, str(error))
    values = body.model_dump(exclude={"request_id", "source_quote"})
    assignment(db, project.workspace_id, values)
    proposal = TaskProposal(meeting_id=meeting_id, **values, source_quote=body.source_quote, source_start=start, source_end=end, origin="Manual", request_id=str(body.request_id), input_hash=key)
    meeting.extraction_status = "Ready"
    db.add(proposal)
    db.commit()
    return proposal_json(proposal)


@router.post("/{meeting_id}/extract")
def extract_proposals(project_id: str, meeting_id: str, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    writable(db, project_id, auth.user.id)
    meeting = find_meeting(db, project_id, meeting_id)
    if meeting.extraction_status == "Ready":
        return detail(db, meeting)  # A completed extraction is never repeated by retrying.
    if meeting.extraction_status == "Extracting" and not meeting_json(meeting)["retry_available"]:
        raise HTTPException(409, "Extraction is already in progress; refresh this meeting shortly")
    db.commit()
    with ai_slot():
        ai_budget(db, project_id, auth.user.id)
        writable(db, project_id, auth.user.id)
        meeting = find_meeting(db, project_id, meeting_id)
        if meeting.extraction_status == "Ready":
            return detail(db, meeting)
        if meeting.extraction_status == "Extracting" and not meeting_json(meeting)["retry_available"]:
            raise HTTPException(409, "Extraction is already in progress")
        if meeting.extraction_attempt >= 10:
            raise HTTPException(409, "Extraction retry limit reached; create proposals manually from this source")
        meeting.extraction_status = "Extracting"
        meeting.extraction_attempt += 1
        meeting.extraction_started_at = now()
        attempt = meeting.extraction_attempt
        source, meeting_date, timezone = meeting.source_text, meeting.meeting_date, meeting.timezone
        db.commit()  # No database lock/transaction is held during inference.
        try:
            raw = llm.extract_meeting_proposals(source, meeting_date.isoformat(), timezone)
            if len(raw) > 100:
                raise ValueError("Too many proposals")
            values = []
            for item in raw:
                candidate = llm.MeetingTaskExtract.model_validate(item)
                data = candidate.model_dump()
                quote = data.pop("source_quote")
                start, end = evidence(source, quote)
                values.append({**data, "source_quote": quote, "source_start": start, "source_end": end})
            writable(db, project_id, auth.user.id)
            meeting = find_meeting(db, project_id, meeting_id)
            if meeting.extraction_status != "Extracting" or meeting.extraction_attempt != attempt:
                raise HTTPException(409, "A newer extraction attempt replaced this result; refresh the meeting")
            for value in values:
                db.add(TaskProposal(meeting_id=meeting_id, **value, origin="AI", input_hash=fingerprint(value)))
            meeting.extraction_status = "Ready"
            db.commit()
            return detail(db, meeting)
        except Exception as error:
            db.rollback()
            # Internal attempt cleanup does not grant a removed member project access.
            db.execute(update(Meeting).where(Meeting.id == meeting_id, Meeting.extraction_status == "Extracting", Meeting.extraction_attempt == attempt).values(extraction_status="Failed"))
            db.commit()
            if isinstance(error, HTTPException):
                raise
            logger.warning("Meeting proposal extraction failed")
            raise HTTPException(502, "Extraction failed validation or is unavailable. Your saved source is unchanged; retry or add proposals manually.")


def apply_edits(db, workspace_id, proposal, edits):
    values = edits.model_dump(exclude_unset=True)
    if "owner" in values and proposal.assignee_id and "assignee_id" not in values:
        values["assignee_id"] = None
    assignment(db, workspace_id, values)
    for key, value in values.items():
        setattr(proposal, key, value)


@router.patch("/{meeting_id}/proposals/{proposal_id}")
def edit_proposal(project_id: str, meeting_id: str, proposal_id: str, body: EditRequest, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    find_meeting(db, project_id, meeting_id)
    proposal = find_proposal(db, meeting_id, proposal_id)
    if proposal.status != "Pending" or proposal.version != body.version:
        raise HTTPException(409, "This proposal changed or was reviewed. Refresh before editing.")
    if not body.edits.model_fields_set:
        raise HTTPException(422, "Provide at least one edit")
    apply_edits(db, project.workspace_id, proposal, body.edits)
    proposal.version += 1
    db.commit()
    return proposal_json(proposal)


@router.post("/{meeting_id}/proposals/{proposal_id}/review")
def review_proposal(project_id: str, meeting_id: str, proposal_id: str, body: ReviewRequest, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    project = writable(db, project_id, auth.user.id)
    find_meeting(db, project_id, meeting_id)
    proposal = find_proposal(db, meeting_id, proposal_id)
    decision_key = fingerprint({"user_id": auth.user.id, **body.model_dump(mode="json")})
    if proposal.status != "Pending":
        if proposal.decision_hash == decision_key:
            task = db.scalar(select(Task).where(Task.source_proposal_id == proposal.id))
            return {"proposal": proposal_json(proposal, task.id if task else None), "task": task_json(task) if task else None}
        raise HTTPException(409, "This proposal has already been reviewed. Refresh to see the decision.")
    if proposal.version != body.version:
        raise HTTPException(409, "This proposal changed. Refresh before reviewing.")
    task = None
    if body.action == "accept":
        if body.edits:
            apply_edits(db, project.workspace_id, proposal, body.edits)
        values = {key: getattr(proposal, key) for key in ("description", "due_date", "owner", "assignee_id", "priority")}
        assignment(db, project.workspace_id, values)
        task = Task(project_id=project_id, source_proposal_id=proposal.id, **values)
        db.add(task)
    proposal.status = "Accepted" if body.action == "accept" else "Rejected"
    proposal.version += 1
    proposal.reviewed_by = auth.user.id
    proposal.reviewed_at = now()
    proposal.decision_hash = decision_key
    db.commit()
    return {"proposal": proposal_json(proposal, task.id if task else None), "task": task_json(task) if task else None}
