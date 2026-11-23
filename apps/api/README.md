# Thorax backend

Independent Python package for the application specified in `docs/spec.md`, `docs/implementation_plan.md` and `docs/frontend_des.md`. The application is housed in `apps/api` by the current backend implementation request; the research package and its dependency pins are unchanged.

## Structure and rules

```text
thorax/
  domain/          ReadingSession, SessionBinding, PixelBox, FindingSet, Report
  application/     AuthService, AdminService, ReadingService, AIJobService,
                   ContextBuilder, AccessPolicy, CommandService, OutputValidator
  infrastructure/  SQLAlchemy Repository/UnitOfWork, ImageStore, HTTPInferenceProvider
  presentation/    request DTOs, HTTP authentication and FastAPI route composition
  worker/          durable claims, execution, heartbeat, deadline reaper and publication
  inference/       private mock service and offline MedGemma LoRA/CV composition
  bootstrap.py     Container: constructor injection and object composition
  config.py        validated Settings loaded from environment and policy TOML
migrations/        explicit Alembic schema plus PostgreSQL integrity triggers
tests/             synthetic domain, API, provider, recovery and opt-in PostgreSQL tests
```

Domain code uses only the standard library. Business behavior belongs to service/entity objects. HTTP handlers parse commands and call services; database access belongs to repositories. Objects receive dependencies through constructors. The isolated real inference entry point reuses `cxr_research.models.medgemma.load_model`; the API/worker do not import torch. There is no automatic model download, training, GPU rental or clinical dataset access.

Environment-specific URLs, secrets, provider mode and private paths are required environment variables. Business limits and timing rules are in `policy.toml`. Enum values, HTTP paths and schema invariants are versioned protocol definitions rather than deployment configuration. Mock responses come from a configured JSON file; application code contains no embedded patient records or AI answers. Do not change locked clinical rules merely by supplying a different policy file.

## Installation and configuration

For the PostgreSQL-backed mock demo, see [Compose setup and backup](../../infra/README.md).

