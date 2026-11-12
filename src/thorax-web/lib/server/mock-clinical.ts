import type {
  AICommand, AIRequest, Blocker, BoxCoordinates, BoxDTO, BoxSnapshot, FindingItem, FindingsDTO, ImageDTO,
  MutationReceipt, ReportDTO, ReportNarrative, ReviewAction, StudyDTO, Workspace,
} from '../contracts';
import { modelArtifacts, patients, studies, studyHistory, type FixtureStudy } from './fixtures';
import { assigned, event, fail, hash, invalid, isoNow, missing, nextId, type MockSession, type MockState } from './mock-state';

const activeJob = (reading: MockSession) => reading.jobs.find(({ request }) => request.status === 'queued' || request.status === 'running');
const latestFindings = (reading: MockSession) => reading.findings.at(-1);
const included = (item: FindingItem) => ['accepted', 'edited', 'added'].includes(item.action);
const activeBox = (box: BoxDTO) => !box.deleted_at && box.review_action !== 'rejected';

export function fields(body: Record<string, unknown>, keys: string[]) {
  if (Object.keys(body).some((key) => !keys.includes(key))) invalid('Unexpected request field.');
}

export function integer(input: unknown, label: string) {
  if (typeof input !== 'number' || !Number.isSafeInteger(input) || input < 1) invalid(`Invalid ${label}.`);
  return input;
}

export function string(input: unknown, label: string, min: number, max: number) {
  if (typeof input !== 'string' || input.trim().length < min || input.length > max) {
    invalid(`${label} must be ${min}–${max} characters.`);
  }
  return input;
}

function nullableString(input: unknown, label: string, max: number) {
  return input == null ? null : string(input, label, 0, max);
}

export function studyDto(state: MockState, study: FixtureStudy, actorId: number): StudyDTO {
  const all = state.sessions.filter(({ session }) => session.study_id === study.id);
  const own = all.find(({ session }) => session.doctor_id === actorId)?.session;
  return {
    id: study.id,
    patient_id: study.patient_id,
    study_date: study.study_date,
    indication: study.indication,
    image_count: study.images.length,
    status: all.some(({ session }) => session.status === 'final') ? 'final' : all.length ? 'in_progress' : 'new',
    own_session_id: own?.id ?? null,
    own_session_status: own?.status ?? null,
  };
}

export function openStudy(state: MockState, study: FixtureStudy, actorId: number) {
  const existing = state.sessions.find(({ session }) => session.doctor_id === actorId && session.study_id === study.id);
  if (existing) return { reading: existing, status: 200 };
  const reading: MockSession = {
    session: {
      id: nextId(state), doctor_id: actorId, patient_id: study.patient_id, study_id: study.id, status: 'active', revision: 1,
    },
    findings: [], boxes: [], messages: [], reports: [], jobs: [], events: [], analyzedImageId: null,
  };
  event(state, reading, 'session_opened', { study_id: study.id });
  state.sessions.push(reading);
  return { reading, status: 201 };
}

function blocker(gate: Blocker['gate'], code: string, resource_type: string, message: string, resource_id: Blocker['resource_id'] = null): Blocker {
  return { gate, code, resource_type, resource_id, message };
}

function gate1(reading: MockSession): Blocker[] {
  const findings = latestFindings(reading);
  if (findings?.status === 'approved') return [];
  const pending: Blocker[] = [];
  if (!findings) return [blocker('gate1', 'ANALYSIS_REQUIRED', 'findings', 'Run the initial analysis.')];
  if (findings.status !== 'in_review') pending.push(blocker('gate1', 'SAVE_REQUIRED', 'findings', 'Save findings before approval.'));
  for (const item of findings.items) {
    if (item.action === 'pending') pending.push(blocker('gate1', 'UNRESOLVED_REVIEW', 'finding', 'Review this AI finding.', item.id));
    if (included(item) && item.box_ids.some((id) => !reading.boxes.some((box) => box.id === id && activeBox(box)))) {
      pending.push(blocker('gate1', 'INVALID_EVIDENCE', 'finding', 'Remove the unavailable region link.', item.id));
    }
  }
  for (const box of reading.boxes) {
    if (box.review_action === 'pending' && activeBox(box)) {
      pending.push(blocker('gate1', 'UNRESOLVED_REVIEW', 'box', 'Review this AI region.', box.id));
    }
  }
  if (!findings.items.some(included) && !findings.no_abnormality_confirmed) {
    pending.push(blocker('gate1', 'NORMAL_CONFIRMATION_REQUIRED', 'findings', 'Include a finding or explicitly confirm no abnormal findings.'));
  }
  return pending;
}

