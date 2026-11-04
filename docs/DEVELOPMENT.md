# TaskIQ internal development reference

Maintainer reference for local development, verification, and operations. The repository README presents TaskIQ to its audience; release details live in this directory. Run the commands below from the repository root.

TaskIQ turns meeting notes into reviewed project tasks. Phase 1 provides Google sign-in, private workspaces, projects, roles, invitations, and member assignments. Phase 2 adds saved meeting sources, transcript imports, source-backed task proposals and explicit review before task creation.

## Run locally (Python 3.12)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Configure Google credentials in .env, then:
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn main:app --port 8000 --no-access-log
```

Open http://localhost:8000. Use that exact hostname: cookies, CSRF origin checks and OAuth redirects must agree with `APP_URL`.

Create a Google OAuth **Web application** client in Google Cloud Console. Set the redirect URI to `http://localhost:8000/api/auth/google/callback` locally and `https://YOUR_DOMAIN/api/auth/google/callback` for production. Configure the consent screen/test users or publish it for your intended audience. Put the client ID and secret in `.env`. Only `openid email profile` is requested, not Calendar/Drive access. There is no password database, development login bypass, or authentication SaaS.

Without credentials, protected APIs remain closed and the landing page explains that sign-in is not configured. Live Google sign-in requires operator credentials and an interactive check. Tests mock Google's exchange and separately test signed-token validation with generated RSA keys.

## Using Phase 1

1. Sign in, open **Workspace & account settings**, create a workspace, and add a project.
2. Create invitation links for teammates' Google account emails. Share them yourself; TaskIQ does not send email. Links expire after seven days and can be revoked. They are shown only when generated. Reissuing an invitation invalidates the previous link.
3. Invitees sign in with the exact invited email and explicitly accept. Acceptance is single-use and never changes an existing member's role.
4. Add tasks manually, or open **Meetings & review** to save notes/transcripts, generate proposals when Gemini is enabled, and edit/accept/reject them. Accepted tasks link back to their meeting and exact source passage. Unknown names are not silently mapped to accounts.
5. Admins manage roles/removal. The last Admin cannot be demoted or removed. Removal revokes access and clears assignments in that workspace.

| Capability | Admin | Member | Viewer |
|---|---|---|---|
| Read projects, tasks, team directory; export loaded tasks | Yes | Yes | Yes |
| Create/edit/delete tasks; assign members | Yes | Yes | No |
| Extract tasks / generate summaries if enabled | Yes | Yes | No |
| Create projects, invite/revoke, manage team | Yes | No | No |

Roles apply workspace-wide, including all projects. Project-specific ACLs are not included. Any signed-in user can create a workspace and become its Admin. A workspace supports 200 projects, 200 members and 200 pending invitations. A user can belong to 50 workspaces through creation or invitations. Account settings support name changes and signing out all devices.

## Storage and migrations

The new development database is `taskiq.db`. The original `tasks.db` is **not modified or exposed**: old tasks have no trustworthy user ownership and are never assigned to the first person who signs in.

After signing in and creating an empty target project, its Admin can ask an operator to import the original tasks:

```powershell
.\.venv\Scripts\python manage.py import-legacy --source tasks.db --project-id PROJECT_UUID --admin-email owner@example.com
```

Back up the source first. The command reads it in read-only mode, validates fields, requires the specified user to be the target workspace Admin, and refuses nonempty projects. IDs are not preserved. The target may be PostgreSQL. Do not repeat an import into a deliberately emptied destination.

Startup checks the expected Alembic revision; it does not create tables. Run migrations once as a release step, never from each web replica.

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic check
.\.venv\Scripts\python manage.py cleanup
```

Schedule cleanup hourly for expired OAuth flows, sessions, invitations and quota counters. Expiry checks apply even when cleanup has not run.

## Verification

```powershell
.\.venv\Scripts\python -m pytest -q
node --check static/app.js
```

Set `TASKIQ_TEST_DATABASE_URL` to a **dedicated disposable PostgreSQL test database** to run the same tests there. Tests create/drop a unique schema per test; never use production. CI includes SQLite and PostgreSQL 16 tests, migration drift checks and JavaScript syntax checks.

Optional browser smoke: install Playwright and Chromium in your tooling Python, then run `python scripts/browser_smoke.py`. It uses the repo `.venv` to run an isolated server, seeds a test session directly into its temporary DB, and exercises the UI without adding a login bypass to the app. Screenshots/logs go under ignored `test-results/`. It does not test Google's live consent flow.

`requirements.txt` pins the dependency set; `requirements.in` records direct ranges. Review upgrades and refresh the lock in a clean Python 3.12 environment.

## Deployment

Read [the system design and launch runbook](SYSTEM_DESIGN.md). Production requires HTTPS, PostgreSQL, Google OAuth credentials, a configured edge proxy, backups and operational checks. `APP_ENV=production` refuses missing credentials, HTTP origins and SQLite.

For the existing `ai-project-manager-f6yk.onrender.com` prototype, follow [the Render update guide](RENDER_DEPLOYMENT.md). Its old data can be discarded; use a new PostgreSQL database without importing legacy tasks.

For local Docker/PostgreSQL verification, set a URL-safe `POSTGRES_PASSWORD` in `.env` and run `docker compose up --build`. The app binds `127.0.0.1:8000`; PostgreSQL is not exposed to the host. This is not a public TLS deployment.

Read [the Phase 2 workflow and migration notes](PHASE2.md). Extraction saves pending proposals; only explicit acceptance creates board tasks. The old immediate-save extraction endpoint returns 410. Set `AI_ENABLED=false` to run without Gemini: meeting records and manual evidence-backed proposals still work. Production defaults to AI disabled unless explicitly enabled. Browser/audio capture and meeting bots are not implemented.
