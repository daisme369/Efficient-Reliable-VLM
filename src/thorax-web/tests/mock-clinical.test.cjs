const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join, resolve } = require('node:path');
const { after, test } = require('node:test');

const temporaryRoot = resolve(tmpdir());
const output = mkdtempSync(join(temporaryRoot, 'thorax-mock-test-'));
execFileSync(process.execPath, [require.resolve('typescript/bin/tsc'),
  '--target', 'es2022', '--module', 'commonjs', '--moduleResolution', 'node',
  '--esModuleInterop', '--skipLibCheck', '--rootDir', '.', '--outDir', output,
  'lib/server/mock-clinical.ts', 'app/api/v1/[...path]/route.ts',
], { cwd: resolve(__dirname, '..'), stdio: 'pipe' });
after(() => {
  assert.equal(dirname(output), temporaryRoot);
  rmSync(output, { recursive: true, force: true });
});

const clinical = require(join(output, 'lib/server/mock-clinical.js'));
const { studies } = require(join(output, 'lib/server/fixtures.js'));

function fixture(view = 'PA') {
  const study = studies.find(row => row.images.some(image => image.view === view));
  const state = {
    sequence: 10000, secret: 'synthetic-test',
    users: [{ id: 1, is_active: true }], sessions: [],
    assignments: [{ doctor_id: 1, patient_id: study.patient_id, active: true }],
  };
  const { reading } = clinical.openStudy(state, study, 1);
  return { state, reading, image: study.images.find(image => image.view === view) };
}

function finishAfterMinute(state, reading, command) {
  const request = clinical.submit(state, reading, command);
  reading.jobs.at(-1).submittedAt -= 61000;
  request.deadline_at = new Date(Date.now() - 1000).toISOString();
  clinical.settleJobs(state);
  assert.equal(request.status, 'succeeded');
  assert.equal(request.error, null);
  return request;
}

test('late refresh completes Analyze, discussion and report without a false timeout', () => {
  for (const view of ['PA', 'AP', 'LAT']) {
    const { state, reading, image } = fixture(view);
    finishAfterMinute(state, reading, { operation: 'analyze', image_id: image.id });
    assert.equal(reading.boxes.length, view === 'LAT' ? 0 : 1);
    assert.ok(clinical.workspace(state, reading, { items: [], next_cursor: null }).preliminary_report.text);
    finishAfterMinute(state, reading, { operation: 'ask', image_id: image.id, question: 'Review this image.' });
    assert.equal(reading.messages.length, 2);
    clinical.saveFindings(state, reading, {
      items: reading.findings[0].items.map(item => ({ ...item, action: 'accepted' })),
      no_abnormality_confirmed: view === 'LAT',
    });
    for (const box of reading.boxes) box.review_action = 'accepted';
    clinical.approveFindings(state, reading, { findings_version: 1 });
    finishAfterMinute(state, reading, { operation: 'draft_report', findings_version: 1 });
    const workspace = clinical.workspace(state, reading, { items: [], next_cursor: null });
    assert.equal(workspace.reports[0].source, 'ai');
  }
});

test('late refresh preserves injected timeout, detector failure and revoked access', () => {
  for (const [failure, status, error] of [
    ['timeout', 'timed_out', 'AI_DEADLINE_EXCEEDED'],
    ['detector', 'failed', 'AI_UNAVAILABLE'],
    [undefined, 'cancelled', 'AUTHORIZATION_REVOKED'],
  ]) {
    const { state, reading, image } = fixture();
    const request = clinical.submit(state, reading, { operation: 'analyze', image_id: image.id });
    const job = reading.jobs[0];
    job.failure = failure;
    job.submittedAt -= 61000;
    request.deadline_at = new Date(Date.now() - 1000).toISOString();
    if (!failure) state.assignments[0].active = false;
    clinical.settleJobs(state);
    assert.equal(request.status, status);
    assert.equal(request.error.code, error);
    assert.equal(reading.findings.length, 0);
    assert.equal(reading.boxes.length, 0);
  }
});

