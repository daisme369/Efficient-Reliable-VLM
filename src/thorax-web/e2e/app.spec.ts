import { expect, test } from '@playwright/test';
import { PASSWORDS, signIn, syntheticPng } from './accounts';

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

test('doctor uploads, edits, extends and deletes a study before reading it', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await page.getByRole('button', { name: 'Show studies for Daniel Morgan' }).click();
  const studies = page.locator('#patient-studies-4');

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
  await signIn(page, 'annotator2@thorax.demo', PASSWORDS.annotator, /\/labeling$/);
  const change = async (from: string, to: string) => {
    await page.getByRole('button', { name: 'Change password' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Current password').fill(from);
    await dialog.getByLabel('New password', { exact: true }).fill(to);
    await dialog.getByLabel('Repeat new password').fill(to);
    await dialog.getByRole('button', { name: 'Change password' }).click();
    await expect(page.getByText('Your password was changed.')).toBeVisible();
  };
  await change(PASSWORDS.annotator, next);
  await page.getByRole('button', { name: /Sign out/ }).click();
  await signIn(page, 'annotator2@thorax.demo', next, /\/labeling$/);
  await change(next, PASSWORDS.annotator);  // restore for reruns against the same data
});
