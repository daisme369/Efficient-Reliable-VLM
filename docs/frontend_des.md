# Thorax AI — Frontend Architecture and Interaction Specification

Version: 1.0 · 6 November 2026 · Documentation only

This document defines the desktop frontend to be created for the academic Thorax AI MVP. Read [spec.md](spec.md) for authoritative product rules and [implementation_plan.md](implementation_plan.md) for API schemas, database constraints, diagrams, and executable tasks. The related `D:/Efficient-Reliable-VLM` repository contains research software, not this patient workspace; every application path below is **TO CREATE** in the future application repository. No application functionality is asserted to exist.

## 1. Design basis and scope

The supplied 17-page MVP specification establishes English UI, desktop browsers at least 1280 CSS pixels wide, assigned patients, persistent study chat, doctor-drawn regions, two approval gates, and an immutable final session. The accessible “Xây dựng Feature List” discussion establishes patient isolation and the distinction between historical report context and historical image comparison. Its five user turns were accessible; one early assistant response was truncated by the source tool, so missing text is not treated as an approved requirement. The source inventory and decision log in the companion documents record the original five-page project description and older research architecture.

The current user's clarifications supersede the PDF's exclusion of automatic boxes: the initial workflow includes boxes detected from the image by a CV inference component (FR-009). Their provenance remains visible and doctors review them. Training and fine-tuning are separate from this software stage. The UI supports supplied model artifacts through the backend and the identical mock contract; it contains no training, model selection, retraining, or model deployment workflow.

The `ui-ux-pro-max` skill was applied through its design-system and React searches. Adopt its accessible interaction, contrast, visible focus, stable layout, and reduced-motion principles. Discard its generated marketing-page pattern because this product is a clinical workspace. Preserve PNG/JPG source pixels in the viewer; do not apply the skill's generic image compression advice to medical assets. Supported desktop layout takes precedence over generic mobile checklist recommendations.

### 1.1 Frontend obligations

| ID | Required behavior |
| --- | --- |
| FR-001 | Authenticate; route doctor and admin separately; expose account, assignment and allowlisted mock-fixture management to admin. |
| FR-002 | List only assigned patients and studies; distinguish aggregate study status from **My reading** progress. |
| FR-003 | Display immutable PNG/JPG assets with zoom, pan, brightness, contrast and reset. |
| FR-004 | Submit one successful initial analysis per session using one selected current image; show the exact historical report sources supplied by the backend. |
| FR-005 | Show saved study chat and send current-image questions; preserve failed submission recovery without inventing an AI reply. |
| FR-006 | Create, select, move, resize, delete and numerically edit boxes; ask about a saved cropped region. |
| FR-007 | Review all pending findings and AI boxes; save corrections; pass Gate 1 explicitly. |
| FR-008 | Draft only from the current approved findings version; edit, save, detect staleness, and explicitly pass Gate 2. |
| FR-009 | Distinguish CV suggestions from doctor boxes; show only genuine finding-to-box associations. |
| FR-010 | Present source and approval metadata and an own-session audit summary; no audit mutation controls. |
| FR-011 | Display the configured mock or real mode consistently; give the same workflow and errors in either mode. |

Manual prior-image comparison (FR-012), answer ratings (FR-013), a separate session index (FR-014) and final PDF export (FR-015) are SHOULD work after the complete MUST flow. Autoarchive (FR-016), training-data export (FR-017), hospital integration, segmentation and longitudinal AI (FR-018) have no active MVP navigation, placeholder buttons or dependencies.

## 2. Application architecture and information architecture

Retain the PDF's Next.js 15, React, TypeScript and Tailwind stack. TASK-001 selects patched package versions and compatible lockfiles. Use the App Router for route boundaries, TanStack Query for authenticated remote state, and React component state/reducers for viewer gestures and unsaved forms. Interactive browser components form explicit client boundaries, consistent with the [Next.js client-component reference](https://nextjs.org/docs/app/api-reference/directives/use-client). This is a proposed implementation structure, not an inventory of existing code.

The browser calls only the same-origin `/api/v1` backend. It never calls the inference service, database or storage path. Authenticated clinical pages and image responses must be private and `no-store`; no service worker, static generation, CDN cache or browser persistence of clinical content is enabled. Route metadata uses generic titles such as “Thorax AI — Study workspace,” without patient names or codes.

### 2.1 Routes and navigation

| Screen | Route | Access and hierarchy |
| --- | --- | --- |
| UI-001 Login | `/login` | Public form. Authenticated doctor redirects to `/worklist`; admin to `/admin`. |
| UI-002 Doctor worklist | `/worklist` | Active doctor only. Patient list → expandable study list → own workspace. |
| UI-003 Study workspace | `/patients/[patient_id]/studies/[study_id]` | Active doctor with current patient assignment and own session. One page contains context, viewer, AI, chat, boxes, findings and draft report. |
| UI-004 Final report | `/patients/[patient_id]/studies/[study_id]/report` | Same doctor/assignment/own-session policy; final report only. Link back to the same read-only workspace. |
| UI-005 Admin | `/admin` | Active admin only; tabs `Doctors`, `Assignments`, `Mock fixtures`. No patient workspace links. |

Workspace tab selection may use `?panel=analysis|chat|findings|report` and selected image `?image_id=...`; these are UI state and resource references, never authorization. Validate each against the loaded workspace before use. Session ID comes from the authorized open-session response; patient/study identifiers are never editable in a form. A mismatching route patient and returned study patient renders the generic unavailable screen and clears the previous workspace.

Doctor navigation contains Worklist, the current study breadcrumb, and Sign out. Admin navigation contains only Admin and Sign out. There is no global patient search from chat, no free-floating chat creation and no account switching without logout. Browser Back/Forward follows the same dirty-form, context-reset and authorization checks as clicking a study link.

### 2.2 Layout at supported sizes

UI-003 uses a 64 px application header, a 72 px persistent patient/study strip and a remaining-height workspace. At 1440 px or wider, use a 240 px context rail, an image region taking the flexible remainder, and a 440 px review panel, with 12 px gaps. At 1280–1439 px, collapse the context rail into a labelled drawer and use `minmax(560px,1fr) 420px` for viewer and review. The drawer must remain keyboard accessible and must not cover approval buttons while open. A secondary horizontal image strip sits under the viewer if the study contains more than one image.

```text
Application header: Thorax AI | Worklist                         User | Sign out
Patient/study strip: mock identity | study date/view | My reading | demo/mode
Context rail         Image toolbar + original image       Review panel tabs
  Study metadata     Canvas image + SVG regions           Analysis / Chat
  Prior reports      Region list / numeric editor        Findings / Report
  Source dates       Image thumbnails                     Current gate status
                                                           Save / Approve
```

All regions have independent vertical scrolling with visible headings. The patient/study strip stays visible while reading chat, findings or report. Approval controls stay within their panel, after the content, with reserved bottom space; they must not cover the last line. Do not make the whole page a dense grid of tiny cards. Reflow at browser zoom rather than clipping controls. Below 1280 CSS pixels show “Thorax AI is designed for a desktop window at least 1280 px wide” plus Worklist and Sign out; do not present truncated approval controls. Mobile and tablet workflows are outside this delivery.

## 3. Screen specifications

### 3.1 UI-001 — Login

**Permission and purpose.** Public; obtain a secure application session for an existing doctor or admin. No registration, recovery email, role picker or demo credentials embedded in the page.

**Layout and components.** A centred 440 px form with product name, “Academic demonstration — mock data,” labelled email and password fields, a show/hide password button and Sign in. Use an inline error summary linked to fields. Password managers and paste are allowed. Email uses autocomplete `username`, password `current-password`.

