# Case search: prior studies and similar cases — design

Date: 2026-11-26 · Branch: vietanh · Status: awaiting approval

## Intent

While reading a chest X-ray, a doctor (or resident) wants to:

1. find whether and when **this patient** had a given disease before, across their earlier studies;
2. find **other patients** with the same disease and compare them, X-ray side by side;
3. use those earlier studies to support what the new study shows.

Success for the demo (06/12): searching "pleural effusion" (or "tràn dịch màng phổi") in a reading shows this patient's
earlier reports that mention it and confirmed cases from other patients, and one click puts a case's X-ray next to the current one.

## Goals

- Keyword search with a built-in chest X-ray synonym list (English + Vietnamese, with and without diacritics).
- Negated mentions do not match: "no pleural effusion", "không tràn dịch" must not return a case for effusion.
- Own patient: results show name, code and exact date (doctor already has access).
- Other patients: **de-identified** — X-ray, findings, impression, study month, age band (decade) and sex only; no name, code,
  exact date, or link to open the record.
- Compare view: current image and the case image side by side in the reading workspace.
- Every view of another patient's case image is recorded (who, which case, when).

## Non-goals (this round)

- Meaning-based search (embeddings / pgvector) and AI-written summaries — approaches B and C. The search sits behind one
  function so B can replace the engine later without changing the screens.
- A stand-alone "Case search" page in the top nav (later; reuses the same API).
- Feeding earlier Thorax reports to the AI as history (changes the AI provenance contract).
- Image-similarity search.

## Anti-goals (failure even if it works)

- A doctor can learn the identity of a patient who is not assigned to them.
- Search returns a case for a disease the report explicitly rules out.
- Search becomes slow or breaks on PostgreSQL/RDS.

## Constraints

- Backend FastAPI `src/thorax_api`, SQLite locally and PostgreSQL on RDS via `db.py` — SQL must run on both.
- No new dependencies.
- Roles: `doctor` and `annotator` (Resident) may search; technician and admin may not.
- Desktop only (the workspace already requires ≥1280 px).

## Approaches

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Keyword search + synonym list, negation rule, ranked, matching sentence highlighted | No new software, explainable, works on RDS; misses phrasings not in the list |
| B | Embedding search (small multilingual model + pgvector) | Catches paraphrase and VI↔EN; new dependency, ~0.5 GB RAM on a 2 GB server, little visible gain on ~25 studies |
| C | B + AI-written summary (full RAG) | Needs the GPU (quota pending); can state things the records don't say |

The user picked A now; B may follow.

## What is searched

- **Own patient:** earlier `history_reports` text **and** this patient's earlier **final** Thorax readings (findings items that
  were included + report findings/impression). Today the "Prior reports" rail shows only `history_reports`, so a report signed
  in Thorax never shows up for the next study — this closes that gap.
- **Other patients ("cases"):** only **final** readings (signed by a doctor). Preliminary, in-progress and draft readings are
  never cases. One case per study (its latest final report).

## Matching

- Normalise text and query: lower-case, strip Vietnamese diacritics, collapse spaces.
- The query is mapped to **concepts** from the synonym list (about 15: pleural effusion, pneumothorax, consolidation/pneumonia,
  cardiomegaly, atelectasis, pulmonary oedema, nodule/mass, rib fracture, emphysema, fibrosis, pleural thickening, hilar
  enlargement, interstitial pattern, tube/line, no acute abnormality). A query that matches no concept falls back to its own words.
- A sentence matches a concept when it contains any of its terms **and** no negation cue ("no", "without", "negative for",
  "resolved", "không", "không có", "không thấy") appears earlier in the same sentence.
- Rank: number of query concepts matched, then most recent first. Return the top 20, each with the matching sentence and
  the matched span(s) for highlighting.
- Done in Python over the fetched rows (data is small). `ponytail:` fine up to a few thousand final reports; beyond that,
  move to PostgreSQL full-text or approach B.

## API

- `GET /api/v1/studies/{study_id}/case-search?q=…` (doctor, annotator; `q` 0–500 chars, so the Similar cases tab can send
  the current findings text)
  - The study is the one being read; "prior" means before its date. The actor must be assigned to its patient (same 404 rule as today).
  - Empty `q`: `prior` lists all earlier studies of this patient, `cases` is empty (the left rail uses this).
  - Concepts are detected in `q` with the same negation rule, so "No pneumothorax" in the findings does not search for pneumothorax.
  - One case per study: its latest final report.
  - Returns `{ concepts: [...], prior: [...], cases: [...] }`.
    - `prior`: this patient's matches — `{ source: 'history'|'reading', date, snippet, spans, study_id? }`.
    - `cases`: other patients' matches — `{ case_id, study_month, age_band, sex, view, findings: [text], impression, snippet, spans }`;
      if that patient is also assigned to the actor, add `patient: { id, code, name }` and the exact date.
- `GET /api/v1/cases/{case_id}/image` (doctor, annotator) — the image of a final reading only (the analysed image, else the
  first PA/AP); 404 for anything else. Each call writes a row to a new `case_views (user_id, session_id, created_at)` table.
- `case_id` is the final reading session id. Builder's call (mine): acceptable, because the endpoint only serves de-identified
  data for final readings.

## Screens (reading workspace)

```
┌ Study context (left rail) ─┐  ┌ X-ray viewer ─────────────┐  ┌ Discussion | Findings | Similar cases ┐
│ PRIOR STUDIES              │  │                           │  │ 🔍 pleural effusion                    │
│ 🔍 search this patient     │  │   (Compare: current │ case)│  │ Effusion · Case · 2026-09 · 60–69 · M  │
│  2026-09 "small left       │  │                           │  │  "…small left **effusion**…" [Compare] │
│   effusion"  · Thorax      │  │                           │  │ (de-identified, no name or code)       │
└────────────────────────────┘  └───────────────────────────┘  └────────────────────────────────────────┘
```

- **Left rail "Prior reports"** becomes "Prior studies": lists `prior` (history + earlier final Thorax readings), with a search box.
- **New "Similar cases" tab** beside Discussion / Findings. Pre-filled with the concepts found in the current findings; the
  doctor can type a new search. Each result has **Compare**.
- **Compare** splits the viewer: current image left (unchanged tools), case image right (read-only, zoom/pan only), the case's
  findings and impression beneath it, and a "Close compare" button. Banner: "De-identified case — for comparison only."
- English UI with Vietnamese entries in `lib/i18n` like the rest of the app; the mock API (`playwright.mock.config.ts`) gets
  the two endpoints so e2e runs without the backend.

## Tests

- Backend: synonym + diacritics match; negation excluded; only final readings are cases; de-identified fields only for
  unassigned patients; unassigned `patient_id` → 404; technician/admin → 403; case image of a non-final reading → 404;
  `case_views` row written; runs on PostgreSQL (`THORAX_TEST_DATABASE_URL`).
- e2e: doctor searches "effusion" in a reading, sees a prior and a case, opens Compare, closes it.

## Left to the builder

File layout (suggest `src/thorax_api/case_search.py` for synonyms, normalising and matching), exact synonym terms, the
compare layout details, and how the read-only viewer is reused from `features/viewer/StudyViewer.tsx`.

## Follow-up (separate small change)

Urgent studies more visible: red border and "URGENT" label on the worklist row and the workspace header. Short design to follow.
