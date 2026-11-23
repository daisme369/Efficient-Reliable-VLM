# Thorax AI software handoff (TAS-28)

Status on 19 November 2026 for the Software MVP release (22 November). The app is the Next.js workspace in `src/thorax-web` and the FastAPI + SQLite backend in `src/thorax_api`. All AI output is the deterministic **mock** provider. No real model is connected, and no clinical claim is made. All data is synthetic.

Contracts: [`docs/spec.md`](docs/spec.md), [`docs/frontend_des.md`](docs/frontend_des.md), [`docs/implementation_plan.md`](docs/implementation_plan.md). Where the built app differs, [`SYSTEM_DESIGN_PLAN.md` section 0](SYSTEM_DESIGN_PLAN.md) and [`src/thorax_api/README.md`](src/thorax_api/README.md) describe the as-built system.

## Start and test

| Purpose | Command (repository root unless noted) |
| --- | --- |
| Web + backend locally | See [`src/thorax-web/README.md`](src/thorax-web/README.md): `python -m thorax_api.accounts seed-demo`, `python scripts/serve_api.py`, then `pnpm dev` in `src/thorax-web` |
| Web + backend in Docker | `docker compose --env-file src/thorax-web/.env.local -f docker/compose.yaml up --build -d` |
| Backend tests | `python -m unittest discover -s tests/api -t .` (needs `requirements/api.txt`; about 10 minutes because sign-in uses scrypt) |
| Repository tests | `python -m unittest discover -s tests -p "test_*.py"` |
| Web checks | in `src/thorax-web`: `pnpm typecheck`, `pnpm build`, `node --test tests/` |
| Latency and privacy headers | `THORAX_VERIFY_PASSWORD=<doctor password> python scripts/verify_demo.py latency --email doctor@thorax.demo` |
| Backup | `python scripts/verify_demo.py backup --db thorax.sqlite3 --images thorax-images --out <new folder>` |
| Restore check | `python scripts/verify_demo.py check --db <folder>/thorax.sqlite3 --images <folder>/thorax-images` |

To restore, stop the API, copy `thorax.sqlite3` and `thorax-images/` from the backup folder into place (or into a fresh Docker volume), then run the restore check. It verifies SQLite integrity, foreign keys, every image hash and that every final report is signed. Never restore over a live demo.

## Acceptance status (mock mode)

| Ticket | Status | Evidence |
| --- | --- | --- |
| TAS-6 repository and contracts | Done locally | Spec documents in `docs/`; web build/typecheck and API tests pass |
| TAS-7 Compose stack | Written, **not run** | `docker/compose.yaml`; `tests/ci/test_compose.py` checks ports, network, volume, health. Docker was not installed on the test machine |
| TAS-9 fixtures and insert-only import | Done | `tests/api/test_fixtures.py` |
| TAS-12 worklist | Done | `tests/api/test_catalog.py`; shared-study check in `test_full_reading_to_signed_report_and_it_survives_a_restart` |
| TAS-13 reading session | Done | `test_concurrent_opens_create_one_session_bound_to_the_url_study` |
| TAS-14 viewer | Done (code and unit tests) | `src/thorax-web/tests/image-viewport.test.cjs` round-trip test |
| TAS-18 chat context | Done | `test_chat_keeps_last_ten_messages_labels_images_and_excludes_failures` |
| TAS-19 ROI questions | Done, mock only | `test_roi_bounds_image_binding_and_answer_snapshot_survive_box_changes`. No server crop exists; the mock answers from the saved box coordinates |
| TAS-20 Gate 1 | Done | `test_gate1_blocks_pending_bypass_and_contradictions_and_signs_once` |
| TAS-21 Gate 2 | Done | `test_gate2_needs_gate1_rejects_stale_and_identity_edits_and_locks_final` |
| TAS-25 real CV detector | **Blocked** | No CV artifact, label taxonomy or coordinate transform has been supplied (OQ-003). There is no inference service; `THORAX_VLM_URL` only shows a health check to admins. Nothing was trained or substituted |
| TAS-26 privacy, latency, restart, restore | Done on a development PC, not on demo hardware | Measurements below; `tests/ci/test_verify_demo.py`; restart persistence in the reading tests |
| TAS-27 rehearsal and acceptance | **Needs people** | Checklist below; a doctor-role trial cannot be done by an agent |

TAS-22, TAS-23 and TAS-24 belong to Cáp Việt Anh and are not covered here.

## Measured latency

19 November 2026, Windows 11 development PC, Python 3.13, backend run directly with uvicorn on loopback, fresh synthetic database, mock AI, warm sequential requests, 20 runs each, `scripts/verify_demo.py latency` against the API port (not through the Next.js proxy):

| Operation | p50 | p95 (nearest rank) | max | Failures |
| --- | --- | --- | --- | --- |
| Worklist (`GET /patients`) | 20.3 ms | 33.1 ms | 37.2 ms | 0 |
| Viewer (study + image bytes, hash checked) | 54.9 ms | 75.0 ms | 86.9 ms | 0 |

Every response carried `Cache-Control: no-store`. The target is p95 < 2 s on the demo laptop through the web app; repeat the command there with `--base http://127.0.0.1:3000` and record the hardware. AI operation latency (target < 30 s, hard limit 60 s) can only be measured once a real model is connected; the mock reports a fixed 1.4 s.

## Rehearsal checklist (TAS-27)

1. Clean machine: clone, fill `.env.local`, start with Compose, confirm both services are healthy.
2. Admin: sign in, check users and assignments, confirm AI monitoring shows mock mode.
3. Doctor: worklist → study → Analyze → review AI boxes and findings → chat and ROI question → Gate 1 → draft and edit report → Gate 2 → reload and confirm read-only.
4. Negative paths: second doctor cannot see unassigned patients; stale report after a box edit; AI paused still allows manual signing; injected timeout (`THORAX_DEMO_AI_FAULT=timeout`).
5. Restart the stack and confirm the signed reading is unchanged; run the backup and restore check.
6. One person acting as a doctor uses the app unaided; record feedback, fixes and date in Jira.
7. Present mock and real results separately. If no real model is connected, say so.

## Open decisions and known limits

- **Database:** SQLite, one writer, one API worker; sign-in sessions live in memory, so a restart signs everyone out. The plan's PostgreSQL migration (TAS-8) was not done.
- **Real models:** VLM (TAS-24) and CV (TAS-25) artifacts, manifests, hardware and data location are still open questions OQ-002 and OQ-003.
- **No worker or inference containers:** AI jobs settle inside the backend; there is no separate model service yet.
- **Compose** has not been run on Docker; the first run must be checked by hand.
- **Fixture limits** of 20 MiB and 40 megapixels per image are demo values, not from the spec.
- **Safety checker change:** `scripts/ci/check_repository.py` now treats every `*.dockerignore` file as an ignore file that must deny the full context first. It needs review by the CI owner.
- **Labeling vocabulary** (`configs/labeling_vocabulary.json`) is unverified; training on labels needs a scope-change record.