**Data and state.** Local state contains email, password, visibility and submitting state. AuthContext contains only the authenticated user's ID, display name, role, idle expiry and CSRF token after `GET /api/v1/auth/me`. The opaque httpOnly cookie is inaccessible to JavaScript. Clear password state after a terminal response and on unmount.

**Flow and API.** Submit `POST /auth/login`, then load `/auth/me` and route by the returned role. Trim outer email whitespace; preserve the password exactly. Do not derive role from email. All nonempty email/password failures use the same visible text, “Email or password is incorrect.” Disabled account behavior uses that same login failure. `429` shows the server retry delay and disables resubmission until it expires. A network failure shows “Could not reach the server. Try again.”

**Loading/empty/error/disabled states.** Sign in is disabled while submitting or required fields are empty. Keep the form height stable, replace button text with “Signing in…,” and announce failure through an alert. If the initial auth check is pending, render a neutral shell instead of flashing protected routes. Successful login clears the old actor's query cache before protected data is loaded.

**Acceptance.** TST-001/TST-013: Enter submits once; wrong email and wrong password produce indistinguishable errors; doctor/admin land on their own routes; direct navigation cannot render another role's screen. TASK-005 owns this screen.

### 3.2 UI-002 — Assigned worklist

**Permission and purpose.** Active doctor; find an assigned patient and open a study. Assignment and account status are checked by the API on every request.

**Layout and components.** Page title “Assigned patients,” labelled search, patient list/table and expandable study rows. Patient columns: patient code, name, birth year, sex and study count. Study rows show date, indication, image count, aggregate Study status and separate My reading status. The primary action says Open, Continue or View final report from the requesting doctor's session state. A study that another doctor finalized remains openable for this doctor's independent reading.

**Data and state.** `GET /patients` with optional `search`, `cursor` and `limit` supplies the authorized patient page; omit `search` when blank and omit an absent cursor. Expanding a row calls `GET /patients/{patient_id}/studies`. Search text, expanded patient ID and pagination cursor are local. Search is debounced 300 ms, submitted on Enter immediately and kept out of the URL to avoid placing names/codes in browser history. Cancel obsolete search requests. A query key includes doctor ID and search; list page size is 25, with “Load more” and a disabled state while fetching. No clinical data is retained through logout.

**Interaction and navigation.** Clicking a study navigates to UI-003. Row expansion has its own button and does not open a study. Search clear returns to the first page. Deliberate open/search/navigation calls carry `X-User-Activity: 1`; automatic background refetch does not.

**Loading/empty/error states.** Initial loading uses stable table skeletons. No assignments: “No patients are assigned to you.” Zero search results: “No assigned patients match this search,” with Clear search. No studies: “No studies are available for this patient.” A list failure has Retry and does not render stale rows under a changed actor or patient. A forbidden/unassigned study shows generic “This study is unavailable” and Return to worklist; do not explain that it belongs to another doctor.

**Read-only/disabled behavior.** Final sessions offer View final report, while the same final study's image remains viewable. Double clicks do not create duplicate sessions. Admin cannot enter this screen; no clinical metadata is requested in the admin shell.

**Acceptance.** TST-002/TST-013/TST-018: only assigned patients appear; changing a URL cannot expose an unassigned image; search cannot leak other doctors' results; own progress stays independent of aggregate study status. TASK-007 and TASK-008 own this flow.

### 3.3 UI-003 — Patient/study workspace shell

**Permission and purpose.** Assigned doctor and own session; coordinate the entire reading workflow without losing the current identity. The path never binds a session by itself.

**Entry data sequence.** Read `/studies/{study_id}`, check its patient against the route, then `POST /studies/{study_id}/sessions`. The response is `201` for new and `200` for resume, containing the Workspace aggregate. This natural idempotent open operation returns the same own session for repeated visits. Restore images, history, saved chat, boxes, latest findings, reports and live request. Load the selected asset through `/images/{image_id}/content`; never render `file_path`. While this sequence runs, show a blank labelled workspace skeleton with no previous patient's content.

**Server state.** Workspace owns the fixed doctor/patient/study/session binding, revision/ETag, patient/study metadata, image inventory, `history` containing at most five `{id, report_date, text}` source reports, paginated saved messages, boxes, latest findings and report versions, `live_request_id`, `blockers[]`, capabilities and configured provider mode. The capabilities `can_detect` and `can_ask_roi` describe configured integration, not clinically validated quality. Server-provided artifact metadata is shown in a collapsible “AI source” section.

**Local state.** Selected panel/image/box, viewer transform and filters, draft text, dirty flags, pending mutation keys and per-panel scroll positions. Local state is scoped by actor+patient+study+session and unmounted on context changes. The viewer can switch among current-study images for manual review; initial Analyze uses exactly one explicit selected image, defaulting PA then AP then LAT, ties by image ID. Show “Initial AI analysis: [view and image number] only” after analysis; changing the displayed image does not change the analyzed image or create another analysis. Any subsequent VQA identifies its own full-image or ROI attachment; it does not turn initial analysis into implicit whole-study coverage.

**Context panel.** Show mock patient identity, indication, study date, image count and My reading. Prior reports are labelled “Historical report — not current-image evidence,” include date and source ID, and expand to server-supplied text. No history displays “No earlier reports; analysis will use the current image.” The heading says “Up to 5 earlier reports,” not semantic search. Same-day and future reports are not offered as AI context. The frontend does not compose model history or choose arbitrary reports.

**Global workspace states.** Render unavailable on `404`; clear and redirect to login on `401`; show a retryable offline banner on network loss and disable all server mutations. Preserve unsaved local text only within the current page while access remains valid. After finalization, show “Final — read-only” persistently, remove composers/handles and retain view filters, source inspection, saved chat and navigation. A session can be `active`, `findings_approved` or `final`; show exact textual progress and never synthesize a fourth persisted state.

**Navigation behavior.** If any form/box edit is dirty, leaving asks “Leave without saving these changes?” with Stay and Discard changes. Completed saved server work never needs resaving. Leaving while a job is live is allowed with “Analysis continues for this study”; cancel client polling and reconnect on reopen, without a server cancel request. No endpoint is invented for cancelling a running job. On assignment revocation or disabled account, discard clinical cache and drafts immediately when detected; permission takes precedence over the unsaved-change prompt.

**Acceptance.** TST-003/TST-013/TST-014/TST-018: identity strip never belongs to a different image/chat; reload restores saved content and live job; late responses for a prior workspace never populate the current one. TASK-008, TASK-009 and TASK-017 own shell behavior.

### 3.4 UI-003 Analysis panel

**Purpose and components.** Show initial analysis status, AI text findings, CV boxes, source metadata, history-source count and recovery actions. The primary Analyze button displays the selected image. Every raw AI text block and CV box detail includes the exact label **“AI suggestion — needs doctor review”** even after a reviewed derivative is approved. Doctor approval badges attach to the reviewed derivative, not retroactively to the model's original output.

**API and flow.** Require all local drafts saved or discarded. Submit `POST /sessions/{session_id}/ai-requests` with `{operation:"analyze", image_id}` and current ETag/idempotency key; persist the returned request ID in memory. Poll the scoped request resource every second while visible. The backend executes VLM analysis and CV detection and publishes their validated result atomically. On success, refetch Workspace, select Findings, and show “Review [n] findings and [m] AI regions.” A valid detector result with zero detections is distinct from a failed detector. Empty text findings do not mean normal; the doctor must explicitly confirm normal or add a finding before Gate 1.

**Loading/error states.** `queued`: “Waiting to analyze…”; `running`: “Analyzing image…” with elapsed time, no fabricated percentage. At 30 seconds show “This is taking longer than the demo target.” At the server's 60-second deadline, show the terminal timeout and Try again. A client timer never publishes timeout or success independently. A request poll network failure says “Connection interrupted; checking the existing request,” with Check status. It must not create another job automatically.

