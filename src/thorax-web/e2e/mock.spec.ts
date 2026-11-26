import { expect, test } from '@playwright/test';
import { PASSWORDS, signIn } from './accounts';

// Mock mode: the reading workflow works, and backend-only features stay hidden instead of failing.

test('mock doctor reads a study and sees no upload actions', async ({ page }) => {
  await signIn(page, 'doctor@thorax.demo', PASSWORDS.doctor, /\/worklist$/);
  await expect(page.getByText('Demo · mock AI')).toBeVisible();
  await page.getByRole('button', { name: 'Show studies for Eleanor Brooks' }).click();
  await expect(page.locator('#patient-studies-1').getByRole('row').nth(1)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add study' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Edit the study/ })).toHaveCount(0);
  await page.goto('/patients/1/studies/2411');
  await page.getByRole('button', { name: 'Analyze selected image' }).click();
  await expect(page.getByText('Preliminary AI report')).toBeVisible({ timeout: 20_000 });
  await page.getByRole('tab', { name: 'Similar cases' }).click();  // the mock has no signed cases: empty states, no errors
  await expect(page.getByText('Type a finding to search signed reports.')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search similar cases' }).fill('effusion');
  await expect(page.getByText('Nothing to search for: the text names no finding, or only negated ones.')).toBeVisible();
  await expect(page.getByText('Only signed reports are shown. Other patients are de-identified.')).toBeVisible();

  // A stubbed match opens the side-by-side compare next to the untouched study viewer.
  await page.route('**/api/v1/studies/*/case-search?*', route => route.fulfill({ json: { concepts: [{ id: 'pleural_effusion', label: 'Pleural effusion' }], prior: [], cases: [{
    case_id: 99, study_month: '2025-03', age_band: '60–69', sex: 'F', findings: ['Small right effusion.'], impression: 'Right effusion.', concepts: ['pleural_effusion'],
    snippet: 'Small right effusion', spans: [[12, 20]], image: { view: 'PA', width: 1, height: 1, media_type: 'image/png' } }] } }));
  await page.route('**/api/v1/cases/99/image', route => route.fulfill({ contentType: 'image/png',
    body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64') }));
  await page.getByRole('searchbox', { name: 'Search similar cases' }).fill('right effusion');
  await expect(page.getByText('Searching for:')).toBeVisible();
  await expect(page.getByText('2025-03 · 60–69 · F')).toBeVisible();
  await page.getByRole('button', { name: 'Compare with 2025-03 · 60–69 · F' }).click();
  const compare = page.getByRole('region', { name: 'Comparison case' });
  await expect(compare.getByText('De-identified case — for comparison only')).toBeVisible();
  await expect(compare.getByRole('img', { name: 'Chest X-ray, PA, 1 by 1 pixels' })).toBeVisible();
  await expect(compare.getByText('Right effusion.', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Study viewer' })).toHaveCount(2);
  await compare.getByRole('button', { name: 'Close compare' }).click();
  await expect(compare).toHaveCount(0);
});

test('mock admin gets the demo console only', async ({ page }) => {
  await signIn(page, 'admin@thorax.demo', PASSWORDS.admin, /\/admin$/);
  await expect(page.getByRole('tab', { name: 'Mock fixtures' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Patients' })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Activity log' })).toHaveCount(0);
});
