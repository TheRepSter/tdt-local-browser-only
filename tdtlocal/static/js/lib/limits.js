// The limits of a plugin frame in a browser (the desktop app's desktop/lib/plugin-limits.js, adapted; docs/PARIDAD-NAVEGADOR.md
// phase 4). Electron measures each frame's own process; a browser cannot see inside another frame. What a browser CAN measure
// from outside is the round trip of the «are you there?» ping — a frame whose event loop is blocked by a loop without a pause
// answers late — and, when the frame reports it, the JS heap of the page (performance.memory), which belongs to the whole page,
// so it is only blamed on a plugin when that plugin is the only frame mounted. Same shape as the desktop's watch: pure functions.

export const BROWSER_LIMITS = {
  memoryMB: 300, // the page's JS heap over this, in two looks in a row, and only if the plugin is the only frame: otherwise it is shared and not its fault
  stallMs: 3000, // a ping that takes longer than this to come back: the frame's event loop is busy...
  stallLooks: 3, // ...in this many looks in a row (a busy load at start is not a stuck loop)
  everyMs: 5000, // how often the page looks (BEAT_MS in plugin-host.js)
};

/**
 * The watch: give it each look and it says who went over. A plugin is reported once, until its frame is gone.
 * @param {{id: string, rttMs: number, memoryMB: number, shared: boolean}[]} perPlugin
 * @returns {{id: string, reason: 'memory' | 'cpu', detail: string}[]}
 */
export function createBrowserWatch(limits = BROWSER_LIMITS, now = () => Date.now()) {
  const state = new Map(); // id -> {stalls, memory}: consecutive looks over the limit
  const reported = new Set();
  return {
    look(perPlugin) {
      now();
      const present = new Set();
      const violations = [];
      for (const p of perPlugin || []) {
        present.add(p.id);
        const s = state.get(p.id) || { stalls: 0, memory: 0 };
        s.stalls = (p.rttMs || 0) > limits.stallMs ? s.stalls + 1 : 0;
        s.memory = !p.shared && (p.memoryMB || 0) > limits.memoryMB ? s.memory + 1 : 0;
        state.set(p.id, s);
        if (reported.has(p.id)) continue;
        if (s.stalls >= limits.stallLooks) {
          reported.add(p.id);
          violations.push({ id: p.id, reason: 'cpu', detail: `tarda ${Math.round(p.rttMs || 0)} ms en contestar al «¿estás ahí?» (más de ${limits.stallMs} ms, ${s.stalls} veces seguidas): su bucle no deja respirar` });
        } else if (s.memory >= 2) {
          reported.add(p.id);
          violations.push({ id: p.id, reason: 'memory', detail: `la pila JS de la página usa ${Math.round(p.memoryMB || 0)} MB y el tope es ${limits.memoryMB} MB` });
        }
      }
      for (const id of [...state.keys()]) {
        if (!present.has(id)) { state.delete(id); reported.delete(id); } // the frame is gone: next time it starts clean
      }
      return violations;
    },
  };
}
