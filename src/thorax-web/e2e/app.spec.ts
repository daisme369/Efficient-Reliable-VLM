import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { API_PORT, LABELING_ON, PASSWORDS, signIn, syntheticPng, WEB_PORT } from './accounts';

const png = (name: string, seed: number) => ({ name, mimeType: 'image/png', buffer: syntheticPng(seed) });

test('doctor reads a study: analyze runs and the review step opens', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await expect(page.getByRole('rowheader', { name: /Eleanor Brooks/ })).toBeVisible();
  await page.goto('/patients/1/studies/2411');
  await page.getByRole('button', { name: 'Analyze selected image' }).click();
  // The mock AI settles in about 1.5 s; then the preliminary report and the findings to review appear.
  await expect(page.getByText('Preliminary AI report')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: /Findings review \d+ findings/ })).toBeVisible();
});

test('technician pushes, edits, extends and deletes a study; the doctor only reads', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await page.getByRole('button', { name: 'Show studies for Daniel Morgan' }).click();
  await expect(page.locator('#patient-studies-4').getByRole('button', { name: 'Add study' })).toHaveCount(0);
  await page.getByRole('button', { name: /Sign out/ }).click();

  await signIn(page, 'technician@thorax.demo', PASSWORDS.technician, /\/intake$/);
  await expect(page.getByRole('heading', { name: 'Patient intake' })).toBeVisible();
  await page.getByRole('row', { name: /PX-1045/ }).getByRole('button', { name: 'Studies', exact: true }).click();
  const studies = page.locator('.study-cell');

  await studies.getByRole('button', { name: 'Add study' }).click();
  let dialog = page.getByRole('dialog');
  await dialog.getByLabel('Indication').fill('E2E upload');
  await dialog.locator('input[type=file]').setInputFiles([png('e2e-pa.png', 1)]);
  await dialog.getByRole('button', { name: 'Upload study' }).click();
  await expect(dialog.getByRole('alert')).toHaveText(/Confirm the images are synthetic/);
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Upload study' }).click();
  await expect(studies.getByText(/added with 1 image/)).toBeVisible();
  const row = studies.getByRole('row', { name: /E2E upload/ });

  await row.getByRole('button', { name: /Edit the study/ }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('Indication').fill('E2E upload, corrected');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(studies.getByRole('row', { name: /E2E upload, corrected/ })).toBeVisible();

  await studies.getByRole('row', { name: /E2E upload, corrected/ }).getByRole('button', { name: /Add images/ }).click();
  dialog = page.getByRole('dialog');
  await dialog.locator('input[type=file]').setInputFiles([png('e2e-lat.png', 2)]);
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Upload images' }).click();
  await expect(studies.getByText('The study now has 2 images.')).toBeVisible();

  await studies.getByRole('row', { name: /E2E upload, corrected/ }).getByRole('button', { name: /Edit the study/ }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Remove' }).last().click();
  await expect(dialog.getByRole('button', { name: 'Remove' })).toHaveCount(1);
  await dialog.getByRole('button', { name: 'Delete study…' }).click();
  await dialog.getByRole('button', { name: 'Delete study', exact: true }).click();
  await expect(studies.getByText('Study deleted.')).toBeVisible();
  await expect(studies.getByRole('row', { name: /E2E upload/ })).toHaveCount(0);
  await expect(page.getByRole('tab')).toHaveCount(0);  // no admin console, accounts or clinical screens
});

test('technician registers a patient and adds the first study straight away', async ({ page }) => {
  await signIn(page, 'technician@thorax.demo', PASSWORDS.technician, /\/intake$/);
  await page.getByRole('button', { name: 'Register new patient' }).click();
  await page.getByLabel('Full name').fill('E2E Intake Patient');
  await page.getByLabel('Birth year').fill('1980');
  await page.getByRole('button', { name: 'Register and add study' }).click();
  const dialog = page.getByRole('dialog');  // opens by itself for the new patient
  await expect(dialog.getByRole('heading', { name: /Add a study for E2E Intake Patient \(PX-\d+\)/ })).toBeVisible();
  await dialog.getByLabel('Indication').fill('E2E intake upload');
  await dialog.locator('input[type=file]').setInputFiles([png('e2e-intake.png', 4)]);
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Upload study' }).click();
  await expect(page.getByText(/added for E2E Intake Patient .*No doctor is assigned yet/)).toBeVisible();
  await page.getByPlaceholder('Search by name or patient code').fill('intake patient');
  const row = page.getByRole('row', { name: /E2E Intake Patient/ });
  await expect(row).toContainText('1980');
  await expect(row).toContainText('No doctor assigned');
});

test('admin creates a patient, adds a study, and sees it in the activity log', async ({ page }) => {
  await signIn(page, 'admin@thorax.demo', PASSWORDS.admin, /\/admin$/);
  await page.getByRole('tab', { name: 'Patients' }).click();
  await page.locator('#admin-patient-name').fill('E2E Patient');
  await page.getByRole('button', { name: 'Create patient' }).click();
  const created = page.getByText(/Created patient (PX-\d+)/);
  await expect(created).toBeVisible();
  const code = (await created.textContent())!.match(/PX-\d+/)![0];

  await page.getByRole('row', { name: new RegExp(code) }).getByRole('button', { name: 'Manage studies' }).click();
  await page.getByRole('button', { name: 'Add study' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Indication').fill('E2E admin upload');
  await dialog.locator('input[type=file]').setInputFiles([png('e2e-admin.png', 3)]);
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Upload study' }).click();
  await expect(page.locator('.nested-table').getByRole('row', { name: /E2E admin upload/ })).toBeVisible();

  await page.getByRole('tab', { name: 'Activity log' }).click();
  await page.getByLabel('Filter activity').selectOption({ label: 'Patients and uploads' });
  const upload = page.getByRole('row', { name: /Uploaded study/ }).first();
  await expect(upload).toContainText('Administrator');
  await expect(page.getByRole('row', { name: /Paused AI|Created account/ })).toHaveCount(0);
});

test('annotator labels an image and submits it for review', async ({ page }) => {
  test.skip(!LABELING_ON, 'labeling is off by default (THORAX_LABELING)');
  await signIn(page, 'annotator@thorax.demo', PASSWORDS.annotator, /\/labeling$/);
  await page.getByRole('button', { name: /Label next image/ }).click();
  await expect(page).toHaveURL(/\/labeling\/tasks\/\d+$/);
  const viewer = page.getByLabel('Image viewer');
  await expect(viewer).toBeVisible();
  const box = (await viewer.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByRole('button', { name: /^Box 1:/ })).toBeVisible();
  await page.getByRole('button', { name: 'Submit for review' }).click();
  await expect(page.getByText(/Awaiting review|submitted/i).first()).toBeVisible();
});

test('a second annotator reviews the submitted labels and approves them', async ({ page }) => {
  test.skip(!LABELING_ON, 'labeling is off by default (THORAX_LABELING)');
  await signIn(page, 'annotator2@thorax.demo', PASSWORDS.annotator, /\/labeling$/);
  await page.getByRole('link', { name: 'Review', exact: true }).first().click();
  await expect(page).toHaveURL(/\/labeling\/tasks\/\d+$/);
  await expect(page.getByRole('button', { name: /^Box 1:/ })).toBeVisible();
  await page.getByRole('button', { name: /^Approve/ }).click();
  await expect(page.getByText('Approved').first()).toBeVisible();
});

test('doctor completes a reading: discuss with AI, accept, approve findings, draft and sign the report', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await page.goto('/patients/2/studies/2421');
  await page.getByRole('button', { name: 'Analyze selected image' }).click();
  await expect(page.getByText('Preliminary AI report')).toBeVisible({ timeout: 20_000 });

  // Ask the AI in the discussion; its reply is saved to the discussion.
  await page.getByLabel('Clinical observation or question').fill('Is there a pleural effusion?');
  await page.getByRole('button', { name: 'Add to discussion' }).click();
  await expect(page.getByLabel('Saved study discussion').getByText(/Mock response/)).toBeVisible({ timeout: 20_000 });

  // Accept every AI suggestion, then approve the findings (Gate 1).
  await page.getByRole('tab', { name: 'Findings' }).click();
  for (let left = 0; left < 10 && await page.getByRole('button', { name: 'Accept', exact: true }).count(); left++) {
    await page.getByRole('button', { name: 'Accept', exact: true }).first().click();
  }
  await page.getByRole('button', { name: 'Save findings' }).click();  // accepting leaves unsaved changes
  // AI regions are reviewed in the viewer's region editor.
  await page.getByRole('button', { name: 'Left basilar region' }).click();
  await page.getByRole('button', { name: 'Accept region' }).click();
  await page.getByRole('button', { name: 'Approve findings (Gate 1)' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Approve findings' }).click();
  await page.getByRole('button', { name: /Go to report/ }).click();
  await page.getByRole('button', { name: 'Generate report' }).click();
  await expect(page.getByRole('button', { name: 'Approve final report (Gate 2)' })).toBeEnabled({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Approve final report (Gate 2)' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Approve final report' }).click();
  // Signing opens the read-only final report.
  await expect(page.getByRole('heading', { name: 'Final chest X-ray report' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('Final — read-only')).toBeVisible();
});

test('admin pauses AI, the doctor is told, and AI resumes', async ({ browser }) => {
  const admin = await browser.newPage();
  await signIn(admin, 'admin@thorax.demo', PASSWORDS.admin, /\/admin$/);
  await admin.getByRole('tab', { name: 'AI' }).click();
  await admin.getByRole('button', { name: 'Pause AI' }).click();
  await admin.getByRole('dialog').getByRole('button', { name: /Pause/ }).click();
  await expect(admin.getByText('AI paused').first()).toBeVisible();

  const doctor = await browser.newPage();
  await signIn(doctor, 'doctor2@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await doctor.goto('/patients/11/studies/2511');
  await doctor.getByRole('button', { name: 'Analyze selected image' }).click();
  await expect(doctor.getByText(/paused AI/)).toBeVisible({ timeout: 20_000 });

  await admin.getByRole('button', { name: 'Resume AI' }).click();
  await expect(admin.getByText('AI accepting requests')).toBeVisible();
});

test('a user changes their password and signs in with it', async ({ page }) => {
  const next = 'e2e-changed-pass-2';
  await signIn(page, 'technician@thorax.demo', PASSWORDS.technician, /\/intake$/);
  const change = async (from: string, to: string) => {
    await page.getByRole('button', { name: 'Change password' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Current password').fill(from);
    await dialog.getByLabel('New password', { exact: true }).fill(to);
    await dialog.getByLabel('Repeat new password').fill(to);
    await dialog.getByRole('button', { name: 'Change password' }).click();
    await expect(page.getByText('Your password was changed.')).toBeVisible();
  };
  await change(PASSWORDS.technician, next);
  await page.getByRole('button', { name: /Sign out/ }).click();
  await signIn(page, 'technician@thorax.demo', next, /\/intake$/);
  await change(next, PASSWORDS.technician);  // restore for reruns against the same data
});

/** The demo resident reads a study by manual review up to a saved draft report. */
async function residentDraft(page: import('@playwright/test').Page, patient: number, study: number) {
  await signIn(page, 'annotator@thorax.demo', PASSWORDS.annotator, LABELING_ON ? /\/labeling$/ : /\/worklist$/);
  await page.goto(`/patients/${patient}/studies/${study}`);
  await page.getByRole('button', { name: 'Start manual review' }).click();
  await page.getByLabel('No abnormal findings confirmed').check();
  await page.getByRole('button', { name: 'Save findings' }).click();
  await page.getByRole('button', { name: 'Approve findings (Gate 1)' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Approve findings' }).click();
  await page.getByRole('button', { name: /Go to report/ }).click();
  await page.getByRole('button', { name: 'Create report manually' }).click();
  await expect(page.getByRole('button', { name: 'Send to doctor' })).toBeEnabled({ timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Approve final report (Gate 2)' })).toHaveCount(0);  // only doctors sign
}

test('annotator sends a routine study to the doctor, who signs it', async ({ browser }) => {
  const resident = await (await browser.newContext()).newPage();
  await residentDraft(resident, 3, 2431);
  await resident.getByRole('button', { name: 'Send to doctor' }).click();
  await resident.getByRole('dialog').getByRole('button', { name: 'Send to doctor' }).click();
  await expect(resident.getByText('Sent to the doctor for sign-off. It is read-only for you now.')).toBeVisible();

  const doctor = await (await browser.newContext()).newPage();
  await signIn(doctor, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await doctor.getByRole('row', { name: /PX-1044/ }).getByRole('button', { name: /Show studies/ }).click();
  await expect(doctor.getByText('Awaiting sign-off')).toBeVisible();
  await doctor.goto('/patients/3/studies/2431?panel=report');
  await expect(doctor.getByText('Prepared by')).toBeVisible();
  await doctor.getByRole('button', { name: 'Approve final report (Gate 2)' }).click();
  await doctor.getByRole('dialog').getByRole('button', { name: 'Approve final report' }).click();
  await expect(doctor.getByRole('heading', { name: 'Final chest X-ray report' })).toBeVisible({ timeout: 20_000 });
  await doctor.goto('/patients/3/studies/2431?panel=findings');
  await expect(doctor.getByText(/^Approved by .+ \(resident\)\.$/)).toBeVisible();
  await doctor.goto('/worklist');
  await doctor.getByRole('row', { name: /PX-1044/ }).getByRole('button', { name: /Show studies/ }).click();
  await expect(doctor.locator('a[href="/patients/3/studies/2431/report"]')).toHaveText('View final report');
});

test('annotator releases a preliminary on an urgent study; doctor grades it', async ({ browser }) => {
  const resident = await (await browser.newContext()).newPage();
  await residentDraft(resident, 4, 2441);
  await resident.getByRole('button', { name: 'Release preliminary' }).click();
  const dialog = resident.getByRole('dialog');
  await dialog.getByLabel('Critical finding?').check();
  await dialog.getByLabel('Who was told, and how').fill('ER doctor, phone');
  await dialog.getByRole('button', { name: 'Release preliminary' }).click();
  await expect(resident.getByText('PRELIMINARY: not signed by a doctor')).toBeVisible();

  const doctor = await (await browser.newContext()).newPage();
  await signIn(doctor, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await doctor.goto('/patients/4/studies/2441?panel=report');
  await expect(doctor.getByText('PRELIMINARY: not signed by a doctor')).toBeVisible();
  await expect(doctor.getByText('Critical finding communicated to ER doctor, phone')).toBeVisible();
  const sign = doctor.getByRole('button', { name: 'Approve final report (Gate 2)' });
  await expect(sign).toBeDisabled();
  await doctor.getByLabel('Minor change').check();
  await expect(sign).toBeEnabled();
  await sign.click();
  await doctor.getByRole('dialog').getByRole('button', { name: 'Approve final report' }).click();
  await expect(doctor.getByRole('heading', { name: 'Final chest X-ray report' })).toBeVisible({ timeout: 20_000 });
  await expect(doctor.getByText('PRELIMINARY: not signed by a doctor')).toHaveCount(0);

  const admin = await (await browser.newContext()).newPage();
  await signIn(admin, 'admin@thorax.demo', PASSWORDS.admin, /\/admin$/);
  const stats = admin.locator('.admin-stats').filter({ hasText: 'Over-read by a doctor' });
  await expect(stats.locator('div', { hasText: 'Minor change' })).toContainText('100% of over-reads');
});

test('doctor finds a signed similar case and compares it side by side', async ({ page, playwright }) => {
  // Sign study 2451 (patient 5, PX-1046) through the API, the way tests/api/test_reading.py does.
  const web = `http://127.0.0.1:${WEB_PORT}`;
  const api = await playwright.request.newContext({ baseURL: `http://127.0.0.1:${API_PORT}`, extraHTTPHeaders: { Origin: web } });
  const login = await api.post('/api/v1/auth/login', { data: { email: 'doctor@thorax.demo', password: PASSWORDS.doctor } });
  expect(login.ok()).toBeTruthy();
  const csrf = { 'X-CSRF-Token': (await login.json()).csrf_token };
  const send = async (method: 'post' | 'patch', path: string, data: object | undefined, revision: number, key = true) => {
    const res = await api[method](path, { data, headers: { ...csrf, 'If-Match': `"${revision}"`, ...(key ? { 'Idempotency-Key': randomUUID() } : {}) } });
    expect(res.ok(), await res.text()).toBeTruthy();
    return res;
  };
  const opened = await api.post('/api/v1/studies/2451/sessions', { headers: csrf });
  expect(opened.ok()).toBeTruthy();
  let ws = await opened.json();
  const sid = ws.session.id;
  const settle = async (done: (w: any) => boolean) => {  // the real server runs the mock AI on wall-clock time
    await expect.poll(async () => { ws = await (await api.get(`/api/v1/sessions/${sid}`)).json(); return done(ws); }, { timeout: 20_000 }).toBe(true);
  };
  const pa = ws.images.find((i: { view: string }) => i.view === 'PA');
  await send('post', `/api/v1/sessions/${sid}/ai-requests`, { operation: 'analyze', image_id: pa.id }, ws.session.revision);
  await settle(w => w.latest_findings?.items?.length > 0);
  const findings = ws.latest_findings;
  let rev = (await (await send('patch', `/api/v1/sessions/${sid}/boxes/${ws.boxes[0].id}`, { review_action: 'accepted' }, ws.session.revision, false)).json()).session_revision;
  rev = (await (await send('patch', `/api/v1/sessions/${sid}/findings`, { items: findings.items.map((i: object) => ({ ...i, action: 'accepted' })), no_abnormality_confirmed: false }, rev, false)).json()).session_revision;
  rev = (await (await send('post', `/api/v1/sessions/${sid}/findings/approve`, { findings_version: findings.version }, rev)).json()).session_revision;
  await send('post', `/api/v1/sessions/${sid}/ai-requests`, { operation: 'draft_report', findings_version: findings.version }, rev);
  await settle(w => w.reports?.length > 0);
  await send('post', `/api/v1/sessions/${sid}/reports/${ws.reports[0].id}/approve`, { findings_version: findings.version }, ws.session.revision);
  await api.dispose();

  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await page.goto('/patients/6/studies/2461');
  await page.getByRole('button', { name: 'Analyze selected image' }).click();
  await expect(page.getByText('Preliminary AI report')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('heading', { name: 'Prior studies' })).toBeVisible();
  await page.getByRole('tab', { name: 'Similar cases' }).click();
  await page.getByRole('searchbox', { name: 'Search similar cases' }).fill('opacity');
  const row = page.getByRole('listitem').filter({ hasText: 'PX-1046' });
  await expect(row.getByText(/basilar opacity/)).toBeVisible();
  await row.getByRole('button', { name: /^Compare with/ }).click();
  const compare = page.getByRole('region', { name: 'Comparison case' });
  await expect(page.getByRole('region', { name: 'Study viewer' })).toHaveCount(2);
  await expect(compare.getByText('Assigned patient — for comparison only')).toBeVisible();
  await compare.getByRole('button', { name: 'Close compare' }).click();
  await expect(page.getByRole('region', { name: 'Study viewer' })).toHaveCount(1);
});
