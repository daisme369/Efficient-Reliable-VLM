# Thorax AI — Product Requirements and Functional Specification

Version: 1.0 · 6 November 2026 · Status: implementation specification with explicitly identified provisional decisions.

This document defines the software to build. [implementation_plan.md](implementation_plan.md) owns the database, API schemas, Mermaid diagrams and ordered tasks. [frontend_des.md](frontend_des.md) owns screen and interaction behavior. Application modules, endpoints and database structures described here are **TO CREATE**, unless explicitly labelled EXISTING. This delivery contains documentation; it does not implement the application or train a model.

## 1. Evidence, authority and delivery boundary

### 1.1 Source ledger

| Source | Inspection | Authority and limits |
| --- | --- | --- |
| Current pasted architecture/specification request | Read in full from `C:\Users\Admin\.codex\attachments\c2454b3f-11af-4f87-9848-751a9fe1ea2f\Pasted text.txt` | Governs this documentation task and its three deliverables. |
| Current user clarifications | “Automatics AI boxes work as CV model detect from image”; model training/fine-tuning is separated from the current software-building stage | Overrides the PDF's exclusion of automatic boxes. Specifies a CV detection capability and a separate training workstream. |
| Thorax AI — Product Specification (MVP), 30 October 2026 | All 17 pages extracted and read from `C:\Users\Admin\Downloads\Thorax AI — Product Specification (MVP).pdf` | Primary approved product baseline except where an explicit newer user decision supersedes it. Page references below refer to this PDF. |
| “Xây dựng Feature List” discussion | Retrieved through the connected chat reader using conversation `6ae2a22a-8088-83ec-b269-655647803d38`; all five user turns and four assistant responses complete; earliest assistant response was truncated by the reader at 20,000 characters | Explicit user statements are requirements. Assistant recommendations are proposals, not approvals. The missing tail of the earliest assistant response was not inspected. |
| Project Description.pdf | All five pages reviewed by a source-review subagent, including visual inspection of the hospital workflow on page 4; located at `C:\Users\Admin\Documents\Codex\2026-10-14\referenced-chatgpt-conversation-this-is-an\outputs\nguon\Project Description.pdf` | Original CDSS/PACS context. Its real integration vision is narrowed by the later mock-data MVP. |
| Related research repository | Source-review subagent inspected `D:\Efficient-Reliable-VLM\README.md`, `src\cxr_research\README.md`, `pyproject.toml`, `requirements.txt`, `SYSTEM_DESIGN_PLAN.md`, model configs and repository policy files | EXISTING research/model/data code; package README explicitly says it does not implement the application. No app entry point or existing three core spec documents found. Git working-tree status could not be read; cleanliness is unknown. No repository changes made. |
| Requested skills | `software-architecture`, `senior-architect`, `ui-ux-pro-max` found and read under `C:\Users\Admin\.codex\skills`; UI design-system search run | Used as design guidance. Generic sample architectures and marketing layout recommendations do not override clinical workflow requirements. |
| Prior October design/prototype | Read source design and provenance notes; prototype file existence confirmed | UI/reference history only. Its upload-only workflow, nurse role, RAM-only chat and SQLite design are superseded where they conflict with the current MVP. |

The generated working directory has no application checkout, attached worktree or established documentation convention. These three reviewable deliverables are therefore saved in `outputs/docs/`; copy them into `docs/` of the selected application repository at TASK-001. The related research repository is a read-only integration reference, not an assumed application write target.

Requirements use three evidence labels: **CONFIRMED** for explicit product/user requirements; **PROPOSED DECISION** for a concrete engineering resolution adopted by this specification; **OPEN QUESTION** for an external fact or decision still needed. Proposed decisions permit deterministic mock implementation; they must not be described as historical approvals.

### 1.2 Decision and conflict log

| ID | Evidence/conflict | Resolution and status | Implementation consequence |
| --- | --- | --- | --- |
| DEC-001 | Original project describes PACS integration and no new image archive; PDF pp.3,14 excludes PACS/EHR/DICOM integration | CONFIRMED: simulate patients, studies and PNG/JPG assets locally; no PACS replacement or hospital connector | Use repository/database adapters and immutable demo assets; no DICOM server or FHIR client in the MVP. |
| DEC-002 | PDF excludes automatic boxes; later discussion requests spatial evidence; current user specifies CV detection | CONFIRMED capability; PROPOSED DECISION to treat software integration as FR-009 MUST | Initial analysis orchestrates VLM findings and a supplied CV detector. Mock detector is required; real completion depends on an externally supplied usable detector. No claim that the research repo already has one. |
| DEC-003 | Discussion considers a unified VLM, SigLIP detection backbone and specialized detector | PROPOSED DECISION: separate `VLMProvider` and `DetectorProvider` ports; one private inference service may host both | No requirement for separate physical weights or training jobs. A shared backbone is permissible only if delivered artifacts actually support both contracts. |
| DEC-004 | User explicitly separates model training/fine-tuning from software building | CONFIRMED: this roadmap integrates supplied inference artifacts only | No dataset construction, training, fine-tuning, detection-head development or model release research task is an application dependency. Missing real artifacts remain external dependencies. |
| DEC-005 | Early discussion mentions raw chat logs; PDF FR-13 requires saved/restored chat | CONFIRMED PDF baseline: structured persisted sessions/messages; restored within the study | Session history index is SHOULD, automatic archive LATER. Technical logs are not the chat database. |
| DEC-006 | PDF S3 labels findings versions SHOULD; pp.5,7 require immutable approval versions and stale-report rejection | PROPOSED DECISION: minimum versioning and stale prevention are MUST correctness requirements under FR-007/008 | Approved snapshots never change. A richer version browser/diff can wait under FR-014. |
| DEC-007 | Discussion calls history “RAG”; PDF FR-09 specifies up to five earlier reports without semantic search design | PROPOSED DECISION: deterministic patient/date-scoped history retrieval for MVP; preserve a history-retrieval port | No embedding/vector database. Semantic ranking is an extension requiring its own contract and relevance evaluation. Never claim current retrieval compares historical images. |
| DEC-008 | Multiple doctors can be assigned one patient; one session is unique per doctor and study; global study status is ambiguous | PROPOSED DECISION: independent sessions; own reading status drives the worklist | Aggregate study `final` never locks another doctor's nonfinal reading. Only the current session's Gate 2 freezes its clinical artifacts. |
| DEC-009 | Admin assigns patients but must not open clinical resources | PROPOSED DECISION: minimal assignment catalogue exposes internal patient ID and mock patient code only | Admin cannot read names, demographics, indication, image, history, chat, findings, report or clinical event payload. Fixture import returns counts/status only. |
| DEC-010 | PDF has 12 tables but requires robust login expiry and asynchronous inference | PROPOSED DECISION: add `auth_sessions` and durable `ai_requests`, yielding 14 tables | PostgreSQL provides login revocation, durable job status and recovery; no Redis/Celery dependency. |
| DEC-011 | Earlier discussion suggests final report amendments | CONFIRMED PDF restriction: final session is read-only; amendments deferred | No edit, reopen or amendment endpoint in the MVP. A future amendment must preserve the final artifact and obtain a separate approved design. |
| DEC-012 | Study may contain several images; AI operations are single-image | PROPOSED DECISION: doctor explicitly selects one `image_id` for the session's first analysis; ROI/chat also specify a current-study image | Other study images remain viewable. No implicit multi-image fusion; report identifies the analyzed image. Re-running successful initial analysis is deferred. |
| DEC-013 | Bounded demo should operate without inference readiness | CONFIRMED: mock and real use identical contracts; PROPOSED DECISION: one explicit deployment setting selects both providers | Never silently replace failed real inference with mock content. A mock demo does not satisfy real-model acceptance. |
| DEC-014 | Current specification retains Next.js 15; dependencies age independently | PROPOSED DECISION: retain source stack, resolve patched versions at TASK-001, record support/upgrade checkpoint | No invented lockfile or assertion that the existing research repo already contains this app stack. |

### 1.3 Open questions and provisional assumptions

| ID | Type | Question/assumption | Owner and impact |
| --- | --- | --- | --- |
| OQ-001 | OPEN QUESTION | Which application repository will host the software? | Project owner at TASK-001. Use proposed module paths until selected; do not place application code into the research package by assumption. |
| OQ-002 | OPEN QUESTION | Which delivered VLM bundle supports full-image analysis, multi-turn VQA, crop VQA and approved-findings report drafting? Exact artifact revision and runtime/hardware? | Model workstream supplies manifest before TASK-019. Mock development continues independently. |
| OQ-003 | OPEN QUESTION | Which supplied CV detector, supported finding labels, preprocessing/inverse transform and measured limitations will be integrated? | Model workstream supplies manifest before TASK-020. Do not substitute an untrained detector or invented localization. |
| OQ-004 | OPEN QUESTION | What test hardware and latency measurements demonstrate the real VLM plus detector target? | Integration owner at TASK-021. Failure must be reported as unmet real-model acceptance, not hidden by precomputed/mock answers. |
| OQ-005 | OPEN QUESTION | Which image assets and reports have authorization for the software demo? | Data owner before TASK-004. Default to synthetic/nonrestricted authorized fixtures. Restricted MIMIC content stays in its approved environment. |
| ASM-001 | ASSUMPTION | Approximately 12 mock patients, two doctors and one admin are sufficient for the demo; include historical/current studies and a shared assignment | Seed fixture volume is a test/demo baseline, not a patient limit. |
| ASM-002 | ASSUMPTION | One inference request per session and one real inference execution at a time on the demo machine are sufficient | Queue delay counts toward the 60-second deadline; load test verifies behavior rather than promising throughput. |
| ASM-003 | ASSUMPTION | Minimal administrative patient codes are necessary for assignment without clinical access | Catalogue remains metadata-only. Changing this policy requires updating RBAC, API and tests together. |
| ASM-004 | ASSUMPTION | PNG/JPG images can be canonicalized once at import to a fixed orientation/pixel grid | Store input and canonical asset hashes. The canonical imported image is immutable; viewer effects never rewrite it. |