test('manual review uses both gates and stales reports after an approved finding changes', () => {
  const { state, reading, image } = fixture();
  clinical.startManualFindings(state, reading, {});
  assert.equal(reading.analyzedImageId, null);
  assert.equal(reading.jobs.length, 0);
  assert.throws(() => clinical.startManualFindings(state, reading, {}), { code: 'FINDINGS_ALREADY_EXIST' });
  assert.throws(() => clinical.submit(state, reading, { operation: 'analyze', image_id: image.id }), { code: 'FINDINGS_ALREADY_EXIST' });
  assert.throws(() => clinical.createReport(state, reading, { findings_version: 1 }), { code: 'GATE1_REQUIRED' });
  clinical.saveFindings(state, reading, { items: [], no_abnormality_confirmed: true });
  clinical.approveFindings(state, reading, { findings_version: 1 });
  const first = clinical.createReport(state, reading, { findings_version: 1 });
  let workspace = clinical.workspace(state, reading, { items: [], next_cursor: null });
  assert.equal(workspace.reports[0].source, 'doctor');
  assert.equal(workspace.reports[0].content.study.analyzed_image_id, null);
  clinical.saveFindings(state, reading, { items: [], no_abnormality_confirmed: true });
  assert.equal(reading.reports[0].status, 'stale');
  assert.throws(() => clinical.editReport(state, reading, first.resource_id, { findings_version: 1 }, true), { code: 'REPORT_STALE' });
  clinical.approveFindings(state, reading, { findings_version: 2 });
  const current = clinical.createReport(state, reading, { findings_version: 2 });
  clinical.editReport(state, reading, current.resource_id, { findings_version: 2 }, true);
  workspace = clinical.workspace(state, reading, { items: [], next_cursor: null });
  assert.equal(workspace.session.status, 'final');
  assert.equal(workspace.analyzed_image_id, null);
  assert.equal(workspace.preliminary_report, null);
  assert.equal(reading.jobs.length, 0);
  assert.throws(() => clinical.checkWrite(new Request('http://localhost', { headers: { 'If-Match': `"${reading.session.revision}"` } }), reading), { code: 'FINAL_READ_ONLY' });
});

test('Next route persists manual findings, idempotent creation and a final report', async () => {
  const route = require(join(output, 'app/api/v1/[...path]/route.js'));
  const previous = Object.fromEntries(['THORAX_DEMO_STATE_PATH', 'THORAX_DEMO_DOCTOR_PASSWORD', 'THORAX_API_ORIGIN'].map(key => [key, process.env[key]]));
  process.env.THORAX_DEMO_STATE_PATH = join(output, 'synthetic-state.json');
  process.env.THORAX_DEMO_DOCTOR_PASSWORD = 'synthetic-route-pass';
  delete process.env.THORAX_API_ORIGIN;
  let cookie;
  let csrf;
  let revision;
  async function send(method, path, body, extra = {}) {
    const response = await route.POST(new Request(`http://127.0.0.1:3000/api/v1${path}`, {
      method,
      headers: { Origin: 'http://127.0.0.1:3000', 'Content-Type': 'application/json',
        ...(cookie ? { Cookie: cookie, 'X-CSRF-Token': csrf } : {}),
        ...(revision ? { 'If-Match': `"${revision}"`, 'Idempotency-Key': randomUUID() } : {}), ...extra },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }), { params: Promise.resolve({ path: path.slice(1).split('/') }) });
    if (response.headers.has('Set-Cookie')) cookie = response.headers.get('Set-Cookie').split(';')[0];
    const data = await response.json();
    csrf = data.csrf_token ?? csrf;
    revision = data.session_revision ?? data.session?.revision ?? revision;
    return { status: response.status, data };
  }
  try {
    assert.equal((await send('POST', '/auth/login', { email: 'doctor@thorax.demo', password: process.env.THORAX_DEMO_DOCTOR_PASSWORD })).status, 200);
    const opened = await send('POST', `/studies/${studies[0].id}/sessions`, {});
    assert.equal(opened.status, 201);
    const url = `/sessions/${opened.data.session.id}`;
    const startHeaders = { 'If-Match': `"${revision}"`, 'Idempotency-Key': randomUUID() };
    const started = await send('POST', url + '/findings', {}, startHeaders);
    assert.equal(started.status, 201);
    assert.deepEqual(await send('POST', url + '/findings', {}, startHeaders), started);
    assert.equal((await send('POST', url + '/findings/approve', { findings_version: 1 })).status, 409);
    assert.equal((await send('PATCH', url + '/findings', { items: [], no_abnormality_confirmed: true })).status, 200);
    assert.equal((await send('POST', url + '/findings/approve', { findings_version: 1 })).status, 200);
    const created = await send('POST', url + '/reports', { findings_version: 1 });
    assert.equal(created.status, 201);
    const reportUrl = url + `/reports/${created.data.resource_id}`;
    assert.equal((await send('PATCH', reportUrl, { impression: 'Synthetic reviewed assessment.' })).status, 200);
    assert.equal((await send('POST', reportUrl + '/approve', { findings_version: 1 })).status, 200);
    const restored = await send('GET', url);
    assert.equal(restored.data.session.status, 'final');
    assert.equal(restored.data.reports[0].source, 'doctor');
    assert.equal(restored.data.reports[0].content.impression, 'Synthetic reviewed assessment.');
    assert.equal(restored.data.analyzed_image_id, null);
    assert.equal(restored.data.latest_ai_request, null);
    assert.equal((await send('PATCH', reportUrl, { impression: 'Late change' })).data.error.code, 'FINAL_READ_ONLY');
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
