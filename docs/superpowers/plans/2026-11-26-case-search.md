# Case Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the reading workspace, a doctor or resident searches this patient's earlier studies and other patients'
de-identified signed cases by disease (EN/VI synonyms, negation-aware), and compares a case's X-ray side by side.

**Architecture:** One new backend module `src/thorax_api/case_search.py` (synonyms, text folding, matching, a `CaseIndex`
store and its two routes) registered from `app.py`. Search runs in Python over rows fetched with plain SQL (SQLite and
PostgreSQL). The web adds a `features/cases/` folder (Prior studies rail section, Similar cases tab, compare pane reusing
`StudyViewer` read-only) wired into `StudyWorkspace.tsx`.

**Tech Stack:** FastAPI + `thorax_api.db.connect` (SQLite/psycopg), Next.js 15 + TanStack Query, Playwright, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-case-search-design.md`

## Global Constraints

- No new dependencies (Python or npm).
- SQL must run on SQLite and PostgreSQL: `?` placeholders, no SQLite-only functions; JSON columns are parsed in Python.
- Roles allowed: `doctor`, `annotator`. `technician`/`admin` → 403 (via `actor(request, "doctor", "annotator")`).
- Unassigned or nonexistent study → 404 `missing()` (same as today; never reveal existence).
- Other patients' cases: no name, code, exact date or study/patient id; only `case_id`, `study_month` (YYYY-MM), `age_band`
  ("60–69", en dash), `sex`, `image` metadata, findings, impression, snippet, spans.
- Only final reports are cases; one case per study (its latest final report by `reports.id`).
- `q` is 0–500 characters after trim; more → 400 `invalid("Search must be at most 500 characters.")`.
- Every successful `GET /api/v1/cases/{case_id}/image` writes one `case_views` row.
- UI copy in English with Vietnamese entries added to `lib/i18n-vi.ts`.
- Commit to branch `vietanh` only; no push or deploy in this plan.

## Review Focus

1. Vietnamese text with diacritics: highlight spans must land on the right characters of the *original* snippet → `test_fold_keeps_length_and_matches_vietnamese` (Task 1).
2. "No change in the left effusion" is still an effusion → `test_no_change_is_not_negation` (Task 1).
3. A study read twice (resident then doctor over-read, or two doctors) → one case, the latest final report → `test_one_case_per_study_latest_final` (Task 2).
4. A patient assigned to the searching doctor shows up identified, an unassigned one de-identified, and the current patient never appears in `cases` → `test_cases_identity_follows_assignment` (Task 2).
5. Case image for a draft/preliminary-only study or a made-up id → 404, and nothing is logged → `test_case_image_only_for_final` (Task 2).

---

### Task 1: Matching core (synonyms, folding, negation)

**Files:**
- Create: `src/thorax_api/case_search.py`
- Test: `tests/api/test_case_search.py`

**Interfaces:**
- Produces:
  - `fold(text: str) -> str` — lower-case, Vietnamese diacritics removed (`đ`→`d`), **same length as input** (char by char).
  - `CONCEPTS: dict[str, tuple[str, tuple[str, ...]]]` — `id -> (English label, folded terms)`.
  - `detect(query: str) -> list[str]` — concept ids found un-negated in the query; if none, `["query"]`.
  - `match(text: str, concepts: list[str], query: str) -> dict | None` — `{"concepts": [ids matched], "snippet": str, "spans": [[start, end], ...]}`
    for the first sentence with a match (snippet = original sentence text, spans relative to it), else `None`.
    The pseudo-concept `"query"` matches the folded query phrase itself.

Concept ids and the terms tests pin (builder adds more synonyms per concept; all terms stored folded):
`pleural_effusion` ("pleural effusion", "effusion", "tran dich mang phoi", "tran dich"), `pneumothorax` ("pneumothorax", "tran khi"),
`consolidation` ("consolidation", "opacity", "pneumonia", "dong dac", "viem phoi"), `cardiomegaly` ("cardiomegaly", "bong tim to"),
`atelectasis` ("atelectasis", "xep phoi"), `pulmonary_edema` ("pulmonary edema", "pulmonary oedema", "phu phoi"),
`nodule_mass` ("nodule", "mass", "khoi u"), `fracture` ("fracture", "gay xuong"), `emphysema` ("emphysema", "khi phe thung"),
`fibrosis` ("fibrosis", "xo phoi"), `pleural_thickening` ("pleural thickening", "day mang phoi"),
`hilar_enlargement` ("hilar enlargement", "ron phoi to"), `interstitial` ("interstitial", "reticular", "linear markings"),
`support_device` ("tube", "catheter", "pacemaker"), `normal` ("no acute", "normal", "binh thuong").
Do **not** use the folded "nốt" (= "not") as a term.

Rules: sentences split on `.`, `;`, newline. Terms match on word boundaries in the folded sentence. A match is negated when a
cue (`no`, `without`, `negative for`, `resolved`, `khong`, `khong co`, `khong thay`) occurs earlier in the same sentence,
unless that cue is part of `no change` / `no interval change` / `khong thay doi`. The `normal` concept is never negated by
its own leading "no".

- [ ] **Step 1: Write the failing tests** in `tests/api/test_case_search.py` (unittest style like `test_reading.py`, `sys.path` insert of `src`):

```python
class MatchingTests(unittest.TestCase):
    def test_fold_keeps_length_and_matches_vietnamese(self):
        from thorax_api.case_search import fold, detect, match
        text = "Tràn dịch màng phổi trái lượng ít."
        self.assertEqual(len(fold(text)), len(text))
        self.assertEqual(detect("tran dich"), ["pleural_effusion"])
        hit = match(text, ["pleural_effusion"], "tran dich")
        start, end = hit["spans"][0]
        self.assertEqual(hit["snippet"][start:end].lower(), "tràn dịch màng phổi")

    def test_synonyms_cross_languages(self):
        from thorax_api.case_search import detect, match
        self.assertEqual(detect("pleural effusion"), ["pleural_effusion"])
        self.assertIsNotNone(match("Small left effusion.", detect("tràn dịch màng phổi"), "tràn dịch màng phổi"))

    def test_negated_mention_does_not_match(self):
        from thorax_api.case_search import match
        for text in ("No pleural effusion.", "Không tràn dịch màng phổi.", "Lungs clear without effusion."):
            self.assertIsNone(match(text, ["pleural_effusion"], "effusion"), text)

    def test_no_change_is_not_negation(self):
        from thorax_api.case_search import match
        self.assertIsNotNone(match("No change in the small left effusion.", ["pleural_effusion"], "effusion"))

    def test_query_negation_and_fallback(self):
        from thorax_api.case_search import detect
        self.assertEqual(detect("No pneumothorax. Small right effusion."), ["pleural_effusion"])
        self.assertEqual(detect("bibasilar"), ["query"])
        from thorax_api.case_search import match
        self.assertIsNotNone(match("Mild bibasilar linear markings.", ["query"], "bibasilar"))

    def test_snippet_is_the_matching_sentence(self):
        from thorax_api.case_search import match
        hit = match("Heart size normal. Small left effusion; no pneumothorax.", ["pleural_effusion"], "effusion")
        self.assertEqual((hit["snippet"], hit["concepts"]), ("Small left effusion", ["pleural_effusion"]))
