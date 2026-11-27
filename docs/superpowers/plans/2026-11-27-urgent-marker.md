# Urgent marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Urgent, unsigned studies are visible at a glance on the worklist and in the reading screen.

**Architecture:** One backend field (`has_urgent` on the patient list, same rule as the existing sort) plus CSS classes and a bar in the web.

**Tech Stack:** FastAPI + SQLite/PostgreSQL (`src/thorax_api`), Next.js (`src/thorax-web`), Playwright.

**Spec:** docs/superpowers/specs/2026-11-27-urgent-marker-design.md

## Global Constraints

- No new dependencies. SQL runs on SQLite and PostgreSQL.
- The word "Urgent" always accompanies the colour; Vietnamese uses the existing `'Urgent': 'Khẩn'` entry.
- "Unsigned" = no `reading_sessions` row with `status = 'final'` for that study.

## Review Focus

1. A preliminary released (not final) urgent study still shows the marker — covered by the e2e (marker checked after release).
2. A signed urgent study drops the marker everywhere — backend test + e2e after sign.
3. Mock API without `has_urgent`/`priority` renders no marker and no crash — field optional; mock e2e must stay green.

---

### Task 1: `has_urgent` on the patient list

**Files:** Modify `src/thorax_api/catalog.py` (`patients_for`); Test `tests/api/test_reading.py` (class with `make_urgent`).

**Interfaces:** Produces `GET /api/v1/patients` items with `has_urgent: bool`.

- [ ] Test `test_patient_list_flags_unsigned_urgent`: `make_urgent(2421)`; patient 2 `has_urgent is True`, patient 1 `False`, patient 2 listed first;
  insert a `final` reading_session for 2421 → patient 2 `has_urgent is False`.
- [ ] Run, expect FAIL (`KeyError`/missing field).
- [ ] Select the urgent `CASE WHEN EXISTS … THEN 1 ELSE 0 END AS has_urgent`, `ORDER BY has_urgent DESC, p.id`; return it as `bool`.
- [ ] Run test file, expect PASS. Commit `feat(worklist): has_urgent on patient list`.

### Task 2: Web markers

**Files:** `src/thorax-web/lib/contracts.ts` (`PatientSummary.has_urgent?: boolean`), `features/worklist/Worklist.tsx`,
`features/workspace/StudyWorkspace.tsx`, `app/globals.css`, `e2e/app.spec.ts`.

- [ ] e2e (in "annotator releases a preliminary on an urgent study"): after release, doctor on `/worklist` sees the patient-4 row with
  text "Urgent"; at `/patients/4/studies/2441` sees `getByText('Urgent study — not yet signed')`;
  after signing, that text has count 0.
- [ ] Run e2e, expect FAIL.
- [ ] Patient row: `className` adds `row-urgent` and an error `Badge` "Urgent" after the name when `has_urgent`. Study row: `row-urgent` when
  `priority === 'urgent' && status !== 'final'`. Workspace: `<div className="urgent-bar">` with an icon and "Urgent study — not yet signed"
  above the identity strip when `data.study.priority === 'urgent' && data.study.status !== 'final'`; VI entry "Ca khẩn — chưa ký".
  CSS: `.data-table tr.row-urgent > :first-child { box-shadow: inset 4px 0 0 var(--error-text) }`; `.urgent-bar` red text on `--error-bg`, bold.
- [ ] Run real + mock e2e, expect PASS. Commit `feat(thorax-web): red marker on urgent studies`.
