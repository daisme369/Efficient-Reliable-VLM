# Thorax AI backend

FastAPI and SQLite backend for `src/thorax-web`. It serves the same `/api/v1` contract as the web app's local demo API, so the frontend runs unchanged against it (`THORAX_API_ORIGIN`).

## Run

```powershell
# Once: accounts (passwords from THORAX_DEMO_*_PASSWORD), 12 synthetic patients, studies, images, prior reports, labeling tasks.
$env:PYTHONPATH='src'; python -m thorax_api.accounts seed-demo
# Start on 127.0.0.1:8000 with one worker.
python scripts/serve_api.py
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `THORAX_DB_PATH` | `thorax.sqlite3` | SQLite database file |
| `THORAX_IMAGE_ROOT` | `thorax-images` | Folder holding image files |
| `THORAX_WEB_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | Origins allowed to send changes (CSRF/Origin check) |
| `THORAX_API_PORT` | `8000` | Port for `scripts/serve_api.py` |
| `THORAX_VLM_URL` | unset | Health check target for the model service (`scripts/serve_vlm.py`) shown to admins |
| `THORAX_DEMO_AI_FAULT` | unset | `timeout` or `detector` to simulate AI failures |

Other commands: `python -m thorax_api.accounts create|disable|enable|reset-password`, `python -m thorax_api.labeling create-tasks|export --out <dir>`.

## Modules

| Module | Contents |
| --- | --- |
| `app.py` | App factory, sign-in (HttpOnly session cookie, CSRF, Origin check, rate limits, 30 min idle / 2 h absolute), password change, worklist and image routes |
| `accounts.py` | Users (doctor, admin, annotator), scrypt password hashes, sign-in history, account CLI |
| `catalog.py` | Patients, studies, images, assignments, prior reports, synthetic demo seed |
| `reading.py` | Doctor readings: findings (with revisions), regions, chat, reports, audit events, AI jobs, idempotency |
| `labeling.py` | Annotator tasks, labeler → reviewer flow, reviewer fixes, approved-label export |
| `admin.py` | Overview, user and access control, AI monitoring and pause switch, labeling oversight, admin activity log |
| `storage.py` | Image storage interface (local folder; S3 later) |
| `db.py` | Shared SQLite connection; portable SQL for a later move to PostgreSQL |

Tests: `python -m unittest discover -s tests/api -t .` (they skip when FastAPI is not installed).

## Limits

- AI answers are the same fixed demo answers as the web mock. The trained model (`scripts/serve_vlm.py`) is not connected yet.
- Sign-in sessions live in memory: a restart signs everyone out, and only one worker may run.
- SQLite allows one writer. Deploy on one server with its own disk (for example EC2 with EBS, backed up to S3 with Litestream); move to PostgreSQL for several servers.
- The labeling vocabulary (`configs/labeling_vocabulary.json`) is unverified. Training on exported labels needs an approved scope-change record (`docs/agents/RESEARCH-SCOPE.md`).

## Differences from SYSTEM_DESIGN_PLAN.md

The plan predates the Next.js frontend. This backend follows the frontend's contract where they differ; the team should update the plan or record a decision:

- Session cookie with CSRF token instead of an in-memory bearer token.
- Email sign-in instead of username; roles doctor, admin and annotator instead of doctor and nurse.
- Patients, studies, reading sessions, findings, reports and audit events are stored in SQLite, not only users and roles, because readings are signed and audited.
- Passwords use scrypt (N=2^17, r=8, p=1) from the standard library; the plan's Argon2id proposal would add a dependency.
