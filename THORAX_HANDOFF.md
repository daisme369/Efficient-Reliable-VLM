# Thorax AI software handoff (TAS-28)

Status on 23 November 2026, branch `kiet`. It contains `main`, the TAS acceptance work and Cáp Việt Anh's `vietanh` branch (model service, uploads, Compose deploy with HTTPS, Playwright tests). The app is the Next.js workspace in `src/thorax-web` and the FastAPI + SQLite backend in `src/thorax_api`. All data is synthetic, and no clinical claim is made.

The AI runs in one of two modes:
- **mock**: fixed demo answers. This is the default when no model service is configured.
- **real**: Analyze and Ask call `scripts/serve_vlm.py`. That service runs MedGemma with a trained B1/M1 adapter on a rented GPU. `THORAX_VLM_FAKE=1` runs the same chain with fixed outputs and no GPU.

Contracts: [`docs/spec.md`](docs/spec.md), [`docs/frontend_des.md`](docs/frontend_des.md), [`docs/implementation_plan.md`](docs/implementation_plan.md). Where the built app differs, [`SYSTEM_DESIGN_PLAN.md` section 0](SYSTEM_DESIGN_PLAN.md) and [`src/thorax_api/README.md`](src/thorax_api/README.md) describe the as-built system.

`apps/api/` is an earlier, separate PostgreSQL backend that was already on `kiet` (11 November). The web app does not use it; its contracts were not kept in step.

## Start and test

| Purpose | Command (repository root unless noted) |
| --- | --- |
| Web + backend locally | See [`src/thorax-web/README.md`](src/thorax-web/README.md): `python -m thorax_api.accounts seed-demo`, `python scripts/serve_api.py`, then `pnpm dev` in `src/thorax-web` |
| Model service without a GPU | `THORAX_VLM_FAKE=1 THORAX_VLM_KEY=<16+ chars> python scripts/serve_vlm.py`, then start the API with `THORAX_VLM_URL=http://127.0.0.1:8500` and the same `THORAX_VLM_KEY` |
| Server deploy (Docker, HTTPS) | [`docker/DEPLOY.md`](docker/DEPLOY.md): `docker compose -f docker/compose.yaml up -d --build --wait` |
| Backend tests | `python -m unittest discover -s tests/api -t .` (needs `requirements/api.txt`; slow because sign-in uses scrypt) |
| Repository tests | `python -m unittest discover -s tests -p "test_*.py"` |
| Web checks | in `src/thorax-web`: `pnpm typecheck`, `pnpm build`, `node --test tests/`, `pnpm e2e` (Playwright) |
| Scripted doctor journey | `THORAX_VERIFY_PASSWORD=<doctor password> python scripts/verify_demo.py journey --email doctor2@thorax.demo --study <unread study>` |
| Latency and privacy headers | `THORAX_VERIFY_PASSWORD=<doctor password> python scripts/verify_demo.py latency --email doctor@thorax.demo` |
| Backup | `python scripts/verify_demo.py backup --db thorax.sqlite3 --images thorax-images --out <new folder>` |
| Restore check | `python scripts/verify_demo.py check --db <folder>/thorax.sqlite3 --images <folder>/thorax-images` |

**Restore:**
1. Stop the API.
2. Copy `thorax.sqlite3` and `thorax-images/` from the backup folder into place, or into a fresh Docker volume.
3. Run the restore check. It verifies SQLite integrity, foreign keys, every image hash, and that every final report is signed.

Never restore over a live demo.

## Acceptance status

| Ticket | Status | Evidence |
| --- | --- | --- |
| TAS-6 repository and contracts | Done | Spec documents in `docs/`; web typecheck and API tests pass |
| TAS-7 Compose stack | Done by Cáp Việt Anh's deploy work | [`docker/DEPLOY.md`](docker/DEPLOY.md). Not run on this machine (no Docker) |
| TAS-9 fixtures and insert-only import | Done | `tests/api/test_fixtures.py` (also keeps admin-created patients and uploads) |
| TAS-12 worklist | Done | `tests/api/test_catalog.py`; shared-study check in `test_full_reading_to_signed_report_and_it_survives_a_restart` |
| TAS-13 reading session | Done | `test_concurrent_opens_create_one_session_bound_to_the_url_study` |
| TAS-14 viewer | Done (code and unit tests) | `src/thorax-web/tests/image-viewport.test.cjs` round-trip test |
| TAS-18 chat context | Done | `test_chat_keeps_last_ten_messages_labels_images_and_excludes_failures`; journey step "ask" |
| TAS-19 ROI questions | Done | `test_roi_bounds_image_binding_and_answer_snapshot_survive_box_changes`; journey step "ask about region" |
| TAS-20 Gate 1 | Done | `test_gate1_blocks_pending_bypass_and_contradictions_and_signs_once`; journey |
| TAS-21 Gate 2 | Done | `test_gate2_needs_gate1_rejects_stale_and_identity_edits_and_locks_final`; journey |
| TAS-25 real CV detector | **Decision needed** | No separate CV artifact exists. In real mode, region boxes come from the VLM's grounding prompt (M1), labelled "Model-generated region; not validated". The team must decide whether this replaces a CV detector. Nothing was trained or substituted |
| TAS-26 privacy, latency, restart, restore | Done on a development PC, not on demo hardware | Measurements below; `tests/ci/test_verify_demo.py`; restart persistence in the reading tests |
| TAS-27 rehearsal and acceptance | Scripted part done; **human trial still needed** | Journey below. A doctor-role usability trial cannot be done by an agent |

