import type {
  AdminDoctor, AdminPatient, AICommand, AIRequest, AIStatus, AuditEvent, AuthUser, BoxCreate,
  BoxPatch, ErrorDetail, FindingsEdit, FixtureStatus, MessageDTO, MutationReceipt, Page, PatientSummary,
  AdminAi, AdminEvent, AdminLabeling, AdminOverview, AdminUser, AiPause, LabelBox, LabelQueue, LabelTask, LabelVocabulary, ReportNarrative, StudyDetail, StudyDTO, Workspace,
} from './contracts';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: ErrorDetail[] = [],
    readonly retryAfter?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const authLostEvent = 'thorax:auth-lost';

let csrfToken: string | undefined;
let authEpoch = 0;

// Images are immutable, so one download per image serves the viewer and its thumbnail.
// Held in page memory only and dropped with the login, never in browser storage.
const imageCache = new Map<number, Promise<Blob>>();

export function clearApiAuth() {
  csrfToken = undefined;
  authEpoch += 1;
  imageCache.clear();
}

function abortable<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

export const actionKey = () => crypto.randomUUID();

export function isTerminal(status: AIStatus) {
  return status !== 'queued' && status !== 'running';
}

export interface MutationOptions {
  revision: number;
  key?: string;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: object;
  revision?: number;
  key?: string;
  activity?: boolean;
  signal?: AbortSignal;
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const { method = 'GET', body, revision, key, activity, signal } = options;
  const started = authEpoch;
  const headers = new Headers();
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  if (method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken);
  if (revision !== undefined) headers.set('If-Match', `"${revision}"`);
  if (key) headers.set('Idempotency-Key', key);
  if (activity) headers.set('X-User-Activity', '1');

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, 'NETWORK_ERROR', 'Could not reach the server. Try again.');
  }

  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login' && started === authEpoch) {
      const hadSession = csrfToken !== undefined;
      clearApiAuth();
      if (hadSession && typeof window !== 'undefined') window.dispatchEvent(new Event(authLostEvent));
    }
    const { error } = await response.json().catch(() => ({}));
    const retryAfter = response.headers.get('Retry-After');
    throw new ApiError(
      response.status,
      error?.code ?? 'REQUEST_FAILED',
      error?.message ?? 'The request could not be completed. Try again.',
      Array.isArray(error?.details) ? error.details : [],
      retryAfter && Number.isFinite(Number(retryAfter)) ? Number(retryAfter) : undefined,
    );
  }
  return response;
}

async function json<T>(path: string, options?: RequestOptions): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  return response.json();
}

function query(path: string, cursor?: string, search = '') {
  const params = new URLSearchParams();
  if (cursor) params.set('cursor', cursor);
  if (search.trim()) params.set('search', search.trim());
  return params.size ? `${path}?${params}` : path;
}

function mutate<T>(
  method: 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body?: object,
  options?: MutationOptions,
  key = actionKey(),
) {
  return json<T>(path, { method, body, revision: options?.revision, key: options?.key ?? key });
}

const session = (id: number) => `/sessions/${id}`;

