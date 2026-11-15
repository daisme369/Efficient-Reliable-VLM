const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join, resolve } = require('node:path');
const { after, test } = require('node:test');

const temporaryRoot = resolve(tmpdir());
const output = mkdtempSync(join(temporaryRoot, 'thorax-viewport-test-'));
after(() => {
  assert.equal(dirname(output), temporaryRoot);
  rmSync(output, { recursive: true, force: true });
});
execFileSync(process.execPath, [require.resolve('typescript/bin/tsc'),
  '--target', 'es2022', '--module', 'commonjs', '--skipLibCheck',
  '--outDir', output, 'features/viewer/imageCoordinates.ts',
], { cwd: resolve(__dirname, '..'), stdio: 'pipe' });
const { clampView, imageTransform, toSource, wheelView, zoomAt, drawBounds, moveBounds, resizeBounds } = require(join(output, 'features/viewer/imageCoordinates.js'));

const image = { width: 800, height: 880 };
const viewport = { width: 800, height: 600 };
const center = { x: 400, y: 300 };
const initial = { zoom: 1, pan: { x: 0, y: 0 } };
const wheel = (overrides = {}) => ({ deltaX: 0, deltaY: 100, deltaMode: 0, ctrlKey: false, metaKey: false, shiftKey: false, ...overrides });
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);

function contained(image, viewport, view) {
  const transform = imageTransform(image, viewport, view);
  for (const [origin, length, available] of [[transform.x, image.width * transform.scale, viewport.width], [transform.y, image.height * transform.scale, viewport.height]]) {
    if (length <= available) close(origin, (available - length) / 2);
    else {
      assert.ok(origin <= 1e-8, 'image starts beyond viewport edge');
      assert.ok(origin + length >= available - 1e-8, 'image ends before viewport edge');
    }
  }
}

test('extreme pan stays at image edges on both axes, including below-fit zoom', () => {
  for (const image of [{ width: 800, height: 880 }, { width: 1200, height: 400 }]) {
    for (const viewport of [{ width: 800, height: 500 }, { width: 400, height: 900 }]) {
      for (const zoom of [0.25, 1, 1.2, 2, 8]) {
        for (const x of [-100000, 0, 100000]) for (const y of [-100000, 0, 100000]) {
          contained(image, viewport, clampView(image, viewport, { zoom, pan: { x, y } }));
        }
      }
    }
  }
});

test('plain wheel pans vertically; Shift-wheel pans horizontally in either browser event form', () => {
  const view = { zoom: 3, pan: { x: 0, y: 0 } };
  assert.deepEqual(wheelView(image, viewport, view, center, wheel()).pan, { x: 0, y: -100 });
  assert.deepEqual(wheelView(image, viewport, view, center, wheel({ shiftKey: true })).pan, { x: -100, y: 0 });
  assert.deepEqual(wheelView(image, viewport, view, center, wheel({ shiftKey: true, deltaX: 100, deltaY: 0 })).pan, { x: -100, y: 0 });
  assert.deepEqual(wheelView(image, viewport, view, center, wheel({ deltaY: -100 })).pan, { x: 0, y: 100 });
  assert.equal(wheelView(image, viewport, view, center, wheel()).zoom, view.zoom);
});

test('wheel normalizes line, page and trackpad deltas, and stops at each edge', () => {
  const view = { zoom: 8, pan: { x: 0, y: 0 } };
  close(wheelView(image, viewport, view, center, wheel({ deltaMode: 1, deltaY: 3 })).pan.y, -48);
  close(wheelView(image, viewport, view, center, wheel({ deltaMode: 2, deltaY: 1 })).pan.y, -600);
  close(wheelView(image, viewport, view, center, wheel({ deltaX: 2.5, deltaY: 0 })).pan.x, -2.5);
  close(wheelView(image, viewport, view, center, wheel({ shiftKey: true, deltaMode: 2, deltaY: 1 })).pan.x, -800);
  for (const deltaY of [-100000, 100000]) {
    contained(image, viewport, wheelView(image, viewport, view, center, wheel({ deltaY })));
    contained(image, viewport, wheelView(image, viewport, view, center, wheel({ deltaY, shiftKey: true })));
  }
  const fitted = wheelView(image, viewport, initial, center, wheel());
  close(fitted.pan.x, 0); close(fitted.pan.y, 0);
});