function gate2(reading: MockSession): Blocker[] {
  if (reading.session.status === 'final') return [];
  if (reading.session.status !== 'findings_approved') {
    return [blocker('gate2', 'GATE1_REQUIRED', 'findings', 'Approve the latest findings before drafting a report.')];
  }
  const report = reading.reports.find(({ status }) => status === 'draft');
  if (!report) return [blocker('gate2', 'DRAFT_REQUIRED', 'report', 'Generate a draft report.')];
  if (!report.content.findings.trim() || !report.content.impression.trim()) {
    return [blocker('gate2', 'REQUIRED_FIELDS', 'report', 'Save a findings section and an impression before approval.', report.id)];
  }
  return [];
}

export function workspace(state: MockState, reading: MockSession, messagePage: Workspace['messages']): Workspace {
  const { session } = reading;
  const study = studies.find(({ id }) => id === session.study_id)!;
  const live = activeJob(reading)?.request;
  return {
    session,
    patient: patients.find(({ id }) => id === session.patient_id)!,
    study: studyDto(state, study, session.doctor_id),
    images: study.images,
    history: studyHistory(study),
    latest_findings: latestFindings(reading) ?? null,
    reports: reading.reports,
    boxes: reading.boxes,
    messages: messagePage,
    live_request_id: live?.request_id ?? null,
    latest_ai_request: live ?? reading.jobs.at(-1)?.request ?? null,
    initial_analysis: reading.jobs.find(({ request }) => request.operation === 'analyze' && request.status === 'succeeded')?.request ?? null,
    blockers: [...gate1(reading), ...gate2(reading)],
    capabilities: { provider_mode: 'mock', can_detect: true, can_ask_roi: true, model_artifacts: modelArtifacts },
  };
}

export function checkWrite(request: Request, reading: MockSession, requiresAnalysis = false) {
  const tag = request.headers.get('If-Match');
  if (!tag) fail(428, 'PRECONDITION_REQUIRED', 'Reload this reading before saving.');
  if (tag !== `"${reading.session.revision}"`) {
    fail(412, 'REVISION_CONFLICT', 'This reading changed in another tab. Reload it before saving.');
  }
  if (reading.session.status === 'final') fail(409, 'FINAL_READ_ONLY', 'This final reading is read-only.');
  if (activeJob(reading)) fail(409, 'AI_BUSY', 'Wait for the current AI request before making changes.');
  if (requiresAnalysis && reading.analyzedImageId === null) {
    fail(409, 'ANALYSIS_REQUIRED', 'Run the initial analysis before making this change.');
  }
}

function invalidateApproval(state: MockState, reading: MockSession) {
  const latest = latestFindings(reading);
  if (latest?.status !== 'approved') return;
  reading.findings.push({
    ...structuredClone(latest), id: nextId(state), version: latest.version + 1, status: 'in_review',
    approved_at: null, approved_by: null, annotation_snapshot: null,
  });
  reading.session.status = 'active';
  for (const report of reading.reports) if (report.status === 'draft') report.status = 'stale';
  event(state, reading, 'findings_reopened', { previous_version: latest.version });
}

function receipt(reading: MockSession, resource_type: MutationReceipt['resource_type'], resource_id: number): MutationReceipt {
  return { resource_type, resource_id, session_revision: reading.session.revision };
}

function recordEdit(state: MockState, reading: MockSession, name: string, data: Record<string, unknown>) {
  reading.session.revision++;
  event(state, reading, name, data);
}

