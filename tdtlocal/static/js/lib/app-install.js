// «Instalar como aplicación» and the system notifications, the browser's substitute of the desktop app's own window and
// tray icon (docs/PARIDAD-NAVEGADOR.md, phase 8). In the desktop app (window.tdtDesktop) none of this is needed and none
// of it is shown. The tray icon and the global shortcuts of the OS do not exist in a browser; that is documented there.
//
// - The manifest (/manifest.webmanifest, display: standalone) plus a passthrough service worker (/sw.js) make the
//   browser offer the installation; the button uses beforeinstallprompt when it arrives and otherwise says to use the
//   browser's menu. Once installed, the app opens in its own window (display-mode: standalone) and the button hides.
// - The notifications (Notification API) are the browser's tray: they are asked for explicitly, only fire when the page
//   is not visible, and carry the app's icon.

import { $ } from '../dom.js';
import { store } from '../store.js';

const NOTIF_KEY = 'sysnotif';
let deferredInstall = null;

/** In a browser (not in the app's own window), wire the install button and the notifications checkbox. Returns notify(). */
export function initAppInstall(footMsg) {
  if (typeof window === 'undefined' || (window.tdtDesktop && window.tdtDesktop.plugins)) return () => {};
  const btn = $('btnInstall');
  const wrap = $('notifWrap');
  const chk = $('chkNotif');
  if (!btn || !wrap || !chk) return () => {};
  wrap.hidden = false;

  // the service worker is a pure passthrough; registering it is what makes the install prompt appear
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* without it the menu still offers «Instalar» in some browsers */ });
  }

  const standalone = () => window.matchMedia && window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true; // iOS Safari
  const paint = () => { btn.hidden = standalone() || !deferredInstall; };
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault(); // we show our own button instead of the browser's mini-infobar
    deferredInstall = e;
    paint();
  });
  window.addEventListener('appinstalled', () => {
    deferredInstall = null;
    paint();
    footMsg('TDT local instalado: a partir de ahora ábrelo desde su icono, en su propia ventana.');
  });
  btn.addEventListener('click', async () => {
    if (!deferredInstall) {
      footMsg('Usa el menú de tu navegador (⋮ o ≡ → «Instalar» / «Crear acceso directo» / «Añadir a la pantalla de inicio»).');
      return;
    }
    const prompt = deferredInstall;
    deferredInstall = null;
    paint();
    prompt.prompt();
    const choice = await prompt.userChoice.catch(() => null);
    footMsg(choice && choice.outcome === 'accepted'
      ? 'Instalando TDT local como aplicación…'
      : 'Instalación cancelada. También puedes usar el menú del navegador (⋮ → «Instalar»).');
  });
  paint();

  chk.checked = store.get(NOTIF_KEY, false) === true;
  chk.addEventListener('change', async () => {
    if (!chk.checked) { store.set(NOTIF_KEY, false); footMsg('Avisos del sistema desactivados.'); return; }
    const res = await requestNotificationPermission();
    if (res === 'granted') {
      store.set(NOTIF_KEY, true);
      footMsg('Avisos del sistema activados: los verás aunque la pestaña no esté visible.');
    } else {
      chk.checked = false;
      footMsg(res === 'denied'
        ? 'El navegador ha denegado los avisos (puedes permitirlos en los ajustes del sitio).'
        : 'Este navegador no permite avisos.');
    }
  });

  return (title, body) => sysNotify(title, body);
}

export async function requestNotificationPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported';
  if (Notification.permission === 'granted') return 'granted';
  if (Notification.permission === 'denied') return 'denied';
  try { return await Notification.requestPermission(); } catch { return 'unsupported'; }
}

/** A system notification (the browser's tray), only if allowed, only when the page is not visible. */
export function sysNotify(title, body) {
  if (typeof window === 'undefined' || !('Notification' in window)) return false;
  if (Notification.permission !== 'granted') return false;
  if (store.get(NOTIF_KEY, false) !== true) return false;
  if (!document.hidden) return false; // what is on screen is already on screen
  try {
    const n = new Notification(title, { body: String(body || ''), icon: '/icon.svg', tag: 'tdt-local' });
    n.onclick = () => { window.focus(); n.close(); };
    return true;
  } catch { return false; }
}
