"""Optional local UI QA: run with Python that has Playwright and Chromium installed.

Starts only a temporary local test instance. No Google credentials or auth bypass routes.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.request
import uuid

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test-results"
OUT.mkdir(exist_ok=True)
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
database_path = OUT / ("browser-" + uuid.uuid4().hex + ".db")
env = dict(os.environ, APP_ENV="test", APP_URL="http://127.0.0.1:8765", DATABASE_URL="sqlite:///" + str(database_path), GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", AI_ENABLED="false")
subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
token = secrets.token_urlsafe(32)
env["TEST_SESSION"] = token
seed = """
import os,secrets
from database import SessionLocal,User,LoginSession
from security import digest,now
with SessionLocal.begin() as db:
    u=User(google_sub='browser-test',email='qa@example.com',name='QA Admin')
    db.add(u);db.flush()
    db.add(LoginSession(token_hash=digest(os.environ['TEST_SESSION']),user_id=u.id,csrf=secrets.token_urlsafe(32),expires_at=now()+3600))
"""
subprocess.run([str(PYTHON), "-c", seed], cwd=ROOT, env=env, check=True)
log = open(OUT / "browser-server.log", "w", encoding="utf8")
server = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"], cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(env["APP_URL"] + "/health/ready", timeout=1)
            break
        except Exception:
            if server.poll() is not None: raise RuntimeError("QA server exited; inspect browser-server.log")
            time.sleep(.25)
    else:
        raise RuntimeError("QA server did not start")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": "taskiq_session", "value": token, "url": env["APP_URL"]}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(env["APP_URL"])
        page.locator("#application").wait_for(state="visible")
        page.locator("summary").click()
        page.locator("#workspace-name").fill("Delivery team")
        page.locator("#workspace-form button").click()
        page.locator("#role-label").filter(has_text="Admin").wait_for()
        page.locator("#project-name").fill("Client launch")
        page.locator("#project-form button").click()
        page.locator("#project-content").wait_for(state="visible")
        page.locator("#new-task").click()
        page.locator("#edit-description").fill('<img src=x onerror="alert(1)"> Review authentication')
        page.locator("#edit-assignee").select_option(label="QA Admin")
        page.locator("#edit-form button[type=submit], #edit-form button:not([type])").first.click()
        page.locator("#task-list .task-card").wait_for()
        assert page.locator("#task-list .task-desc").inner_text().startswith("<img")
        assert page.locator("#task-list img").count() == 0
        page.locator("#invite-email").fill("teammate@example.com")
        page.locator("#invite-role").select_option("Viewer")
        page.locator("#invite-form button").click()
        page.locator("#invite-result").wait_for(state="visible")
        assert "#invite=" in page.locator("#invite-link").input_value()
        page.locator("summary").click()
        page.screenshot(path=str(OUT / "phase1-desktop.png"), full_page=True)
        page.locator('[data-tab="tab-kanban"]').click()
        assert page.locator('.kanban-col[data-status="To Do"] .task-card').count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-tab="tab-list"]').click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(OUT / "phase1-mobile.png"), full_page=True)
        page.locator("#logout").click()
        page.locator("#login-panel").wait_for(state="visible")
        assert page.locator("#application").is_hidden()
        assert not errors, errors
        browser.close()
    print("Browser smoke passed: workspace, project, task assignment, XSS-safe display, invitation, Kanban, mobile, logout")
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