## 2. Product overview and scope

Thorax AI is a desktop web Clinical Decision Support System for a mock chest X-ray reading workflow. Doctors must inspect images, consult previous reports and write a report; a single unreviewable AI response does not support that work. Thorax provides suggested findings, detected regions, image/ROI questioning, findings review and report drafting while keeping two explicit doctor approvals.

Objectives are to demonstrate a complete, repeatable reading workflow; preserve patient and session isolation; retain source/provenance and doctor corrections; produce a report from approved findings; and operate with both deterministic mock inference and supplied real inference. Faster reading and improved diagnostic quality are project goals, not demonstrated product claims.

The target user is a doctor or a medical student acting as a doctor in the academic demo. Admins manage demo access and fixtures. The application worker and inference service are technical actors with narrowly scoped responsibilities, not additional clinical user roles.

### 2.1 Priority catalogue

| Feature | Priority | Scope and baseline mapping |
| --- | --- | --- |
| FR-001 | MUST | Authentication, doctor/admin permissions, doctor accounts, patient assignment and allowlisted fixture import; PDF M1 |
| FR-002 | MUST | Assigned patient/study worklist; PDF M2 |
| FR-003 | MUST | Immutable PNG/JPG viewer; PDF M3 |
| FR-004 | MUST | Initial image/history findings analysis; PDF M4 |
| FR-005 | MUST | Persisted same-study VQA/chat; PDF M5 |
| FR-006 | MUST | Doctor ROI boxes and cropped-region VQA; PDF M6 |
| FR-007 | MUST | Findings/box review, Gate 1, immutable approved versions; PDF M7 and minimum S3 correctness |
| FR-008 | MUST | Approved-only draft, report editing, staleness, Gate 2; PDF M8 |
| FR-009 | MUST | Automatic CV detections with provenance and review; current user scope clarification |
| FR-010 | MUST | Persistent audit and AI provenance; PDF cross-cutting FR-24/NFR-07 |
| FR-011 | MUST | Equivalent mock/real inference contracts and explicit provider setting; PDF NFR-12/13 |
| FR-012 | SHOULD | Prior/current X-ray side-by-side for doctor comparison; PDF S1 |
| FR-013 | SHOULD | AI answer rating; PDF S2 |
| FR-014 | SHOULD | Read-only own past-session index and richer findings version inspection; PDF S5/richer S3 |
| FR-015 | SHOULD | Final report PDF export; PDF S4 |
| FR-016 | LATER | Seven-day inactivity archive; PDF L2 |
| FR-017 | LATER | Curated training-data export; PDF L3; training/fine-tuning is a separate workstream |
| FR-018 | LATER | Real PACS/EHR/DICOM/FHIR, segmentation and longitudinal AI interpretation; PDF L4/L5 and later discussion |

All MUST features must work end to end before any SHOULD implementation starts. A real-provider dependency may remain externally unavailable, but this does not change the target priority or permit a false claim of real-provider completion.

### 2.2 Exclusions and AI responsibility boundary

There is no real patient intake, patient self-registration, hospital integration, DICOM reader, billing, scheduling, prescription workflow, mobile/tablet layout, multi-hospital tenancy, segmentation, automated retraining/deployment, or clinical certification claim. No free-form global chat, cross-patient search by the model, model-selection marketplace or continuous learning is required. Approved data is retained for traceability; it is not automatically a training label.

The VLM suggests observations and wording; the CV detector suggests regions. Neither approves findings, signs a report, changes identity, grants access, selects another patient, executes tools, or proves a diagnosis. A box is a spatial prediction, not proof that the linked interpretation is correct. Confidence probabilities, segmentation masks and reliable temporal conclusions are not assumed. Historical text can inform a current observation but cannot prove a visual increase/decrease between images.

## 3. Actors and authorization

`doctor` and `admin` are the only supported human roles. A doctor sees assigned patients and studies and works only in sessions they own. An admin manages doctor account activation, assignments and known fixture imports. The first admin is created through controlled local setup; there is no public sign-up or admin-creation UI.

Every resource request passes authentication, role, patient assignment and ownership checks in the backend. IDs supplied in a URL or body are resource references, never authorization evidence. Anonymous requests receive 401. An authenticated wrong role receives 403. A doctor requesting an unassigned patient or another doctor's session receives the same 404 as a nonexistent resource, including nested image, box, message, report and AI request references.

### 3.1 RBAC matrix

“Own” below means session owner **and** currently assigned patient; “assigned” means active assignment. A successful Gate 2 makes all session clinical writes unavailable.

| Resource/action | Doctor | Admin | Worker/inference boundary |
| --- | --- | --- | --- |
| Log in, inspect own authentication, log out | Yes | Yes | No human account access |
| Create/list/disable doctor account | No | Yes; no password hashes returned | No |
| Create admin account/change own role | No | No UI/API | Controlled seed/setup only |
| Read patient name/demographics/history | Assigned only | No | Backend context builder reads only authorized bound context; inference receives de-identified bounded text |
| Read minimal patient ID/code assignment catalogue | No separate admin catalogue | Yes | No |
| Read study metadata/image bytes | Assigned only | No | Worker reads current authorized study asset; no direct public storage |
| Create/resume reading session | Assigned study, own doctor ID derived from login | No | No independent session creation |
| Read session/messages/boxes/findings/reports | Own only | No | Worker uses bound session for its accepted job only |
| Analyze/chat/draft report | Own nonfinal session | No | Worker invokes selected private provider; no arbitrary tool calls |
| Create/move/resize/soft-delete doctor ROI | Own nonfinal session; no live AI job | No | Inference cannot edit doctor boxes |
| Create automatic AI boxes | No client source spoofing; review/correct via box API | No | Worker creates source `ai` boxes only from validated CV result |
| Review findings and AI box dispositions | Own nonfinal session | No | Inference cannot set doctor review state |
| Approve findings/Gate 1 | Own nonfinal session, all guards satisfied | No | Never |
| Edit draft report | Own current, nonstale draft | No | Provider supplies draft text; backend persists validated result |
| Approve report/Gate 2 | Own current draft, all guards satisfied | No | Never |
| Modify final clinical content | No | No | No |
| Hard-delete clinical content/audit | No | No | No |
| Read clinical event summaries | Own session only | No | Operational diagnostics expose IDs/timings/errors only |
| Create/revoke patient assignment | No | Yes, doctor + metadata catalogue IDs | Job checks assignment at dispatch and completion |
| Import patient/study/image/history fixtures | No | Allowlisted server fixture key only; counts/status response | Setup imports validated mock assets |

An assignment can be shared by multiple doctors. It grants access to patient data and study images, not another doctor's conversation or report. `sessions(study_id, doctor_id)` is unique. Restoring a revoked assignment resumes the same doctor's existing session; it does not transfer ownership. Disabling an account or revoking assignment immediately blocks further applicable API access and invalidates an in-flight job's right to commit.

## 4. Business rules and invariants

These rules are normative. All mutations and asynchronous completion apply them server-side; disabled buttons are only a usability aid.