function currentImage(reading: MockSession, imageId: number): ImageDTO {
  return studies.find(({ id }) => id === reading.session.study_id)!.images.find(({ id }) => id === imageId) ?? missing();
}

function boxSnapshot(box: BoxDTO): BoxSnapshot {
  return { box_id: box.id, revision: box.revision, image_id: box.image_id, bbox_xyxy: [...box.bbox_xyxy] };
}

function geometry(value: unknown, image: ImageDTO): BoxCoordinates {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) invalid('Enter four finite region coordinates.');
  const [x1, y1, x2, y2] = value as BoxCoordinates;
  if (x1 < 0 || y1 < 0 || x1 >= x2 || y1 >= y2 || x2 > image.width || y2 > image.height) {
    invalid('The region must be inside the original image and have positive width and height.');
  }
  return [x1, y1, x2, y2];
}

export function createBox(state: MockState, reading: MockSession, body: Record<string, unknown>) {
  fields(body, ['image_id', 'bbox_xyxy', 'label', 'note']);
  const image = currentImage(reading, integer(body.image_id, 'image'));
  const box: BoxDTO = {
    id: nextId(state), image_id: image.id, bbox_xyxy: geometry(body.bbox_xyxy, image),
    label: nullableString(body.label, 'Region label', 200), note: nullableString(body.note, 'Region note', 2000),
    source: 'doctor', review_action: 'added', revision: 1, deleted_at: null, ai_request_id: null,
  };
  reading.boxes.push(box);
  invalidateApproval(state, reading);
  recordEdit(state, reading, 'box_created', { box_id: box.id, image_id: image.id });
  return receipt(reading, 'box', box.id);
}

export function editBox(state: MockState, reading: MockSession, id: number, body: Record<string, unknown>, deleting: boolean) {
  const box = reading.boxes.find((entry) => entry.id === id) ?? missing();
  if (box.deleted_at) {
    if (deleting) return receipt(reading, 'box', box.id);
    return missing();
  }
  const before = boxSnapshot(box);
  if (deleting) {
    fields(body, []);
    box.deleted_at = isoNow();
    box.review_action = 'rejected';
  } else {
    fields(body, ['bbox_xyxy', 'label', 'note', 'review_action']);
    if (!Object.keys(body).length) invalid('Enter at least one region change.');
    if ('bbox_xyxy' in body) box.bbox_xyxy = geometry(body.bbox_xyxy, currentImage(reading, box.image_id));
    if ('label' in body) box.label = nullableString(body.label, 'Region label', 200);
    if ('note' in body) box.note = nullableString(body.note, 'Region note', 2000);
    if ('review_action' in body) {
      const actions = box.source === 'ai' ? ['accepted', 'edited', 'rejected'] : ['added', 'rejected'];
      if (typeof body.review_action !== 'string' || !actions.includes(body.review_action)) invalid('Invalid region review decision.');
      box.review_action = body.review_action as ReviewAction;
    }
    const edited = 'bbox_xyxy' in body || 'label' in body || 'note' in body;
    if (edited && box.source === 'ai' && box.review_action !== 'rejected') box.review_action = 'edited';
  }
  box.revision++;
  invalidateApproval(state, reading);
  recordEdit(state, reading, deleting ? 'box_deleted' : 'box_updated', { box_id: id, before, after: boxSnapshot(box) });
  return receipt(reading, 'box', id);
}

