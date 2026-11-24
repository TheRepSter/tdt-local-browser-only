// The approval questions of the browser mode (docs/PARIDAD-NAVEGADOR.md, phase 2 and 5): what the page shows before the
// user says yes to something that changes what runs — activating a plugin, installing one, a write an AI asks for.
// Pure functions: no DOM here, browser-plugins.js and the control bus draw the dialog with what these return.

/** The honest text every in-page dialog carries: in the desktop app these were system windows no page could fake. */
export const HONEST = 'Este diálogo lo dibuja la página de TDT local; en la app de escritorio era una ventana del sistema que ninguna página podía falsificar.';

/** One question at a time: a queue of thunks returning promises, run in order, each one waiting for the answer. */
export function createQueue() {
  let tail = Promise.resolve();
  return (fn) => {
    const run = tail.then(fn, fn);
    tail = run.then(() => {}, () => {});
    return run;
  };
}

const line = (text, cls = '') => ({ text: String(text ?? ''), cls });

/**
 * What the dialog shows for a review — an install inspection (`kind: 'install'`) or an activation (`kind: 'activate'`):
 * a title, the lines to read, the code preview (full plugins), the button and whether it is a dangerous one.
 */
export function planReview(review, kind = 'install') {
  const lines = [];
  lines.push(line(`${review.name || review.id}${review.version ? ' ' + review.version : ''}${review.author ? ' — de ' + review.author : ''}`, 'approve-head'));
  if (review.description) lines.push(line(review.description));
  if (review.replaces) lines.push(line(`Ya tienes «${review.id}»${review.oldVersion ? ' ' + review.oldVersion : ''} instalado: se reemplaza.`, 'field-error'));
  if (review.power) lines.push(line(review.power, 'field-error'));
  if (review.asks && review.asks.trim() !== '• ninguno') for (const l of review.asks.split('\n')) lines.push(line(l));
  if (review.sha256) lines.push(line(`SHA-256 del archivo: ${review.sha256}${review.pinned ? ' (fijado por quien lo publicó)' : ''}`, 'approve-sha'));
  if (review.hash) lines.push(line(`Huella del código (SHA-256 de sus archivos): ${review.hash}`, 'approve-sha'));
  if (review.signature && review.signature.text) lines.push(line(review.signature.text, review.signature.warning ? 'field-error' : ''));
  if (review.installs) lines.push(line(review.installs));
  lines.push(line(HONEST, 'dlg-hint'));
  return {
    title: kind === 'install' ? `Instalar «${review.name || review.id}»` : `Activar «${review.name || review.id}»`,
    lines,
    code: review.preview || '',
    approveLabel: kind === 'install' ? 'Instalar (no se activa solo)' : 'Activar',
    danger: !!review.full && !review.theme,
  };
}

/**
 * What to do after asking the server to turn a plugin on: `done` (it went on, or off, no question needed), `ask` (the user
 * must see the review first), or `error`. `review` is the answer of /api/plugins/review, when there is one.
 */
export function enablePlan(res, review) {
  if (res && res.ok) return { action: 'done', list: res.list };
  if (res && res.needsApproval && review && !review.error) return { action: 'ask', review };
  return { action: 'error', message: String((res && (res.detail || res.error)) || (review && review.error) || 'no se pudo activar') };
}