Use Python 3.12 or newer and a separate virtual environment. From this directory:

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.lock
uv pip install --python .venv/Scripts/python.exe --no-deps -e .
```

`requirements.lock` pins the tested runtime and test dependencies, including transitive dependencies. `pyproject.toml` declares the package's direct dependencies. Install this package independently of the repository's ML requirements. Dependencies retain their upstream licenses: FastAPI/Pydantic/SQLAlchemy/Alembic/HTTPX/Uvicorn/python-multipart are MIT or BSD licensed, bcrypt is Apache-2.0, Pillow uses its upstream permissive license, and psycopg uses LGPL-3.0. Distribution must retain the applicable notices.

Set every variable in `.env.example` in the process environment. The example is a template and is not loaded automatically. Generate separate random values for the three secrets; there is no default password, database URL or fallback secret. `THORAX_POLICY_PATH`, `IMAGE_STORAGE_ROOT`, `FIXTURE_ROOT` and `THORAX_MOCK_RESPONSES_PATH` should be absolute paths. Local HTTP cookies require the explicit loopback flag; use HTTPS and secure cookies elsewhere.

Provision PostgreSQL separately. Use a migration-owner connection for the following command, then switch `DATABASE_URL` to a restricted runtime login:

```powershell
.venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head
.venv/Scripts/python.exe -m thorax.cli create-admin --email <admin-email> --name <admin-name>
```

The CLI prompts for the admin password. Never put it in a command or committed file. The runtime role needs SELECT/INSERT/UPDATE on application tables and sequence USAGE, SELECT/INSERT on events, and read-only access to canonical image assets. It must not own the schema, create/drop tables, disable triggers or hard-delete clinical/audit data. Role provisioning is an operator step, not an implicit startup privilege escalation. Migrations are explicit and forward-only; startup does not reset data or call `create_all()`.

Run these as separate processes, selecting host/port on the command line to match your environment:

```powershell
.venv/Scripts/python.exe -m uvicorn thorax.inference.api:create_app --factory --host 127.0.0.1 --port 8001
.venv/Scripts/python.exe -m uvicorn thorax.presentation.api:create_app --factory --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m thorax.worker.main
```

Keep database and inference ports private. Put web/API behind one origin and route `/api/v1` to the API. Public Swagger/OpenAPI endpoints are disabled; trusted tooling can call `create_app().openapi()` locally. Run one worker for the single-inference-slot demo profile. PostgreSQL stores accepted requests before returning 202; an API restart or browser close does not cancel them. The worker independently reaps expired jobs, rejects unauthorized/late publication and never holds a DB transaction across inference.

## Synthetic fixtures and mock responses

Prepare only authorized synthetic/de-identified PNG/JPG assets. `python -m thorax.cli prepare-image <source>` writes a hash-addressed canonical PNG under `IMAGE_STORAGE_ROOT` and returns image metadata for the manifest. It normalizes EXIF orientation, strips metadata, checks size limits and preserves input/canonical hashes. Manually review source assets for burned-in identifiers; metadata stripping cannot detect those.

Store `<fixture_key>.json` under `FIXTURE_ROOT`, with the key included in `policy.toml`'s allowlist. Its exact top-level keys are:

```json
{
  "fixture_key": "thorax-demo-v1",
  "patients": [],
  "studies": [],
  "images": [],
  "past_reports": []
}
```

Patient entries have `id,code,name,birth_year,sex`; study entries have `id,patient_id,study_date,indication`; images have `id,study_id,view` plus the returned canonical image metadata; reports have `id,patient_id,report_date,text,source_key`. Dates are ISO dates. Every study needs at least one image. IDs must be positive, unique and consistent across references. The importer validates the allowlisted bundle in one transaction and rejects conflicts instead of overwriting imported clinical records. It accepts no browser file paths or arbitrary URLs. The admin API returns only metadata/counts. Approximately 12 patients are the specified demo target; tests generate their own smaller synthetic fixtures.

Point `THORAX_MOCK_RESPONSES_PATH` at a reviewed JSON document containing `capabilities` and `operations`. Capabilities declare `provider_mode: mock`, readiness, `can_detect`, `can_ask_roi`, `model_artifacts` and the allowed detector `labels`. An artifact is `{id,version,sha256}`. Operations are `analyze`, `detect`, `ask` and `draft_report`; each maps an actual SHA-256 image/crop hash or `default` to a complete response in the documented private contract. Mock report drafting fills `source_item_ids` from the approved request. The tests include a complete fixture-backed service example.

Import through the admin API, or through controlled local setup:

```powershell
.venv/Scripts/python.exe -m thorax.cli import-fixture --admin-email <admin-email> --fixture-key thorax-demo-v1
```

Real mode uses the same private HTTP contract. `thorax.inference.medgemma` verifies the adapter hashes and pinned base revision, loads only the offline BF16 unquantized base plus safetensors LoRA on Linux CUDA, verifies processor identity and composes a separate configured detector. Prompts and token limits are in `infra/real.inference.toml`; secrets and private addresses are environment variables. It refuses unsupported Windows execution, changed artifacts, incompatible precision or a missing base cache. The detector must implement `/internal/v1/capabilities` and `/internal/v1/detect` with real artifacts, supported labels and original-pixel coordinates. There is no automatic real-to-mock fallback or substitute untrained detector. See `docs/LOCAL_INTEGRATION.md` for the supplied artifact and remaining acceptance steps.

### Private inference contract

Inference uses `AI_PROVIDER_MODE`, `THORAX_POLICY_PATH`, `INFERENCE_BASE_URL`, `INFERENCE_SERVICE_TOKEN` and the provider-specific variables. Its process does not require database or browser session secrets. Keep the token and port on the private network.

Capabilities include `operations`, `accepted_media` (canonical `image/png`), `context_limits` and `coordinate_system` (`original_pixels_xyxy`) along with readiness, labels and artifact hashes. A detector-only service advertises `detect` and image/payload limits. POST requests include `X-Thorax-Deadline-Ms` as Unix epoch milliseconds. The HTTP client and service share the remaining deadline. The VLM passes the remaining budget to generation, and late output cannot publish. [Transformers generation settings](https://huggingface.co/docs/transformers/main/en/main_classes/text_generation).

Mock analyze/detect fixtures can vary by canonical image SHA-256. Private fixture entries can inject an error or a delay for tests. API, worker and inference emit JSON logs with the same opaque request ID and safe operational fields. Image and prompt contents are excluded.

## HTTP usage

Login with `POST /api/v1/auth/login`, sending the configured Origin and JSON email/password. Keep the returned HttpOnly cookie; use `/auth/me` to obtain CSRF and expiry. Clinical writes require Origin, `X-CSRF-Token` and the latest session `ETag` as `If-Match`. Creation, approvals and AI submissions require an `Idempotency-Key`; a lost-response retry uses the exact original key/body. Polling does not extend idle expiry.

The API includes admin accounts/assignments/fixture imports, assigned worklist, authorized image streaming, unique doctor-study sessions, persistent VQA/ROI, findings review, both approval gates, source-linked report drafting, append-only audit projections and durable AI status polling. The report provider receives only approved included findings, never image/history/chat or patient identity. Changing findings or boxes after Gate 1 creates the next review version and stales existing drafts. Gate 2 freezes all clinical writes. A shared patient does not expose another doctor's session.

## Verification

From the repository root, using the independent backend environment:

```powershell
<backend-python> -m pytest apps/api/tests -q --basetemp .test-tmp/thorax-tests
```

Choose a fresh scratch directory for `--basetemp`: pytest owns and clears it. Domain/API/provider tests use SQLite only as a unit-test adapter; application startup rejects SQLite. These tests do not establish PostgreSQL locking/trigger behavior. To run PostgreSQL migration/integrity tests, set `THORAX_TEST_DATABASE_URL` to a dedicated database whose name ends in `_test`. Tests create and remove only their own random schemas. Never point this variable at a shared research or application database.

Initial verification: synthetic workflow, patient/role isolation, stale report rejection, idempotency/revision conflicts, exact ROI cropping, history/message bounds, idle expiry, worker timeout/loss, authorization revocation, audit rollback, metadata stripping and mock HTTP contracts. Live PostgreSQL tests are skipped when no dedicated database is supplied. FastAPI's current TestClient emits an upstream HTTPX deprecation warning; this does not affect the passing assertions.

The exported integration folder now includes the connected frontend, local synthetic runtime and verified supplied LoRA adapter handoff. Native model execution, the real end-to-end performance target, bcrypt hardware calibration, PostgreSQL concurrency acceptance and backup/restore still require their specified integration environment. Do not treat synthetic test success as clinical or real-model acceptance.

## Task record

- Jira issue / reviewer: not supplied; no human review asserted.
- Goal: implement the documented backend as an OOP package with external configuration.
- Scope: `apps/api`, tests, package documentation, a root README link and a synthetic backend CI job.
- Inputs: the three local Thorax specifications; package pins in `requirements.lock`.
- Deliverables: code, explicit migrations, worker/mock entry points, tests and setup instructions.
- Acceptance: demonstrated synthetic backend workflow and negative guards; live PostgreSQL and real-model acceptance remain separate.
- Compute: CPU only; no GPU budget or paid service.
- Execution: single agent; no delegation because code and verification share one backend contract.
- External research: official FastAPI lifespan and SQLAlchemy transaction documentation.
- Stop conditions: restricted data, changes to research decisions, missing real artifacts or material paid compute.
