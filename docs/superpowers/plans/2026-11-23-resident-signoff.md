# Resident pre-read, doctor sign-off and emergency preliminary report: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An annotator (the hospital "resident") reads a study in the same workspace as a doctor, up to a draft report, but cannot finalize it. Routine studies are sent to a doctor for sign-off. On **urgent** studies, the annotator may release a **preliminary** report at once, and a doctor over-reads it later and grades any discrepancy.

**Architecture:** This reuses the existing reading session in `src/thorax_api/reading.py`. A session keeps its owner in `doctor_id`; for an annotator reading, the owner is the annotator. New `handoff` columns mark a session as sent for sign-off or released as preliminary. Once a session has a handoff, any active doctor assigned to the patient may open and finish it. Gate 2 (`approve_report`) is limited to doctors. Studies gain a `priority` (routine or urgent), set at intake.

**Tech Stack:** FastAPI + SQLite or PostgreSQL (`src/thorax_api`, `db.py`), Next.js 15 + TypeScript (`src/thorax-web`), unittest, Playwright.

**Spec:** this document, plus `docs/workflow-review.md` §1 (hospital workflow). The two flows below are the specification.

```
Routine:  annotator reads → Gate 1 → draft → "Send to doctor" → doctor reviews/edits → Gate 2 (final)
Urgent:   annotator reads → Gate 1 → draft → "Release preliminary" (+ critical-finding call logged)
          → report visible as PRELIMINARY → doctor over-reads → grades agree/minor/major → Gate 2 (final)
```

## Global Constraints

- **Names:** the role key stays `annotator` everywhere (database, API, tests). The UI label is decided in the open questions below.
- **Avoid a name clash:** `preliminary_report` already exists in the workspace and means the **AI's** first report (`reading.py:359`). New fields use `handoff` and `released_content`, never `preliminary_report`.
- **Handoff values:** `signoff` (routine) and `preliminary` (urgent). **Discrepancy values:** `agree`, `minor`, `major`. **Priority values:** `routine` (default) and `urgent`.
- **Who may sign:** only a `doctor` may sign Gate 2. An annotator gets `403 SIGNOFF_REQUIRES_DOCTOR`.
- **Who may release a preliminary:** only an annotator, and only on an `urgent` study. Otherwise the request gets `409 NOT_URGENT`.
- **Signed reports stay intact:** a final report is never edited. The released preliminary text is kept unchanged in `released_content`.
- **Database upgrades:** only `ALTER TABLE … ADD COLUMN`, guarded by `db.columns()` (works on SQLite and PostgreSQL), following the pattern at `catalog.py:66-70`. No table rebuilds, and existing data must survive. The live database has 12 patients and 23 studies.
- **Separate labeling queue:** `THORAX_LABELING` now gates only the labeling queue routes. Annotators can always sign in and read their assigned patients.
- **Mock mode:** the web mock API (`src/thorax-web/lib/server/mock-*.ts`) is **not** extended, because it has no annotator or technician users. The new flows need the real backend, which is how the demo runs.
- **No new dependencies.**
- **Translations:** new UI strings get a Vietnamese entry in `src/thorax-web/lib/i18n-vi.ts`.

## Review Focus

1. **A doctor not assigned to the patient tries to open a handed-off session.** Expect 404, as for any unassigned patient. Test: `test_unassigned_doctor_cannot_see_handed_off_reading` (Task 3).
2. **The annotator edits after handoff, for example from a stale second tab.** Expect `409 HANDED_OFF`, and nothing changes. Test: `test_annotator_is_read_only_after_handoff` (Task 3).
3. **A doctor edits the findings of a released preliminary.** This reopens Gate 1. `released_content` must keep the original text, and sign-off still requires a discrepancy grade. Test: `test_released_content_survives_doctor_edits` (Task 4).
4. **A double-click on "Release preliminary".** The idempotency key replays the result, and only one event is logged. Test: `test_release_preliminary_is_idempotent` (Task 4).
5. **Databases created before this change** (live server). Every new column must appear, and old rows must read as `routine` with no handoff. Test: `test_old_database_gains_handoff_columns` (Task 1).

---

### Task 1: Database columns and annotator access to readings