**Disabled/read-only states.** Analyze is unavailable during any live job, in final sessions, or after the first successful initial analysis (`ALREADY_ANALYZED`). Viewing another image does not re-enable it. Missing configured real detector/VLM shows a clear integration error; no silent switch to mock or text-only success. Current mode is a read-only badge, e.g. “Mock AI” or “Real AI,” consistent across all panels. Configuration changes happen outside the clinical UI.

**CV box interaction.** Selecting a detection selects its overlay and opens the box review editor. Text and detection association is shown only when the server supplies a validated link or the doctor explicitly saves a link; no matching by label in the frontend. Unlinked CV detections appear under “AI regions without a linked finding.” A text finding without a box says “No linked region.” Do not add a confidence gauge, risk badge, probability percentage or segmentation overlay.

**Acceptance.** TST-004/TST-009/TST-011/TST-015/TST-017: failure of either required inference component produces a failed job without partial findings/boxes; success shows correct source and mode; no automatic repeat on refresh. TASK-010–TASK-012, TASK-019 and TASK-020 own this panel.

### 3.5 UI-003 VQA/chat panel

**Purpose and components.** A saved chronological transcript, per-message provenance, ROI attachment chip, multiline question field (maximum 2000 characters), Send and request status. Each AI answer retains the required AI-suggestion label. Doctor and AI bubbles have explicit sender names and timestamps; color alone does not identify them. Show “Questions concern this study only.” Chat cannot navigate or retrieve a different patient from a typed instruction.

**Data/API.** Initial messages come from Workspace; older messages use `GET /sessions/{session_id}/messages` with its returned cursor and limit<=50. Loading older messages preserves scroll position. `POST .../ai-requests` uses `operation:"ask"`, current image ID and question; a region question also includes the saved `box_id`. No patient ID, history array or arbitrary previous message array is sent by the frontend. The backend assembles the last ten completed messages preceding the question, selected source history, and only the authorized current image or crop. Each MessageDTO includes `image_id` derived from the linked request, so every doctor/AI message shows a view/image chip, including region questions. Historical context includes opaque per-message image/box references and the current image reference, never previous image bytes; a question about a different image is not presented as a pixel comparison.

**Interaction.** Enter adds a newline; Ctrl/Cmd+Enter sends; the button is always available as a keyboard alternative. Do not send whitespace-only input. During the job show a pending local question/request item, not an invented assistant message. On success replace pending presentation by authoritative persisted messages. Preserve typed question after failure in the current workspace. For a terminal job whose `error.retryable` is true, offer Retry with identical original input, a new idempotency key and `retry_of`; a corrected/shortened question is a new intentional request without `retry_of`. A nonretryable input/schema error offers Edit question or Return to findings rather than replaying the invalid input. This failed item is excluded from completed conversation context. If a response arrives while the user has scrolled up, show “New answer” instead of forcing scroll. Announce answer completion politely once; do not read the entire transcript on every poll.

**ROI context.** “Ask about this box” saves/validates the selected box first, then opens Chat with an attachment chip “Region [id], image [view].” The sent message displays the immutable submitted coordinate snapshot/revision, not live geometry. If the box is changed or deleted later, old messages continue to show “Region as submitted” with its snapshot; they must not highlight an unrelated current box. The crop is made by the backend from unfiltered source pixels.

**Loading/empty/errors.** Empty transcript: “Ask a question about this X-ray or select a region.” Submit/poll state follows section 3.4. `422` displays the field-specific limit; model context overflow displays the backend's typed failure and preserves the question for shortening. Avoid automatically retrying all errors. On lost authorization, clear transcript and attachment, not merely disable Send.

**Disabled/read-only states.** Send is disabled if initial analysis has not succeeded, any server job is live, question is invalid, selected box is dirty/deleted, workspace is offline/unavailable, or session is final. VQA may continue after Gate 1 while the session is not final; a chat answer does not automatically alter findings or their approval. New clinically relevant information must be entered into findings and approved explicitly.

**Acceptance.** TST-005/TST-006/TST-013/TST-015/TST-018: reopen restores transcript; wrong-session box is rejected; only the existing study is used; failed polls do not duplicate questions; previous ROI messages retain their submitted geometry. TASK-013 and TASK-014 own this panel.

### 3.6 UI-003 Findings review and Gate 1

**Purpose/layout.** Present the current findings version, its source image, saved-versus-unsaved status, each finding's source/action, linked boxes, and a pending-review counter. Keep original AI text available in an expandable comparison under the edited derivative; never overwrite `ai_items` in client state. Separate a box review list from textual findings because an unlinked detector box still needs disposition.

**Row interaction.** AI findings begin `pending`; offer Accept, Edit and Reject. Edit opens labelled text/location/link fields, preserves the AI source and produces action `edited` when saved. Add finding creates a doctor-sourced row with action `added` and an explicit current-study image choice. Every saved FindingItem preserves all required fields `{id,text,location,image_id,source,action,box_ids}`; text is1..2000 characters, location at most200, and the set at most50 items. Rejected rows remain visible collapsed, labelled “Excluded,” with Restore to pending before Gate 1. A bulk Accept all control is not included in the MUST UI. Box review uses `pending`, `accepted`, `edited` or `rejected` for AI source; doctor-created boxes use `added`. Changing a box's source label is prohibited. Link selectors list active boxes of the same session and exactly the item's `image_id`, show an explicit “No linked region” option, and reject a box from another image even when both images belong to the same study.

**Save/API/state.** Local row edits are dirty until Save findings completes `PATCH /sessions/{session_id}/findings` with the complete current item list and `no_abnormality_confirmed`. Box edits save separately. Mutations return MutationReceipt; refetch Workspace before changing gate status or enabling approval. The first review Save moves `ai_proposed` to `in_review`; Gate 1 requires that saved state even for an empty initial result. An explicit “No abnormal findings confirmed” checkbox is available only when all model suggestions are resolved and zero included findings remain. If the doctor includes any accepted/edited/added finding, clear the normal flag before Save. Empty model output is not auto-accepted. Do not substitute the normal checkbox for resolving pending suggestions.

**Gate 1 enabled condition.** Current latest findings are saved `in_review`; no AI finding is pending; every active AI box is accepted, edited or rejected; either at least one accepted/edited/added nonempty finding exists with normal=false, or zero included findings exist with normal explicitly confirmed; no local dirty form, live inference, permission error or offline state exists. Use Workspace `blockers[]` as authoritative reasons; derive only supplementary local reasons. Show a visible checklist and count, e.g. “Review 2 findings and 1 AI region,” rather than an unexplained disabled button. An already-approved latest version displays its approval metadata and Generate report instead of another active Gate 1 control.

**Approval interaction.** The button says “Approve findings (Gate 1).” A review dialog identifies the current patient/study and findings version and says “Approve these findings and reviewed regions for report drafting?” Its primary action is Approve findings; Cancel returns focus to the trigger. POST `/sessions/{session_id}/findings/approve` with `findings_version` and concurrency/idempotency headers. Only a confirmed server success displays approver/time, immutable version and session `findings_approved`. The Report panel then offers Generate report. The confirmation is the existing authorized product action, not an added external approval workflow.

**Edits after approval.** Editing a finding or box after Gate 1 first explains “Saving this change creates a new findings version and makes any draft report stale.” Save invokes the ordinary mutation; the backend clones the next version `in_review`, moves session to `active`, and marks drafts `stale` in one transaction. The prior approved version remains intact. Show the new version, reapproval checklist and a stale report banner. No frontend-only unapprove action or deletion of an approved snapshot is allowed.

**Failure/read-only states.** `UNRESOLVED_REVIEW` highlights remaining rows; `REVISION_CONFLICT` opens conflict recovery; `FINAL_READ_ONLY` reloads the final state. Old approved versions are read-only. Final sessions disable all findings and box mutations. A missing initial analysis offers Analyze in the Analysis panel; a valid successful analysis containing zero suggestions still permits doctor additions or explicit normal confirmation.