| Rule | Required invariant and rejection behavior |
| --- | --- |
| BR-001 — Authentication | Use a random opaque login token in an httpOnly cookie; store only its SHA-256 hash. Expire after 30 minutes without deliberate user activity. Polling and health checks do not keep the login alive. Passwords use bcrypt; reject passwords above 72 UTF-8 bytes rather than truncate. Uniform invalid-credential response. |
| BR-002 — Role, assignment and ownership | Every clinical request checks doctor role, current assignment and session ownership as applicable. The worker checks at dispatch and completion too. Wrong patient/session references return indistinguishable 404. Admin cannot open clinical resources. |
| BR-003 — Immutable binding | On session creation derive `doctor_id` from authentication and `patient_id` from the authorized study. `doctor_id`, `patient_id` and `study_id` cannot be changed. Nested resources must belong to that same session/study. Reject extra identity fields rather than using them to retarget context. |
| BR-004 — Patient-scoped temporal history | Query only bound patient reports with `report_date < study_date`, order date descending then ID descending and take at most five. Recheck ownership before model invocation. No current-study target report, future report or other-patient document enters context. No history is valid: continue image-only with `history_used: []`. |
| BR-005 — De-identification and model boundary | Never send patient name, patient code, login identity or raw source identifiers to inference. Use opaque request-local references. Only synthetic/authorized de-identified fixture text and images are permitted. Server allowlists outbound context fields and strips identity/image metadata. Normalize outbound text with Unicode NFKC and casefold; an exact normalized occurrence of the current patient's name/code in question, history, finding or note is rejected with 422 `PHI_IN_MODEL_INPUT` before queueing, or fails the job if detected later. Fixture import rejects known identifiers in narratives; approved fixtures are manually checked for burned-in identifiers. This is a bounded safeguard, not a general PHI detector; arbitrary real-data input is unsupported. Provider adapters receive no lookup credentials or patient database access. Text that asks to switch patient cannot alter binding. |
| BR-006 — Immutable images and coordinates | Canonical imported PNG/JPG bytes/dimensions/hash are immutable. Zoom, pan, brightness, contrast and boxes are presentation/overlay data only. Store bounded float `xyxy` coordinates in original canonical pixels, top-left origin. Server crop uses floor start, ceil end and the unfiltered image. |
| BR-007 — Explicit clinical review | Each AI finding and active AI box starts `pending`. Doctor explicitly accepts, edits or rejects; doctor-added findings/boxes have `added`. An AI finding with action `accepted` must retain the original text/location; corrections require action `edited`. Original AI item IDs, source and analyzed image remain fixed. No pending item may pass Gate 1. Rejected findings and boxes remain retained but are excluded from approved content. Nothing is automatically accepted because it was displayed or discussed. |
| BR-008 — Gate 1 | Require latest saved `in_review` findings version, no live AI request, no pending finding/AI box and valid same-session evidence links. Require either at least one included finding with `no_abnormality_confirmed: false`, or an empty included list with `no_abnormality_confirmed: true`; true plus included items is 422. Approve transaction records actor/time and immutable findings plus annotation snapshot, moves session to `findings_approved` and emits audit event. Preserve original `ai_items`. |
| BR-009 — Approved-only report source | Draft only from the current approved findings snapshot; pass included items and explicit normal confirmation to report provider. Do not pass image, history, chat, rejected/pending items or patient identifiers. Backend builds patient/study metadata from DB and initial findings text deterministically from included approved item text. VLM drafts comparison/impression/recommendation and returns `source_item_ids` from the included approved IDs. Report records exact source `findings_version`; schema/reference validation does not prove semantic fidelity, which the doctor reviews at Gate 2. |
| BR-010 — Staleness and reapproval | Any saved finding or box change after Gate 1 creates the next `in_review` findings version, returns session to `active` and marks existing `draft` reports `stale` atomically. Previous approved versions remain immutable. A new Gate 1 and new report draft are required; neither reapproval nor regeneration silently reuses the old report. |
| BR-011 — Gate 2 and final lock | Require report `draft`, latest findings `approved`, identical source version, no live job and saved nonempty findings/impression text. Finalize report and session atomically with doctor/time. Afterwards reject all session clinical writes, including chat/AI/boxes/findings/report edits, with `FINAL_READ_ONLY`. Read/audit access and logout remain allowed. No MVP amendment path. |
| BR-012 — AI request lifecycle | Persist before returning 202. One `queued`/`running` request per session. Total deadline is 60 seconds from creation, including queueing and all provider calls. `retry_of` is permitted only for same-session/same-operation `failed` or `timed_out` requests with `retryable: true` and unchanged original input. Cancelled requests, including `AUTHORIZATION_REVOKED` and `CONTEXT_CHANGED`, are not retryable. A fresh intentional command after a context change may be submitted without `retry_of` if all current guards hold. Same idempotency key+canonical payload returns the same logical submission; changed payload/key reuse is rejected. Late results never create clinical artifacts. |
| BR-013 — Retention and audit | Clinical deletion is soft deletion; no clinical hard-delete API. Original AI responses, rejected content and approval versions remain stored. Events are append-only. Clinical change and its event commit in the same transaction; an event failure rolls back the change. Operational logs exclude prompts, images, patient names and report text. |
| BR-014 — Provider consistency | Both mock and real implement identical validated contracts. Deployment configuration chooses provider mode. No automatic real-to-mock fallback. UI exposes mode/artifact provenance; every output is marked “AI suggestion — needs doctor review” until its corresponding doctor approval. |
| BR-015 — CV provenance and evidence | Automatic boxes come from `DetectorProvider`, with source `ai`, model/artifact and preprocessing versions. Validate pixel mapping before insertion. Do not infer a clinical finding/box association merely from similar labels. Allow unlinked boxes and findings without boxes; preserve contradictions for doctor review. A detector failure fails initial analysis as a whole. |
| BR-016 — Transactions and concurrency | Lock in order: user row `FOR SHARE`, assignment row `FOR SHARE`, session `FOR UPDATE`, then child rows. Disable/revoke takes exclusive user/assignment lock respectively without locking the session; this orders revocation against result publication. Require `If-Match` session revision; missing gives 428, obsolete gives 412. Reject clinical mutations while an AI request is live with `AI_BUSY`, except external access revocation. Submission changes revision r to r+1 and records `input_revision=r+1`; success compares that value under locks and changes revision to r+2. Failure completion also increments once; claim/lease/poll do not. Commit only while authorization/revision/nonfinal checks still hold. Never keep DB locks open during inference. |

### 4.1 Gate details

The findings review screen can submit a saved complete item list. Each item has `id`, `text`, nullable `location`, `image_id`, `source`, `action` and `box_ids`; `image_id` is required and must belong to the current study. An AI-origin item retains its immutable original ID/text in `ai_items`; edited displayed text does not erase original provenance. Action `accepted` is valid only when text/location equal that original; an actual correction uses `edited`. AI IDs/source/image cannot be reassigned. Item IDs must be unique and included text nonblank. Every JSON evidence link must satisfy both `box.session_id == session.id` and `box.image_id == FindingItem.image_id`, with active, nonrejected box; these JSON cross-references are validated by the application inside the locked transaction, not claimed as foreign keys inside JSON. The API rejects unknown/missing original item IDs rather than silently dropping an AI proposal.

Included actions are `accepted`, `edited` and `added`. `pending` and `rejected` never enter the report provider. If all findings are rejected, the doctor must explicitly affirm the no-abnormality path or add an observation; an empty list alone is not a normal result. An empty model finding list likewise requires this explicit saved decision before approval. `no_abnormality_confirmed: true` is valid only with zero included items; true plus a nonempty included list is rejected with 422. “No abnormality confirmed” means the doctor-confirmed report input; it is not a model performance assertion. Successful initial analysis is required before any box creation/editing/deletion or chat/ROI question; attempts earlier return 409 `ANALYSIS_REQUIRED`.

Gate 1 includes a snapshot of each included annotation's geometry, label, note, source and review disposition. Mutable working boxes may subsequently change, but old approved snapshots never follow those changes. Even a box-only correction creates a new findings version and stales the report, because spatial evidence is part of what was approved.

Chat after Gate 1 is permitted while nonfinal and does not itself invalidate approval: it cannot silently alter findings. A doctor must explicitly save a findings/box change to incorporate a chat-derived correction. During any chat/draft/analysis job, competing clinical mutations are rejected until terminal completion.

Successfully generating another report from the same current approved version creates a new draft row and marks the previous editable draft stale in one transaction; failed generation leaves the existing draft intact. There is at most one current editable draft. A stale report remains readable for provenance but is never editable or finalizable. Changing a draft's comparison/findings/impression/recommendation text does not change Gate 1's source version; the doctor checks wording and consistency at Gate 2. If a change constitutes a new clinical observation, the user must return to findings review and reapprove it rather than introduce it only in the report.

Non-AI creation/approval idempotency receipts are persisted with the event, uniquely keyed by authenticated user, scope and key. Receipt contains only resource type/ID and applicable revision, not credentials or full clinical content. A same-key/hash replay reauthorizes current access and returns the original status/receipt before checking the now-stale revision; changed payload returns 409 `IDEMPOTENCY_CONFLICT`. New keys require current revision. Mutation responses are receipts followed by workspace refetch: box creation is 201, box/findings/report edits and approvals are 200. Assignment revocation is a retained `revoked_at` update, not a deleted assignment row; repeated revoke is naturally idempotent. Fixture import is insert-only and checksum-idempotent for its allowlisted key and cannot overwrite referenced patient/image/history records.

### 4.2 Context and provenance

For analyze, the backend loads the selected current-study image, retrieves the bounded history set and captures a request snapshot: canonical image hash/dimensions, request-local image reference, history source IDs/dates retained internally, de-identified selected text, prompt/preprocessing versions and supplied model artifact IDs. Request-local provider IDs must not be reversible patient codes. The CV detector receives the current image, not history or patient identity. The worker validates and commits both outputs together.

For ask, the backend uses the current session's selected image or saved ROI crop, the new question, the same patient-scoped history rules and at most the last ten **completed** prior doctor/AI messages before that question. Failed/unpaired turns do not enter future context. No global model memory is shared between requests. Context assembly uses server-side session keys; frontend tabs cannot append arbitrary prior messages. Workspace returns the actual bounded historical sources as `{id, report_date, text}`, current `blockers[]`, `live_request_id` and capability flags, so the doctor sees the source context and action guards the server used.

For recovery after page reload, Workspace also includes `latest_ai_request` (or null), including the original public `submitted_input` for that authorized owner. This restores a failed question/request card without treating it as a completed chat message or exposing assembled private context. Polling the same request returns that submission for an exact retry. A retry may reuse the original snapshot only if the current image/box/messages/approved-source context still matches; otherwise 409 `CONTEXT_CHANGED` requires a fresh intentional command. No automatic repeat occurs on reopening.

The last-ten-message rule is preserved even when the doctor displayed another image in the same study. `MessageDTO.image_id` is derived from the linked successful request's immutable input snapshot. Model context uses `last_messages: [{role, text, image_ref, box_ref?}]` and `current_image_ref`, with request-local opaque image/ROI aliases. Thus prior text stays labelled with its actual image/ROI provenance and cannot be represented as an observation on the current input image. No previous image bytes accompany the text, and this does not authorize pixel comparison or longitudinal inference. Old ROI answers retain the submitted geometry/revision snapshot even after the working box changes.

Fixture historical report text is at most 8,000 characters per report; a question is 1–2,000 characters after trimming; the last-ten-message context totals at most 20,000 characters. Findings are at most 50 unique items, each with 1–2,000 text characters and a location up to 200 characters. Report narrative fields are at most 8,000 characters each. Other provider-response limits are defined by the concrete DTOs in implementation_plan.md. No silent truncation is permitted: return a typed context/output-size error if the supplied model cannot fit the bounded request. History source views and output labels distinguish historical text, current AI suggestion and doctor-approved content.

Every AI result records request ID, operation, provider mode, model/artifact revision, prompt version, preprocessing version, source image hash, selected internal history IDs/dates, requested ROI snapshot/hash where relevant, start/end/latency and error if any. An original AI output remains distinguishable from doctor edits throughout the lifecycle.