**Files:**
- Modify: `src/thorax_api/reading.py`
  - `ReadingStore.__init__`: add columns.
  - `_load`, `_save`: persist the new columns.
  - `_authorize` (606-613): allow annotators.
  - `owned`, `open_session` and the other `actor(request, "doctor")` calls in `register_reading`: allow `"doctor", "annotator"`.
- Modify: `src/thorax_api/catalog.py`: `Catalog.__init__` adds `studies.priority`.
- Modify: `src/thorax_api/app.py`
  - `actor()` (279-290): drop the `annotator and not labeling_on` rejection.
  - Login (`ok = …`): annotators may sign in.
  - `/api/v1/patients` and `patient_studies` / `study`: allow annotators, limited to their assigned patients.
  - `image_content`: an annotator assigned to the patient may view the image.
  - Labeling routes: keep `actor(request, "annotator")`, and return 404 when `labeling_on` is false.
- Modify: `src/thorax_api/admin.py`, `assign` (249-261): accept a user with role `doctor` **or** `annotator`.
- Test: `tests/api/test_reading.py`
  - Extract the helpers of `ReadingTests` (`setUp`, `make_app`, `client`, `write`, `open`, `settle`, `analyze`) into a `ReadingBase(unittest.TestCase)` class with no tests. `ReadingTests(ReadingBase)` keeps its tests.

**Interfaces:**
- Produces, on `reading_sessions`:
  - `handoff TEXT CHECK (handoff IN ('signoff','preliminary'))`
  - `handoff_at TEXT`
  - `critical_communicated_to TEXT`
- Produces, on `reports`: `discrepancy TEXT CHECK (discrepancy IN ('agree','minor','major'))` and `released_content TEXT` (JSON of the four `NARRATIVE` fields).
- Produces, on `studies`: `priority TEXT NOT NULL DEFAULT 'routine' CHECK (priority IN ('routine','urgent'))`.
- Produces: `reading["session"]` gains the keys `handoff`, `handoff_at` and `critical_communicated_to`. Each report dict gains `discrepancy` and `released_content` (a parsed dict or `None`).
- Produces: `reading["actor_id"]`, set by `open_reading`, `read` and `mutate`. `_save` writes `reading_events.user_id = reading["actor_id"]` instead of `s["doctor_id"]`.
- Produces: `mutate(..., actor_role: str)`, a new keyword-only parameter. Routes pass `user.role`.

- [ ] **Step 1: Write the failing tests.** Add to the new `SignoffTests(ReadingBase)`. `setUp` creates user 7 `annotator@example.test` with role `annotator`, assigns it to patient 2 (`catalog.set_assignment(7, 2, True)`), and adds `self.annotator = self.client("annotator@example.test")`. Patient 2 has one study, 2421, and in the demo seed only doctor 1 is assigned to it; doctor 2 is not (`catalog.py:290-293`). Also add `make_urgent(self, study_id)`, which runs `UPDATE studies SET priority='urgent' WHERE id=?` through `sqlite3`.

```python
def test_annotator_opens_assigned_study_with_labeling_off(self):
    r = self.annotator.post("/api/v1/studies/2421/sessions")
    self.assertEqual(r.status_code, 201, r.text)
    self.assertEqual(r.json()["session"]["doctor_id"], 7)
    self.assertIsNone(r.json()["session"]["handoff"])

def test_annotator_cannot_open_unassigned_study(self):
    self.assertEqual(self.annotator.post("/api/v1/studies/2411/sessions").status_code, 404)   # patient 1: not assigned

def test_events_record_the_acting_user(self):
    ws = self.annotator.post("/api/v1/studies/2421/sessions").json()
    events = self.annotator.get(f"/api/v1/sessions/{ws['session']['id']}/events").json()["items"]
    self.assertEqual(events[-1]["user_id"], 7)

def test_old_database_gains_handoff_columns(self):
    import sqlite3
    db = sqlite3.connect(self.db)
    cols = lambda t: {row[1] for row in db.execute(f"PRAGMA table_info({t})")}
    self.assertTrue({"handoff", "handoff_at", "critical_communicated_to"} <= cols("reading_sessions"))
    self.assertTrue({"discrepancy", "released_content"} <= cols("reports"))
    self.assertEqual(db.execute("SELECT DISTINCT priority FROM studies").fetchall(), [("routine",)])
```