export function saveFindings(state: MockState, reading: MockSession, body: Record<string, unknown>) {
  fields(body, ['items', 'no_abnormality_confirmed']);
  if (!Array.isArray(body.items) || body.items.length > 50 || typeof body.no_abnormality_confirmed !== 'boolean') {
    invalid('Save up to 50 findings and an explicit normal confirmation state.');
  }
  const latest = latestFindings(reading)!;
  const ids = new Set<string>();
  const items = body.items.map((raw: unknown): FindingItem => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) invalid('Invalid finding.');
    const row = raw as Record<string, unknown>;
    fields(row, ['id', 'text', 'location', 'image_id', 'source', 'action', 'box_ids']);
    const id = string(row.id, 'Finding ID', 1, 128);
    const text = string(row.text, 'Finding text', 1, 2000);
    const location = nullableString(row.location, 'Finding location', 200);
    const image_id = currentImage(reading, integer(row.image_id, 'finding image')).id;
    if (ids.has(id)) invalid('Finding IDs must be unique.');
    ids.add(id);
    const original = latest.ai_items.find((item) => item.id === id);
    const allowed = original ? ['pending', 'accepted', 'edited', 'rejected'] : ['added', 'rejected'];
    if (typeof row.action !== 'string' || !allowed.includes(row.action)) invalid('Invalid finding review decision.');
    if (original) {
      if (row.source !== 'ai' || image_id !== original.image_id) invalid('AI finding provenance is immutable.');
      if (['accepted', 'pending'].includes(row.action) && (text !== original.text || location !== original.location)) {
        invalid('Mark changed AI text or location as edited.');
      }
    } else if (row.source !== 'doctor') invalid('Unknown AI finding.');
    if (!Array.isArray(row.box_ids)) invalid('Provide the list of linked regions.');
    const box_ids = row.box_ids.map((value: unknown) => integer(value, 'region link'));
    if (new Set(box_ids).size !== box_ids.length || box_ids.some((id) =>
      !reading.boxes.some((box) => box.id === id && box.image_id === image_id && activeBox(box))
    )) invalid('Link only available regions on this finding’s image.');
    return { id, text, location, image_id, source: original ? 'ai' : 'doctor', action: row.action as ReviewAction, box_ids };
  });
  if (latest.ai_items.some(({ id }) => !ids.has(id))) invalid('Keep every original AI finding in the review.');
  if (body.no_abnormality_confirmed && items.some(included)) invalid('Normal confirmation requires no included findings.');
  invalidateApproval(state, reading);
  const target = latestFindings(reading)!;
  target.items = items;
  target.no_abnormality_confirmed = body.no_abnormality_confirmed;
  target.status = 'in_review';
  recordEdit(state, reading, 'findings_saved', { findings_version: target.version });
  return receipt(reading, 'findings', target.id);
}

export function approveFindings(state: MockState, reading: MockSession, body: Record<string, unknown>) {
  fields(body, ['findings_version']);
  const version = integer(body.findings_version, 'findings version');
  const latest = latestFindings(reading);
  if (latest?.version !== version) fail(409, 'FINDINGS_VERSION_MISMATCH', 'Review the latest findings version.');
  if (latest.status === 'approved') return receipt(reading, 'findings', latest.id);
  if (gate1(reading).length) fail(409, 'UNRESOLVED_REVIEW', 'Resolve the outstanding findings and region review before approving.');
  latest.status = 'approved';
  latest.approved_at = isoNow();
  latest.approved_by = reading.session.doctor_id;
  latest.annotation_snapshot = reading.boxes.filter(activeBox).map(boxSnapshot);
  reading.session.status = 'findings_approved';
  recordEdit(state, reading, 'gate1_approved', { findings_version: latest.version });
  return receipt(reading, 'findings', latest.id);
}

const narrativeKeys = ['comparison', 'findings', 'impression', 'recommendation'] as const;

export function editReport(state: MockState, reading: MockSession, id: number, body: Record<string, unknown>, approving: boolean) {
  const report = reading.reports.find((row) => row.id === id) ?? missing();
  const latest = latestFindings(reading);
  if (report.status !== 'draft' || latest?.status !== 'approved' || report.findings_version !== latest.version) {
    fail(409, 'REPORT_STALE', 'The report is no longer based on the latest approved findings.');
  }
  if (approving) {
    fields(body, ['findings_version']);
    if (body.findings_version !== latest.version) fail(409, 'REPORT_STALE', 'Review the latest approved findings version.');
    if (reading.session.status !== 'findings_approved') fail(409, 'GATE1_REQUIRED', 'Approve the latest findings first.');
    if (!report.content.findings.trim() || !report.content.impression.trim()) invalid('Findings and impression are required.');
    report.status = 'final';
    report.approved_by = reading.session.doctor_id;
    report.approved_at = isoNow();
    reading.session.status = 'final';
  } else {
    fields(body, [...narrativeKeys]);
    if (!Object.keys(body).length) invalid('Enter at least one report change.');
    for (const key of narrativeKeys) {
      if (key in body) report.content[key] = string(body[key], key, 0, 8000);
    }
  }
  report.updated_at = isoNow();
  recordEdit(state, reading, approving ? 'gate2_approved' : 'report_saved', { report_id: id, findings_version: latest.version });
  return receipt(reading, 'report', report.id);
}