```

- [ ] **Step 2: Run to verify they fail**

Run (repo root): `python -m pytest tests/api/test_case_search.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'thorax_api.case_search'`.

- [ ] **Step 3: Implement `fold`, `CONCEPTS`, `detect`, `match`** in `src/thorax_api/case_search.py` (stdlib `unicodedata`, `re` only). `fold` maps each character through NFD, drops combining marks, keeps the first base char, `đ/Đ`→`d`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/api/test_case_search.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/thorax_api/case_search.py tests/api/test_case_search.py
git commit -m "feat(api): case search matching with EN/VI synonyms and negation"
```

---

### Task 2: CaseIndex store and the two routes

**Files:**
- Modify: `src/thorax_api/case_search.py` (add `CaseIndex`, `register_case_search`)
- Modify: `src/thorax_api/app.py` (call `register_case_search` right after `register_reading(...)`, ~line 503)
- Modify: `src/thorax_api/README.md` (endpoint list: the two new routes, one line each)
- Test: `tests/api/test_case_search.py`

**Interfaces:**
- Consumes: Task 1 `detect`, `match`, `fold`.
- Produces (HTTP, used by Tasks 3–6):
  - `GET /api/v1/studies/{study_id}/case-search?q=` →
    `{"concepts": [{"id": str, "label": str}], "prior": [Prior], "cases": [Case]}`
    - `Prior = {"source": "history"|"reading", "date": "YYYY-MM-DD", "text": str, "snippet": str|None, "spans": [[int,int]], "study_id": int|None}`
      — all earlier history reports and earlier-dated final readings of this patient, newest first. `text` is the full
      report (history text, or for a reading: included findings lines + "Impression: …"). With empty `q`, all priors,
      `snippet` None. With `q`, only matching priors.
    - `Case = {"case_id": int, "study_month": str, "age_band": str|None, "sex": str|None, "image": {"view", "width", "height", "media_type"},
      "findings": [str], "impression": str, "snippet": str, "spans": [[int,int]], "concepts": [str],
      "patient": {"id","code","name"}|absent, "study_date": str|absent}` — `patient`/`study_date` only when the actor is assigned to that patient.
      Top 20, ranked by number of matched concepts desc then study date desc. Empty `q` → `[]`. Current patient never included.
  - `GET /api/v1/cases/{case_id}/image` → image bytes (`no_store`), 404 unless `case_id` is a reading session with a final
    report; writes `case_views(user_id, session_id, created_at)`.
  - `CaseIndex(path)`: `search(actor_id: int, study: dict, q: str) -> dict`, `case_image(session_id: int) -> dict | None`
    (an image row as `catalog.image()` returns it), `record_view(user_id: int, session_id: int) -> None`.
  - `register_case_search(app, *, index: CaseIndex, catalog, actor, path_id, no_store, invalid, missing, error) -> None`.