To make `test_old_database_gains_handoff_columns` prove the upgrade, `setUp` must first create the tables **without** the new columns. Run the SQL of the current `SCHEMA` tuples, copied once into the test as `OLD_SCHEMA`, before calling `make_app()`.

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Command: `$env:PYTHONPATH='src'; python -m unittest tests.api.test_reading.SignoffTests -v`
  - Expected: FAIL, with a 403 on annotator sign-in and a `KeyError: 'handoff'`.

- [ ] **Step 3: Implement the columns, the access changes and `actor_id`** as listed under Files and Interfaces. Today `_authorize` checks `u.role = 'doctor'`; it becomes `u.role IN ('doctor','annotator')`. `admin.assign` keeps the `DOCTOR_INACTIVE` error for inactive users of either role.

- [ ] **Step 4: Run the whole backend suite.**
  - Command: `$env:PYTHONPATH='src'; python -m unittest discover -s tests/api -t .`
  - Expected: all pass. The old count was 90; the new tests add to it. Update `LabelingOffTests` in `test_labeling.py`: an annotator now signs in, but `/api/v1/labeling/*` still returns 404.

- [ ] **Step 5: Commit.**
  - Message: `feat(reading): annotators read assigned studies; handoff, discrepancy and priority columns`

### Task 2: Study priority at intake

**Files:**
- Modify: `src/thorax_api/app.py`
  - `create_study` reads the query parameter `priority` (`routine` or `urgent`, default `routine`). Any other value returns `422 VALIDATION_ERROR`.
- Modify: `src/thorax_api/catalog.py`
  - `create_study(..., priority: str = "routine")`.
  - `study()` and `studies_for_patient()` return `priority`.
  - `patients_for()` orders patients that have an urgent unread study first.
- Modify: `src/thorax_api/reading.py`, `_study_dto`: add `"priority"`.
- Modify: `scripts/demo_data/mimic_mock.py`, `push`: send `priority=urgent` when `item["_indication"]` contains "acute" or "trauma" (case-insensitive). Otherwise send `routine`.
- Test: `tests/api/test_uploads.py`

**Interfaces:**
- Consumes: the `studies.priority` column from Task 1.
- Produces: every study DTO has `"priority": "routine" | "urgent"`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_technician_marks_study_urgent(self):
    r = self.new_study(self.tech, 1, synthetic_image("PNG"), priority="urgent")   # existing helper, test_uploads.py:56
    self.assertEqual(r.status_code, 201, r.text)
    self.assertEqual(r.json()["priority"], "urgent")

def test_priority_defaults_to_routine_and_rejects_unknown(self):
    self.assertEqual(self.new_study(self.tech, 1, synthetic_image("PNG")).json()["priority"], "routine")
    self.assertEqual(self.new_study(self.tech, 1, synthetic_image("PNG"), priority="stat").status_code, 422)