function approvedDraft(state: MockState, reading: MockSession) {
  const study = studies.find(({ id }) => id === reading.session.study_id)!;
  const source = latestFindings(reading)!;
  const items = source.items.filter(included);
  const observations = items.map(({ text }) => text.trim()).join('\n');
  const narrative: ReportNarrative = {
    comparison: '',
    findings: observations || 'No abnormal findings were confirmed on review.',
    impression: observations || 'No abnormal findings were confirmed.',
    recommendation: '',
  };
  const report: ReportDTO = {
    id: nextId(state), findings_version: source.version, status: 'draft', approved_by: null, approved_at: null,
    updated_at: isoNow(),
    content: {
      ...narrative,
      patient: patients.find(({ id }) => id === study.patient_id)!,
      study: {
        id: study.id, study_date: study.study_date, indication: study.indication,
        image_ids: study.images.map(({ id }) => id), analyzed_image_id: reading.analyzedImageId!,
      },
      source_item_ids: items.map(({ id }) => id),
    },
  };
  for (const previous of reading.reports) if (previous.status === 'draft') previous.status = 'stale';
  reading.reports.push(report);
  return report.id;
}

function command(body: Record<string, unknown>): AICommand {
  const retry = 'retry_of' in body ? { retry_of: integer(body.retry_of, 'retry request') } : {};
  if (body.operation === 'analyze') {
    fields(body, ['operation', 'image_id', 'retry_of']);
    return { operation: 'analyze', image_id: integer(body.image_id, 'image'), ...retry };
  }
  if (body.operation === 'ask') {
    fields(body, ['operation', 'image_id', 'question', 'box_id', 'retry_of']);
    return {
      operation: 'ask', image_id: integer(body.image_id, 'image'),
      question: string(body.question, 'Question', 1, 2000).trim(),
      ...('box_id' in body ? { box_id: integer(body.box_id, 'region') } : {}), ...retry,
    };
  }
  if (body.operation === 'draft_report') {
    fields(body, ['operation', 'findings_version', 'retry_of']);
    return { operation: 'draft_report', findings_version: integer(body.findings_version, 'findings version'), ...retry };
  }
  return invalid('Choose a supported AI operation.');
}

