import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func
import main
import config
from database import Meeting, TaskProposal, Task, Membership
from security import now

QUOTE = "Aman will update the guide by Friday."


def base(meeting):
    return f"/api/projects/{meeting[2]['id']}/meetings"


def endpoint(meeting):
    return base(meeting) + "/" + meeting[3]["id"]


def draft(client, meeting, **overrides):
    payload = {"request_id": str(uuid.uuid4()), "description": "Update guide", "owner": "Aman", "due_date": "Friday", "source_quote": QUOTE, **overrides}
    result = client.post(endpoint(meeting) + "/proposals", json=payload)
    assert result.status_code == 201, result.text
    return result.json(), payload


def review_path(meeting, proposal):
    return endpoint(meeting) + f"/proposals/{proposal['id']}/review"


def extracted(**overrides):
    return {"description": "Update guide", "owner": "Aman", "due_date": "Friday", "priority": "Medium", "source_quote": QUOTE, **overrides}


def test_source_saved_unchanged_and_no_tasks_created(client, meeting, store):
    result = client.get(endpoint(meeting)).json()
    assert result["meeting"]["source_text"].startswith("🚀")
    assert result["proposals"] == []
    assert result["meeting"]["timezone"] == "Asia/Kolkata"
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("field,value", [("timezone", "Not/AZone"), ("source_text", "x" * 20001), ("source_text", "a\0binary source"), ("meeting_date", "2026-02-30"), ("title", " "), ("source_filename", "../notes.txt")])
def test_meeting_validation(client, meeting, field, value):
    body = {"request_id": str(uuid.uuid4()), "title": "Review", "meeting_date": "2026-11-04", "timezone": "UTC", "source_text": "A valid source text", field: value}
    assert client.post(base(meeting), json=body).status_code == 422


@pytest.mark.parametrize("extension", ["txt", "md", "vtt", "srt"])
def test_transcript_metadata_and_content(client, meeting, extension):
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nSarah: Check the login flow."
    body = {"request_id": str(uuid.uuid4()), "title": "Transcript", "meeting_date": "2026-11-04", "timezone": "UTC", "source_type": "transcript", "source_filename": f"meeting.{extension}", "source_text": text}
    result = client.post(base(meeting), json=body)
    assert result.status_code == 201, result.text
    assert result.json()["meeting"]["source_text"] == text
    assert result.json()["meeting"]["source_filename"] == f"meeting.{extension}"


def test_meeting_save_retry_is_idempotent(client, meeting):
    body = {"request_id": str(uuid.uuid4()), "title": "Retry-safe", "meeting_date": "2026-11-04", "timezone": "UTC", "source_text": "A valid source text"}
    first = client.post(base(meeting), json=body).json()["meeting"]
    second = client.post(base(meeting), json=body).json()["meeting"]
    assert first["id"] == second["id"]
    body["source_text"] = "Different source text"
    assert client.post(base(meeting), json=body).status_code == 409


def test_meeting_history_pagination_and_counts(client, meeting):
    draft(client, meeting)
    result = client.get(base(meeting), params={"limit": 1}).json()
    assert result["meetings"][0]["proposal_counts"] == {"Pending": 1}
    assert "source_text" not in result["meetings"][0]
    for i in range(2):
        client.post(base(meeting), json={"request_id": str(uuid.uuid4()), "title": f"Meeting {i}", "meeting_date": "2026-11-04", "timezone": "UTC", "source_text": "A valid source text"})
    first = client.get(base(meeting), params={"limit": 1}).json()
    second = client.get(base(meeting), params={"limit": 1, "before": first["next_cursor"]}).json()
    assert first["meetings"][0]["id"] != second["meetings"][0]["id"]
    assert client.get(base(meeting), params={"before": "bad-cursor"}).status_code == 422