```

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Command: `python -m unittest tests.api.test_uploads -v`
  - Expected: FAIL with a `KeyError: 'priority'`.
- [ ] **Step 3: Implement** as listed under Files.
- [ ] **Step 4: Run the backend suite and `python -m unittest tests.data.test_mimic_mock`.** Expected: all pass.
- [ ] **Step 5: Commit.**
  - Message: `feat(intake): mark a study routine or urgent`

### Task 3: Routine flow, from "Send to doctor" to doctor sign-off

**Files:**
- Modify: `src/thorax_api/reading.py`
  - New route `POST /api/v1/sessions/{session_id}/handoff` (mutation kind `handoff`). It requires `If-Match` and `Idempotency-Key`; add `"handoff"` to the `required` tuple in `mutate`.
  - New handler `_handoff(self, db, reading, study, body, kind, target)`.
  - `mutate`:
    - The access rule replaces `doctor_id != actor_id → missing()` (Interfaces below).
    - A handed-off session owned by an annotator: every write by that annotator gets `409 HANDED_OFF`.
    - `approve_report` by `actor_role == "annotator"` gets `403 SIGNOFF_REQUIRES_DOCTOR`.
  - `read`: the same access rule. The annotator owner can still read after handoff.
  - `_approve_findings`, `_edit_report`: `approved_by = reading["actor_id"]`.
  - `open_reading`: when the actor is a doctor and the study has a session with `handoff IS NOT NULL`, return that session (status 200) instead of creating the doctor's own.
  - `study_states`, and the duplicate `CASE` in `catalog._study_rows` (`catalog.py:120-121`): the `status` value adds `awaiting_signoff`. Precedence: `final` > `preliminary` > `awaiting_signoff` > `in_progress` > `new`.
  - `_workspace`: add `"prepared_by": {"id", "name"}` when the owner's role is annotator, otherwise `None`. Also return `handoff`, `handoff_at` and `critical_communicated_to` in `session`.
  - `owned()` route helper: delegate the owner check to the store. Keep the assignment check for doctors and annotators alike.
- Test: `tests/api/test_reading.py`, `SignoffTests`.

**Interfaces:**
- Consumes: `reading["actor_id"]` and `mutate(..., actor_role)` from Task 1.
- Produces: `ReadingStore._may_access(db, reading, actor_id, actor_role) -> bool`. It is true when either:
  - the actor is the owner and assigned to the patient; or
  - `actor_role == "doctor"`, the actor is assigned to the patient, and `reading["session"]["handoff"] is not None`.
- Produces, the handoff body for the routine case: `{"kind": "signoff"}`. Preconditions:
  - The actor is the annotator owner.
  - `session.status == "findings_approved"`.
  - `_gate2(reading) == []`, which means a draft exists with findings and impression.
  - Otherwise: `409 HANDOFF_NOT_READY`. A non-annotator gets `403 ROLE_FORBIDDEN`.
- Produces: event `sent_for_signoff`, with `{"report_id"}`.
- Produces: the `StudyStatus` union gains `"awaiting_signoff"`, used by Task 5.

- [ ] **Step 1: Write the failing tests.**
  - Add the helper `ready_draft(self, who) -> tuple[int, int, int]`. It opens study 2421, starts a manual review (`POST /findings {}`), saves `{"items": [], "no_abnormality_confirmed": True}`, approves Gate 1 and creates the report. It returns `(sid, report_id, revision)`.

```python
def test_routine_handoff_then_doctor_signs(self):
    sid, rid, rev = self.ready_draft(self.annotator)
    self.assertEqual(self.write("post", f"/api/v1/sessions/{sid}/reports/{rid}/approve", {"findings_version": 1}, revision=rev, who=self.annotator).json()["error"]["code"], "SIGNOFF_REQUIRES_DOCTOR")
    sent = self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "signoff"}, revision=rev, who=self.annotator)
    self.assertEqual(sent.status_code, 200, sent.text)
    ws = self.doctor.post("/api/v1/studies/2421/sessions").json()          # doctor lands on the annotator's reading
    self.assertEqual((ws["session"]["id"], ws["session"]["handoff"], ws["prepared_by"]["id"]), (sid, "signoff", 7))
    self.assertEqual(ws["study"]["status"], "awaiting_signoff")
    signed = self.write("post", f"/api/v1/sessions/{sid}/reports/{rid}/approve", {"findings_version": 1}, revision=ws["session"]["revision"])
    self.assertEqual(signed.status_code, 200, signed.text)
    final = self.doctor.get(f"/api/v1/sessions/{sid}").json()
    self.assertEqual(next(r for r in final["reports"] if r["id"] == rid)["approved_by"], 1)

def test_annotator_is_read_only_after_handoff(self):
    sid, rid, rev = self.ready_draft(self.annotator)
    rev = self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "signoff"}, revision=rev, who=self.annotator).json()["session_revision"]
    late = self.write("patch", f"/api/v1/sessions/{sid}/reports/{rid}", {"comparison": "x"}, revision=rev, key=False, who=self.annotator)
    self.assertEqual(late.json()["error"]["code"], "HANDED_OFF")
    self.assertEqual(self.annotator.get(f"/api/v1/sessions/{sid}").status_code, 200)