TAS-22, TAS-23 and TAS-24 belong to Cáp Việt Anh; their code is merged here, but they are not covered by this report.

## Scripted journey (TAS-27)

**Run:** 23 November 2026, development PC. Web app on 127.0.0.1:3000 → backend → `serve_vlm.py` with `THORAX_VLM_FAKE=1`. User `doctor2@thorax.demo`, study 2521.

**Result:** all 11 steps passed:
1. Sign in.
2. Open the study (provider `real`, model `fake-vlm`).
3. Analyze: 1 finding and 1 region, 0.55 s.
4. Ask: 0.56 s.
5. Draw a region.
6. Ask about the region: 0.55 s, region snapshot stored.
7. Accept the AI region and finding.
8. Gate 1.
9. Draft the report: 1.6 s.
10. Gate 2.
11. Final reading refuses a new region (`FINAL_READ_ONLY`).

The fake service proves the software chain, not model quality. Re-run it against the real GPU service and record the model ID, adapter hash and latency.

## Measured latency

23 November 2026, Windows 11 development PC, merged `kiet` code. Through the Next.js web app (dev server) to the backend on loopback. Warm sequential requests, 20 runs each, `scripts/verify_demo.py latency --base http://127.0.0.1:3000`.

| Operation | p50 | p95 (nearest rank) | max | Failures |
| --- | --- | --- | --- | --- |
| Worklist (`GET /patients`) | 29.0 ms | 31.9 ms | 32.2 ms | 0 |
| Viewer (study + image bytes, hash checked) | 48.0 ms | 65.0 ms | 65.4 ms | 0 |

- Every response carried `Cache-Control: no-store`.
- The target is p95 < 2 s. Repeat the measurement on the demo laptop and record the hardware.
- Real AI latency (target < 30 s, hard limit 60 s) needs the GPU service.

## Rehearsal checklist (human part of TAS-27)

1. Clean machine or server: follow `docker/DEPLOY.md`, then confirm all containers are healthy.
2. Admin: sign in, check users and assignments, and confirm AI monitoring shows the model service as up (real) or not configured (mock).
3. Doctor: worklist → study → Analyze → review AI boxes and findings → chat and ROI question → Gate 1 → draft and edit the report → Gate 2 → reload and confirm read-only.
4. Negative paths:
   - A second doctor cannot see unassigned patients.
   - A box edit makes the report stale.
   - With AI paused, manual signing still works.
   - An injected timeout (`THORAX_DEMO_AI_FAULT=timeout`) is handled.
5. Restart the stack and confirm the signed reading is unchanged. Run the backup and the restore check.
6. One person acting as a doctor uses the app unaided. Record feedback, fixes and the date in Jira.
7. Present mock and real results separately. If no real model is connected, say so.

## Open decisions and known limits

- **Database:** SQLite, with one writer and one API worker. The plan's PostgreSQL migration (TAS-8) is not used by this app.
- **Real models:** the trained adapter, the GPU host and the data location for real-mode demos are still open (OQ-002). Real mode has only been exercised with the fake service here.
- **Analyze in real mode:** it asks ten hand-written screening questions (`src/thorax_api/vlm.py`). Its own comment says they must be checked against the training templates before results are relied on.
- **Vietnamese UI:** the VI/EN switch translates exact English UI text from `src/thorax-web/lib/i18n-vi.ts`, including the upload and admin screens as of 23 November. New or reworded UI text stays English until it is added there.
- **Fixture limits:** 20 MiB and 40 megapixels per image are demo values, not values from the spec.
- **Labeling vocabulary:** see `docs/scope-changes/` on this branch before training on exported labels.