**Acceptance.** TST-007/TST-009/TST-012/TST-014/TST-018: any pending model item blocks approval; all-rejected content needs normal confirmation or a doctor addition; changing geometry after Gate 1 invalidates report eligibility; duplicate approval never creates a second version. TASK-015 and TASK-017 own this panel.

### 3.7 UI-003 Structured report editor and Gate 2

**Purpose/layout.** A report document with database-filled patient/study header, source findings version, `comparison`, `findings`, `impression`, `recommendation`, saved indicator, AI-source label and approval footer. Patient identity, study identifiers/date/indication, author and approval timestamps are read-only. Initial findings text is assembled by the backend from included approved items; VLM drafting supplies the remaining allowed clinical narrative. Treat all draft narrative as needing doctor review.

**Generation/API.** Before Gate 1, show “Approve findings before drafting a report” and a link to Findings; no enabled Generate button. After Gate 1, POST `.../ai-requests` with `{operation:"draft_report", findings_version}`. The backend supplies only approved findings to the model and fills identity/study fields from the database. On job success refetch Workspace and display the new `draft` row. Show “Source: approved findings v[n]” and the frozen `ReportContent.study.analyzed_image_id` with its view, labelled as single-image initial AI coverage; the study's complete `image_ids` inventory must not be presented as the list of AI-analyzed images. For a normal report, the approved included set and model `source_item_ids` are both empty, with explicit normal confirmation true. Never send raw chat or the displayed image to draft a report from the browser.

**Editing/saving.** Explicit Save report patches only editable text fields using `PATCH /sessions/{session_id}/reports/{report_id}`. No background autosave in MUST delivery. Dirty text remains in memory and shows “Unsaved changes.” `findings` and `impression` must be nonempty on final approval; comparison/recommendation may be empty. Field length limits are taken from the API schema and validated on both sides. If the doctor discovers a new observation while editing prose, offer Return to findings; saving prose does not silently approve a new finding or mutate Gate 1.

**Stale state.** A `stale` report is read-only and carries “Stale — findings have changed.” Retain the text for comparison but do not allow Save or Approve. Offer Review findings, then after the new Gate 1 approval offer Generate new report. Regeneration creates a new report row; older draft rows become stale and are not overwritten. A small report-version selector may expose these already-loaded report rows within the same workspace; it is not the deferred global session-history feature.

**Gate 2 enabled condition.** Current report is saved `draft`; source findings version equals the latest approved version in the same session; session is `findings_approved`; required report text is nonempty; no dirty form, live inference, access error or offline state exists. The backend repeats all checks in the approval transaction. Disabled reason text is explicit and remains visible without hover.

**Approval interaction.** Button: “Approve final report (Gate 2).” Dialog identifies patient/study/source version and says “This makes the report and this reading session read-only.” Buttons are Approve final report and Continue editing. POST `/sessions/{session_id}/reports/{report_id}/approve` with source `findings_version` and current headers. On success refetch; then navigate to UI-004. Do not display finality or navigate optimistically. Losing the response uses the original key to recover the existing approval receipt.

**Errors/loading/read-only.** Drafting uses the shared request states. `GATE1_REQUIRED` returns to Findings, `REPORT_STALE` reloads the stale state, and `REVISION_CONFLICT` follows section 7. Final report and all session contents remain viewable but uneditable. There is no amendment, reset-final, reopen-for-editing or delete-report action in the MVP.

**Acceptance.** TST-008/TST-012/TST-014/TST-018: report cannot be drafted before Gate 1 or finalized after a findings/box change; saving prose does not remove provenance; finalization locks chat, analysis, findings, boxes and report together. TASK-016 and TASK-017 own this panel.

### 3.8 UI-004 — Final report viewer

**Permission and data.** Authorized session owner with current assignment; get/resume own session through the same study access sequence and load its Workspace. A report route must not return another doctor's report even for a shared assigned patient. No final report yet shows “No final report is available for this reading” with Return to workspace.

**Layout/components.** Readable report width 800 px, patient/study header, report status, source approved findings version, structured sections and doctor approval name/time. Keep the academic mock-data banner. Show `content.study.analyzed_image_id` as “Initial AI analysis image” separately from the study image inventory. A secondary link opens the same read-only workspace to inspect the image, approved findings, annotations and persisted chat. A collapsible source section identifies mock/real mode and artifact/version provenance. Raw AI content retains its source label; the final document's explicit doctor approval describes the final authored artifact.

**State and interaction.** Server owns content, status and timestamps; local state contains only expanded sections and scroll. No editor, composer, annotation handles, approval button or browser persistence. `401` and `404` clear clinical content. Network failure offers Reload, without showing a different cached patient's report. PDF export is absent until FR-015 is implemented under TASK-023; do not present an enabled browser Print action as if it were that feature.

**Acceptance.** TST-008/TST-013/TST-018: report/approver/source version match the server; browser Back preserves final read-only controls; direct API mutation attempts still fail. TASK-016 owns this screen.

### 3.9 UI-005 — Admin workspace

**Permission and boundary.** Admin only. This role manages the demo but cannot open images, studies, messages, clinical findings/reports or clinical audit events. The proposed least-privilege patient catalogue contains only internal ID and patient code for assignment, never name or clinical metadata. This explicit resolution of the PDF's management-versus-clinical-access ambiguity is recorded in the decision log.

**Doctors tab.** List via `GET /admin/doctors`; columns display name, email, active state and action. Create form posts `/admin/doctors`; role is fixed doctor, with name1..100 characters, email<=254 and initial password12..72 UTF-8 bytes. Create response is a receipt, not a password echo; clear password and refetch. Enable/disable uses `PATCH /admin/doctors/{doctor_id}` with `{is_active:boolean}` only. Existing identity, password and role are not editable in this MVP. Disable confirmation names the doctor and states that access is revoked. No role escalation, admin creation, hard-delete or unsolicited email control. Session invalidation and assignment checks remain server responsibilities.

**Assignments tab.** Load `/admin/doctors` and `/admin/patient-catalog`. Choose one doctor and an allowlisted patient code; show current assignments from `AdminDoctor.assigned_patient_ids` and `AdminPatient.assigned_doctor_ids`. Grant posts `/admin/assignments`; revoke calls `DELETE /admin/assignments/{doctor_id}/{patient_id}`. Revoke confirmation identifies code and doctor and explains that the doctor loses access; repeated revoke returns204 and is a successful no-op. Multiple doctors may be assigned the same patient. Never link the code to UI-003 or fetch patient demographics to decorate this table.

**Mock fixtures tab.** Select an allowlisted server `fixture_key` from the public nonclinical runtime manifest (initial key `thorax-demo-v1`), inspect its manifest/status through `GET /admin/fixtures/imports/{fixture_key}`, and call `POST /admin/fixtures/imports`. Show fixture key, checksum/status and inserted counts only. No arbitrary file path, database query, archive upload or PHI text area. Import is synchronous and checksum-idempotent; reimport does not duplicate or overwrite assets already referenced by a reading. Canonical assets are prepared by setup before runtime; this UI imports metadata from that allowlisted prepared bundle.

**State, loading and errors.** Query data is admin-scoped; local state is selected tab, selected doctor/patient code, form fields and pending confirmation. Each tab owns a skeleton, empty state and Retry. Mutation disables only the relevant form plus overlapping destructive action. Duplicate email uses field error; missing catalogue item uses unavailable selection; network uncertainty on create/import reuses the original idempotency key. Do not retry a disable/revoke by creating a replacement account/assignment. All successes refetch management data and announce a short confirmation.