def test_handoff_requires_a_complete_draft(self):
    ws = self.annotator.post("/api/v1/studies/2421/sessions").json()
    r = self.write("post", f"/api/v1/sessions/{ws['session']['id']}/handoff", {"kind": "signoff"}, revision=ws["session"]["revision"], who=self.annotator)
    self.assertEqual(r.json()["error"]["code"], "HANDOFF_NOT_READY")

def test_unassigned_doctor_cannot_see_handed_off_reading(self):
    sid, rid, rev = self.ready_draft(self.annotator)
    self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "signoff"}, revision=rev, who=self.annotator)
    other = self.client("doctor2@example.test")       # not assigned to this patient
    self.assertEqual(other.get(f"/api/v1/sessions/{sid}").status_code, 404)

def test_doctor_can_still_read_from_scratch(self):
    self.annotator.post("/api/v1/studies/2421/sessions")      # annotator started, not handed off
    ws = self.open(2421)                                        # doctor opens: gets own new session
    self.assertEqual(ws["session"]["doctor_id"], 1)
```

- [ ] **Step 2: Run the tests and confirm they fail.**
  - Command: `python -m unittest tests.api.test_reading.SignoffTests -v`
  - Expected: FAIL, with a 404 on `/handoff`.
- [ ] **Step 3: Implement** as listed under Files and Interfaces.
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: Commit.**
  - Message: `feat(reading): annotator sends a draft to the doctor; only doctors sign`

### Task 4: Emergency flow, the preliminary release and the over-read with a discrepancy grade

**Files:**
- Modify: `src/thorax_api/reading.py`
  - `_handoff` handles `kind == "preliminary"`.
  - `_edit_report` (approve branch): require a `discrepancy` when `handoff == "preliminary"`, and store it on the report. When the session is not preliminary, `discrepancy` in the body gets `422`.
  - `study_states` and `catalog._study_rows` add `preliminary`.
- Test: `tests/api/test_reading.py`, `SignoffTests`.

**Interfaces:**
- Consumes: `_handoff`, `_may_access` and `study_states` from Task 3; `study["priority"]` from Task 2.
- Produces, the preliminary handoff body: `{"kind": "preliminary", "critical": bool, "communicated_to": str | null}`. Rules:
  - The study must be urgent; otherwise `409 NOT_URGENT`.
  - When `critical` is true, `communicated_to` is required (1–200 characters). When `critical` is false, `communicated_to` must be null.
  - The handoff copies the draft's four `NARRATIVE` fields into `report["released_content"]`.
  - It sets `handoff_at = now` and `critical_communicated_to = communicated_to`.
  - Event `preliminary_released`, with `{"report_id", "critical": bool, "communicated_to"}`.
- Produces, the Gate 2 approve body on a preliminary session: `{"findings_version": int, "discrepancy": "agree"|"minor"|"major"}`. A missing or invalid value gets `422 VALIDATION_ERROR` with the message "Grade the preliminary report before signing." The `gate2_approved` event data adds `discrepancy`.
- Produces: the workspace report dict exposes `released_content` and `discrepancy`.

- [ ] **Step 1: Write the failing tests.** Urgent studies use `self.make_urgent(2421)` from Task 1.

```python
def test_preliminary_only_on_urgent_studies(self):
    sid, rid, rev = self.ready_draft(self.annotator)
    r = self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "preliminary", "critical": False, "communicated_to": None}, revision=rev, who=self.annotator)
    self.assertEqual(r.json()["error"]["code"], "NOT_URGENT")

def test_critical_finding_needs_who_was_told(self):
    self.make_urgent(2421)
    sid, rid, rev = self.ready_draft(self.annotator)
    r = self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "preliminary", "critical": True, "communicated_to": None}, revision=rev, who=self.annotator)
    self.assertEqual(r.status_code, 422)

