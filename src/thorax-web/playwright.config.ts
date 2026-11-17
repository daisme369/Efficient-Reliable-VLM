import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { API_PORT, PASSWORDS, WEB_PORT } from './e2e/accounts';

// End-to-end tests against the real backend with a fresh synthetic database per run.
// The production build goes to .next-build, so a running `pnpm dev` (.next) is not disturbed.
const repo = path.resolve(__dirname, '../..');
// Workers re-read this file; the env var keeps one data folder for the whole run.
process.env.THORAX_E2E_DIR ??= mkdtempSync(path.join(tmpdir(), 'thorax-e2e-'));
const data = process.env.THORAX_E2E_DIR;
const python = process.env.PYTHON ?? 'python';
const web = `http://127.0.0.1:${WEB_PORT}`;
const api = `http://127.0.0.1:${API_PORT}`;

export default defineConfig({
  testDir: './e2e',
  testIgnore: 'mock.spec.ts', // runs with playwright.mock.config.ts
  fullyParallel: false,
  workers: 1, // one active login per account: tests share accounts, so they run in order
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: { baseURL: web, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `${python} -m thorax_api.accounts seed-demo && ${python} scripts/serve_api.py`,
      cwd: repo,
      url: `${api}/api/v1/health`,
      timeout: 120_000,
      env: {
        ...process.env as Record<string, string>,
        PYTHONPATH: 'src',
        THORAX_DB_PATH: path.join(data, 'thorax.sqlite3'),
        THORAX_IMAGE_ROOT: path.join(data, 'images'),
        THORAX_API_PORT: String(API_PORT),
        THORAX_WEB_ORIGINS: web,
        THORAX_SIGNIN_PER_ADDRESS_PER_MINUTE: '100', // every test signs in from 127.0.0.1
        THORAX_DEMO_DOCTOR_PASSWORD: PASSWORDS.doctor,
        THORAX_DEMO_ADMIN_PASSWORD: PASSWORDS.admin,
        THORAX_DEMO_ANNOTATOR_PASSWORD: PASSWORDS.annotator,
      },
    },
    {
      command: `node node_modules/next/dist/bin/next build && node node_modules/next/dist/bin/next start --hostname 127.0.0.1 -p ${WEB_PORT}`,
      url: `${web}/login`,
      timeout: 300_000,
      env: { ...process.env as Record<string, string>, THORAX_BUILD_DIR: '.next-build', THORAX_API_ORIGIN: api, NEXT_TELEMETRY_DISABLED: '1' },
    },
  ],
});