**Acceptance.** TST-001/TST-013/TST-019: admin can create a doctor, import allowed fixtures, grant and revoke access; admin cannot obtain any clinical payload by UI or direct URLs; no patient name appears in admin tables. TASK-006 owns this screen.

## 4. Imaging viewer and annotation contract

### 4.1 Rendering and tools

Use a decoded unmodified image on a canvas and an aligned SVG overlay for boxes. Load through the authorized backend content endpoint; preserve aspect ratio. Fit initially without clipping. Canvas backing pixels account for devicePixelRatio, while pointer calculations use CSS pixels. Source width/height and orientation come from the image metadata, not browser thumbnail size. Render brightness and contrast only in the image layer; overlay colors and label legibility are unchanged.

| Tool | Behavior and local state | Server effect |
| --- | --- | --- |
| Fit/Reset | Fit image; pan `(0,0)`; zoom `1`; brightness `1`; contrast `1`; selected tool Select. | None. |
| Zoom | Toolbar minus/plus and wheel while pointer is over viewer; zoom anchored to pointer. Relative zoom range `0.25`–`8` times fit, step `1.2`; display percentage relative to original pixels. | None. |
| Pan | Dedicated Pan tool and pointer drag; keyboard arrow controls when viewer is focused. Do not capture page keys while text fields are focused. | None. |
| Brightness | Labelled slider `0.5`–`2`, step `0.05`, default `1`; numeric value visible. | None. |
| Contrast | Labelled slider `0.5`–`2`, step `0.05`, default `1`; numeric value visible. | None. |
| Draw box | Disabled until initial analysis succeeds. Then pointer-down inside source extent, drag, preview, pointer-up → numeric/details editor. Escape cancels unsaved shape. A zero-area box is discarded. | Explicit Save region posts `/sessions/{session_id}/boxes`. |
| Select/move/resize | Click overlay or list row to select. Edit handles only on selected active box; retain draft geometry until Save/Cancel. | Save patches the selected box; no server call on every pointer move. |
| Delete region | Confirm “Remove this region? Its submitted question history is retained.” | DELETE scoped box endpoint; retained server history/soft deletion. |
| Show/hide overlays | Toggle rendering with a visible state and accessible label. | None; does not reject or delete boxes. |
| Ask about this box | Require saved active geometry, show its image/ID attachment and open Chat. | Ask job includes scoped `box_id`; crop created server-side. |

Reset changes presentation only, not saved boxes. Before successful initial analysis, zoom/pan/filter/reset remain available, while Draw, box save/edit/delete, findings save and all Ask actions are disabled with “Analyze this study first.” A direct bypass receives409 `ANALYSIS_REQUIRED`; no placeholder findings/box is saved. Changing displayed current image resets fit/filter defaults and clears selection/unsaved geometry after dirty handling; boxes belonging to another image are not rendered. Do not implement rotate/flip, DICOM window-level, measurement calibration, segmentation painting or AI heatmaps as implicit viewer requirements.

### 4.2 Original-pixel coordinate mapping

The canonical asset is the orientation-normalized PNG/JPG produced once during fixture import and stored immutably with dimensions and hash. Its coordinate origin is top-left, x increases right, y increases down. The original input hash is also retained by ingestion. The frontend never resaves the displayed image or burns annotations into it.

Canonical API geometry is the floating-point original-image edge array `bbox_xyxy:[x1,y1,x2,y2]`, with `0 <= x1 < x2 <= width` and `0 <= y1 < y2 <= height`. The numeric editor labels individual values x1/y1/x2/y2 but serializes this exact array, not four top-level coordinate fields. Values are not normalized fractions, CSS coordinates, screen coordinates, or device pixels. Never assume a detector's native order; the backend adapter converts detector coordinates before publication. Create sends `{image_id,bbox_xyxy,label,note}`; edit sends only changed permitted fields among `{bbox_xyxy,label,note,review_action}`. Image/source cannot be changed by an edit. Label is at most200 characters and note at most2000; finite coordinate validation and the server's rounded minimum1×1 crop apply.

Let source size be `W,H`; viewer inner CSS size be `Vw,Vh`; fit scale `f=min(Vw/W,Vh/H)`; relative zoom `z`; combined scale `s=f*z`. Let `ox=(Vw-s*W)/2+pan_x` and `oy=(Vh-s*H)/2+pan_y`. After subtracting the viewer's client rectangle from a pointer position, map `source_x=(css_x-ox)/s`, `source_y=(css_y-oy)/s`. For a generalized transform, maintain one transform matrix and apply its inverse to pointer coordinates; SVG rendering and hit testing use the same matrix. Page scroll, panel offsets and devicePixelRatio must not be counted twice.

Example: a `2048×2048` image fits a `768×768` canvas at `s=.375`. With pan `(32,-16)`, the source point `(400,600)` renders at viewer coordinates `(182,209)`. Inverse mapping returns exactly `(400,600)`. Zoom/pan round trips must be within `0.5` source pixel. Store precise floats; display coordinate fields to two decimal places without repeatedly rounding persisted geometry.

Clip drawing/move previews to image extent. Numeric inputs reject reversed bounds, nonfinite values, zero area and coordinates beyond dimensions. A reverse drag is normalized with min/max before presenting the draft. On resize, do not permit an edge to cross its opposite edge. Keyboard and pointer operations produce the identical coordinate schema. The backend repeats all validation and checks same-session/image ownership.

### 4.3 Cropping, snapshots and persistence

For `[x1,y1,x2,y2]=[100.25,200.75,300.5,400.2]`, the server crops the half-open source-pixel rectangle `[100,200,301,401)` by flooring minima and ceiling maxima, clamped to image dimensions. Pixel filters, SVG outlines and frontend resize are not included in the crop. Do not send a screenshot or DOM-rendered thumbnail to the model. Show the submitted original coordinates in an accessible attachment detail; the backend stores its immutable box snapshot/revision with the AI request and resulting message.

Saving an annotation returns a receipt, then Workspace refetch restores the server geometry, source, review action and revision. API failure leaves the clearly marked local draft for correction, never a falsely saved overlay. On reopen, reconstruct overlays from backend records. Deleted/rejected boxes are excluded from active evidence and crops but remain inspectable through approved snapshots/message attachments as allowed by the server. A referenced original AI box remains AI-sourced even after doctor editing; add a “Doctor edited” label and reviewer metadata rather than changing provenance.

Gate 1 freezes a reviewed annotation snapshot inside the approved findings version. A later box change creates a new findings version and stales report drafts just like a findings-text change. Final read-only sessions render the approved snapshot; hidden/visible overlays remain a local viewing preference. These rules implement BR-006, BR-008, BR-010, BR-011 and BR-015, diagrams DIA-012, DIA-015 and DIA-022, tasks TASK-009/TASK-014/TASK-015, tests TST-006/TST-009/TST-012/TST-016.

### 4.4 Keyboard-accessible regions

The region list is the keyboard alternative to the SVG. Each row exposes region ID, source, review state, coordinates and linked finding. Enter selects; actions open labelled numeric fields for all four bounds. Arrow controls in the numeric editor adjust a source edge by one pixel; Shift increases the step to ten, while standard text editing remains intact. Offer labelled Move left/right/up/down controls for the selected box and Save/Cancel. All commands are available without drag precision. SVG handles have 44 px hit areas without increasing the apparent outline; selection includes a dual contrasting outline and textual list state so it remains visible over bright or dark X-ray pixels. Screen readers receive the image description “Chest X-ray, [view], [width] by [height] pixels,” never an AI diagnosis as alt text.

## 5. Components and proposed modules

All filenames in this section are **TO CREATE**. Routes assemble feature components; feature hooks call the typed API; presentational components receive data/events and do not import repositories or provider SDKs. The backend is the sole business-rule authority. Avoid a second domain implementation in client components.

