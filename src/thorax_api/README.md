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
| `THORAX_DATABASE_URL` | unset | `postgresql://user:password@host:5432/db?sslmode=require` runs on PostgreSQL (AWS RDS); otherwise SQLite. `python -m thorax_api.migrate --source <file>` copies a SQLite database into an empty PostgreSQL one |
| `THORAX_DB_PATH` | `thorax.sqlite3` | SQLite database file, used when `THORAX_DATABASE_URL` is unset |
| `THORAX_IMAGE_ROOT` | `thorax-images` | Folder holding image files |
| `THORAX_WEB_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | Origins allowed to send changes (CSRF/Origin check) |
| `THORAX_API_PORT` | `8000` | Port for `scripts/serve_api.py` |
| `THORAX_API_HOST` | `127.0.0.1` | Address `scripts/serve_api.py` listens on; `0.0.0.0` only inside the Compose network (`docker/DEPLOY.md`) |
| `THORAX_TRUST_PROXY` | unset | `1` trusts `X-Forwarded-For` from any peer, for the sign-in limit. Set it only when the API port is reachable solely through the site's own proxy, as in `docker/compose.yaml` |
| `THORAX_SIGNIN_PER_ADDRESS_PER_MINUTE` | `5` | Sign-in attempts allowed per client address per minute; raise it when many users share one public address |
| `THORAX_VLM_URL` | unset | Base URL of the vLLM server (`vllm serve`, OpenAI-compatible API), without `/v1`. With `THORAX_VLM_KEY` set too, Analyze and Ask AI call the real model; otherwise they use the fixed demo answers |
| `THORAX_VLM_KEY` | unset | vLLM's `--api-key`, sent as a Bearer token |
| `THORAX_VLM_MODEL` | `thorax-m1` | vLLM's `--served-model-name`; `scripts/gpu/merge_m1_adapter.py` prints it with the adapter hash, and every answer records it |
| `THORAX_VLM_CALIBRATION` | unset | M1 `metrics.json`; turns on calibrated abstention. `THORAX_VLM_RISK_METHOD` picks `answer_signal` (default), `grounding_signal` or `joint` |
| `THORAX_DEMO_AI_FAULT` | unset | `timeout` or `detector` to simulate AI failures |
| `THORAX_LABELING` | unset | `1` turns the annotator labeling queue on. It is off by default: the doctor's Gate 1 review is the annotation (spec FR-017 keeps training-data export for later). While off, only the labeling routes return 404; annotators (residents) still sign in and read their assigned studies. |
| `THORAX_DEMO_ANNOTATOR_PASSWORD` | unset | `seed-demo` creates the demo resident `annotator@thorax.demo`, assigns it to doctor 1's patients and marks study 2441 urgent |
| `THORAX_DEMO_TECHNICIAN_PASSWORD` | unset | `seed-demo` creates `technician@thorax.demo` (X-ray technician) when set |

Other commands: `python -m thorax_api.accounts create|disable|enable|reset-password`, `python -m thorax_api.labeling create-tasks|export --out <dir>`.

## Modules

| Module | Contents |
| --- | --- |
| `app.py` | App factory, sign-in (HttpOnly session cookie, CSRF, Origin check, rate limits, 30 min idle / 2 h absolute), password change, worklist and image routes |
| `accounts.py` | Users (doctor, admin, annotator), scrypt password hashes, sign-in history, account CLI |
| `catalog.py` | Patients, studies, images, assignments, prior reports, synthetic demo seed, and uploads: admin-created patients, studies with PNG/JPEG images (decoded, limited to 10 MiB and 16 MP, re-encoded as PNG without metadata) |
| `reading.py` | Readings by doctors and residents: findings (with revisions), regions, chat, reports, audit events, AI jobs, idempotency, handoff for sign-off and the urgent preliminary report |
| `labeling.py` | Annotator tasks, labeler → reviewer flow, reviewer fixes, approved-label export |
| `admin.py` | Overview, user and access control, AI monitoring and pause switch, labeling oversight, admin activity log |
| `sessions.py` | Sign-in sessions (token hashes only) and the signing secret, stored in SQLite |
| `storage.py` | Image storage interface (local folder; S3 later) |
| `db.py` | Shared SQLite connection; portable SQL for a later move to PostgreSQL |

Tests: `python -m unittest discover -s tests/api -t .` (they skip when FastAPI is not installed).

## Resident sign-off and the preliminary report

An annotator is the hospital's resident: they read assigned studies in the doctor's workspace up to a draft report, but only a doctor signs (Gate 2).

- `POST /api/v1/sessions/{id}/handoff` (`If-Match` and `Idempotency-Key` required), by the resident who owns the reading, once Gate 1 is approved and the draft has findings and an impression:
  - `{"kind": "signoff"}`: sends the draft to the doctors. The study shows `awaiting_signoff`.
  - `{"kind": "preliminary", "critical": bool, "communicated_to": str | null}`: only on an `urgent` study. The report is released at once as PRELIMINARY (`preliminary`), its text kept unchanged in `released_content`. A critical finding needs who was told and how (1–200 characters).
- After a handoff, any doctor assigned to the patient opens that reading (opening the study lands on it) and finishes it. The resident can still read it but not change it.
- Signing a preliminary needs `"discrepancy": "agree" | "minor" | "major"` in the approve body; it is stored on the report and in the `gate2_approved` event.
- Studies get a `priority` (`routine` by default, `urgent`) at intake: `POST /api/v1/patients/{id}/studies?priority=urgent`.
- Errors: `403 SIGNOFF_REQUIRES_DOCTOR` (a resident tries to sign), `409 HANDED_OFF` (a resident edits after handoff), `409 HANDOFF_NOT_READY`, `409 NOT_URGENT`, `403 ROLE_FORBIDDEN` (a doctor tries to hand off).

## Manual review and recovery

On a fresh reading, **Start manual review** creates an empty findings version through `POST /api/v1/sessions/{id}/findings` with `{}`. The doctor enters findings or confirms a normal study, saves regions if needed, and approves Gate 1. **Create report manually** calls `POST /api/v1/sessions/{id}/reports` with `{findings_version}` to prepare an editable draft from those approved findings. Gate 2 requires saved Findings and Impression. Both creation routes require the current `If-Match` and an `Idempotency-Key` and work while AI is paused. An existing approved findings set can also use manual report creation.

Starting manual review reserves the reading's findings. Subsequent initial Analyze returns `FINDINGS_ALREADY_EXIST` to preserve that work. Workspace exposes nullable `analyzed_image_id`, and each report exposes `source` as `doctor` or `ai`, so the UI can show its origin. Saved readings use the existing database structure.

An active annotator can use **Label next image** to recover unfinished work owned by a disabled account. The claim preserves boxes and records both owners. Every previous labeler is excluded from reviewing the task. The queue includes `claimable_count` for this action. Administrator user PATCH requests accept exactly one of `is_active` or `password`.

Web mock regressions run from `src/thorax-web` with `node --test tests/mock-clinical.test.cjs`.

## Limits

- Without `THORAX_VLM_URL` and `THORAX_VLM_KEY`, AI answers are the web mock's fixed demo answers.
- With a model server (vLLM serving MedGemma with the M1 or B1 adapter merged in), the call runs in a background thread with a 55 s timeout; the questions go to vLLM in parallel.
  - **Analyze** asks one yes/no screening question per finding. "Yes" answers become suggested findings, and the model's boxes become AI regions.
  - **Ask AI** returns the model's answer.
  - With a calibration file, answers below the calibrated threshold are withheld ("abstained") and their candidate text is not stored.
  - Draft report stays template-based on the approved findings.
  - In-flight results are held in memory, so a backend restart turns them into retryable timeouts.
  - Test the chain without a GPU with `THORAX_VLM_KEY=<key> python scripts/fake_vllm.py` and `THORAX_VLM_URL=http://127.0.0.1:8500`.
  - `scripts/serve_vlm.py` (the earlier Transformers service) stays only to compare answers with vLLM on a GPU; delete it after that check.
- Sign-in sessions are stored in SQLite, so a restart keeps users signed in. Sign-in rate-limit counters are in memory and reset on restart, so run one worker.
- Each request logs one line: method, route template, status, time and request ID. Errors return the same request ID. Raw URLs, search text and bodies are never logged.
- SQLite allows one writer; PostgreSQL (`THORAX_DATABASE_URL`) is used on AWS RDS. One API worker either way, because sign-in rate limits are in memory.
- The labeling vocabulary (`configs/labeling_vocabulary.json`) is unverified. Training on exported labels needs an approved scope-change record (`docs/agents/RESEARCH-SCOPE.md`).

## Differences from SYSTEM_DESIGN_PLAN.md

The plan predates the Next.js frontend. This backend follows the frontend's contract where they differ. Section 0 of the plan records all differences for team review; the main ones:

- Session cookie with CSRF token instead of an in-memory bearer token.
- Email sign-in instead of username; roles doctor, admin and annotator instead of doctor and nurse.
- Patients, studies, reading sessions, findings, reports and audit events are stored in SQLite, not only users and roles, because readings are signed and audited.
- Passwords use scrypt (N=2^17, r=8, p=1) from the standard library; the plan's Argon2id proposal would add a dependency.