Data rules: a reading's searchable text = text of `findings_versions` items where `included(item)` (from `thorax_api.reading`)
in the session's latest `approved` version, plus the latest final report's `findings` and `impression` (`reports.content` JSON).
Case image = the session's `analyzed_image_id`, else its study's first PA/AP image by id, else first image. `age_band` =
decade of `study_year - birth_year` (None if birth year missing). Access for the search route: `catalog.study(id, user.id)`
exists and `catalog.is_assigned(user.id, study["patient_id"])`, else `missing()`.

- [ ] **Step 1: Write the failing tests** — class `CaseIndexTests(ReadingBase)` reusing `tests.api.test_reading.ReadingBase`
  (doctor 1 is assigned patients 1–10, doctor 2 patients 1, 11, 12; seeded studies: patient p has current study `2400+10p+1`
  and, except p=2, prior `2400+10p` dated 2026-08-18). Add a helper `final_reading(self, doctor_id, study_id, items, impression, *, report_id=None)`
  that inserts directly via `thorax_api.db.connect(self.db)`: a `reading_sessions` row (status `final`), an approved
  `findings_versions` row with `items` JSON (`{"id","text","location":None,"image_id","source":"doctor","action":"accepted","box_ids":[]}`),
  and a `reports` row (status `final`, content JSON with `comparison/findings/impression/recommendation`). Use the real column
  lists from `reading.py` SCHEMA (including later-added columns such as `handoff`/`discrepancy`, which have defaults).

```python
    def test_prior_lists_history_and_earlier_final_readings(self):
        self.final_reading(1, 2410, ["Small left pleural effusion."], "Left effusion.")
        body = self.doctor.get("/api/v1/studies/2411/case-search").json()
        self.assertEqual(body["cases"], [])
        self.assertEqual([p["source"] for p in body["prior"]][:1], ["history"])  # history 2026-10-02 is newest
        self.assertIn("reading", [p["source"] for p in body["prior"]])
        found = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "tràn dịch"}).json()
        self.assertEqual([(p["source"], p["study_id"]) for p in found["prior"]], [("reading", 2410)])

    def test_cases_identity_follows_assignment(self):
        self.final_reading(1, 2431, ["Moderate right pleural effusion."], "Right effusion.")   # patient 3: doctor 1 only
        self.final_reading(2, 2511, ["Small left effusion."], "Left effusion.")                # patient 11: doctor 2 only
        one = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertEqual(len(one), 2)
        identified = [c for c in one if "patient" in c]
        hidden = [c for c in one if "patient" not in c]
        self.assertEqual((identified[0]["patient"]["code"], len(hidden)), ("PX-1044", 1))
        self.assertTrue(set(hidden[0]) .isdisjoint({"patient", "study_date", "study_id", "patient_id"}))
        # patient 11 = Sofia Reyes, born 1969, study 2511 dated 2026-11-03
        self.assertEqual((hidden[0]["study_month"], hidden[0]["age_band"], hidden[0]["sex"]), ("2026-11", "50–59", "Female"))
        own = self.doctor.get("/api/v1/studies/2431/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertNotIn("PX-1044", [c.get("patient", {}).get("code") for c in own])  # current patient never a case

    def test_one_case_per_study_latest_final(self):
        self.final_reading(1, 2431, ["Small right effusion."], "Old impression.")
        self.final_reading(2, 2431, ["Small right effusion."], "Newest impression.")
        cases = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertEqual([c["impression"] for c in cases], ["Newest impression."])

    def test_negated_and_unsigned_are_not_cases(self):
        self.final_reading(1, 2431, ["No pleural effusion."], "Normal chest.")
        self.open(2441)  # an in-progress reading is never a case
        self.assertEqual(self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"], [])

    def test_case_image_only_for_final(self):
        sid = self.final_reading(1, 2431, ["Small right effusion."], "Right effusion.")
        draft = self.open(2441)["session"]["id"]
        self.assertEqual(self.doctor.get(f"/api/v1/cases/{draft}/image").status_code, 404)
        self.assertEqual(self.doctor.get("/api/v1/cases/999999/image").status_code, 404)
        image = self.doctor.get(f"/api/v1/cases/{sid}/image")
        self.assertEqual((image.status_code, image.headers["content-type"]), (200, "image/png"))
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM case_views").fetchone()[0], 1)

    def test_access_rules(self):
        admin = self.client("admin@example.test")
        self.assertEqual(admin.get("/api/v1/studies/2411/case-search").status_code, 403)
        self.assertEqual(self.doctor.get("/api/v1/studies/2511/case-search").status_code, 404)  # patient 11 not assigned
        self.assertEqual(self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "x" * 501}).status_code, 400)
```