| Component / location under `apps/web/src/` | Inputs and responsibility | Events and state owner |
| --- | --- | --- |
| `features/auth/AuthProvider.tsx` | User/role, CSRF token, expiry; bootstraps `/auth/me`. | `onSignOut`; memory-only auth state; clears QueryClient on actor change. |
| `lib/api/client.ts` | Same-origin fetch, typed errors, CSRF/activity/ETag/idempotency headers; no credential logging. | Returns DTO or typed error; never auto-retries clinical mutation with a new key. |
| `lib/api/contracts.ts` | Generated or checked-in DTO types matching OpenAPI; central enum names. | No runtime clinical state. TASK-001/TASK-008 establish generation/check process. |
| `features/worklist/WorklistPage.tsx` | Patient pages and own-study summaries. | Search/expand/open; query hooks own server cache, page owns local search/expansion. |
| `features/workspace/StudyWorkspace.tsx` | Authorized Workspace, current route tuple. | Owns selected panel/image and context generation; unmounts child draft state on binding change. |
| `features/workspace/PatientContextPanel.tsx` | Patient/study metadata, `history`, source dates. | Expand/collapse source text; no model-context editing. |
| `features/viewer/StudyViewer.tsx` | Image dimensions/content reference, boxes, selected box, `readOnly`, selected tool. | `onDraftBox`, `onSelectBox`; local transform/filter/pointer reducer. |
| `features/viewer/AnnotationToolbar.tsx` | Tool, transform values, selected box and allowed actions. | Zoom/pan/draw/reset/show overlays; no persistence. |
| `features/annotations/RegionEditor.tsx` | Existing box or draft coordinates, source/review status, image limits. | Save/cancel/review/delete/ask; local draft, hook persists and refetches. |
| `features/analysis/AIAnalysisPanel.tsx` | Findings origins, detections, artifact metadata, request state and blockers. | Analyze/retry/select finding/box; no direct provider call. |
| `features/analysis/useAIRequest.ts` | Session tuple, revision, operation parameters; current request ID. | Submit/check/retry; query owns request polling and terminal transition. |
| `features/chat/VQAChat.tsx` | Saved messages, ROI snapshot attachment, current image, disabled reason. | Send/retry/load older; local unsent question, query-owned transcript. |
| `features/findings/FindingsReview.tsx` | Current findings version and original AI items, boxes, blockers. | Accept/edit/reject/add/link/save; local full-list draft. |
| `features/report/ReportEditor.tsx` | Current report, approved findings source, session state. | Save/generate/return-to-findings; local editable text and dirty marker. |
| `features/review/ApprovalControls.tsx` | Gate 1/2, resource/version, blockers, submitting state. | Opens specific confirmation and invokes explicit approval; no generic unchecked Approve callback. |
| `features/audit/SessionAuditPanel.tsx` | `/sessions/{session_id}/events` summaries. | Refresh/view details; read-only own-session metadata; no clinical audit admin route. |
| `features/report/FinalReportView.tsx` | Authorized final report plus provenance and approver. | Return to same workspace; no editing. |
| `features/admin/AdminWorkspace.tsx` | Doctor/assignment/catalogue/fixture DTOs. | Create/disable/grant/revoke/import; tab-local forms. |
| `components/StatusBadge.tsx`, `components/FieldError.tsx`, `components/ConfirmDialog.tsx` | Explicit textual state, errors or action-specific confirmation. | Presentational only; accessible focus/label primitives. |

`StudyViewer` may use a small domain-specific coordinate module `features/viewer/imageCoordinates.ts` to isolate transform math from React. Test this module against source-pixel examples, not by repeating its implementation. Do not introduce a global store solely to hold data already in QueryClient. The document's local behavior for presentation controls is complete; no persistence endpoint is implied for panel size, filters or selected tab.

## 6. Design system and accessible interaction

### 6.1 Visual hierarchy and tokens

Use a light document/review surface and dark image stage. The strongest persistent hierarchy is patient/study identity, current reading state, current content, then the next clinical action. Use one primary action per panel. Use no gradients, animated image effects, decorative medical illustrations or score dashboards. Icons use a single SVG icon family with text or accessible names; never emoji status icons.

| Token | Value and usage | Verified example contrast |
| --- | --- | --- |
| `surface` / `text` | `#FFFFFF` / `#0F172A` | 17.85:1 |
| `text-muted` | `#475569` on white | 7.58:1 |
| `primary` | `#0E7490` with white label | 5.36:1 |
| `approved` | `#047857` with white label | 5.48:1 |
| `pending/stale` | `#FEF3C7` surface, `#92400E` text | 6.37:1 |
| `error` | `#FEE2E2` surface, `#991B1B` text | 6.80:1 |
| `viewer` / `viewer-text` | `#0B1220` / `#E2E8F0` | 15.19:1 |
| `ai-region` | `#FBBF24` on dark viewer chrome | 11.22:1 |
| `doctor-region` | `#22D3EE` on dark viewer chrome | 10.36:1 |
| `border-strong` | `#334155` against `#E2E8F0` | 8.40:1 |

Ratios were calculated from these exact opaque color pairs. They do not guarantee contrast over arbitrary image pixels; overlays therefore require dual outlines and a labelled region list. Verify hover, focus, selected, error and disabled states with the final rendered colors. The normal-text minimum is 4.5:1 and large-text/nontext boundaries use the applicable criteria; see the [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html). This is an implementation target, not a claim of certified WCAG conformance.

Use locally available system sans-serif (`Segoe UI`, `Arial`, sans-serif) to avoid runtime font downloads; self-hosted fonts may be added without changing metrics after review. Body 16 px/1.5; dense metadata and table rows 14 px/1.45 minimum; page headings 24 px/1.25; section headings 18 px/1.35; button labels 14–16 px. Numerals use tabular spacing where useful for dates/coordinates. Text editor line length targets 65–80 characters. Spacing scale is 4, 8, 12, 16, 24 and 32 px; inputs and primary click targets at least 44 px high; radii 4–8 px. Reserve consistent rows for async status to avoid layout jumps.

### 6.2 State language

| Underlying state | Visible label and treatment |
| --- | --- |
| Raw model output / pending AI box | “AI suggestion — needs doctor review”; amber dashed outline or badge plus pending text. |
| Doctor-added content | “Doctor added”; cyan region outline and explicit source label. |
| Edited derivative | “Doctor edited”; original source retained in details; no claim model produced the correction. |
| Findings `in_review` | “In review”; pending count and unresolved checklist. |
| Findings `approved` | “Findings approved · v[n]”; approver/time and read-only approved snapshot. |
| Report `draft` | “Draft — awaiting final approval”; saved/unsaved text kept separate. |
| Report `stale` | “Stale — findings changed”; amber banner, read-only text, recovery link. |
| Session/report `final` | “Final — read-only”; lock icon plus text, approver/time and source version. |
| Job terminal failure | “Analysis failed” / “Request timed out”; precise recoverable action, no green success indicator. |

Approvals never rely on green alone; stale status never relies on amber alone. A disabled action shows its reason adjacent to it. Do not use “Diagnosis confirmed” for AI output, a box or the final report. Academic mock-data and AI-source labels remain short and readable; they must not compete with the patient's identity or hide the review workflow.

### 6.3 Keyboard, focus and feedback

Provide a skip link to main content, semantic landmarks, labelled controls, semantic table headers and `aria-selected` tabs. Keyboard order follows header → identity/context → viewer toolbar/region list → review tabs → active panel → actions. Dialogs trap focus, close on Escape unless a committed request is still resolving, and restore focus to the trigger. The two gate confirmations have distinct titles and action labels. No global single-letter shortcuts can fire while typing clinical text.

