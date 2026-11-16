import { expect, test } from '@playwright/test';
import { PASSWORDS, signIn, syntheticPng } from './accounts';

const png = (name: string, seed: number) => ({ name, mimeType: 'image/png', buffer: syntheticPng(seed) });

test('doctor reads a study: analyze runs and the review step opens', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await expect(page.getByRole('rowheader', { name: /Eleanor Brooks/ })).toBeVisible();
  await page.goto('/patients/1/studies/2411');
  await page.getByRole('button', { name: 'Analyze selected image' }).click();
  // The mock AI settles in about 1.5 s; then the Review step opens with items to review.
  await expect(page.getByRole('tab', { name: /^Analyze Done/ })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('tab', { name: /^Review \d+ left/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Review this AI region.')).toBeVisible();
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