`final_reading` returns the new session id; it inserts rows with explicit ids from a counter starting at 90000 so they
never collide with ids the app allocates (`id_sequence`). Add `from thorax_api.db import connect` to the test imports.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/api/test_case_search.py -q`
Expected: Task 1's 6 pass; the new ones FAIL with 404 on `/case-search` routes.

- [ ] **Step 3: Implement `CaseIndex` and `register_case_search`**, create `case_views` with `CREATE TABLE IF NOT EXISTS`
  in `CaseIndex.__init__` (same pattern as other stores' SCHEMA), and register in `app.py` with `index=CaseIndex(db_path)`.
  `ponytail:` comment on the in-Python scan: fine up to a few thousand final reports; move to PostgreSQL full-text or embeddings beyond that.

- [ ] **Step 4: Run backend tests**

Run: `python -m pytest tests/api -q`
Expected: all pass (new 12 included). If Docker Desktop is running, also
`THORAX_TEST_DATABASE_URL=postgresql://thorax:thorax@127.0.0.1:55432/thorax python -m pytest tests/api/test_case_search.py -q` → 12 passed;
otherwise ledger that PostgreSQL was not run.

- [ ] **Step 5: Commit**

```bash
git add src/thorax_api/case_search.py src/thorax_api/app.py src/thorax_api/README.md tests/api/test_case_search.py
git commit -m "feat(api): prior studies and de-identified similar cases search, case image with view log"
```

---

### Task 3: Web contracts, API client, mock, i18n

**Files:**
- Modify: `src/thorax-web/lib/contracts.ts` (types)
- Modify: `src/thorax-web/lib/api.ts` (two methods)
- Modify: `src/thorax-web/app/api/v1/[...path]/route.ts` (mock: both routes)
- Modify: `src/thorax-web/lib/i18n-vi.ts` (all new UI strings from Tasks 4–5)

**Interfaces:**
- Consumes: Task 2 HTTP shapes.
- Produces: `CaseConcept`, `PriorMatch`, `CaseMatch`, `CaseSearchDTO` in `contracts.ts` mirroring Task 2;
  `api.caseSearch(studyId: number, q: string, signal?: AbortSignal): Promise<CaseSearchDTO>`;
  `api.caseImage(caseId: number, signal: AbortSignal): Promise<Blob>` (same mechanics as `api.image`).

- [ ] **Step 1:** Add the types and methods. Mock: `studies/{id}/case-search` returns this patient's mock history as `prior`
  (source `history`, snippet null) for empty `q`, and `{ concepts: [], prior: [], cases: [] }` otherwise; `cases/{id}/image`
  returns 404. `ponytail:` comment — the mock has no signed cases; search is exercised against the real backend.
- [ ] **Step 2: Typecheck**

