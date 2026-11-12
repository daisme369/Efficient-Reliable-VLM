import { randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { AuthUser, MutationReceipt, Page, View } from '../../../../lib/contracts';
import {
  approveFindings, checkWrite, createBox, editBox, editReport, fields, integer, openStudy,
  saveFindings, settleJobs, string, studyDto, submit, workspace,
} from '../../../../lib/server/mock-clinical';
import {
  assigned, fail, hash, hashPassword, idleMs, idempotent, invalid, keyedHash, missing, MockError, nextId,
  passwordMatches, publicUser, readState, saveState, validPassword, type MockSession, type MockState, type MockUser,
} from '../../../../lib/server/mock-state';
import { fixtureChecksum, fixtureKey, imageAssets, patients, studies } from '../../../../lib/server/fixtures';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const cookieName = 'thorax_session';
type Body = Record<string, unknown>;
type RouteContext = { params: Promise<{ path: string[] }> };

function json(data: unknown, status = 200, headers?: HeadersInit) {
  const response = Response.json(data, { status, headers });
  response.headers.set('Cache-Control', 'no-store');
  return response;
}

function empty(headers?: HeadersInit) {
  const response = new Response(null, { status: 204, headers });
  response.headers.set('Cache-Control', 'no-store');
  return response;
}

function unavailable(status = 401): never {
  return fail(status, status === 401 ? 'UNAUTHENTICATED' : 'ROLE_FORBIDDEN', 'This resource is unavailable.');
}

function cookie(request: Request, value = '', clear = false) {
  const url = new URL(request.url);
  const secure = url.protocol === 'https:';
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (!secure && !loopback && !clear) fail(403, 'ROLE_FORBIDDEN', 'Use HTTPS or a loopback address to sign in.');
  return `${cookieName}=${value}; Path=/; HttpOnly; SameSite=Lax${secure ? '; Secure' : ''}${clear ? '; Max-Age=0' : ''}`;
}

function tokenFrom(request: Request) {
  const entry = request.headers.get('Cookie')?.split(';').find((item) => item.trimStart().startsWith(`${cookieName}=`));
  return entry?.trim().slice(cookieName.length + 1);
}

function csrf(state: MockState, token: string) {
  return keyedHash(state, `csrf:${token}`);
}

function sameSecret(expected: string, actual: string | null) {
  const expectedBytes = Buffer.from(expected);
  const actualBytes = Buffer.from(actual ?? '');
  return actualBytes.length === expectedBytes.length && timingSafeEqual(expectedBytes, actualBytes);
}

function originCheck(request: Request) {
  const url = new URL(request.url);
  const host = request.headers.get('Host') ?? '';
  const isLoopback = (value: string) => /^(localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$/i.test(value);
  // Next may normalize its request URL to localhost despite receiving a 127.0.0.1 Host.
  const expected = isLoopback(url.host) && isLoopback(host)
    ? new URL(`${url.protocol}//${host}`).origin
    : url.origin;
  if (request.headers.get('Origin') !== expected) {
    fail(403, 'CSRF_INVALID', 'Refresh the page before trying this action again.');
  }
}

function credentials(state: MockState, request: Request) {
  const token = tokenFrom(request);
  if (!token || !/^[a-f0-9]{64}$/.test(token)) return null;
  const tokenHash = hash(token);
  const login = state.logins[tokenHash];
  if (!login || login.expires <= Date.now()) return null;
  const user = state.users.find(({ id, is_active }) => id === login.userId && is_active);
  return user ? { user, login, token, tokenHash } : null;
}

function authPayload(state: MockState, user: MockUser, token: string, expires: number): AuthUser {
  return { ...publicUser(user), csrf_token: csrf(state, token), expires_at: new Date(expires).toISOString() };
}

function login(state: MockState, request: Request, body: Body) {
  fields(body, ['email', 'password']);
  const email = string(body.email, 'Email', 1, 254).trim().toLowerCase();
  const password = string(body.password, 'Password', 1, 72);
  const generic = 'Email or password is incorrect.';
  if (!validPassword(password, 8)) fail(401, 'UNAUTHENTICATED', generic);
  const now = Date.now();
  const accountKey = keyedHash(state, `login:${email}`);
  const address = request.headers.get('X-Forwarded-For')?.split(',')[0].trim() || 'loopback';
  const rateScopes = [
    { key: accountKey, windowMs: 15 * 60_000, max: 10, countOnlyFailures: true },
    { key: keyedHash(state, `login-address:${address}`), windowMs: 60_000, max: 5, countOnlyFailures: false },
  ];
  for (const scope of rateScopes) {
    const attempts = (state.failedLogins[scope.key] ?? []).filter((at) => now - at < scope.windowMs);
    state.failedLogins[scope.key] = attempts;
    if (attempts.length >= scope.max) {
      throw new MockError(429, 'RATE_LIMITED', 'Too many sign-in attempts. Wait before trying again.',
        Math.max(1, Math.ceil((attempts[0] + scope.windowMs - now) / 1000)));
    }
  }
  const user = state.users.find((entry) => entry.email === email);
  const valid = passwordMatches(password, user) && user?.is_active;
  for (const scope of rateScopes) if (!scope.countOnlyFailures || !valid) state.failedLogins[scope.key].push(now);
  if (!valid || !user) fail(401, 'UNAUTHENTICATED', generic);
  const token = randomBytes(32).toString('hex');
  const cookieHeader = cookie(request, token);
  const expires = now + idleMs;
  state.logins[hash(token)] = { userId: user.id, expires };
  return json(authPayload(state, user, token, expires), 200, { 'Set-Cookie': cookieHeader });
}

function checkedStudy(state: MockState, actor: MockUser, id: number) {
  const study = studies.find((row) => row.id === id);
  if (!study || !assigned(state, actor.id, study.patient_id)) return missing();
  return study;
}

function checkedReading(state: MockState, actor: MockUser, id: number) {
  const reading = state.sessions.find(({ session }) => session.id === id && session.doctor_id === actor.id);
  if (!reading || !assigned(state, actor.id, reading.session.patient_id)) return missing();
  return reading;
}

function id(value: string | undefined) {
  if (!value || !/^[1-9]\d*$/.test(value)) return missing();
  const number = Number(value);
  if (!Number.isSafeInteger(number)) return missing();
  return number;
}

function searchText(url: URL) {
  const raw = url.searchParams.get('search');
  return raw == null ? '' : string(raw, 'Search', 1, 100).trim().toLowerCase();
}

function page<T>(
  state: MockState, items: T[], request: Request, actor: MockUser, messageSession?: number,
): Page<T> {
  const url = new URL(request.url);
  const reverse = messageSession !== undefined;
  const rawLimit = url.searchParams.get('limit');
  const limit = rawLimit === null ? (reverse ? 50 : 25) : integer(Number(rawLimit), 'page limit');
  if (limit > (reverse ? 50 : 100)) invalid('The page size is too large.');
  const pathname = reverse ? `/api/v1/sessions/${messageSession}/messages` : url.pathname;
  const scope = `${actor.id}:${pathname}:${url.searchParams.get('search') ?? ''}`;
  const signed = (index: number) => `${index}.${keyedHash(state, `cursor:${scope}:${index}`)}`;
  const cursor = url.searchParams.get('cursor');
  let index = reverse ? items.length : 0;
  if (cursor) {
    const start = Number(cursor.split('.')[0]);
    if (!Number.isSafeInteger(start) || start < 0 || cursor !== signed(start)) invalid('Invalid page cursor.');
    index = start;
  }
  if (reverse) {
    const start = Math.max(0, index - limit);
    return { items: items.slice(start, index), next_cursor: start ? signed(start) : null };
  }
  const end = index + limit;
  return { items: items.slice(index, end), next_cursor: end < items.length ? signed(end) : null };
}

function imageAsset(view: View) {
  const filenames: Record<View, string> = { PA: 'pa.png', AP: 'ap.png', LAT: 'lat.png' };
  const asset = readFileSync(join(process.cwd(), 'lib', 'server', 'assets', filenames[view]));
  if (hash(asset) !== imageAssets[view].sha256) throw new Error('Fixture checksum mismatch');
  return asset;
}

function loadImage(state: MockState, actor: MockUser, imageId: number) {
  const study = studies.find(({ images }) => images.some(({ id }) => id === imageId));
  if (!study || !assigned(state, actor.id, study.patient_id)) return missing();
  const metadata = study.images.find(({ id }) => id === imageId)!;
  try {
    const image = imageAsset(metadata.view);
    return new Response(Uint8Array.from(image), {
      headers: { 'Content-Type': metadata.media_type, 'Content-Length': String(image.length), 'Cache-Control': 'no-store' },
    });
  } catch {
    return fail(503, 'ASSET_UNAVAILABLE', 'The study image is unavailable. Try again.');
  }
}

function admin(state: MockState, actor: MockUser, request: Request, segments: string[], body: Body) {
  if (actor.role !== 'admin') unavailable(403);
  const { method } = request;
  const [resource, target, child, extra] = segments;
  if (extra) return missing();
  if (method === 'GET' && resource === 'doctors' && target === undefined) {
    const doctors = state.users.filter(({ role }) => role === 'doctor').map((user) => ({
      ...publicUser(user),
      assigned_patient_ids: state.assignments.filter((row) => row.doctor_id === user.id && row.active).map(({ patient_id }) => patient_id),
    }));
    return json(page(state, doctors, request, actor));
  }
  if (method === 'GET' && resource === 'patient-catalog' && target === undefined) {
    const search = searchText(new URL(request.url));
    const catalog = patients.filter(({ code }) => code.toLowerCase().includes(search)).map(({ id, code }) => ({
      id, code,
      assigned_doctor_ids: state.assignments.filter((row) => row.patient_id === id && row.active).map(({ doctor_id }) => doctor_id),
    }));
    return json(page(state, catalog, request, actor));
  }
  if (resource === 'fixtures' && target === 'imports' && method === 'GET') {
    if (child !== fixtureKey) return missing();
    return json({
      fixture_key: fixtureKey, checksum: fixtureChecksum, status: 'loaded',
      patient_count: patients.length, study_count: studies.length,
      image_count: studies.reduce((sum, row) => sum + row.images.length, 0),
    });
  }
  if (resource === 'assignments' && method === 'DELETE') {
    fields(body, []);
    const doctor = id(target);
    const patient = id(child);
    const assignment = state.assignments.find((row) => row.doctor_id === doctor && row.patient_id === patient);
    if (assignment) assignment.active = false;
    settleJobs(state);
    return empty();
  }

  const creates = method === 'POST' && (
    ((resource === 'doctors' || resource === 'assignments') && target === undefined)
    || (resource === 'fixtures' && target === 'imports' && child === undefined)
  );
  const setsActive = method === 'PATCH' && resource === 'doctors' && target !== undefined && child === undefined;
  if (!creates && !setsActive) return missing();
  const result = idempotent(state, request, actor, body, creates, () => {
    let data: MutationReceipt;
    let status = 200;
    if (resource === 'doctors' && creates) {
      fields(body, ['name', 'email', 'password']);
      const name = string(body.name, 'Name', 1, 100).trim();
      const email = string(body.email, 'Email', 1, 254).trim().toLowerCase();
      const password = string(body.password, 'Password', 1, 72);
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) invalid('Enter a valid email address.');
      if (!validPassword(password, 12)) invalid('Use a password between 12 and 72 UTF-8 bytes.');
      if (state.users.some((user) => user.email === email)) fail(409, 'EMAIL_IN_USE', 'That email address is already in use.');
      const doctorId = nextId(state);
      state.users.push({ id: doctorId, name, email, role: 'doctor', is_active: true, password_hash: hashPassword(password) });
      data = { resource_type: 'doctor', resource_id: doctorId };
      status = 201;
    } else if (setsActive) {
      fields(body, ['is_active']);
      if (typeof body.is_active !== 'boolean') invalid('Choose active or disabled.');
      const doctor = state.users.find((row) => row.id === id(target) && row.role === 'doctor') ?? missing();
      doctor.is_active = body.is_active;
      if (!doctor.is_active) {
        for (const [token, login] of Object.entries(state.logins)) if (login.userId === doctor.id) delete state.logins[token];
        settleJobs(state);
      }
      data = { resource_type: 'doctor', resource_id: doctor.id };
    } else if (resource === 'assignments') {
      fields(body, ['doctor_id', 'patient_id']);
      const doctor_id = integer(body.doctor_id, 'doctor');
      const patient_id = integer(body.patient_id, 'patient');
      const doctor = state.users.find((row) => row.id === doctor_id && row.role === 'doctor');
      if (!doctor || !patients.some(({ id }) => id === patient_id)) return missing();
      if (!doctor.is_active) invalid('Choose an active doctor.');
      let assignment = state.assignments.find((row) => row.doctor_id === doctor_id && row.patient_id === patient_id);
      if (!assignment) {
        assignment = { id: nextId(state), doctor_id, patient_id, active: true };
        state.assignments.push(assignment);
      }
      assignment.active = true;
      data = { resource_type: 'assignment', resource_id: assignment.id };
      status = 201;
    } else {
      fields(body, ['fixture_key']);
      if (body.fixture_key !== fixtureKey) fail(422, 'FIXTURE_INVALID', 'Choose a fixture from the allowlist.');
      try {
        for (const view of ['PA', 'AP', 'LAT'] as const) imageAsset(view);
      } catch {
        fail(503, 'ASSET_UNAVAILABLE', 'The fixture could not be verified.');
      }
      data = { resource_type: 'fixture', resource_id: fixtureKey };
    }
    return { data, status };
  });
  return json(result.data, result.status);
}

function workspaceResponse(state: MockState, actor: MockUser, request: Request, reading: MockSession, status = 200) {
  return json(workspace(state, reading, page(state, reading.messages, request, actor, reading.session.id)), status, {
    ETag: `"${reading.session.revision}"`,
  });
}

function clinical(state: MockState, actor: MockUser, request: Request, segments: string[], body: Body) {
  if (actor.role !== 'doctor') unavailable(403);
  const { method } = request;
  const [resource, target, part, child, action, extra] = segments;
  if (extra) return missing();
  if (resource === 'patients' && method === 'GET') {
    if (!target) {
      const search = searchText(new URL(request.url));
      const authorized = patients.filter((patient) =>
        assigned(state, actor.id, patient.id) && [patient.name, patient.code].some((value) => value.toLowerCase().includes(search)),
      ).map((patient) => ({ ...patient, study_count: studies.filter(({ patient_id }) => patient_id === patient.id).length }));
      return json(page(state, authorized, request, actor));
    }
    if (part === 'studies' && !child) {
      const patientId = id(target);
      if (!assigned(state, actor.id, patientId)) return missing();
      const data = studies.filter(({ patient_id }) => patient_id === patientId).map((study) => studyDto(state, study, actor.id));
      return json(page(state, data, request, actor));
    }
  }
  if (resource === 'studies' && !child) {
    const study = checkedStudy(state, actor, id(target));
    if (method === 'GET' && !part) return json({ ...studyDto(state, study, actor.id), images: study.images });
    if (method === 'POST' && part === 'sessions') {
      fields(body, []);
      const { reading, status } = openStudy(state, study, actor.id);
      return workspaceResponse(state, actor, request, reading, status);
    }
  }
  if (resource === 'images' && method === 'GET' && part === 'content' && !child) return loadImage(state, actor, id(target));
  if (resource !== 'sessions') return missing();
  const reading = checkedReading(state, actor, id(target));
  if (method === 'GET') {
    if (!part) return workspaceResponse(state, actor, request, reading);
    if (part === 'messages' && !child) return json(page(state, reading.messages, request, actor, reading.session.id));
    if (part === 'events' && !child) return json(page(state, [...reading.events].reverse(), request, actor));
    if (part === 'ai-requests' && !action) {
      const job = reading.jobs.find(({ request }) => request.request_id === id(child)) ?? missing();
      return json(job.request);
    }
    return missing();
  }

  const startsAI = part === 'ai-requests' && method === 'POST' && !child;
  const addsBox = part === 'boxes' && method === 'POST' && !child;
  const changesBox = part === 'boxes' && (method === 'PATCH' || method === 'DELETE') && child && !action;
  const changesFindings = part === 'findings' && method === 'PATCH' && !child;
  const approvesFindings = part === 'findings' && method === 'POST' && child === 'approve' && !action;
  const changesReport = part === 'reports' && method === 'PATCH' && child && !action;
  const approvesReport = part === 'reports' && method === 'POST' && child && action === 'approve';
  if (!startsAI && !addsBox && !changesBox && !changesFindings && !approvesFindings && !changesReport && !approvesReport) return missing();

  const result = idempotent(state, request, actor, body, Boolean(startsAI || addsBox || approvesFindings || approvesReport), () => {
    checkWrite(request, reading, Boolean(addsBox || changesBox || changesFindings || approvesFindings));
    if (startsAI) return { data: submit(state, reading, body), status: 202 };
    if (addsBox) return { data: createBox(state, reading, body), status: 201 };
    if (changesBox) return { data: editBox(state, reading, id(child), body, method === 'DELETE'), status: 200 };
    if (changesFindings) return { data: saveFindings(state, reading, body), status: 200 };
    if (approvesFindings) return { data: approveFindings(state, reading, body), status: 200 };
    return { data: editReport(state, reading, id(child), body, Boolean(approvesReport)), status: 200 };
  });
  return json(result.data, result.status, { ETag: `"${result.data.session_revision}"` });
}

async function parseBody(request: Request): Promise<Body> {
  if (request.method === 'GET') return {};
  const content = await request.text();
  if (!content) return {};
  if (content.length > 300000 || !request.headers.get('Content-Type')?.startsWith('application/json')) {
    return invalid('Send a valid JSON request.');
  }
  try {
    const data: unknown = JSON.parse(content);
    if (!data || typeof data !== 'object' || Array.isArray(data)) invalid('Send a valid JSON request.');
    return data as Body;
  } catch {
    return invalid('Send a valid JSON request.');
  }
}

function dispatch(state: MockState, request: Request, path: string[], body: Body) {
  const { method } = request;
  const [area, action, extra] = path;
  if (method !== 'GET') originCheck(request);
  if (area === 'auth' && action === 'login' && !extra && method === 'POST') {
    if (!request.headers.get('Content-Type')?.startsWith('application/json')) invalid('Send a valid JSON request.');
    return login(state, request, body);
  }
  const auth = credentials(state, request);
  const logsOut = area === 'auth' && action === 'logout' && !extra && method === 'POST';
  if (!auth && logsOut) return empty({ 'Set-Cookie': cookie(request, '', true) });
  if (!auth) return unavailable();
  if (method !== 'GET' && !sameSecret(csrf(state, auth.token), request.headers.get('X-CSRF-Token'))) {
    fail(403, 'CSRF_INVALID', 'Refresh the page before trying this action again.');
  }
  if (logsOut) {
    fields(body, []);
    delete state.logins[auth.tokenHash];
    return empty({ 'Set-Cookie': cookie(request, '', true) });
  }
  const deliberatelyActive = method !== 'GET' || request.headers.get('X-User-Activity') === '1';
  if (area === 'auth' && action === 'me' && !extra && method === 'GET') {
    if (deliberatelyActive) auth.login.expires = Date.now() + idleMs;
    return json(authPayload(state, auth.user, auth.token, auth.login.expires));
  }
  const response = area === 'admin'
    ? admin(state, auth.user, request, path.slice(1), body)
    : clinical(state, auth.user, request, path, body);
  if (deliberatelyActive) auth.login.expires = Date.now() + idleMs;
  return response;
}

function scheduleJob() {
  for (const delay of [300, 1450, 60100]) {
    setTimeout(() => {
      try {
        const { state, previous } = readState();
        settleJobs(state);
        saveState(state, previous);
      } catch {
        // The next authorized workspace refresh retries settlement from the saved state.
      }
    }, delay).unref();
  }
}

async function handle(request: Request, context: RouteContext) {
  const { path } = await context.params;
  try {
    if (process.env.THORAX_API_ORIGIN) fail(503, 'API_UNAVAILABLE', 'The configured API did not respond.');
    if (path.length === 1 && path[0] === 'health' && request.method === 'GET') return json({ status: 'ok' });
    const body = await parseBody(request);
    const { state, previous } = readState();
    settleJobs(state);
    saveState(state, previous);
    const baseline = JSON.stringify(state);
    try {
      const response = dispatch(state, request, path, body);
      saveState(state, baseline);
      if (response.status === 202) scheduleJob();
      return response;
    } catch (error) {
      if (path.join('/') === 'auth/login' && error instanceof MockError && [401, 429].includes(error.status)) {
        saveState(state, baseline);
      }
      throw error;
    }
  } catch (error) {
    const request_id = randomBytes(8).toString('hex');
    if (error instanceof MockError) {
      const headers = error.retryAfter ? { 'Retry-After': String(error.retryAfter) } : undefined;
      return json({ error: { code: error.code, message: error.message, request_id, details: [] } }, error.status, headers);
    }
    return json({
      error: { code: 'INTERNAL_ERROR', message: 'The request could not be completed. Try again.', request_id, details: [] },
    }, 500);
  }
}

export { handle as DELETE, handle as GET, handle as PATCH, handle as POST };
