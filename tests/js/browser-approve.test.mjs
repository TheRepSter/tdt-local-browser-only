// The approval questions of the browser mode (tdtlocal/static/js/lib/approve.js): the queue is one at a time, the review
// plan shows what the server said (permissions, code preview, hashes, the honest text), and the enable flow decides what
// to do next from the server's answer.

import assert from 'node:assert/strict';

import { test } from 'node:test';

import { HONEST, createQueue, enablePlan, planReview } from '../../tdtlocal/static/js/lib/approve.js';

test('the queue asks one question at a time and keeps going after a refusal', async () => {
  const ask = createQueue();
  const order = [];
  const p1 = ask(async () => { order.push('1-start'); await new Promise((r) => setTimeout(r, 20)); order.push('1-end'); return true; });
  const p2 = ask(async () => { order.push('2-start'); return false; });
  const p3 = ask(async () => { order.push('3-start'); return true; });
  assert.deepEqual(await Promise.all([p1, p2, p3]), [true, false, true]);
  assert.deepEqual(order, ['1-start', '1-end', '2-start', '3-start'], 'the second waits for the first to be answered');
});

test('an install review shows what the plugin asks, the hashes and the honest text', () => {
  const plan = planReview({
    id: 'de-prueba', name: 'De prueba', version: '1.0.0', author: 'tests', description: 'lo que hace',
    kind: 'ui', full: false, asks: '  • ver qué estás viendo\n  • guardar sus propios datos', sha256: 'ab'.repeat(32),
    pinned: true, signature: { text: 'Firmado por la clave 1234…', warning: false }, installs: 'Instalarlo no lo activa.',
  }, 'install');
  assert.equal(plan.title, 'Instalar «De prueba»');
  assert.equal(plan.approveLabel, 'Instalar (no se activa solo)');
  assert.ok(!plan.danger);
  assert.equal(plan.code, '');
  const texts = plan.lines.map((l) => l.text);
  assert.ok(texts.some((t) => t.includes('De prueba 1.0.0 — de tests')));
  assert.ok(texts.some((t) => t.includes('ver qué estás viendo')));
  assert.ok(texts.some((t) => t.includes('SHA-256 del archivo: ' + 'ab'.repeat(32) + ' (fijado')));
  assert.ok(texts.some((t) => t.includes('Firmado por la clave 1234…')));
  assert.ok(texts.some((t) => t === HONEST), 'every dialog carries the honest text');
});

test('a full plugin review shows the code preview, the fingerprint and marks the button dangerous', () => {
  const plan = planReview({
    id: 'full-x', name: 'Full X', kind: 'full', full: true, theme: false, power: 'Es un plugin con TODO el poder…',
    preview: 'tdt.on(...)', hash: 'cd'.repeat(32), asks: '  • ninguno', signature: { text: 'Sin firma de autor', warning: true },
  }, 'activate');
  assert.equal(plan.title, 'Activar «Full X»');
  assert.equal(plan.code, 'tdt.on(...)');
  assert.ok(plan.danger);
  assert.ok(plan.lines.some((l) => l.text.includes('TODO el poder') && l.cls === 'field-error'));
  assert.ok(plan.lines.some((l) => l.text.includes('Huella del código') && l.text.includes('cd'.repeat(32))));
  assert.ok(plan.lines.some((l) => l.cls === 'field-error' && l.text === 'Sin firma de autor'), 'a warning signature is shown as such');
});

test('a theme review is not dangerous', () => {
  const plan = planReview({ id: 'tema', name: 'Tema', kind: 'full', full: true, theme: true, power: 'Es un tema…', asks: '' }, 'activate');
  assert.ok(!plan.danger);
});

test('enablePlan: on, off and refusal are done without asking; a plugin needing approval asks for the review', () => {
  assert.deepEqual(enablePlan({ ok: true, list: { plugins: [] } }, null), { action: 'done', list: { plugins: [] } });
  assert.deepEqual(enablePlan({ ok: true }, null), { action: 'done', list: undefined });
  const ask = enablePlan({ ok: false, detail: 'necesita aprobación del usuario', needsApproval: true }, { id: 'x', signature: 's' });
  assert.equal(ask.action, 'ask');
  assert.equal(ask.review.signature, 's');
  const err = enablePlan({ ok: false, detail: 'el plugin tiene problemas' }, null);
  assert.equal(err.action, 'error');
  assert.equal(err.message, 'el plugin tiene problemas');
  const errNoReview = enablePlan({ ok: false, needsApproval: true }, { error: 'no está' });
  assert.equal(errNoReview.action, 'error');
  assert.equal(errNoReview.message, 'no está');
});