def test_over_read_requires_discrepancy_grade(self):
    self.make_urgent(2421)
    sid, rid, rev = self.ready_draft(self.annotator)
    self.write("post", f"/api/v1/sessions/{sid}/handoff", {"kind": "preliminary", "critical": True, "communicated_to": "Dr. ER on call, ext 4410"}, revision=rev, who=self.annotator)
    ws = self.doctor.post("/api/v1/studies/2421/sessions").json()
    self.assertEqual((ws["study"]["status"], ws["session"]["critical_communicated_to"]), ("preliminary", "Dr. ER on call, ext 4410"))
    url = f"/api/v1/sessions/{sid}/reports/{rid}/approve"
    self.assertEqual(self.write("post", url, {"findings_version": 1}, revision=ws["session"]["revision"]).status_code, 422)
    ok = self.write("post", url, {"findings_version": 1, "discrepancy": "minor"}, revision=ws["session"]["revision"])
    self.assertEqual(ok.status_code, 200, ok.text)
    report = next(r for r in self.doctor.get(f"/api/v1/sessions/{sid}").json()["reports"] if r["id"] == rid)
    self.assertEqual(report["discrepancy"], "minor")

def test_released_content_survives_doctor_edits(self):
    # release, then doctor reopens Gate 1 (save findings), re-approves, re-drafts and signs with "major"
    # assert: the released report's released_content == the annotator's original four fields, unchanged
    ...

def test_release_preliminary_is_idempotent(self):
    # same Idempotency-Key twice → same response, exactly one 'preliminary_released' event
    ...
