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
        page.get_by_text("Workspace & account settings", exact=True).click()
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
        page.get_by_text("Workspace & account settings", exact=True).click()
        page.screenshot(path=str(OUT / "phase1-desktop.png"), full_page=True)
        page.locator('[data-tab="tab-kanban"]').click()
        assert page.locator('.kanban-col[data-status="To Do"] .task-card').count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-tab="tab-list"]').click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(OUT / "phase1-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('#open-meetings').click()
        page.locator('#meeting-title').fill('Launch review')
        source = '🚀 Notes. Aman will update the guide by Friday.\nSarah will check the login flow.'
        page.locator('#transcript-file').set_input_files({'name': 'meeting.vtt', 'mimeType': 'text/vtt', 'buffer': source.encode('utf8')})
        page.wait_for_function("value => document.getElementById('meeting-source').value === value", arg=source)
        page.locator('#save-meeting').click()
        try:
            page.locator('#meeting-detail-title').filter(has_text='Launch review').wait_for(timeout=10000)
        except Exception:
            print('Meeting UI diagnostic:', page.evaluate("({notice:document.getElementById('notice').textContent,title:document.getElementById('meeting-detail-title').textContent,classes:document.getElementById('meeting-detail').className,active:[...document.querySelectorAll('.tab-content.active')].map(el=>el.id)})"))
            print('Browser errors:', errors)
            page.screenshot(path=str(OUT / 'phase2-failure.png'), full_page=True)
            raise
        assert page.locator('#saved-source').text_content() == source
        assert page.locator('#extract-proposals').is_hidden()  # Manual review works with AI off.
        page.locator('#new-proposal').click()
        page.locator('#manual-description').fill('Update the guide')
        page.locator('#manual-quote').fill('Aman will update the guide by Friday.')
        page.locator('#manual-due').fill('Friday')
        page.locator('#save-proposal').click()
        pending = page.locator('.proposal-card').filter(has_text='Pending')
        pending.wait_for()
        assert 'Missing owner' in pending.locator('.review-warning').inner_text()
        assert 'Deadline needs clarification' in pending.locator('.review-warning').inner_text()
        pending.get_by_role('button', name='Highlight in saved source').click()
        assert page.locator('#saved-source mark').inner_text() == 'Aman will update the guide by Friday.'
        pending.locator('[name=description]').fill('Review the onboarding guide')
        pending.locator('[name=due_date]').fill('2026-11-06')
        pending.locator('[name=assignee_id]').select_option(label='QA Admin')
        pending.get_by_role('button', name='Accept task', exact=True).click()
        page.locator('.proposal-card').filter(has_text='Accepted').wait_for()
        page.locator('#new-proposal').click()
        page.locator('#manual-description').fill('Check login flow')
        page.locator('#manual-quote').fill('Sarah will check the login flow.')
        page.locator('#save-proposal').click()
        page.locator('.proposal-card').filter(has_text='Pending').get_by_role('button', name='Reject', exact=True).click()
        page.locator('.proposal-card').filter(has_text='Rejected').wait_for()
        page.screenshot(path=str(OUT / 'phase2-desktop.png'), full_page=True)
        page.locator('[data-tab=tab-list]').click()
        assert page.locator('#task-list .task-card').count() == 2
        sourced = page.locator('#task-list .task-card').filter(has_text='Review the onboarding guide')
        with page.expect_response(lambda response: '/meetings/' in response.url and response.request.method == 'GET'):
            sourced.get_by_role('button', name='Source: Launch review').click()
        page.locator('.proposal-card').filter(has_text='Accepted').wait_for()
        page.locator('#saved-source mark').wait_for()
        assert page.locator('#saved-source mark').inner_text() == 'Aman will update the guide by Friday.'
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(OUT / 'phase2-mobile.png'), full_page=True)
        page.locator("#logout").click()
        page.locator("#login-panel").wait_for(state="visible")
        assert page.locator("#application").is_hidden()
        assert not errors, errors
        browser.close()
    print("Browser smoke passed: accounts, board, transcript upload, exact Unicode evidence highlight, edited acceptance, rejection, provenance link, desktop/mobile, logout")
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