export function submit(state: MockState, reading: MockSession, body: Record<string, unknown>): AIRequest {
  const input = command(body);
  const patient = patients.find(({ id }) => id === reading.session.patient_id)!;
  const history = studyHistory(studies.find(({ id }) => id === reading.session.study_id)!);
  const originalImage = 'image_id' in input ? currentImage(reading, input.image_id) : undefined;
  const lastMessages = input.operation === 'ask' ? reading.messages.slice(-10) : [];
  let box: BoxSnapshot | null = null;

  if (input.operation === 'analyze' && reading.analyzedImageId !== null) {
    fail(409, 'ALREADY_ANALYZED', 'Initial analysis already succeeded for this reading.');
  }
  if (input.operation === 'ask') {
    if (reading.analyzedImageId === null) fail(409, 'ANALYSIS_REQUIRED', 'Run the initial analysis before asking a question.');
    const text = input.question.normalize('NFKC').toLowerCase();
    if ([patient.name, patient.code].some((token) => text.includes(token.normalize('NFKC').toLowerCase()))) {
      fail(422, 'PHI_IN_MODEL_INPUT', 'Remove patient names and identifiers before sending your question.');
    }
    if (input.box_id) {
      const region = reading.boxes.find((row) => row.id === input.box_id && row.image_id === input.image_id && activeBox(row));
      if (!region) {
        if (input.retry_of) fail(409, 'CONTEXT_CHANGED', 'The original region changed. Send a new question with the current region.');
        return missing();
      }
      box = boxSnapshot(region);
    }
  }
  if (input.operation === 'draft_report') {
    const latest = latestFindings(reading);
    if (latest?.status !== 'approved' || latest.version !== input.findings_version) {
      if (input.retry_of) fail(409, 'CONTEXT_CHANGED', 'The original approved findings changed. Start a new draft.');
      fail(409, 'GATE1_REQUIRED', 'Approve the latest findings before drafting a report.');
    }
  }

  const { retry_of, ...originalInput } = input;
  const context = hash(JSON.stringify({
    originalInput, imageHash: originalImage?.sha256, box,
    messageIds: lastMessages.map(({ id }) => id),
    historyIds: input.operation === 'draft_report' ? [] : history.map(({ id }) => id),
  }));
  if (retry_of) {
    const original = reading.jobs.find(({ request }) => request.request_id === retry_of) ?? missing();
    if (!['failed', 'timed_out'].includes(original.request.status) || !original.request.error?.retryable) {
      fail(409, 'CONTEXT_CHANGED', 'This request cannot be retried. Submit a new request.');
    }
    if (original.context !== context) fail(409, 'CONTEXT_CHANGED', 'The original context changed. Submit a new request.');
  }

  const request_id = nextId(state);
  const submittedAt = Date.now();
  recordEdit(state, reading, 'ai_request_queued', { request_id, operation: input.operation, retry_of: retry_of ?? null });
  const request: AIRequest = {
    request_id, operation: input.operation, status: 'queued',
    poll_url: `/api/v1/sessions/${reading.session.id}/ai-requests/${request_id}`,
    session_revision: reading.session.revision, deadline_at: new Date(submittedAt + 60000).toISOString(),
    retry_of: retry_of ?? null, error: null, output_refs: {}, submitted_input: input,
    provenance: {
      provider_mode: 'mock',
      vlm_artifact: modelArtifacts[0],
      cv_artifact: input.operation === 'analyze' ? modelArtifacts[1] : null,
      prompt_version: box ? 'roi-v1' : `${input.operation}-v1`,
      preprocessing_version: 'canonical-v1',
      image_sha256: originalImage?.sha256 ?? null,
      history_report_ids: input.operation === 'draft_report' ? [] : history.map(({ id }) => id),
      history_hashes: input.operation === 'draft_report' ? [] : history.map(({ text }) => hash(text)),
      message_ids: lastMessages.map(({ id }) => id),
      box_snapshot: box,
      source_findings_version: input.operation === 'draft_report' ? input.findings_version : null,
    },
  };
  const fault = process.env.THORAX_DEMO_AI_FAULT;
  reading.jobs.push({
    request, context, submittedAt,
    failure: fault === 'timeout' || (fault === 'detector' && input.operation === 'analyze') ? fault : undefined,
  });
  return request;
}

function completeAnalysis(state: MockState, reading: MockSession, request: AIRequest, imageId: number) {
  const image = currentImage(reading, imageId);
  const suggestions: FindingItem[] = [];
  const boxes: BoxDTO[] = [];
  if (image.view !== 'LAT') {
    suggestions.push(
      {
        id: `ai-${request.request_id}-1`, text: 'Possible mild left basilar opacity.',
        location: 'Left lower zone', image_id: imageId, source: 'ai', action: 'pending', box_ids: [],
      },
      {
        id: `ai-${request.request_id}-2`, text: 'Cardiomediastinal silhouette appears mildly prominent.',
        location: 'Mediastinum', image_id: imageId, source: 'ai', action: 'pending', box_ids: [],
      },
    );
  }
  if (image.view === 'PA') {
    boxes.push({
      id: nextId(state), image_id: imageId,
      bbox_xyxy: [image.width * 0.63, image.height * 0.62, image.width * 0.83, image.height * 0.78],
      label: 'Left basilar region', note: null, source: 'ai', review_action: 'pending', revision: 1,
      deleted_at: null, ai_request_id: request.request_id,
    });
  }
  const findings: FindingsDTO = {
    id: nextId(state), version: 1, status: 'ai_proposed', items: suggestions, ai_items: structuredClone(suggestions),
    no_abnormality_confirmed: false, annotation_snapshot: null, approved_by: null, approved_at: null,
  };
  reading.analyzedImageId = imageId;
  reading.findings.push(findings);
  reading.boxes.push(...boxes);
  return { findings_version: findings.version, box_ids: boxes.map(({ id }) => id) };
}

