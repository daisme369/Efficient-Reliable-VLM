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
});

test('mock admin gets the demo console only', async ({ page }) => {
  await signIn(page, 'admin@thorax.demo', PASSWORDS.admin, /\/admin$/);
  await expect(page.getByRole('tab', { name: 'Mock fixtures' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Patients' })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Activity log' })).toHaveCount(0);
});