## 5. Functional use cases

All paths below have prefix `/api/v1`. `UI-xxx`, `DIA-xxx`, `TASK-xxx` and `TST-xxx` are shared identifiers across the three documents. “Standard errors” means 401 authentication, 403 role/CSRF, 404 unauthorized/nonexistent resource, 412 revision conflict, 422 invalid input and 428 missing revision; each API returns the common error envelope in implementation_plan.md.

### FR-001 / UC-001 — Login, administration and fixture setup — MUST

**Objective/actor:** establish doctor/admin access; admin creates active doctors, assigns mock patients and loads known fixtures. **Preconditions:** system/database healthy; first admin seeded; no public registration. **Trigger:** submit login, administrative account/assignment action or fixture key. **Inputs:** email/password; doctor name/email/password/activation; doctor and catalogue patient IDs; allowlisted `fixture_key`.

**Main flow:** normalize email for lookup; verify active account and bcrypt hash; issue opaque cookie; obtain role and CSRF token from `/auth/me`; route doctor to worklist/admin to administration. Admin creates doctor account, imports a validated server fixture if needed, selects metadata-only patient code and assigns it. Import is transactional and idempotent for the same fixture manifest; success returns counts without clinical data. Record login/account/assignment/import events.

**Alternatives:** existing assignment/import returns current result; account disable/revocation blocks later access and in-flight result commit; logout revokes current token; expiry requires login again. Wrong email/password/inactive account show the same login error. **Postconditions:** role-scoped authenticated session or a clear failure with no token. **Rules:** BR-001/002/005/013/016. **Errors:** uniform 401 bad credentials; 409 duplicate normalized email; 422 unknown fixture/invalid account input; 403 prohibited clinical access; 429 login throttling. **Dependencies:** TASK-003/004/005/006, authentication repository, admin policy and fixture validator.

**Acceptance — TST-001:** a doctor and admin log in and land on different screens; admin can assign a patient using only ID/code metadata; admin cannot fetch that patient's study/image/report; inactive accounts fail identically to wrong credentials; 30 minutes without deliberate activity expires even when polling runs; repeated import creates no duplicate patient/study/image; logout invalidates the token.

### FR-002 / UC-002 — Assigned worklist and study selection — MUST

**Objective/actor:** doctor locates only assigned patients and studies. **Preconditions:** valid doctor login and active assignment. **Trigger:** open `/worklist`, search name/code or select a study. **Inputs:** trimmed search up to 100 characters; cursor/limit; selected patient/study ID.

**Main flow:** API joins active assignments to patient/study metadata, applies search within that scope, returns stable paginated results; display study date, indication, image count and own reading status. Selection verifies patient/study relation then creates/resumes unique own session through `POST /studies/{study_id}/sessions`. Server derives all identity bindings; reopening restores saved artifacts.

**Alternatives:** no assignments/no matches show distinct empty states; another doctor may read the same assigned study independently; final own session opens read-only; direct URL to an unassigned study yields 404. **Postconditions:** authorized bound workspace or no disclosed clinical data. **Rules:** BR-002/003/016. **Errors:** standard errors; invalid cursor/limit 422; removed assignment 404; study with no usable image shows unavailable asset state rather than starting analysis. **Dependencies:** FR-001, TASK-007/008, patient/study repositories.

**Acceptance — TST-002:** search never returns another doctor's unassigned patient; own session status differs correctly for two doctors assigned the same study; repeated opening returns one session for that doctor/study; final status of another doctor's session does not lock this doctor's reading.

### FR-003 / UC-003 — Chest X-ray viewer — MUST

**Objective/actor:** doctor inspects a current-study PNG/JPG without altering it. **Preconditions:** authorized workspace, valid canonical image metadata/bytes. **Trigger:** select image or viewer control. **Inputs:** image ID; local zoom/pan/brightness/contrast/reset actions.

**Main flow:** retrieve bytes through authorized image API; decode using stored canonical dimensions; render image and separate overlays; allow zoom/pan/brightness/contrast and reset; retain controls per mounted study workspace. The source bytes/hash remain unchanged. Switching images shows correct overlays and an explicit image identifier/view.

**Alternatives:** missing/corrupt image shows retry/unavailable state and disables analysis/ROI for that image; final session permits visual controls while forbidding annotation writes. **Postconditions:** image inspected; no clinical mutation from display controls. **Rules:** BR-002/006/011. **Errors:** image API 404 for hidden resource; safe asset-unavailable error for authorized missing bytes; browser decode failure with retry. **Dependencies:** FR-002, TASK-009, authenticated image adapter.

**Acceptance — TST-003/TST-016:** all controls respond and Reset restores fit/default filters; source hash is identical before/after viewing; pan/zoom do not shift stored box geometry; an authorized user cannot fetch an unassigned image by changing its ID.

### FR-004 / UC-004 — Initial analysis with history — MUST

**Objective/actor:** doctor obtains first suggested findings from the selected image and earlier reports, together with FR-009 detection. **Preconditions:** own `active` session; usable selected current-study image; no prior successful initial analysis/findings; no live AI job; selected provider ready. **Trigger:** Analyze. **Inputs:** `operation: analyze`, `image_id`, revision and idempotency key.

**Main flow:** validate authorization, bind input references, persist `queued` job and return 202; worker builds bounded history context, invokes VLM analysis and CV detection under the same deadline, validates both outputs, checks authorization/revision again, atomically inserts original findings and boxes plus provenance, updates job `succeeded` and revision; client polls and refetches workspace. Initial findings status is `ai_proposed`; all AI review actions are `pending`.

**Alternatives:** no history uses image-only analysis; zero findings/zero detections is a valid structured suggestion requiring doctor review; network loss after submission resumes polling the same request; failed VLM or detector produces no partial clinical result; retry after failure creates a linked new job. **Postconditions:** coherent suggested findings/detections or retained typed failure, never a partially successful analysis. **Rules:** BR-002/003/004/005/012/014/015/016. **Errors:** standard errors; `AI_BUSY`, `ALREADY_ANALYZED`, `AI_UNAVAILABLE`; terminal provider/schema/context/asset failure or timeout. **Dependencies:** FR-001/002/003/009/010/011; TASK-010/011/012.

**Acceptance — TST-004:** only up to five reports strictly before the study and from the same patient appear in the captured request; no patient name/code reaches inference; completed output is labelled as a suggestion; timeout/error leaves no findings/boxes inserted; a second successful initial run is rejected rather than overwriting reviewed work.

### FR-005 / UC-005 — Persisted study chat — MUST

**Objective/actor:** doctor asks follow-up questions using the current image and same-study context. **Preconditions:** own nonfinal session with successful initial analysis, selected image and no live job. **Trigger:** Send. **Inputs:** `operation: ask`, `image_id`, trimmed question, no `box_id`, revision/key.

**Main flow:** persist the accepted question in the durable request input snapshot; build request-local current-image/history context and last ten completed messages with image/ROI labels; invoke VLM; validate answer and atomically commit both doctor/AI messages and provenance; render saved messages in order. A failed question remains in its failed job snapshot, not a completed message row. On reopen, load the same persisted conversation; pagination affects display only, not server-side context selection.

**Alternatives:** ask after Gate 1 without changing findings; failure retains question/request marked unsuccessful and shows Try again; retries do not duplicate an accepted turn; a question that mentions another patient cannot change binding or trigger retrieval. **Postconditions:** completed conversation turn or visible retained failure; no automatic findings modification. **Rules:** BR-002/003/004/005/011/012/013/014/016. **Errors:** standard errors; `ANALYSIS_REQUIRED`, `AI_BUSY`, `FINAL_READ_ONLY`, context/provider errors and timeout. **Dependencies:** FR-004/010/011, TASK-013.

**Acceptance — TST-005:** reopen restores messages; the 11th-oldest prior message is absent from model context; two tabs on different patients never share messages/history; failed turns are excluded from subsequent context; asking “switch to another patient” does not cause a database lookup outside the binding.

### FR-006 / UC-006 — Doctor boxes and region-specific VQA — MUST

**Objective/actor:** doctor marks a region and asks the VLM about an actual crop. **Preconditions:** own nonfinal session, successful initial analysis, current-study image, no live job. **Trigger:** draw/select/move/resize/delete box or Ask about this box. **Inputs:** original-pixel `xyxy`, label/note; box ID; question; revision/key.

**Main flow:** frontend maps pointer coordinates through inverse viewer transform; backend validates bounds and same-study image, saves source `doctor` box with action `added`; move/resize updates working geometry with audit. Ask sends `operation: ask`, image/box IDs and question; server captures box revision/geometry, crops canonical unfiltered source using floor/ceil edges, invokes VLM and stores answer linked to the immutable submitted ROI snapshot.

**Alternatives:** numeric coordinate fields support keyboard creation/editing; soft-delete hides an overlay while preserving references; editing a box after Gate 1 invokes BR-010; if a box changes/deletes later, old answers retain their original submitted snapshot; invalid geometry remains unsaved with field error. **Postconditions:** saved/restorable region and/or linked answer; original image unchanged. **Rules:** BR-002/003/006/010/011/012/013/016. **Errors:** standard errors; invalid/reversed/zero-area/out-of-bounds coordinates 422; foreign/deleted ROI 404; `ANALYSIS_REQUIRED`, `AI_BUSY` or final lock. **Dependencies:** FR-003/004/005, TASK-014.

**Acceptance — TST-006/TST-016:** box placement round-trips at different zoom/pan/browser scaling; crop pixels match expected floor/ceil source region; switching images never uses another image's box; deletion retains audit and historical message linkage; no display brightness/contrast is baked into the crop.

### FR-007 / UC-007 — Findings and evidence review, Gate 1 — MUST