Run (in `src/thorax-web`): `node node_modules/typescript/bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit** — `feat(thorax-web): case search contracts, client and mock`.

---

### Task 4: Prior studies in the left rail

**Files:**
- Create: `src/thorax-web/features/cases/PriorStudies.tsx`, `src/thorax-web/features/cases/cases.css`
- Modify: `src/thorax-web/features/workspace/StudyWorkspace.tsx` (replace the "Prior reports" section, ~line 299, with `<PriorStudies actor={…} studyId={data.study.id} />`)

**Interfaces:**
- Consumes: `api.caseSearch`, `PriorMatch`.
- Produces: `PriorStudies({ actor, studyId }: { actor: number; studyId: number })`;
  `Highlight({ text, spans }: { text: string; spans: [number, number][] })` exported from `features/cases/Highlight.tsx` (used by Task 5).

Section title "Prior studies"; search input (label "Search this patient's prior studies", `useDeferredValue`, query key
`['actor', actor, 'case-search', studyId, term]`). Each item: date, source tag ("Thorax report" / "Imported report"),
expandable full `text` (reuse the existing `history-list` details/summary markup and classes), and when searching the
`snippet` with `<mark>` highlights. Empty states: "No earlier studies." / "No earlier study mentions “{term}”."
Keep the existing "Historical report — not current-image evidence" label.

- [ ] **Step 1:** Implement; typecheck (`tsc --noEmit`, expect no errors).
- [ ] **Step 2: Commit** — `feat(thorax-web): prior studies with search in the reading rail`.

---

### Task 5: Similar cases tab and side-by-side compare

**Files:**
- Create: `src/thorax-web/features/cases/SimilarCases.tsx`, `src/thorax-web/features/cases/CaseCompare.tsx`
- Modify: `src/thorax-web/features/workspace/StudyWorkspace.tsx` (`discussionTabs` gains `{ id: 'cases', label: 'Similar cases', icon: Images }`; `compare` state; viewer area split when comparing)
- Modify: `src/thorax-web/features/cases/cases.css`

**Interfaces:**
- Consumes: `api.caseSearch`, `api.caseImage`, `CaseMatch`, `Highlight`, `StudyViewer`.
- Produces: `SimilarCases({ actor, studyId, seed, onCompare }: { actor: number; studyId: number; seed: string; onCompare: (c: CaseMatch) => void })`;
  `CaseCompare({ match, onClose }: { match: CaseMatch; onClose: () => void })`.

`seed` = text of included items of `data.latest_findings` joined with ". ", cut to 500 chars; the search box starts with
it and shows the detected concepts as chips ("Searching for: Pleural effusion"). Each case row: concept labels, month · age
band · sex (or patient name + code + date when present), highlighted snippet, **Compare** button. Under the list: "Only
signed reports are shown. Other patients are de-identified."
Compare: viewer area becomes two columns — left the existing `StudyViewer` unchanged, right `CaseCompare` rendering
`StudyViewer` with `images=[{ id: match.case_id, study_id: 0, ...match.image, sha256: '' , content_url: '' }]`, `readOnly`,
`boxes=[]`, `canAnnotate={false}`, `canAskRegion={false}`, no-op handlers, `loadImage={api.caseImage}`; above it the banner
"De-identified case — for comparison only" and a "Close compare" button; below it the case findings and impression.
If `StudyViewer` cannot take those props cleanly, fall back to an `<img>` from the blob with CSS zoom buttons, and ledger the ruling.

- [ ] **Step 1:** Implement; typecheck (`tsc --noEmit`, expect no errors).
- [ ] **Step 2: Commit** — `feat(thorax-web): similar cases tab with side-by-side compare`.

---

### Task 6: End-to-end test against the real backend

**Files:**
- Modify: `src/thorax-web/e2e/app.spec.ts`

- [ ] **Step 1: Write the test** `doctor finds a signed similar case and compares it side by side`:
  with Playwright's `request` context signed in as the demo doctor, drive study 2431 to final through the API exactly as
  `tests/api/test_reading.py::ReadingTests` lines 96–127 do (open session, analyze PA, accept box + items, approve findings,
  draft_report, approve report; mock AI gives "Possible mild left basilar opacity."). Then in the UI open patient 1's study
  2411, open the **Similar cases** tab, search `opacity`, expect a result row containing "PX-1044" and "basilar opacity"
  (patient 3 is assigned to the demo doctor; other tests may add more rows, so don't assert a count; if another e2e test
  already reads 2431, use another study of patients 3–10 that no test touches), click **Compare** on that row, expect two viewer images and the banner, click
  **Close compare**, expect one. Also expect the rail "Prior studies" heading to be visible.
- [ ] **Step 2: Run**

Run (PowerShell, `src/thorax-web`): `node node_modules\@playwright\test\cli.js test e2e/app.spec.ts`
Expected: all pass (new test included). Also `node node_modules\@playwright\test\cli.js test -c playwright.mock.config.ts` → all pass.

- [ ] **Step 3: Commit** — `test(thorax-web): e2e for similar cases and compare`.