export const api = {
  async me(activity = false, signal?: AbortSignal) {
    const started = authEpoch;
    const user = await json<AuthUser>('/auth/me', { activity, signal });
    if (started === authEpoch && !signal?.aborted) csrfToken = user.csrf_token;
    return user;
  },
  async login(email: string, password: string) {
    const user = await json<AuthUser>('/auth/login', { method: 'POST', body: { email, password } });
    authEpoch += 1;
    csrfToken = user.csrf_token;
    return user;
  },
  changePassword: (currentPassword: string, newPassword: string) =>
    json<void>('/auth/password', { method: 'POST', body: { current_password: currentPassword, new_password: newPassword } }),
  async logout() {
    await json<void>('/auth/logout', { method: 'POST' });
    clearApiAuth();
  },
  // Admin console (real backend only).
  adminOverview: (signal?: AbortSignal) => json<AdminOverview>('/admin/overview', { signal }),
  adminUsers: (signal?: AbortSignal) => json<{ items: AdminUser[] }>('/admin/users', { signal }),
  adminCreateUser: (body: { name: string; email: string; role: string; password: string }) => json<AdminUser>('/admin/users', { method: 'POST', body }),
  adminUpdateUser: (id: number, body: { is_active?: boolean; password?: string }) => json<AdminUser>(`/admin/users/${id}`, { method: 'PATCH', body }),
  adminSignOutUser: (id: number) => json<void>(`/admin/users/${id}/sign-out`, { method: 'POST' }),
  adminAi: (signal?: AbortSignal) => json<AdminAi>('/admin/ai', { signal }),
  adminSetAiPaused: (paused: boolean) => json<AiPause>('/admin/ai/pause', { method: 'PUT', body: { paused } }),
  adminLabeling: (signal?: AbortSignal) => json<AdminLabeling>('/admin/labeling', { signal }),
  adminExportLabels: () => json<{ manifest: Record<string, string | number>; labels_jsonl: string }>('/admin/labeling/export', { method: 'POST' }),
  adminEvents: (signal?: AbortSignal) => json<{ items: AdminEvent[] }>('/admin/events', { signal }),
  // Labeling (annotator role). Served by the real backend only; the local demo API has no labeling.
  labelVocabulary: (signal?: AbortSignal) => json<LabelVocabulary>('/labeling/vocabulary', { signal }),
  labelQueue: (signal?: AbortSignal) => json<LabelQueue>('/labeling/queue', { signal }),
  labelNext: () => json<LabelTask>('/labeling/tasks/next', { method: 'POST' }),
  labelTask: (id: number, signal?: AbortSignal) => json<LabelTask>(`/labeling/tasks/${id}`, { signal }),
  saveLabelBoxes: (id: number, boxes: LabelBox[], revision: number) =>
    json<LabelTask>(`/labeling/tasks/${id}/boxes`, { method: 'PUT', body: { boxes, revision } }),
  submitLabelTask: (id: number, revision: number) =>
    json<LabelTask>(`/labeling/tasks/${id}/submit`, { method: 'POST', body: { revision } }),
  reviewLabelTask: (id: number, decision: 'approve' | 'return', comment: string, revision: number) =>
    json<LabelTask>(`/labeling/tasks/${id}/review`, { method: 'POST', body: { decision, comment: comment || null, revision } }),
  patients: (search = '', cursor?: string, signal?: AbortSignal, activity = false) =>
    json<Page<PatientSummary>>(query('/patients', cursor, search), { activity, signal }),
  studies: (patientId: number, cursor?: string, signal?: AbortSignal, activity = false) =>
    json<Page<StudyDTO>>(query(`/patients/${patientId}/studies`, cursor), { activity, signal }),
  study: (studyId: number, signal?: AbortSignal, activity = false) =>
    json<StudyDetail>(`/studies/${studyId}`, { activity, signal }),
  openStudy: (studyId: number) =>
    json<Workspace>(`/studies/${studyId}/sessions`, { method: 'POST' }),
  workspace: (id: number, signal?: AbortSignal) => json<Workspace>(session(id), { signal }),
  image(imageId: number, signal?: AbortSignal) {
    let blob = imageCache.get(imageId);
    if (!blob) {
      // Shared download: one caller aborting must not cancel it for the others.
      blob = send(`/images/${imageId}/content`).then(response => response.blob());
      imageCache.set(imageId, blob);
      blob.catch(() => { if (imageCache.get(imageId) === blob) imageCache.delete(imageId); });
    }
    return abortable(blob, signal);
  },
  messages: (id: number, cursor?: string, signal?: AbortSignal) =>
    json<Page<MessageDTO>>(query(`${session(id)}/messages`, cursor), { signal }),
  events: (id: number, cursor?: string, signal?: AbortSignal) =>
    json<Page<AuditEvent>>(query(`${session(id)}/events`, cursor), { signal }),
  request: (id: number, requestId: number, signal?: AbortSignal) =>
    json<AIRequest>(`${session(id)}/ai-requests/${requestId}`, { signal }),
  submit: (id: number, body: AICommand, options: MutationOptions) =>
    mutate<AIRequest>('POST', `${session(id)}/ai-requests`, body, options),
  createBox: (id: number, body: BoxCreate, options: MutationOptions) =>
    mutate<MutationReceipt>('POST', `${session(id)}/boxes`, body, options),
  updateBox: (id: number, boxId: number, body: BoxPatch, options: MutationOptions) =>
    mutate<MutationReceipt>('PATCH', `${session(id)}/boxes/${boxId}`, body, options),
  deleteBox: (id: number, boxId: number, options: MutationOptions) =>
    mutate<MutationReceipt>('DELETE', `${session(id)}/boxes/${boxId}`, undefined, options),
  saveFindings: (id: number, body: FindingsEdit, options: MutationOptions) =>
    mutate<MutationReceipt>('PATCH', `${session(id)}/findings`, body, options),
  approveFindings: (id: number, findingsVersion: number, options: MutationOptions) =>
    mutate<MutationReceipt>('POST', `${session(id)}/findings/approve`, { findings_version: findingsVersion }, options),
  saveReport: (id: number, reportId: number, body: Partial<ReportNarrative>, options: MutationOptions) =>
    mutate<MutationReceipt>('PATCH', `${session(id)}/reports/${reportId}`, body, options),
  approveReport: (id: number, reportId: number, findingsVersion: number, options: MutationOptions) =>
    mutate<MutationReceipt>(
      'POST', `${session(id)}/reports/${reportId}/approve`, { findings_version: findingsVersion }, options,
    ),
  adminDoctors: (cursor?: string, signal?: AbortSignal, activity = false) =>
    json<Page<AdminDoctor>>(query('/admin/doctors', cursor), { signal, activity }),
  adminPatients: (search = '', cursor?: string, signal?: AbortSignal, activity = false) =>
    json<Page<AdminPatient>>(query('/admin/patient-catalog', cursor, search), { signal, activity }),
  fixtureStatus: (fixtureKey: string, signal?: AbortSignal, activity = false) =>
    json<FixtureStatus>(`/admin/fixtures/imports/${encodeURIComponent(fixtureKey)}`, { signal, activity }),
  createDoctor: (body: { name: string; email: string; password: string }, key?: string) =>
    mutate<MutationReceipt>('POST', '/admin/doctors', body, undefined, key),
  setDoctorActive: (doctorId: number, isActive: boolean, key?: string) =>
    mutate<MutationReceipt>('PATCH', `/admin/doctors/${doctorId}`, { is_active: isActive }, undefined, key),
  assignPatient: (doctorId: number, patientId: number, key?: string) =>
    mutate<MutationReceipt>('POST', '/admin/assignments', { doctor_id: doctorId, patient_id: patientId }, undefined, key),
  revokeAssignment: (doctorId: number, patientId: number) =>
    json<void>(`/admin/assignments/${doctorId}/${patientId}`, { method: 'DELETE' }),
  importFixture: (fixtureKey: string, key?: string) =>
    mutate<MutationReceipt>('POST', '/admin/fixtures/imports', { fixture_key: fixtureKey }, undefined, key),
};
