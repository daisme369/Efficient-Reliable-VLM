import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { MOCK_PORT, PASSWORDS } from './e2e/accounts';

// The same app with its built-in mock API (no backend), as `pnpm dev` runs without THORAX_API_ORIGIN.
process.env.THORAX_E2E_MOCK_DIR ??= mkdtempSync(path.join(tmpdir(), 'thorax-e2e-mock-'));
const web = `http://127.0.0.1:${MOCK_PORT}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: 'mock.spec.ts',
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-mock' }]] : 'list',
  use: { baseURL: web, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: {
    command: `node node_modules/next/dist/bin/next build && node node_modules/next/dist/bin/next start --hostname 127.0.0.1 -p ${MOCK_PORT}`,
    url: `${web}/login`,
    timeout: 300_000,
    env: {
      ...process.env as Record<string, string>,
      THORAX_BUILD_DIR: '.next-build',
      THORAX_API_ORIGIN: '', // empty overrides a value in .env.local: use the mock API
      THORAX_DEMO_STATE_PATH: path.join(process.env.THORAX_E2E_MOCK_DIR, 'state.json'),
      THORAX_DEMO_DOCTOR_PASSWORD: PASSWORDS.doctor,
      THORAX_DEMO_ADMIN_PASSWORD: PASSWORDS.admin,
      NEXT_TELEMETRY_DISABLED: '1',
    },
  },
});
