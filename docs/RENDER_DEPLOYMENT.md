# Update the existing TaskIQ prototype on Render

Target service: https://ai-project-manager-f6yk.onrender.com
Source: https://github.com/keyur53987/taskiq
Default repository branch verified: `master`.

The owner confirmed that the prototype has no active users and no data that needs preserving. Start Phase 1 with a new empty PostgreSQL database. Do not run the legacy import command. No existing databases or files need to be deleted to make this work.

## Before publishing the code

In the existing Render service, temporarily set Auto-Deploy to Off. Configure the database, OAuth, commands and environment before deploying Phase 1. This prevents a push from deploying code with missing production settings. Do not create a second web service or delete the existing one just to retain the URL.

Create a Render PostgreSQL database in the same region as the web service. Pick the plan in your account; this guide does not provision or upgrade paid resources. Copy its Internal Database URL into the service's DATABASE_URL secret. The application accepts Render's `postgres://` and `postgresql://` URLs and selects psycopg automatically. Use a durable backed-up database plan for a public launch, and follow Render's connection-security instructions for your network setup.

In Google Cloud Console, configure Google Auth Platform and an OAuth client of type Web application:

- Application homepage/origin: `https://ai-project-manager-f6yk.onrender.com`.
- Authorized redirect URI: `https://ai-project-manager-f6yk.onrender.com/api/auth/google/callback`.
- Scopes: openid, email, profile only. No calendar access is needed.
- While the consent screen is in Testing, add the Google accounts that will try TaskIQ as test users. Configure the publishing status for the intended audience before inviting the wider public.

Enter the following in the Render service's Environment page. Keep credentials in Render, never in GitHub or chat:

| Key | Value |
|---|---|
| APP_ENV | production |
| APP_URL | https://ai-project-manager-f6yk.onrender.com |
| DATABASE_URL | New database's Internal Database URL |
| GOOGLE_CLIENT_ID | OAuth web client ID |
| GOOGLE_CLIENT_SECRET | OAuth client secret |
| AI_ENABLED | false initially |

Keep any existing GEMINI_API_KEY secret if you want extraction later. It is not needed for login, teams, projects or manual tasks. Do not copy development SQLite settings into production. For a native Python runtime, select a currently supported Python 3.12 patch version in Render's Python-version configuration.

## Existing Python web service

Repository: `https://github.com/keyur53987/taskiq`. Branch: `master`. Root directory: repository root.

Build command:

```sh
pip install -r requirements.txt
```

For a paid service with Render's pre-deploy support:

```sh
# Pre-deploy command
python -m alembic upgrade head
```

```sh
# Start command
python scripts/render_start.py
```

For the prototype's first deployment on a free, single-instance service without a pre-deploy command:

```sh
# Start command: migrate once before starting the web-worker parent
python -m alembic upgrade head && python scripts/render_start.py
```

The free-service fallback is for one service instance only. Keep overlapping deploys set to Wait. Before scaling replicas or sharing the database between deployments, use a separate migration release job/pre-deploy command. Never run migrations as part of each FastAPI worker's startup. If migrations fail, `&&` prevents the web server starting against the wrong schema.

The entry point honors Render's PORT environment variable, binds 0.0.0.0, starts two workers, bounds connections, and disables query-string access logging and implicit proxy-header trust. Behind Render's proxy, the app's socket-IP login limit may be shared; configure trusted proxy IP handling and per-client edge limits before wider traffic as described in SYSTEM_DESIGN.md. Do not solve this by trusting arbitrary forwarded headers.

Health check path:

```text
/health/ready
```

If your existing service uses Docker instead, retain that runtime: the Dockerfile uses the same PORT-aware entry point. Run migrations via a pre-deploy release step. For a first free, single-instance Docker deployment, an explicit Docker command is `/bin/sh -c 'python -m alembic upgrade head && python scripts/render_start.py'`.

## Publish and verify

The Phase 1 source must first be committed and pushed to GitHub; it is not installed on Render just because it exists in the local folder. Publish it through a reviewed branch/PR or update the existing linked branch deliberately, preserving repository history. Never force-push or upload .env, .venv, databases, or test-results. Run GitHub's SQLite and PostgreSQL checks before deploying.

Once Phase 1 is present on master and the settings above are ready, choose Manual Deploy -> Deploy latest commit. Check build logs, migration success and readiness. Verify:

1. `/health/ready` returns 200.
2. `/api/auth/config` reports google_enabled true and ai_enabled false.
3. `/api/auth/me` returns 401 before login.
4. Real Google sign-in redirects back to the same ai-project-manager-f6yk.onrender.com hostname and creates a session.
5. Workspace/project creation, manual tasks and team assignment work.
6. A second Google account can accept its invitation. Viewer writes are denied; removal revokes access.

Keep Auto-Deploy Off during setup, then choose After CI Checks Pass when ready. Schedule `python manage.py cleanup` hourly through your chosen scheduler. A Render Cron Job is optional and can incur charges; no cron service is provisioned by this change. Complete the wider-launch backup, restore, monitoring, proxy and load gates in SYSTEM_DESIGN.md.

References: [Render FastAPI](https://render.com/docs/deploy-fastapi), [deploy/pre-deploy behavior](https://render.com/docs/deploys), [HTTP health checks](https://render.com/docs/health-checks), [Render PostgreSQL connections](https://render.com/docs/postgresql-creating-connecting).
