// The limits a browser can actually see (tdtlocal/static/js/lib/limits.js): the ping's round trip for a stuck event loop,
// and the page's heap only when the plugin is the only frame mounted (it is shared otherwise, like in the desktop app).

import assert from 'node:assert/strict';

import { test } from 'node:test';

import { BROWSER_LIMITS, createBrowserWatch } from '../../tdtlocal/static/js/lib/limits.js';

test('a frame that answers late three times in a row is reported as cpu, once', () => {
  const watch = createBrowserWatch({ ...BROWSER_LIMITS, stallMs: 3000, stallLooks: 3 });
  const look = (rtt) => watch.look([{ id: 'x', rttMs: rtt, memoryMB: 0, shared: false }]);
  assert.deepEqual(look(100), []);
  assert.deepEqual(look(5000), []);
  assert.deepEqual(look(5000), []);
  const v = look(5000);
  assert.equal(v.length, 1);
  assert.equal(v[0].reason, 'cpu');
  assert.match(v[0].detail, /5000 ms/);
  assert.deepEqual(look(6000), [], 'reported once, until the frame is gone');
  watch.look([]); // the frame is gone
  assert.equal(watch.look([{ id: 'x', rttMs: 9000, memoryMB: 0, shared: false }]).length, 0, 'starts clean after the frame went away');
  assert.equal(watch.look([{ id: 'x', rttMs: 9000, memoryMB: 0, shared: false }]).length, 0);
  assert.equal(watch.look([{ id: 'x', rttMs: 9000, memoryMB: 0, shared: false }]).length, 1);
});

test('a spike is not a stuck loop, but a long one is', () => {
  const watch = createBrowserWatch({ ...BROWSER_LIMITS, stallMs: 3000, stallLooks: 3 });
  const looks = [9000, 10, 10, 9000, 9000, 9000].map((rtt) => watch.look([{ id: 'x', rttMs: rtt, memoryMB: 0, shared: false }]));
  assert.deepEqual(looks.slice(0, 5).flat(), [], 'the spike resets the count: three in a row are needed');
  assert.equal(looks[5].length, 1, 'the third 9000 in a row is reported');
  assert.deepEqual(watch.look([{ id: 'x', rttMs: 9000, memoryMB: 0, shared: false }]), [], 'and only once');
});

test('memory over the limit twice in a row is reported, but not when the heap is shared with other frames', () => {
  const watch = createBrowserWatch({ ...BROWSER_LIMITS, memoryMB: 300 });
  const look = (mem, shared) => watch.look([{ id: 'x', rttMs: 0, memoryMB: mem, shared }]);
  assert.deepEqual(look(400, false), []);
  const v = look(400, false);
  assert.equal(v.length, 1);
  assert.equal(v[0].reason, 'memory');
  const watch2 = createBrowserWatch({ ...BROWSER_LIMITS, memoryMB: 300 });
  assert.deepEqual(watch2.look([{ id: 'x', rttMs: 0, memoryMB: 900, shared: true }]), []);
  assert.deepEqual(watch2.look([{ id: 'x', rttMs: 0, memoryMB: 900, shared: true }]), [], 'shared: the heap is not only this plugin\'s');
});