def test_manual_proposal_requires_exact_quote_and_is_not_a_task(client, meeting, store):
    p, body = draft(client, meeting)
    assert p["status"] == "Pending" and p["origin"] == "Manual"
    assert "Deadline needs clarification" in p["warnings"]
    text = client.get(endpoint(meeting)).json()["meeting"]["source_text"]
    assert text[p["source_start"]:p["source_end"]] == QUOTE
    assert client.post(endpoint(meeting) + "/proposals", json=body).json()["id"] == p["id"]
    body.update(request_id=str(uuid.uuid4()), source_quote="Aman will update a guide by Friday.")
    assert client.post(endpoint(meeting) + "/proposals", json=body).status_code == 422
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("due,warning", [("", "Missing deadline"), ("EOD", "Deadline needs clarification"), ("2026-02-30", "Deadline needs clarification"), ("2026-11-06", None)])
def test_deterministic_review_flags(client, meeting, due, warning):
    p, _ = draft(client, meeting, owner="", due_date=due)
    assert "Missing owner" in p["warnings"]
    assert (warning in p["warnings"]) if warning else p["warnings"] == ["Missing owner"]


def test_edit_accept_and_source_provenance(client, meeting, store):
    p, _ = draft(client, meeting)
    edits = {"description": "Update the onboarding guide", "due_date": "2026-11-06", "assignee_id": meeting[0]["id"]}
    saved = client.patch(endpoint(meeting) + f"/proposals/{p['id']}", json={"version": 1, "edits": edits})
    assert saved.status_code == 200, saved.text
    p = saved.json()
    assert p["version"] == 2 and p["warnings"] == []
    assert client.get(f"/api/projects/{meeting[2]['id']}/tasks").json()["tasks"] == []
    result = client.post(review_path(meeting, p), json={"version": 2, "action": "accept"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["task"]["description"] == edits["description"]
    assert data["task"]["assignee_id"] == meeting[0]["id"]
    assert data["task"]["source"]["quote"] == QUOTE
    assert data["task"]["source"]["meeting_id"] == meeting[3]["id"]
    assert data["proposal"]["status"] == "Accepted"
    assert data["proposal"]["reviewed_by"] == meeting[0]["id"]


def test_review_replay_never_duplicates_or_recreates_deleted_task(client, meeting, store):
    p, _ = draft(client, meeting)
    body = {"version": 1, "action": "accept", "edits": {"description": "Reviewed task"}}
    first = client.post(review_path(meeting, p), json=body).json()
    replay = client.post(review_path(meeting, p), json=body).json()
    assert first["task"]["id"] == replay["task"]["id"]
    task_path = f"/api/projects/{meeting[2]['id']}/tasks/{first['task']['id']}"
    assert client.put(task_path, json={"status": "Done"}).json()["source"]["quote"] == QUOTE
    assert client.delete(task_path).status_code == 204
    result = client.post(review_path(meeting, p), json=body).json()
    assert result["task"] is None and result["proposal"]["status"] == "Accepted"
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_rejection_and_conflicting_decision(client, meeting):
    p, _ = draft(client, meeting)
    body = {"version": 1, "action": "reject"}
    result = client.post(review_path(meeting, p), json=body)
    assert result.json()["proposal"]["status"] == "Rejected" and result.json()["task"] is None
    assert client.post(review_path(meeting, p), json=body).status_code == 200
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept"}).status_code == 409


def test_stale_edits_and_acceptance_conflict(client, meeting):
    p, _ = draft(client, meeting)
    url = endpoint(meeting) + f"/proposals/{p['id']}"
    assert client.patch(url, json={"version": 1, "edits": {"owner": "Sarah"}}).status_code == 200
    assert client.patch(url, json={"version": 1, "edits": {"owner": "Wrong"}}).status_code == 409
    assert client.post(url + "/review", json={"version": 1, "action": "accept"}).status_code == 409


def test_source_cannot_be_changed_through_proposal_edits(client, meeting):
    p, _ = draft(client, meeting)
    assert client.patch(endpoint(meeting) + f"/proposals/{p['id']}", json={"version": 1, "edits": {"source_quote": "forged"}}).status_code == 422
    assert client.patch(endpoint(meeting), json={"source_text": "forged"}).status_code == 405


def test_ai_extraction_pending_only_and_retry_is_read_only(client, meeting, monkeypatch):
    calls = []
    def extract(source, meeting_date, timezone):
        calls.append((source, meeting_date, timezone))
        return [extracted()]
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    result = client.post(endpoint(meeting) + "/extract")
    assert result.status_code == 200, result.text
    assert result.json()["proposals"][0]["status"] == "Pending"
    assert result.json()["meeting"]["extraction_status"] == "Ready"
    assert calls[0][1:] == ("2026-11-04", "Asia/Kolkata")
    assert client.get(f"/api/projects/{meeting[2]['id']}/tasks").json()["tasks"] == []
    assert client.post(endpoint(meeting) + "/extract").status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["quote", "extra", "priority", "partial_batch", "provider"])
def test_ai_failure_is_atomic_and_source_preserved(client, meeting, monkeypatch, bad):
    def extract(*args):
        if bad == "provider": raise RuntimeError("secret-provider-credential")
        if bad == "quote": return [extracted(source_quote="No supporting source exists")]
        if bad == "extra": return [extracted(assignee_id="invented-id")]
        if bad == "priority": return [extracted(priority="Critical")]
        return [extracted(), extracted(source_quote="Forged quote")]
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    result = client.post(endpoint(meeting) + "/extract")
    assert result.status_code == 502 and "secret-provider-credential" not in result.text
    data = client.get(endpoint(meeting)).json()
    assert data["proposals"] == []
    assert data["meeting"]["extraction_status"] == "Failed"
    assert data["meeting"]["retry_available"] is True
    assert data["meeting"]["source_text"].startswith("🚀")


def test_failed_extraction_can_be_retried(client, meeting, monkeypatch):
    monkeypatch.setattr("llm.extract_meeting_proposals", lambda *a: [extracted(source_quote="wrong")])
    assert client.post(endpoint(meeting) + "/extract").status_code == 502
    monkeypatch.setattr("llm.extract_meeting_proposals", lambda *a: [extracted()])
    assert client.post(endpoint(meeting) + "/extract").status_code == 200


def test_empty_ai_result_is_a_completed_review_set(client, meeting, monkeypatch):
    monkeypatch.setattr("llm.extract_meeting_proposals", lambda *a: [])
    result = client.post(endpoint(meeting) + "/extract").json()
    assert result["proposals"] == [] and result["meeting"]["extraction_status"] == "Ready"


def test_extraction_rechecks_removed_member_and_cleans_attempt(client, meeting, monkeypatch, store):
    def extract(*args):
        with store.begin() as db:
            db.delete(db.get(Membership, (meeting[1]["id"], meeting[0]["id"])))
        return [extracted()]
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    assert client.post(endpoint(meeting) + "/extract").status_code == 404
    with store() as db:
        assert db.get(Meeting, meeting[3]["id"]).extraction_status == "Failed"
        assert db.scalar(select(func.count()).select_from(TaskProposal)) == 0


def test_old_extraction_result_cannot_overwrite_newer_attempt(client, meeting, monkeypatch, store):
    def extract(*args):
        with store.begin() as db:
            db.execute(update(Meeting).where(Meeting.id == meeting[3]["id"]).values(extraction_attempt=2, extraction_status="Ready"))
        return [extracted()]
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    assert client.post(endpoint(meeting) + "/extract").status_code == 409
    with store() as db:
        record = db.get(Meeting, meeting[3]["id"])
        assert record.extraction_attempt == 2 and record.extraction_status == "Ready"
        assert db.scalar(select(func.count()).select_from(TaskProposal)) == 0


def test_ai_disabled_still_allows_source_and_manual_review(client, meeting, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    assert client.post(endpoint(meeting) + "/extract").status_code == 503
    p, _ = draft(client, meeting)
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept"}).status_code == 200


def test_stale_attempt_recovery_and_manual_escape(client, meeting, store):
    with store.begin() as db:
        m = db.get(Meeting, meeting[3]["id"])
        m.extraction_status, m.extraction_attempt, m.extraction_started_at = "Extracting", 1, now()
    body = {"request_id": str(uuid.uuid4()), "description": "Guide", "source_quote": QUOTE}
    assert client.post(endpoint(meeting) + "/extract").status_code == 409
    assert client.post(endpoint(meeting) + "/proposals", json=body).status_code == 409
    with store.begin() as db:
        db.execute(update(Meeting).values(extraction_started_at=now() - 121))
    assert client.get(endpoint(meeting)).json()["meeting"]["retry_available"] is True
    assert client.post(endpoint(meeting) + "/proposals", json=body).status_code == 201


def test_cross_workspace_and_project_meeting_isolation(client, meeting, account):
    p, _ = draft(client, meeting)
    other = client.post(f"/api/workspaces/{meeting[1]['id']}/projects", json={"name": "Other"}).json()
    assert client.get(f"/api/projects/{other['id']}/meetings/{meeting[3]['id']}").status_code == 404
    account("outsider@example.com")
    assert client.get(base(meeting)).status_code == 404
    assert client.get(endpoint(meeting)).status_code == 404
    assert client.post(endpoint(meeting) + "/extract").status_code == 404
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept"}).status_code == 404


def test_viewer_reads_but_cannot_review(client, meeting, account, store):
    p, _ = draft(client, meeting)
    person = account("viewer@example.com")
    with store.begin() as db:
        db.add(Membership(workspace_id=meeting[1]["id"], user_id=person["id"], role="Viewer"))
    assert client.get(endpoint(meeting)).status_code == 200
    assert client.post(endpoint(meeting) + "/extract").status_code == 403
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept"}).status_code == 403
    assert client.patch(endpoint(meeting) + f"/proposals/{p['id']}", json={"version": 1, "edits": {"owner": "x"}}).status_code == 403
    assert client.post(endpoint(meeting) + "/proposals", json={"request_id": str(uuid.uuid4()), "description": "x", "source_quote": QUOTE}).status_code == 403


def test_removed_assignee_must_be_reassigned_before_acceptance(client, meeting, account, store):
    member = account("member@example.com", login=False)
    with store.begin() as db:
        db.add(Membership(workspace_id=meeting[1]["id"], user_id=member["id"], role="Member"))
    p, _ = draft(client, meeting, assignee_id=member["id"])
    assert client.delete(f"/api/workspaces/{meeting[1]['id']}/members/{member['id']}").status_code == 204
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept"}).status_code == 422
    assert client.get(endpoint(meeting)).json()["proposals"][0]["status"] == "Pending"
    assert client.post(review_path(meeting, p), json={"version": 1, "action": "accept", "edits": {"assignee_id": None}}).status_code == 200


@pytest.mark.skipif(not os.getenv("TASKIQ_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
@pytest.mark.parametrize("actions", [("accept", "accept"), ("accept", "reject")])
def test_concurrent_reviews_are_serialized(client, meeting, store, actions):
    p, _ = draft(client, meeting)
    user = meeting[0]
    def review(action):
        with TestClient(main.app, base_url=config.APP_URL) as own:
            own.cookies.set(config.SESSION_COOKIE, user["token"])
            result = own.post(review_path(meeting, p), json={"version": 1, "action": action}, headers={"Origin": config.APP_URL, "X-CSRF-Token": user["csrf"]})
            return result.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, actions))
    assert sorted(results) == ([200, 200] if actions[0] == actions[1] else [200, 409])
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Task)) <= 1
        assert db.get(TaskProposal, p["id"]).version == 2


@pytest.mark.skipif(not os.getenv("TASKIQ_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_concurrent_extraction_claims_do_not_call_provider_twice(client, meeting, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def extract(*args):
        calls.append(1)
        started.set()
        assert release.wait(10)
        return [extracted()]
    monkeypatch.setattr("llm.extract_meeting_proposals", extract)
    user = meeting[0]
    def request():
        with TestClient(main.app, base_url=config.APP_URL) as own:
            own.cookies.set(config.SESSION_COOKIE, user["token"])
            return own.post(endpoint(meeting) + "/extract", headers={"Origin": config.APP_URL, "X-CSRF-Token": user["csrf"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request)
        try:
            assert started.wait(10)
            assert request() == 409
        finally:
            release.set()
        assert first.result(timeout=10) == 200
    assert len(calls) == 1