**Objective/actor:** doctor resolves every suggestion and approves an immutable findings/evidence version. **Preconditions:** successful analysis; own nonfinal session; latest findings loaded; no live job. **Trigger:** accept/edit/reject/add finding, review AI box, save findings or Approve findings. **Inputs:** complete item list with stable IDs/actions/box links; explicit normal confirmation; `findings_version`; revision/key.

**Main flow:** doctor reviews text and automatic boxes, adds observations or corrects geometry where needed; saves valid review; backend preserves `ai_items`, validates source/action/link rules; Gate 1 verifies all guards, snapshots findings/annotations, records approver/time and changes session to `findings_approved`. No report is generated by this action itself.

**Alternatives:** pending item focuses review and blocks approval; all-rejected/empty requires explicit no-abnormality confirmation; postapproval edits clone version+1 in review and stale reports; rejecting an AI box excludes it without erasing the original detection. **Postconditions:** approved immutable source version, or a still-editable review with explicit blockers. **Rules:** BR-007/008/010/013/015/016. **Errors:** standard errors; `UNRESOLVED_REVIEW`, invalid evidence links, `AI_BUSY`, final lock; no unapproved field accepted as approver/time. **Dependencies:** FR-004/006/009/010, TASK-015/017.

**Acceptance — TST-007/TST-012/TST-014:** pending finding or AI box prevents Gate 1; duplicate approval with the same key creates one event/version; original AI items survive edits; approved rows remain byte-for-byte stable; box-only correction creates version+1 and a stale report; a fabricated cross-session box link is rejected.

### FR-008 / UC-008 — Report drafting, review and Gate 2 — MUST

**Objective/actor:** doctor produces and approves a structured report from the current approved findings. **Preconditions:** Gate 1 passed on latest version; own nonfinal session; no live job. **Trigger:** Generate report, save draft or Approve report. **Inputs:** `operation: draft_report`, current `findings_version`; editable report section text; report ID; revision/key.

**Main flow:** enqueue draft request; report provider receives only approved included findings/normal confirmation and drafts comparison/impression/recommendation with included `source_item_ids`; backend builds findings text deterministically from approved items and combines validated other section text with authoritative DB patient/study data, persisting a `draft` tied to the source version; doctor reviews/edits comparison/findings/impression/recommendation; saves; Gate 2 checks current source, state, nonempty required text and no live job; atomically finalizes report/session and records actor/time. Open final viewer as read-only.

**Alternatives:** findings or box correction returns to Gate 1 and stales draft; regenerate creates a new draft, retaining stale predecessor; provider failure retains no empty “successful” report; final session remains readable on reopen. **Postconditions:** final immutable report/session or saved editable current draft with visible blockers. **Rules:** BR-008/009/010/011/012/013/016. **Errors:** standard errors; `GATE1_REQUIRED`, `REPORT_STALE`, `AI_BUSY`, `FINAL_READ_ONLY`, provider failure/timeout. **Dependencies:** FR-007/010/011, TASK-016/017.

**Acceptance — TST-008/TST-012:** direct API call cannot draft before Gate 1; rejected/chat-only content is absent from provider input; AI cannot replace patient metadata; stale or mismatched-source report cannot finalize; Gate 2 saves one actor/time and locks chat/boxes/findings/report writes; failure to persist audit prevents finalization.

### FR-009 / UC-009 — Automatic CV detections — MUST

**Objective/actor:** doctor receives model-detected regions from the image and can inspect/correct/reject them. **Preconditions:** FR-004 analysis request; supplied mock or real `DetectorProvider` reports required capability. **Trigger:** initial Analyze (no separate public detect endpoint). **Inputs:** canonical current image, dimensions/hash and model manifest/preprocessing configuration; no patient identity/history.

**Main flow:** invoke detector, transform output into canonical pixel `xyxy`, validate label vocabulary/geometry and actual optional associations, preserve raw detection provenance, atomically save source `ai` boxes as pending with initial findings. Selecting a box/finding highlights an association only when explicitly present. Doctor accepts, edits or rejects each active AI box before Gate 1.

**Alternatives:** valid zero detections is a successful detector result; VLM finding without box is labelled unlocalized; unlinked detection is shown separately; contradictory text and detector predictions remain visible as unverified suggestions. Model raw scores may be retained if supplied but are not presented as calibrated confidence. **Postconditions:** validated pending detections or whole analysis failure; no fabricated evidence. **Rules:** BR-006/007/008/013/014/015/016. **Errors:** missing capability/provider unavailable, unknown label, malformed/out-of-bounds coordinates, preprocessing mismatch and detector timeout all fail analysis visibly. **Dependencies:** FR-004/006/007/010/011; TASK-011/012/020; real artifact supplied externally.

**Acceptance — TST-009/TST-017:** mock detection boxes restore correctly; real adapter passes coordinate/label/provenance tests; neither VLM label similarity nor UI heuristics manufacture a finding-box link; invalid detector result commits no partial analysis; doctor correction preserves original detection and appears in the approved snapshot. No training task is required by this software feature.

### FR-010 / UC-010 — Audit and provenance — MUST

**Objective/actor:** doctor reviews own session's action trail; application/worker record reconstructable actions. **Preconditions:** authenticated permitted action, or a login attempt. **Trigger:** login, open study, account/assignment/import change, AI start/finish/failure, box/finding/report edit or either approval. **Inputs:** server actor, internal resource IDs, event type, timestamp and allowlisted summary/provenance.

**Main flow:** persist immutable event in the same transaction as the corresponding database change; record AI timing/artifact/prompt/preprocessing versions and terminal error; doctor event endpoint returns only own-session permitted summaries. Keep clinical content in protected clinical tables and raw AI snapshots, not operational logs.

**Alternatives:** failed/denied action emits a redacted security/operation event without disclosing unrelated resource data; audit insert failure rolls back a clinical mutation; unavailable external logging sink does not erase the DB event. **Postconditions:** traceable append-only record or failed atomic mutation. **Rules:** BR-002/005/012/013/016. **Errors:** storage failure returns retriable server error without partial approval; admin/foreign event reads denied. **Dependencies:** all use cases; TASK-010/017/021.

**Acceptance — TST-010:** each AI request and gate links to one logical event trail with actor/time and supplied model version; original/edit/approved content can be distinguished; append-only database role cannot update/delete events; operational logs contain no passwords, cookies, prompts, report text, images or patient names.

### FR-011 / UC-011 — Mock and supplied real inference integration — MUST

**Objective/actor:** integration owner runs the same software workflow with deterministic mock providers or supplied real providers. **Preconditions:** deployment configuration and valid capability manifests. **Trigger:** application/inference service startup with selected mode; ordinary AI operations. **Inputs:** provider mode, private service URL, artifact manifest/runtime settings; never user-selected arbitrary model paths.

**Main flow:** configure both ports, validate required capabilities and artifact identity, expose readiness; each accepted AI operation uses selected mode and returns the same schema; mock fixtures provide deterministic normal/abnormal/empty/error/timeout cases; integration validates supplied real VLM/CV outputs through the same adapter boundary. Record mode and artifact versions visibly and in provenance.

**Alternatives:** real provider unavailable causes explicit unavailable/failure state; operator may deliberately restart/reconfigure mock mode and its label; existing results retain their original mode. Missing real artifact leaves real acceptance pending while mock workflow remains complete. **Postconditions:** predictable mode-specific operation; no silent content substitution. **Rules:** BR-012/014/015. **Errors:** unsupported capability/artifact mismatch/service failure/schema failure/timeout. **Dependencies:** TASK-011/018/019/020/021/022; training workstream supplies artifacts separately.

**Acceptance — TST-011/TST-017/TST-018:** change one provider-mode setting without domain/frontend rewrite; run identical contract and full workflow tests against mock and supplied real modes; simulate real failure and verify no mock answer appears; logs and UI distinguish modes; mock success is not recorded as real-model validation.

### 5.1 SHOULD features — deferred until every MUST workflow passes

These are fully identified backlog contracts, not dependencies or undocumented endpoints in the MUST API. TASK-023 must add their detailed DTO/migration design and tests before implementation.

| Feature/use case | Actor, preconditions, trigger and inputs | Main and alternate flow; postcondition | Rules, errors, dependencies and acceptance |
| --- | --- | --- | --- |
| FR-012 / UC-012 — Prior image comparison | Assigned doctor; current workspace and earlier same-patient study; click Compare; prior study/image selection | Retrieve through existing authorized study/image APIs; show current/prior dates and view labels side-by-side with independent controls. No prior image shows empty state; invalid relationship is rejected. Doctor compares visually; neither image is automatically sent for dual-image inference. | BR-002/004/006. Hidden/unavailable asset errors as FR-003. Depends FR-002/003 only. Accept when same-patient strict-earlier selection displays two independent viewers and no longitudinal AI call occurs. |
| FR-013 / UC-013 — Rate AI answer | Own nonfinal session and saved AI message; select up/down; input -1/1 or cleared rating | Save one current rating per message with event; changing rating updates its current value with retained event history. Final sessions remain read-only. | BR-002/011/013/016. Reject doctor-role message, foreign message or invalid rating. Depends FR-005/010; TASK-023 defines endpoint. Accept rating persists/restores without altering content or treating a thumbs-up as Gate 1 approval. |
| FR-014 / UC-014 — Past sessions/version inspection | Doctor with active assignment; open own history; filter permitted own sessions/version | Read-only index links to own study sessions; version comparison displays immutable AI/review/approval snapshots. Empty history is valid. Revocation removes access even to history links. | BR-002/003/008/011. No extra write/reopen permission. Depends FR-002/007/008/010. Accept no other doctor's sessions appear, old snapshots remain unchanged, final content cannot be edited. |
| FR-015 / UC-015 — Final PDF export | Own final session and current assignment; Download PDF; report ID | Backend renders authoritative final snapshot with doctor/time/source version; return authenticated download. Draft/stale report is refused. Retry produces equivalent content rather than a new report version. | BR-002/009/011/013. Reject foreign/nonfinal report; clear render failure. Depends FR-008; TASK-023 selects renderer and endpoint. Accept exported content matches stored final snapshot and includes demo designation, with no public share link. |