Focus rings are visible on light and dark surfaces; use a 3 px high-contrast ring with offset. Error summaries link to invalid fields. Inline Save success uses a polite live region; access loss and failed approval use an alert. AI polling updates elapsed time visually without announcing every second. Visible tooltips supplement rather than replace labels. Loading actions use text and a spinner; animations are limited to 150–200 ms color/opacity changes and removed for `prefers-reduced-motion`. Never animate or progressively modify the image as though an AI region were discovered before validated output arrives.

### 6.4 Error copy and recovery

| Condition | User-facing text and action |
| --- | --- |
| `RESOURCE_NOT_FOUND` | “This study is unavailable.” Return to worklist. Avoid ownership explanations. |
| `UNAUTHENTICATED` | “Your session expired. Sign in again.” Clear clinical state and go to login. |
| `AI_BUSY` | “Another AI request is running for this study.” Check existing request. |
| `GATE1_REQUIRED` | “Approve the current findings before generating a report.” Go to Findings. |
| `ANALYSIS_REQUIRED` | “Analyze this study before asking questions or editing regions and findings.” Go to Analysis. |
| `UNRESOLVED_REVIEW` | “Review the remaining findings and AI regions before approving.” Focus first blocker. |
| `REPORT_STALE` | “Findings changed after this draft was created. Approve them and generate a new report.” |
| `REVISION_CONFLICT` | “This reading changed in another tab. Reload before saving.” Preserve this tab's draft separately in memory. |
| `FINAL_READ_ONLY` | “This reading is final and read-only.” Reload final state. |
| `PHI_IN_MODEL_INPUT` | “Remove the patient name or patient code from the text before sending it to AI.” Focus the offending editable field when the server safely identifies it; do not echo identifiers in diagnostics. |
| Model unavailable / failure | “The AI service could not complete this request.” Try again; show a diagnostic request ID in expandable details. |
| `timed_out` | “The AI request exceeded 60 seconds.” Try again with a new request. |
| uncertain network submission | “The connection was interrupted. Checking whether the action was saved.” Recover with same key before offering a new action. |

Display safe backend validation messages for exact field constraints; never show stack traces, storage paths, prompts, raw detector payloads, secrets or database errors. Console and browser analytics must not record patient identifiers, clinical text or images. Do not insert the identity strip's name/code into questions or model input. The backend's outbound filter checks normalized known current-patient name/code tokens across all model-bound text, rejecting before submit or failing the job if discovered later. This does not claim arbitrary real-data PHI recognition; only de-identified authorized mock fixtures are accepted. Client hints such as “Do not enter patient names” are supplementary and cannot enforce privacy or authorization.

## 7. State ownership, consistency and recovery

### 7.1 State model

| State | Authority/storage | Frontend behavior |
| --- | --- | --- |
| Authentication/role | Server cookie and `auth_sessions`; memory AuthContext projection | Bootstrap once; no token in localStorage; clear all actor-scoped data on logout/401. |
| Patient/study/session binding | Server session row | Route expresses intended resource; accept only authorized response binding; never mutate binding. |
| Worklist, image metadata/history | API + memory QueryClient | Keys contain actor and resource tuple; no persisted query plugin. |
| AI job and result | `ai_requests` through API | Poll known ID; don't infer terminal state from a timer; refetch workspace after terminal completion. |
| Chat transcript | Server messages | Query-owned; unsent question/pending bubble local only. |
| Viewer/filter/tool | Local viewer reducer | Reset on image/context change; never alter stored image. |
| Box geometry/review | Server boxes; temporary edit local | Explicit save; refetch authoritative coordinates/revision. |
| Findings/version/approval | Server findings/session | Local full-list draft; never optimistic approval or source rewrite. |
| Report content/finality | Server reports/session | Local explicit editor; finality only after success + refetch. |
| Audit history | Server append-only events | Own-session read-only summary; not operational console logs. |