test('Ctrl-wheel zooms at the pointer without moving its source coordinate', () => {
  const anchor = { x: 450, y: 350 };
  const view = { zoom: 2, pan: { x: 0, y: 0 } };
  const before = toSource(anchor, imageTransform(image, viewport, view));
  const afterView = wheelView(image, viewport, view, anchor, wheel({ ctrlKey: true, deltaY: -100 }));
  assert.ok(afterView.zoom > view.zoom);
  const after = toSource(anchor, imageTransform(image, viewport, afterView));
  close(before.x, after.x); close(before.y, after.y);
  assert.ok(wheelView(image, viewport, afterView, anchor, wheel({ ctrlKey: true })).zoom < afterView.zoom);
});

test('zoom limits and zooming back to fit remove stale pan', () => {
  const edge = clampView(image, viewport, { zoom: 8, pan: { x: 100000, y: -100000 } });
  const minimum = wheelView(image, viewport, edge, center, wheel({ ctrlKey: true, deltaY: 100000 }));
  assert.equal(minimum.zoom, 0.25);
  close(minimum.pan.x, 0); close(minimum.pan.y, 0);
  const maximum = wheelView(image, viewport, initial, center, wheel({ ctrlKey: true, deltaY: -100000 }));
  assert.equal(maximum.zoom, 8);
  contained(image, viewport, maximum);
  const fitted = zoomAt(image, viewport, edge, center, 1 / 8);
  assert.equal(fitted.zoom, 1);
  close(fitted.pan.x, 0); close(fitted.pan.y, 0);
});

test('zoom before measurement or while the stage is collapsed cannot produce NaN pan', () => {
  for (const viewport of [{ width: 0, height: 600 }, { width: 800, height: 0 }]) {
    assert.deepEqual(zoomAt(image, viewport, initial, center, 1.2), initial);
    assert.deepEqual(wheelView(image, viewport, initial, center, wheel({ ctrlKey: true })), initial);
  }
});

test('resizing the viewport re-centers an axis that no longer overflows', () => {
  const narrow = { width: 400, height: 400 };
  const wide = { width: 1000, height: 400 };
  const view = clampView(image, narrow, { zoom: 2, pan: { x: 10000, y: -10000 } });
  assert.ok(view.pan.x > 0);
  const resized = clampView(image, wide, view);
  assert.equal(resized.pan.x, 0);
  contained(image, wide, resized);
  assert.equal(clampView(image, narrow, resized).pan.x, 0);
});

test('box source coordinates survive pan and zoom and remain valid while editing', () => {
  let view = zoomAt(image, viewport, initial, center, 3);
  view = wheelView(image, viewport, view, center, wheel({ deltaY: 80 }));
  view = wheelView(image, viewport, view, center, wheel({ deltaY: -60, shiftKey: true }));
  const transform = imageTransform(image, viewport, view);
  const box = [100, 150, 450, 600];
  const source = (x, y) => toSource({ x: transform.x + x * transform.scale, y: transform.y + y * transform.scale }, transform);
  const start = source(box[0], box[1]), end = source(box[2], box[3]);
  drawBounds(start, end, image).forEach((value, index) => close(value, box[index]));
  assert.deepEqual(moveBounds(box, { x: -1000, y: 1000 }, image), [0, 430, 350, 880]);
  assert.deepEqual(resizeBounds(box, { x: 1000, y: 1000 }, 'bottom-right', image), [100, 150, 800, 880]);
});