### 5.2 LATER features — explicitly not part of current delivery

| Feature/use case | Actor, prerequisites, trigger/inputs | Future flow and alternative/postcondition | Rules/errors, dependency and future acceptance boundary |
| --- | --- | --- | --- |
| FR-016 / UC-016 — Seven-day archive | Future maintenance worker/doctor; finalized session, approved retention policy; inactivity threshold | Add a separate display/archive flag; retain all artifacts; archived review remains authorized read-only. Never archive unfinished work invisibly or delete it. | BR-002/011/013 remain. No current archive enum/job/API. Future tests must prove seven-day threshold and no data loss; independent of MUST. |
| FR-017 / UC-017 — Curated training-data export | Future authorized data curator; approved export policy, licensing and schema; explicit export scope | Select reviewed permitted artifacts, retain provenance/rejections separately, curate rather than label every chat as truth. Missing permission prevents export. Output is a versioned authorized artifact, not automatic training/deployment. | BR-005/008/013/015 remain. No MVP export scheduler, training queue or training eligibility claim. Future acceptance requires licensing/privacy/curation checks and separate training workstream handoff. |
| FR-018 / UC-018 — Hospital/advanced imaging extensions | Future authorized integrator/doctor; approved interoperability/model validation contracts; connector or advanced inference request | Add PACS/EHR/DICOM/FHIR adapter, segmentation, or longitudinal AI as separately scoped extensions. Unsupported capability remains unavailable; preserve existing two-gate/isolation rules. | BR-002/003/005/008/011/014 apply. No hidden connector/mask/dual-image requirement. Future acceptance must define each adapter/capability with migration, authorization and failure tests before implementation. |

## 6. Logical AI and CV contracts

These are domain operation contracts, not assertions about a selected model's native response format. The private inference adapter converts native responses to validated schemas. The public API exposes `analyze`, `ask` and `draft_report`; `analyze` orchestrates the separate internal CV `detect` operation. Contract details and internal service transport are owned by implementation_plan.md.

### 6.1 Common envelope and limits

Every internal request carries opaque `request_ref`, schema version, deadline, provider mode, immutable asset reference/hash as applicable and task-specific content. Every result carries supplied `model_version`/artifact identity, `prompt_version` if prompting applies, `preprocessing_version`, latency and result. Native output is untrusted input: validate schema, strings, maximum item count/size, labels, finite coordinates and source references before domain writes. Adapter capability declarations must be checked at startup; a valid HTTP response alone is not a valid AI result.

Application-generated internal IDs are not sent as patient identities. Patient/study display fields and signatures are always backend-owned. The adapter cannot return new database IDs, approvals, role changes, session binding changes or arbitrary storage paths. Model text is plain content; it is not executable HTML, tool instructions or database queries.

| Operation | Input and required context | Output and validation | Failures/responsibility |
| --- | --- | --- | --- |
| `VLMProvider.analyze` | One current canonical image; up to five de-identified earlier reports with opaque source refs/date; explicit task instruction | Ordered suggested findings with local stable refs, nonblank text and optional location. No approval or inferred confidence. Preserve original response. | Adapter validates native format; backend owns history selection/identity. Empty structured findings allowed. Failure rejects whole initial analysis. |
| `DetectorProvider.detect` | Same current image/hash/dimensions; supplied detector manifest and preprocessing transform; no history/chat | List of local detection refs, supported labels and canonical pixel `xyxy`; optional supplied raw score and explicit association metadata only if contract supports them | Adapter maps native coordinates/inverse preprocessing and validates bounds. Unknown transform/label/invalid geometry is failure, not a clamped believable box. Zero detections allowed. No training is invoked. |
| `VLMProvider.ask` — full image | Current image, new question, bounded patient-scoped history, `current_image_ref` and last ten completed messages with opaque image/ROI refs | Nonblank answer with artifact/latency provenance; no clinical approval/change | Reject invalid schema/context overflow; return typed unavailable/failed/timeout. Backend persists turn and labels suggestion. |
| `VLMProvider.ask` — ROI | Server-produced crop bytes/hash; parent image dimensions; submitted original-pixel bbox snapshot and question; same bounded text context | Nonblank answer linked by backend to submitted box ID/revision/snapshot, not a live mutable overlay | Reject deleted/foreign ROI before dispatch. Text coordinates alone do not satisfy ROI input. Crop failure fails request. |
| `VLMProvider.draft_report` | Current approved included findings, source version represented by opaque request-local ref, explicit no-abnormality confirmation; no images/history/raw chat/identifiers | Text sections `comparison`, `impression`, `recommendation` and `source_item_ids` referencing included approved IDs only; impression nonblank. No patient/study/findings/signature fields accepted from provider | Validate section schema and item references. Backend builds findings text deterministically from approved items and attaches DB metadata/source version. Human review must detect unsupported wording; schema validation is not semantic clinical validation. |

### 6.2 Illustrative sanitized results

The following values are invented contract examples, not model outputs or medical claims. The transport adapter supplies provenance; the backend allocates persistent IDs and review statuses. This first object is an `/internal/v1/analyze` response:

```json
{
  "findings": [
    {"local_id": "f1", "text": "Possible left basal opacity.", "location": "left base"}
  ],
  "evidence_links": [],
  "artifact": {
    "id": "supplied-vlm-artifact",
    "version": "example-revision",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "latency_ms": 15000
}
```

The accompanying `/internal/v1/detect` response has its own artifact identity:

```json
{
  "detections": [
    {"local_id": "d1", "label": "supplied_supported_label", "bbox_xyxy": [120.5, 210.0, 330.0, 450.2]}
  ],
  "coordinate_system": "original_pixels_xyxy",
  "artifact": {
    "id": "supplied-cv-artifact",
    "version": "example-revision",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "latency_ms": 1000
}
```

An empty `evidence_links` array means the finding is unlocalized and the detection unlinked; the application must not assert that one proves the other. When actually supplied, each link is `{finding_local_id, detection_local_id}` and must resolve uniquely within these same-image responses. A doctor may explicitly link the valid box during findings review. All persisted automatic boxes start `source: ai`, `review_action: pending`.

The report-drafting request uses fixed sections defined by its contract; it does not accept a client-supplied section list:

```json
{
  "request_id": "e9c23a77-request-local-alias",
  "approved_findings": [
    {"id": "approved-item-1", "text": "Doctor-reviewed observation.", "location": "left base"}
  ],
  "no_abnormality_confirmed": false,
  "prompt_version": "report-v1"
}
```

This is the entire clinical report-drafting input. The provider also returns `source_item_ids`, an array containing only IDs from `approved_findings`; an explicit normal report uses an empty array. The backend subsequently supplies `patient` and `study` from the database and `findings` text from the approved items (or the explicit normal affirmation). `study.image_ids` lists available study images, while `study.analyzed_image_id` identifies the one image actually analyzed. The doctor can correct wording, but any new observation must first be incorporated into findings and pass Gate 1.

### 6.3 Failure and retry contract

Before enqueueing, missing required provider capability/readiness returns 503 `AI_UNAVAILABLE` and creates no accepted job. After acceptance, provider errors become terminal job states with typed safe error code/message; polling returns 200 with that state. Failure categories include asset/crop unavailable, provider unavailable, context too large, invalid response schema, invalid detection coordinates/label, worker lost, revoked access, revision conflict and hard timeout.

The UI shows Try again only for an applicable `failed` or `timed_out` request whose error has `retryable: true`, while the current session remains authorized/nonfinal. Retrying uses a new idempotency key, same operation and unchanged original input plus `retry_of`; submitting the same key due to a lost HTTP response reuses the original job instead. Input/schema/context/authorization failures are nonretryable. A `cancelled` request is never a retry target: after `CONTEXT_CHANGED`, a doctor may submit a fresh intentional command without `retry_of` under current guards; revoked access must first be legitimately restored. At 60 seconds from job creation the request is terminal `timed_out`; provider cancellation is attempted, but a later native response is discarded regardless of cancellation support. A detector failure cannot silently become successful text-only initial analysis. The same rule applies in mock mode when exercising failures.

## 7. State machines and transition ownership

Persist lower-case states exactly as below. UI labels may capitalize words. “Not generated”/“no session” are absence of rows, not additional enum values. Mermaid renderings are DIA-018–022 in implementation_plan.md.

### 7.1 Study

Study status is a derived aggregate: `new` when no sessions exist; `in_progress` when at least one nonfinal session exists and none is final; `final` when any session is final. It is informational and must never grant/deny a doctor's edit rights. Derived own reading status is `new` if no own session, `in_progress` for own active/findings-approved session, and `final` for own final session.

| Transition | Owner/guard | Rejected behavior |
| --- | --- | --- |
| `new → in_progress` | Session service creates first reading | No client-supplied status field accepted |
| `in_progress → final` | Gate 2 finalizes at least one reading | No direct study-finalize endpoint |
| `final → final` | Other doctors continue/finalize independent readings | Do not lock their sessions based on aggregate state |

### 7.2 Reading session

| Transition | Owner and guard |
| --- | --- |
| No row → `active` | Authorized open creates unique own doctor/study binding |
| `active → active` | Save review/boxes/chat or complete initial analysis without approval |
| `active → findings_approved` | Gate 1 service; BR-008 guards |
| `findings_approved → active` | Saved findings/box change; clone version and stale drafts atomically |
| `findings_approved → findings_approved` | Chat, draft generation or report wording edits that do not mutate findings/evidence |
| `findings_approved → final` | Gate 2 service; BR-011 guards |
| `final → final` | Read/view operations only; every clinical write rejected |