```

  - The last two tests follow the same calls; their assertions are stated in their comments.
  - Two decisions for `test_released_content_survives_doctor_edits`:
    - Reopening Gate 1 marks the released draft `stale` (existing `_invalidate_approval`). Its `released_content` stays on that stale report.
    - Sign-off happens on the new draft. A discrepancy is still required, because `handoff` is still `preliminary`.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: FAIL with an unknown handoff kind (422).
- [ ] **Step 3: Implement** as listed under Interfaces.
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: Commit.**
  - Message: `feat(reading): urgent preliminary release with critical-finding log and graded over-read`

### Task 5: Web screens

**Files:**
- Modify: `src/thorax-web/lib/contracts.ts`
  - `StudyStatus` adds `'awaiting_signoff' | 'preliminary'`.
  - Study adds `priority: 'routine' | 'urgent'`.
  - Session adds `handoff: 'signoff' | 'preliminary' | null`, `handoff_at: string | null` and `critical_communicated_to: string | null`.
  - Workspace adds `prepared_by: { id: number; name: string } | null`.
  - Report adds `discrepancy: 'agree' | 'minor' | 'major' | null` and `released_content: ReportNarrative | null`.
- Modify: `src/thorax-web/components/shell.tsx`
  - `homeFor`: annotator goes to `/worklist`.
  - The descriptor text and navigation treat annotators like doctors. The labeling queue link shows only when `labeling_enabled`.
- Modify: `src/thorax-web/features/worklist/Worklist.tsx`
  - An **Urgent** badge.
  - Status badges for "Awaiting sign-off" and "Preliminary released".
  - A filter chip **"Awaiting my sign-off"** for doctors, matching `awaiting_signoff` or `preliminary`.
  - The worklist route allows annotators.
- Modify: `src/thorax-web/features/workspace/review-report.tsx`
  - **Annotator:** the sign button is replaced by **"Send to doctor"**. On urgent studies a **"Release preliminary"** button is added; it opens a small form with a "Critical finding?" checkbox and a "Who was told, and how" text field (required when checked).
  - **Doctor on a handed-off reading:** a "Prepared by {name}" line.
  - **Doctor on a preliminary reading:** a radio group "Agree / Minor change / Major change" that must be chosen before **Approve and sign**, plus the released text shown read-only for comparison.
- Modify: `src/thorax-web/features/workspace/StudyWorkspace.tsx`
  - A warning banner **"PRELIMINARY: not signed by a doctor"** while `handoff === 'preliminary'` and the session is not final. When `critical_communicated_to` is set, the banner adds "Critical finding communicated to …".
  - A read-only notice for the annotator after handoff.
- Modify: `src/thorax-web/features/workspace/useReading.ts`
  - `handoff(kind, extra)` posts to `/handoff` with the same `If-Match` and idempotency handling as `approveReport`.
- Modify: `src/thorax-web/features/admin/AdminConsole.tsx`, intake form: a **Priority** select (Routine or Urgent) sent as the query parameter `priority`.
- Modify: `src/thorax-web/features/admin/Admin.tsx`, Assignments tab: list annotators as well as doctors.
- Modify: `src/thorax-web/lib/i18n-vi.ts`: an entry for every new string.
- Test: `src/thorax-web/e2e/app.spec.ts`, `accounts.ts`, `playwright.config.ts`.
  - The demo seed (`accounts.py` `DEMO_ACCOUNTS`) creates `annotator@thorax.demo` when `THORAX_DEMO_ANNOTATOR_PASSWORD` is set, regardless of `THORAX_LABELING`. Assign it to the same demo patients as doctor 1, and mark one of those patients' studies `urgent`.

**Interfaces:**
- Consumes: every API field from Tasks 2–4.

- [ ] **Step 1: Write the failing Playwright tests.**
  - `annotator sends a routine study to the doctor, who signs it`:
    - The annotator reads a study, approves Gate 1, makes a draft, clicks **Send to doctor**, and sees the read-only notice.
    - The doctor sees "Awaiting sign-off" on the worklist, opens the study, sees "Prepared by", and signs. The status becomes Final.
  - `annotator releases a preliminary on an urgent study; doctor grades it`:
    - The annotator releases a preliminary with "Critical finding" checked and "ER doctor, phone" filled in.
    - The doctor sees the banner. **Approve and sign** stays disabled until "Minor change" is chosen. After signing, the banner is gone.
- [ ] **Step 2: Run the tests and confirm they fail.**
  - Command: from PowerShell, `cd D:\Efficient-Reliable-VLM\src\thorax-web; pnpm exec playwright test e2e/app.spec.ts`
  - Use the uppercase `D:` path; a lowercase `d:` breaks module loading.
  - Expected: the 2 new tests fail.
- [ ] **Step 3: Implement the screens** as listed under Files.
- [ ] **Step 4: Run all web checks.**
  - Commands: `pnpm typecheck`, `node --test tests/*.test.cjs`, Playwright default, Playwright mock mode, and Playwright with `THORAX_LABELING=1`.
  - Expected: all pass. Mock mode is unchanged.
- [ ] **Step 5: Commit.**
  - Message: `feat(thorax-web): resident sign-off and emergency preliminary screens`

### Task 6: Documentation and deployment

**Files:**
- Modify: `docs/workflow-review.md`: add the resident, attending and over-read section with both flow diagrams, and update the status table.
- Modify: `src/thorax_api/README.md`
  - The new routes (`/handoff`, the approve `discrepancy`) and the error codes `HANDED_OFF`, `HANDOFF_NOT_READY`, `NOT_URGENT` and `SIGNOFF_REQUIRES_DOCTOR`.
  - The `THORAX_LABELING` row: it now gates only the labeling queue.
  - `THORAX_DEMO_ANNOTATOR_PASSWORD`.
- Modify: `src/thorax-web/PRODUCT.md`: the roles paragraph.
- Modify: `docker/DEPLOY.md`, step 9: the annotator password prompt.

- [ ] **Step 1: Update the docs.**
- [ ] **Step 2: Run every suite once more.** Expected: all pass.
- [ ] **Step 3: Commit, then deploy.**
  - Commit message: `docs(thorax): resident sign-off and emergency preliminary`
  - Deploy to EC2 (ask the user first): SSM Run Command `sudo -u ubuntu git -C /opt/thorax pull && bash /opt/thorax/docker/aws-deploy.sh`, then check the new columns on RDS. The PostgreSQL suite (`THORAX_TEST_DATABASE_URL`) must pass too.

## Decisions (user chose the defaults, 24 Nov 2026)

1. **UI label for `annotator`:** "Annotator", "Resident", or "Junior reader"? Default if nobody decides: **"Resident"**. Only the label changes; the role key stays `annotator`.
2. **May a doctor start a fresh reading** while an annotator's reading is in progress but not yet handed off? Default: **yes** (`test_doctor_can_still_read_from_scratch`).
3. **Discrepancy rate per annotator on the admin overview:** in this plan, or later? Default: **later**. The data is stored, so it's only a query and a panel.

## Out of scope

- Sending a reading back from the doctor to the annotator.
- Notifications (email or push) on handoff.
- A referring-clinician role that reads preliminary reports.
- The mock-mode web API.
