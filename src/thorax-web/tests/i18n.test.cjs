const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { after, test } = require('node:test');

const output = mkdtempSync(join(resolve(tmpdir()), 'thorax-i18n-test-'));
after(() => rmSync(output, { recursive: true, force: true }));
execFileSync(process.execPath, [require.resolve('typescript/bin/tsc'), '--target', 'es2022', '--module', 'commonjs', '--skipLibCheck',
  '--outDir', output, 'lib/i18n-vi.ts'], { cwd: resolve(__dirname, '..'), stdio: 'pipe' });
const { translate, VI } = require(join(output, 'i18n-vi.js'));

test('known UI text becomes Vietnamese and keeps its surrounding spaces', () => {
  assert.equal(translate(' Sign out'), ' Đăng xuất');
  assert.equal(translate('Clinical Discussion'), 'Thảo luận lâm sàng');
  assert.equal(translate('Too many sign-in attempts. Try again in 12 seconds.'), 'Đăng nhập sai quá nhiều lần. Thử lại sau 12 giây.');
});

test('clinical content and unknown text are left alone', () => {
  for (const text of ['Possible mild left basilar opacity.', 'Eleanor Brooks', 'PX-1042', '2026-11-06', '   ']) assert.equal(translate(text), null, text);
});

test('every entry has a non-empty Vietnamese value', () => {
  for (const [english, vietnamese] of Object.entries(VI)) assert.ok(vietnamese.trim(), english);
});

test('the Gate 1 approver line is translated whoever approved it', () => {
  assert.equal(translate('Approved by you.'), 'Bạn đã duyệt.');
  assert.equal(translate('Approved by Ana Resident (resident).'), 'Người duyệt: Ana Resident (bác sĩ nội trú).');
  assert.equal(translate('Approved by doctor 4.'), 'Người duyệt: bác sĩ 4.');
  assert.equal(translate('25% of over-reads'), '25% số ca đọc lại');
});