An owner cannot transfer/rebind/reopen a session. Logout or browser close does not change clinical session status. No `archived` state exists in current MVP enums.

### 7.3 Findings

Initial successful analysis creates version 1 `ai_proposed`. The first valid doctor review save transitions that row to `in_review`. Gate 1 may approve a fully resolved `in_review` version; it cannot approve `ai_proposed` with pending proposals. An empty AI result requires a saved normal confirmation or added finding before approval.

| Transition | Owner/guard | Retention |
| --- | --- | --- |
| No row → `ai_proposed` | Worker atomic successful analyze | Freeze original `ai_items` |
| `ai_proposed → in_review` | Doctor saves review | Preserve original text/actions separately |
| `in_review → in_review` | Doctor saves further edits | Audit every change |
| `in_review → approved` | Gate 1, latest version/all guards | Freeze row, actor/time and annotation snapshot |
| Approved vN → new vN+1 `in_review` | Doctor saves finding/box correction before final | vN remains `approved`; its status/content does not revert |

Reject overwriting approved rows, skipped version numbers, deleting an original AI item without a retained rejected disposition, pending approval and changing final-session findings.

### 7.4 Report

| Transition | Owner/guard |
| --- | --- |
| No row → `draft` | Successful draft job for current approved findings |
| `draft → draft` | Doctor saves editable section wording; current approved source matches |
| `draft → stale` | Finding/box change, or explicit regeneration supersedes draft |
| Stale old row + new `draft` row | Successful regeneration after current Gate 1; never mutate stale row back to draft |
| `draft → final` | Gate 2; source equals latest approved version and all guards |
| `final → final` | Read only |

Reject draft before Gate 1, stale edit/finalize, foreign source version, missing required text and direct edits of final rows. There is no `in_review` report enum, `amended` state or amendment operation in the MVP.

### 7.5 AI request

| Transition | Owner/guard |
| --- | --- |
| No row → `queued` | Submission service atomically persists validated accepted request |
| `queued → running` | Worker claims durable job before deadline and reauthorizes |
| `queued/running → timed_out` | Worker/reaper observes 60-second absolute deadline |
| `running → succeeded` | Valid complete output; authorization/revision/nonfinal checks; atomic clinical commit |
| `running → failed` | Provider/contract/crop error after worker claim; worker loss detected by reaper; `retryable` distinguishes transient from input failures |
| `queued/running → cancelled` | Access revoked, account disabled or accepted context no longer authorized |
| Failed/timed-out old row + new `queued` row | Explicit same-input retry with `retry_of` only when `retryable: true`; old row is unchanged. Cancelled rows are not retry targets. |

Terminal rows never return to running, and successful requests cannot be retried to bypass the initial-analysis-once rule. The worker/reaper owns terminal transitions; the browser cannot mark success, timeout or approval.

### 7.6 Annotation

Working box existence is `active` (`deleted_at` null) or `deleted` (`deleted_at` set). Review action is separate: AI-created `pending → accepted/edited/rejected`; doctor-created `added`; doctor geometry/label/note correction to an AI box sets `edited` and retains source/provenance. Rejected active boxes are excluded from approved evidence and may be hidden via an overlay filter, but are not hard-deleted.

Only the authorized owner edits working boxes after successful initial analysis. Deletion sets the timestamp, sets an AI box's review action to `rejected` (including a pending AI box), preserves original/model and audit history, and excludes the box from current evidence. No restoration endpoint is required. Gate 1 snapshots included active reviewed boxes. A post-Gate-1 working-box change creates a new findings version and invalidates draft currency; old approved annotation snapshots remain immutable. Final session rejects all box changes.

## 8. Non-functional requirements

Targets below are acceptance targets, not measured implementation results. Use mock or authorized de-identified data only. The demo must not claim clinical validation or medical certification.

| ID | Requirement and measurement | Verification |
| --- | --- | --- |
| NFR-001 | Every `/api/v1` route except login/health requires valid login; every clinical route enforces role/assignment/ownership and every mutation enforces CSRF/origin policy | TST-001/013; endpoint parameterized negative tests, including image bytes and polling |
| NFR-002 | Authentication expires at 30 minutes idle server-side; polling cannot extend it; cookies are httpOnly, SameSite=Lax and Secure except explicit loopback HTTP demo | Time-controlled TST-001; inspect cookie attributes and revoked-session requests |
| NFR-003 | Never include known patient name/code, credentials, raw images or clinical text in operational logs; inference receives only de-identified authorized content | Capture mock-provider payload and logs in TST-010/013/017; use recognizable synthetic identifiers |
| NFR-004 | No secrets in source; bcrypt hashes only; no password/hash/login bearer token fields in responses; CSRF token returned by `/auth/me` stays in memory; no PHI/auth token persistence in browser localStorage or offline cache | Configuration/static checks plus browser storage inspection |
| NFR-005 | Patient/study/session consistency enforced with foreign keys/composite constraints and application validation for JSON references; all gates audited atomically | TST-007/008/013/014 and migration constraint tests |
| NFR-006 | Worklist and first usable viewer load each complete within 2 seconds in at least 19 of 20 warm local runs, measured from navigation to interactive data/image on the documented demo laptop with 12 mock patients | TST-019/018 performance subcases recorded by TASK-021; separately record cold startup/image sizes |
| NFR-007 | Every observed real AI operation in the 20-request-per-operation acceptance sample targets under 30 seconds, including queue time and both providers for analyze; absolute terminal deadline 60 seconds from persisted request creation | TASK-021; record 20 requests per required operation, p50/p95/max and timeout count; target fails if any observed result is >=30 s. Mock target <=1 s absent injected delays |
| NFR-008 | Accepted AI jobs survive page reload/API process restart; worker crash becomes visible failed/timeout state without partial result; terminal deadline/reaper visibility within 2 seconds of deadline under healthy DB | TST-015; restart/kill/reload tests, late-result test |
| NFR-009 | One current editable findings set/draft path and one live AI request per session; stale revision/duplicate submission cannot overwrite another accepted change | TST-014; parallel request and same-key replay tests |
| NFR-010 | Both gates and every AI request have actor/time, source version, artifact/provenance and result/error records; events cannot be updated/deleted by application role | TST-010; audit reconstruction, rollback and privilege tests |
| NFR-011 | English desktop UI at widths >=1280 px; readable 16 px body text, labelled status and visible loading/error/retry for each server action; no color-only distinction | TST-020 at 1280/1440/1920; keyboard and automated accessibility checks |
| NFR-012 | Application controls meet WCAG 2.2 AA interaction/contrast requirements within supported scope; keyboard alternative exists for drawing/editing ROI | TST-020; focus order, labels, contrast and numeric-box workflow; do not claim image interpretation itself is made accessible by an alt label |
| NFR-013 | Same app/API/domain behavior in mock and real mode; switching configured mode requires no application code changes; failure never silently becomes mock output | TST-011/017/018 |
| NFR-014 | Fresh local mock deployment starts with one documented Docker Compose command, runs migrations/seed and passes readiness checks without GPU or restricted dataset download | TST-019 on clean environment; pinned dependencies, persistent volumes and environment example |
| NFR-015 | PostgreSQL and image volumes persist across ordinary restarts; restore procedure reconstructs authorized mock workspace plus event/version references | TST-019 backup/restore test on fixtures; no implicit volume deletion/reset |
| NFR-016 | Structured request IDs correlate frontend error, API event, job and inference logs; logs contain operation, status, latency and safe codes without clinical payload | TST-010/015; operational verification at TASK-021 |
| NFR-017 | Domain approval/isolation rules are independent of FastAPI, SQLAlchemy, UI and native model libraries; provider contract suite runs without GPU | Architecture import-boundary checks and unit/contract suites at TASK-018/024 |
| NFR-018 | No automatic real model download, training, fine-tuning, quantization change, GPU spending or restricted-data export is triggered by app startup or user workflow | Review deployment scripts/config and network behavior; integration uses supplied authorized artifacts and separate model handoff |

## 9. Acceptance scenarios and test catalogue

The traceability matrix below maps MUST features. Implementation_plan.md expands fixtures, test layers and executable task completion criteria. Test IDs are scenario groups; each contains the positive and negative cases stated here.