Use query keys such as `['actor', doctor_id, 'patient', patient_id, 'study', study_id, 'session', session_id, 'workspace']`; messages and requests append their resource name/ID. Worklist uses actor+search+cursor; admin keys use admin actor and management resource. Variables affecting data belong in the key, following the [TanStack Query key model](https://tanstack.com/query/latest/docs/framework/react/guides/query-keys). Do not reuse a key named only `chat` or `patient` and do not use previous-data placeholders while changing patient/study.

### 7.2 Mutation protocol

1. Check local dirty/validity/gate blockers and current permission projection. These checks improve feedback; they do not grant authority.
2. Send the current session ETag as `If-Match`, `X-CSRF-Token` on mutations, and a cryptographically generated per-action `Idempotency-Key` of16..128 printable ASCII characters. Required for AI submissions, creates and approvals; also use it for clinical edits/deletes to recover response loss. Session open, login/logout and natural soft revocation follow their documented natural-idempotency contract.
3. Disable conflicting clinical mutations for this session while a mutation/request is resolving. Preserve local draft until its server result is known. Do not optimistically mark findings approved, report final, box accepted or text saved.
4. For box/findings/report mutations, parse the MutationReceipt (`resource_type`, `resource_id`, optional `session_revision`) and refetch Workspace. Use the returned revision; never increment it locally. AI submission returns the job resource and updated session revision, followed by polling.
5. When authoritative state arrives, replace the saved baseline and clear the corresponding dirty draft. Invalidate own study/worklist summaries after session creation, Gate 1, reversion to review and Gate 2; invalidate workspace/messages/boxes/findings/reports after AI success or relevant edits. Fetch current session events only when the audit panel is open.
6. A network replay reuses the exact key and canonical payload. The server may return the original receipt even though the old ETag is now stale, after current authorization. A changed payload gets a new key and current ETag; never reuse a key to mean “try a different edit.”

HTTP `412` is a revision conflict; `428` means a missing precondition and is a client integration defect, so refetch state and require the original user action again. HTTP `409` describes semantic blockers such as stale report or live AI job; it does not justify blindly retrying. Rate limits honor Retry-After. A successful poll response can carry `failed`, `timed_out` or `cancelled`; HTTP 200 is not equivalent to inference success.

### 7.3 AI request protocol

The job lifecycle is `queued → running → succeeded|failed|timed_out|cancelled`, with failure/cancellation also possible before execution as specified in DIA-021. One queued/running job per session is allowed. Submit increments the server session revision once, the worker publishes completion once, and status polling/lease updates do not change the clinical revision. Frontend code must not synthesize that arithmetic.

Poll every 1000 ms only while the document is visible and the request is nonterminal. While hidden, stop polling; on visibility/focus refetch `/auth/me` and current workspace/job without an activity header, then resume if permitted. A job continues on the server while hidden or after navigation. A terminal status stops polling and triggers a workspace refetch, including failures, because server revision changed. If a terminal retryable job is retried explicitly, use a new key and `retry_of` while preserving the original record. Do not change provider mode automatically after a real-model failure.

On a fresh page load, restore the request card from `Workspace.latest_ai_request`, using its authorized `submitted_input` for the original question/operation; `live_request_id` determines whether to resume polling. The same fields are returned by request GET. Keep failed requests outside completed chat context. If an exact retry is rejected with `409 CONTEXT_CHANGED` because an image/box/message/approved-source changed, keep the visible original input and require a fresh intentional submission without `retry_of`; never silently replay an obsolete crop/context.

### 7.4 Study switching and late results

Before a binding change, settle the dirty-form choice. Then increment a local context-generation token, cancel old fetches/polling, unmount all workspace children, clear selected box/ROI attachment/drafts and release the previous image's object URL/canvas. Fetch the new binding into a blank shell. Only commit a callback if actor, patient, study, session and generation still match its originating request. An aborted browser request does not imply the server cancelled the job. Never reuse an old ROI or unsent question when the next patient is loaded.

Revisiting a study obtains fresh authorized server state and restores its saved transcript/annotations/findings/report. Viewer preferences may reset; local unsaved drafts from another study are not restored. On logout, clear the entire QueryClient, in-memory drafts, object URLs and image buffers. A same-origin cross-tab logout message may contain only a logout event, no patient or clinical content. Do not persist sensitive draft recovery in localStorage, sessionStorage or IndexedDB.

### 7.5 Concurrent edits and connection loss

When `412` arrives, freeze Save/Approve, retain this page's unsaved text in a separate in-memory conflict buffer, and fetch the new server baseline. Show read-only “Your unsaved text” and “Current saved text,” with explicit Discard my changes or Reapply manually. Do not automatically merge clinical prose or coordinates. Reapplying creates a new draft against the current revision and requires another explicit Save. If the server is now final, offer only discard/inspect; no reapply action.

On ordinary network loss, keep clearly labelled already-loaded content in the same workspace, show Offline, disable mutations, and offer Reconnect. On `401`/authorization loss, clear clinical content rather than retaining an offline copy. If an approval response is lost, checking with the original idempotency key and/or reloading Workspace must recover the existing approved artifact; clicking a new approval is disabled while outcome is uncertain. After reconnect, refresh before enabling any mutation.

### 7.6 Thirty-minute idle expiry

The backend enforces 30 minutes without user activity. Automatic polling, health checks and background refetch do not keep a login alive. Mark deliberate navigation/search/refresh and human mutations with the contract's `X-User-Activity: 1` behavior; do not send it on timers or repeated mouse movement. Display an expiry warning two minutes before the server-provided expiry with Continue session and Sign out. Continue session deliberately calls `/auth/me` with the activity header and refreshes expiry if still valid. A local warning cannot extend the server deadline.

On expiry/401, immediately clear clinical state and navigate to UI-001; after reauthentication return only to an authorized worklist, with no automatic mutation replay. Saved server content is resumed by opening the study again. Unsent text is lost on logout/expiry because browser clinical persistence is excluded; the warning tells the user to save current edits. Client clock drift must not grant extra access; the server response determines validity.

## 8. Action-to-contract inventory

All paths below are relative to `/api/v1`. API DTO definitions and error codes in the implementation plan are authoritative; these mappings prohibit accidental frontend-only clinical actions.

| UI action | API or explicitly local effect | Primary rule/test/task |
| --- | --- | --- |
| Sign in / bootstrap / sign out | POST `/auth/login`; GET `/auth/me`; POST `/auth/logout` | BR-001; TST-001; TASK-005 |
| Search patients / expand studies | GET `/patients`; GET `/patients/{patient_id}/studies` | BR-002; TST-002/TST-013; TASK-007 |
| Open/resume selected study | GET `/studies/{study_id}`; POST `/studies/{study_id}/sessions`; GET `/sessions/{session_id}` for refresh | BR-002/003; TST-013/TST-014; TASK-008 |
| Load image | GET `/images/{image_id}/content` | BR-006; TST-003; TASK-009 |
| Zoom, pan, filters, reset, panel/tab selection | Local presentation state only | BR-006; TST-003/TST-016; TASK-009 |
| Analyze, full-image ask, ROI ask, draft report | POST `/sessions/{session_id}/ai-requests`, corresponding operation | BR-004/005/009/012/014/015; TST-004/005/006/008/009/011/017; TASK-010–TASK-014/TASK-016 |
| Poll/check job / retry | GET `/sessions/{session_id}/ai-requests/{request_id}`; explicit new POST with `retry_of` | BR-012; TST-015; TASK-010/TASK-017 |
| Restore/load older chat | GET `/sessions/{session_id}`; GET `/sessions/{session_id}/messages` | BR-003; TST-005; TASK-013 |
| Create/review/edit/delete region | POST/PATCH/DELETE `/sessions/{session_id}/boxes[/{box_id}]` | BR-006/008/010/015; TST-006/009/012/016; TASK-014/TASK-015 |
| Link region to finding; accept/edit/reject/add finding; confirm normal; save | PATCH `/sessions/{session_id}/findings` with complete current list | BR-007/008; TST-007/009; TASK-015 |
| Gate 1 | POST `/sessions/{session_id}/findings/approve` | BR-007/008; TST-007/014; TASK-015 |
| Edit report / Gate 2 | PATCH `/sessions/{session_id}/reports/{report_id}`; POST scoped `/approve` | BR-009/010/011; TST-008/012/014; TASK-016 |
| Inspect saved source reports / approved versions | Local expansion of authorized Workspace content | BR-004/008; TST-004/007; TASK-008/TASK-015 |
| View own-session audit | GET `/sessions/{session_id}/events` | BR-013; TST-010; TASK-010 |
| Admin doctor/assignment/fixture actions | Management endpoints enumerated in UI-005 | BR-002/013; TST-001/013/019; TASK-006 |
| Final report route | Authorized own-session load; render final report content only | BR-011; TST-008/013; TASK-016 |

FR-012–FR-015 require separate approved API/design increments under TASK-023. They must not borrow a current-study session to send an older image to AI, expose another doctor's session, add rating edits to final sessions without an explicit policy, or present a local browser export as an audited server artifact. No deferred feature is a prerequisite for the MUST frontend.

## 9. Frontend verification and implementation handoff

Use the companion plan's TST IDs and Given/When/Then scenarios. Frontend checks target observable behavior, patient isolation, coordinate fidelity and critical gates rather than duplicating backend implementation. This document specifies tests to create later; no UI or application tests were run in this documentation task.

| Test group | Required observable checks |
| --- | --- |
| TST-001/TST-013 | Same login failure; route role guards; no admin clinical payload; revoked assignment clears the active workspace; named patient text never appears after actor change. |
| TST-002/TST-003 | Search and independent own progress; 1280/1440/1920 layouts; image loading within configured demo target; reset preserves asset hash and boxes. |
| TST-004/TST-009/TST-011/TST-017 | Mock/real mode badge; VLM and CV provenance; zero detections versus failed detector; no invented links/scores; full mock workflow while real artifacts unavailable. |
| TST-005/TST-006/TST-016 | Persisted transcript; last-study isolation; ROI crop/original coordinates; high-DPI and scroll transforms; reverse drag; border boxes; keyboard numeric alternative; old question snapshot after box edit/delete. |
| TST-007/TST-008/TST-012 | Pending findings/AI regions block Gate 1; explicit normal path; latest approved version required; post-Gate-1 geometry/text change stales draft; Gate 2 locks every clinical editor. |
| TST-014/TST-015 | Double-click and response-loss replay create one action; two tabs conflict safely; reload while job running; timeout/worker loss/authorization cancellation; late prior-patient response ignored. |
| TST-018/TST-019 | Login → own worklist → image → analysis/detections → full/ROI chat → all reviews → Gate 1 → draft/edit/save → Gate 2 → final reopen. Restart restores saved state. |
| TST-020 | Keyboard-only complete flow including boxes and confirmations; visible focus; no color-only status; exact contrast tokens; reduced motion; 200% zoom reflow at sufficient viewport; screen-reader labels/announcements. |

The frontend sequence follows TASK-005–TASK-009 for access and viewer, TASK-010–TASK-014 for mock analysis/chat/regions, TASK-015–TASK-018 for the two gates and complete mock E2E, then TASK-019–TASK-022 for supplied real-model integration and acceptance. TASK-023 remains optional after every MUST scenario passes. TASK-024 records unresolved integration facts and handoff. No task trains a VLM or CV model.

Before implementation, obtain the supplied detector artifact/version, supported labels and coordinate/preprocessing manifest plus the VLM serving artifact and tested image/ROI interfaces; these are OPEN integration questions in the decision log. Until then the exact same frontend is built and exercised against the mock contracts. The first frontend-dependent implementation work is TASK-005 after TASK-001–TASK-004 establish the baseline, infrastructure, schema and fixtures.