function completeAnswer(state: MockState, reading: MockSession, request: AIRequest, input: Extract<AICommand, { operation: 'ask' }>) {
  const box_snapshot = request.provenance.box_snapshot ?? null;
  const image = currentImage(reading, input.image_id);
  const answer = box_snapshot
    ? 'Mock response for the selected region. The synthetic crop may contain overlapping lung and rib markings. Review the original image and the saved region before drawing a conclusion.'
    : image.view === 'LAT'
      ? 'Mock response for the lateral view. The fixture supplies no additional observation for this image.'
      : 'Mock response for the selected frontal image. The fixture marks a possible left basilar opacity. Review the image and current findings to determine whether it should be included.';
  const context = { image_id: input.image_id, box_id: input.box_id ?? null, box_snapshot, created_at: isoNow() };
  const question = { ...context, id: nextId(state), role: 'doctor' as const, text: input.question, model_version: null, latency_ms: null };
  const response = { ...context, id: nextId(state), role: 'ai' as const, text: answer, model_version: modelArtifacts[0].version, latency_ms: 1400 };
  reading.messages.push(question, response);
  return { message_ids: [question.id, response.id] };
}

export function settleJobs(state: MockState) {
  const now = Date.now();
  for (const reading of state.sessions) {
    const job = activeJob(reading);
    if (!job) continue;
    const { request, submittedAt } = job;
    const input = request.submitted_input!;
    const { doctor_id, patient_id } = reading.session;
    const allowed = state.users.some(({ id, is_active }) => id === doctor_id && is_active) && assigned(state, doctor_id, patient_id);
    if (!allowed) {
      request.status = 'cancelled';
      request.error = { code: 'AUTHORIZATION_REVOKED', message: 'Access to this study was revoked.', retryable: false };
    } else if (now >= Date.parse(request.deadline_at)) {
      request.status = 'timed_out';
      request.error = { code: 'AI_DEADLINE_EXCEEDED', message: 'The AI request reached its time limit.', retryable: true };
    } else if (job.failure !== 'timeout' && now - submittedAt >= 1400) {
      if (job.failure === 'detector') {
        request.status = 'failed';
        request.error = { code: 'AI_UNAVAILABLE', message: 'The mock detector did not finish. No suggestions were saved.', retryable: true };
      } else {
        if (input.operation === 'analyze') request.output_refs = completeAnalysis(state, reading, request, input.image_id);
        if (input.operation === 'ask') request.output_refs = completeAnswer(state, reading, request, input);
        if (input.operation === 'draft_report') request.output_refs = { report_id: approvedDraft(state, reading) };
        request.status = 'succeeded';
      }
    } else {
      if (now - submittedAt >= 250) {
        request.status = 'running';
        request.provenance.started_at ??= new Date(submittedAt + 250).toISOString();
      }
      continue;
    }
    request.provenance.started_at ??= new Date(submittedAt + 250).toISOString();
    request.provenance.completed_at = isoNow();
    request.provenance.latency_ms = request.status === 'succeeded' ? 1400 : now - submittedAt;
    recordEdit(state, reading, `ai_request_${request.status}`, {
      request_id: request.request_id, operation: request.operation, provider_mode: 'mock',
      ...('image_id' in input ? { image_id: input.image_id } : {}),
      model_version: modelArtifacts[0].version, output_refs: request.output_refs, error_code: request.error?.code ?? null,
    });
    request.session_revision = reading.session.revision;
  }
}