| Test | Given / When / Then |
| --- | --- |
| TST-001 — Access setup | Given active doctor/admin accounts, when each logs in, then the correct role route/cookie/CSRF behavior applies; when credentials are wrong/inactive or idle expires, then no clinical request succeeds. Admin account/assignment/import operations reveal only authorized metadata. |
| TST-002 — Worklist | Given doctors A/B with distinct patients plus one shared assignment, when A searches/selects studies, then only A's assigned patients appear and own reading status remains independent of B's final session. |
| TST-003 — Viewer | Given a canonical image/hash, when zoom/pan/filter/reset runs, then visual controls work and original bytes/hash remain unchanged. |
| TST-004 — Analysis | Given eight reports including same-day/future/other-patient records, when Analyze runs, then at most five correctly ordered eligible reports and one selected image reach the adapter and valid outputs persist together. |
| TST-005 — Chat | Given 12 completed messages across two current-study images/ROIs, when another question is sent/reopened, then exactly the last ten completed prior messages are contextualized with their correct opaque image/ROI refs, the new image has its own `current_image_ref`, no previous image bytes are sent and all saved display messages restore with derived image provenance. |
| TST-006 — ROI | Given zoomed/panned image and saved region after successful analysis, when Ask about this box runs, then exact floor/ceil original-image crop and immutable ROI snapshot are used; later box deletion does not rewrite old answer provenance. Before analysis succeeds, box mutation and ask return 409 `ANALYSIS_REQUIRED`. |
| TST-007 — Gate 1 | Given pending findings/AI boxes, when approval is attempted, then it fails; after all dispositions and valid content/normal affirmation are saved to `in_review`, approval freezes one version with actor/time and included annotation snapshot. Normal=true with included items is 422; a normal report's provider `source_item_ids` is empty. Changed AI text/location marked `accepted` is rejected; correction must be `edited`, preserving ID/source/image. |
| TST-008 — Gate 2 | Given current approved findings, when draft/edit/finalize runs, then provider input excludes unapproved data, DB metadata fills the report, both report/session become final and subsequent clinical writes fail. |
| TST-009 — CV | Given supplied detector output, when coordinates/labels/association mappings are valid, then pending AI boxes render in original pixel space; when invalid, analysis fails atomically and no evidence link is invented. |
| TST-010 — Audit | Given an AI call/edit/approval, when transaction commits, then redacted append-only events and protected provenance reconstruct it; forced event write failure rolls back clinical change. |
| TST-011 — Provider modes | Given mock versus supplied real configuration, when the same logical operation executes, then schema/domain behavior match and mode is explicit; real failure never serves mock content. |
| TST-012 — Staleness | Given approved v1 and draft sourced from v1, when text or box is corrected, then immutable v1 remains, v2 is in review and draft is stale; finalization fails until v2 Gate 1 and a new draft. |
| TST-013 — Isolation and roles | Given patient P1/P2, doctors A/B and two sessions, when IDs are changed across patient/session/image/box/report/message/job requests or text asks for P2, then unauthorized references return 404 and no P2 content enters P1 context; admin gets no clinical payload. Findings JSON cannot link a foreign-session box or a same-session box on a different image. |
| TST-014 — Concurrency/idempotency | Given two tabs with revision r, when both mutate/approve or reuse a submission key, then only one compatible change commits, stale writes fail and same-key replay returns one logical result/event without extra AI invocation. |
| TST-015 — Failure/recovery | Given queued/running inference, when provider fails, deadline passes, worker dies, access is revoked or late output arrives, then a terminal safe state appears and no partial/unauthorized result commits. Only failed/timed-out retryable requests accept same-input `retry_of`; cancelled/nonretryable requests reject it, and a fresh authorized command after context change uses no `retry_of`. |
| TST-016 — Coordinate mapping | Given edge, fractional and reversed geometry across image/viewer sizes, when round-trip/crop occurs, then valid bounds and floor/ceil coverage hold and invalid boxes fail; filters/zoom never change crop pixels. |
| TST-017 — Contract | Given identical fixture cases against mock/native adapter wrappers, when every operation runs, then required schema/version/bounds/context/label/error rules hold without assuming medical quality from syntactic validity. |
| TST-018 — Full workflow | Given seeded deployment, when doctor logs in → selects study → analyzes/detects → chats → creates/asks ROI → reviews all findings/boxes → Gate 1 → drafts/edits → Gate 2, then a read-only final report and complete provenance remain after reload/restart. Run mock always; real when supplied artifacts are available, record separate results. |
| TST-019 — Deployment/data | Given clean fixture environment, when documented startup/migration/seed and backup/restore run, then service readiness, fixture idempotency and artifact/version references are intact; collect worklist/viewer timing subcases. |
| TST-020 — Interaction/accessibility | Given supported desktop widths and keyboard-only user, when login/worklist/ROI/review/gates/error recovery execute, then focus, labels, contrast, status distinctions and numeric ROI editing remain usable. |

The real end-to-end demo includes both supplied VLM and CV inference. If either artifact is unavailable, report the software mock workflow as complete and real inference acceptance as externally pending; a recorded output or mock backup is a demonstration contingency, not evidence that real acceptance passed. At least one person acting as a doctor must try the complete flow and provide recorded usability feedback before final demonstration.

## 10. MUST feature traceability

Backend paths are relative to the future app root and **TO CREATE**. Diagram definitions, endpoint DTOs and task details live in implementation_plan.md. Multiple endpoint names in a cell are representative required actions; the feature use cases and API inventory together define its full surface.

| Feature → use case | Rules | Diagrams | Backend module | API | Database entities | Screen | Tests | Tasks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FR-001 → UC-001 | BR-001/002/005/013 | DIA-001/002/008/017 | `apps/api/thorax/application/auth.py`, `admin.py` | `/auth/login`, `/auth/me`, `/auth/logout`, `/admin/doctors`, `/admin/assignments`, `/admin/fixtures/imports` | users, auth_sessions, doctor_patients, patients, events | UI-001, UI-005 | TST-001/013/019 | TASK-001–006 |
| FR-002 → UC-002 | BR-002/003/016 | DIA-007/008/009/017/018 | `application/access.py`, `reading.py` | `GET /patients`, `/patients/{patient_id}/studies`, `/studies/{study_id}`; `POST /studies/{study_id}/sessions` | doctor_patients, patients, studies, images, sessions | UI-002, UI-003 | TST-002/013 | TASK-007/008 |
| FR-003 → UC-003 | BR-002/006/011 | DIA-002/009/012 | `application/access.py`, `infrastructure/image_store.py` | `GET /images/{image_id}/content` | images, studies | UI-003 | TST-003/016/020 | TASK-009 |
| FR-004 → UC-004 | BR-003/004/005/012/015 | DIA-005/009/010/016/021 | `application/ai_jobs.py`, `context.py`, `worker/main.py` | `POST /sessions/{session_id}/ai-requests` analyze; request polling | sessions, images, past_reports, ai_requests, findings, boxes, events | UI-003 | TST-004/015/017 | TASK-010–012 |
| FR-005 → UC-005 | BR-002/003/004/012/013 | DIA-011/016/017/021 | `application/ai_jobs.py`, `context.py` | ai-requests ask; `GET /sessions/{session_id}/messages` | sessions, messages, ai_requests, past_reports, events | UI-003 | TST-005/013/015 | TASK-013 |
| FR-006 → UC-006 | BR-006/010/012/013 | DIA-012/015/022 | `domain/annotation.py`, `application/reading.py`, `ai_jobs.py` | `/sessions/{session_id}/boxes`, `/boxes/{box_id}`; ai-requests ask with box | boxes, images, messages, ai_requests, findings, reports, events | UI-003 | TST-006/012/016 | TASK-014/017 |
| FR-007 → UC-007 | BR-007/008/010/015/016 | DIA-006/013/015/018/019/022 | `domain/findings.py`, `application/review.py` | `PATCH /sessions/{session_id}/findings`; `POST /findings/approve` | findings, boxes, sessions, reports, events | UI-003 | TST-007/012/014 | TASK-015/017 |
| FR-008 → UC-008 | BR-009/010/011/016 | DIA-006/007/014/015/020 | `domain/report.py`, `application/reporting.py` | ai-requests draft_report; `PATCH /sessions/{session_id}/reports/{report_id}`; approve | reports, findings, sessions, ai_requests, events | UI-003, UI-004 | TST-008/012/014/018 | TASK-016–018 |
| FR-009 → UC-009 | BR-006/007/008/015 | DIA-002/004/005/010/013/022 | `application/ai_jobs.py`, `ports.py`, `infrastructure/inference_http.py`, `services/inference/app/detector.py` | analyze orchestration; boxes review API | boxes, findings, images, ai_requests, events | UI-003 | TST-009/016/017 | TASK-011/012/020 |
| FR-010 → UC-010 | BR-005/012/013/016 | DIA-002/006/008–017 | `application/ports.py`, `infrastructure/audit.py`, `infrastructure/db/repositories.py` | `GET /sessions/{session_id}/events`; cross-cutting writes | events, ai_requests, users, sessions | UI-003 | TST-010/013/015 | TASK-010/017/021 |
| FR-011 → UC-011 | BR-012/014/015 | DIA-002/004/010/016/021 | `infrastructure/inference_http.py`, `services/inference/app/` | defined ai-requests and private inference contract; no browser model endpoint | ai_requests, events | UI-003 | TST-011/017/018 | TASK-011/018–022 |

### 10.1 Source requirement crosswalk

New IDs have three digits to avoid silently reusing the PDF's two-digit functional requirement identifiers. This table retains baseline traceability.

| PDF requirement | Current implementation requirement |
| --- | --- |
| PDF FR-01/02/03 | FR-001, BR-001/002 |
| PDF FR-04/05 | FR-002 |
| PDF FR-06/07 | FR-003, BR-006 |
| PDF FR-08/09 | FR-002/004, BR-003/004 |
| PDF FR-10/11 | FR-004/005/006/008/009/011, BR-012/014 |
| PDF FR-12/13 | FR-005 |
| PDF FR-14/15 | FR-006 |
| PDF FR-16 | FR-006/007/008/010, BR-013 |
| PDF FR-17/18/19 | FR-007, BR-007/008 |
| PDF FR-20/21/22/23 | FR-008, BR-009/010/011 |
| PDF FR-24 | FR-010 |
| PDF S3 | MUST minimum FR-007/008; SHOULD richer inspection FR-014 |
| PDF L1 | Superseded by current user CV-detection clarification, FR-009 |

## 11. Completion and change control

The approved build order is defined by TASK-001–024 in implementation_plan.md. Start with TASK-001: choose the application repository, verify the documentation decisions against its actual baseline and lock supported dependencies. Complete the mock vertical slice before integrating supplied real artifacts; complete real VLM/CV acceptance separately. No application or training implementation was performed by this documentation task.

Any change to role permissions, immutable bindings, source-version rules, model capability assumptions, priority or public API must update all three documents and the corresponding traceability/test row before a coding agent implements it. Open artifact/hardware facts remain open until measured or supplied; they must not be “resolved” by inventing model performance, a detector, confidence calibration or historical-image reasoning.
