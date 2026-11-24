// Diario de tele: apunta solo qué canal hay puesto, desde cuándo y hasta cuándo (evento channel.changed + player.state),
// y pinta minutos por canal, lo de hoy y la lista reciente. Guarda en su storage (entradas, máximo 500) y puede
// exportar todo a un JSON. Permisos del manifest: player.read, storage, ui.toast.

const $ = (id) => document.getElementById(id);

const MAX_ENTRADAS = 500; // con {c, n, k, i, e} son pocos bytes; 500 entradas caben de sobra en los 256 KB
const POLL_MS = 30000; // repinta para que la duración de lo que estás viendo ahora se actualice sola

const estado = {
  entradas: [], // [{c: id, n: nombre, k: lista, i: inicio, e: fin | null}]
  abierta: null, // la entrada en curso (e === null)
  dias: 7,
};

const ahora = () => Math.floor(Date.now() / 1000);
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

function fmtDur(seg) {
  const min = Math.round(seg / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return `${h} h ${min % 60} min`;
}

const inicioDeHoy = () => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
};

async function persistir() {
  if (estado.entradas.length > MAX_ENTRADAS) estado.entradas = estado.entradas.slice(-MAX_ENTRADAS);
  await tdt.storage.set('entradas', estado.entradas);
}

async function cargar() {
  const conf = await tdt.config.all();
  estado.dias = Number(conf.dias) || 7;
  const corte = ahora() - estado.dias * 86400;
  estado.entradas = ((await tdt.storage.get('entradas')) || []).filter((x) => x.i >= corte);

  // Cierra lo que quedara abierto al apagar: o sigue siendo el canal puesto, o se cierra ahora.
  estado.abierta = estado.entradas.find((x) => x.e === null) || null;
  if (estado.abierta) {
    const tile = canalActual(await tdt.player.state());
    if (!tile || tile.id !== estado.abierta.c) {
      estado.abierta.e = ahora();
      estado.abierta = null;
      await persistir();
    }
  }
  if (!estado.abierta) {
    const tile = canalActual(await tdt.player.state());
    if (tile) estado.entradas.push({ c: tile.id, n: tile.name, k: tile.kind, i: ahora(), e: null }), estado.abierta = estado.entradas[estado.entradas.length - 1];
  }
}

const canalActual = (st) => st && Array.isArray(st.tiles) ? (st.tiles.find((t) => t.slot === st.focus) || st.tiles[0] || null) : null;

async function registrar(ev) {
  const t = ahora();
  if (estado.abierta) estado.abierta.e = t;
  const entrada = { c: ev.id, n: ev.name, k: ev.kind, i: t, e: null };
  estado.entradas.push(entrada);
  estado.abierta = entrada;
  await persistir();
  render();
}

function estadisticas() {
  const t = ahora();
  const porCanal = new Map();
  let total = 0, hoyMin = 0, hoyCambios = 0;
  for (const x of estado.entradas) {
    const dur = (x.e === null ? t : x.e) - x.i;
    if (dur < 0) continue;
    total += dur;
    porCanal.set(x.n, (porCanal.get(x.n) || 0) + dur);
    if (x.i >= inicioDeHoy()) { hoyMin += dur; hoyCambios++; }
  }
  const top = [...porCanal.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  return { total, hoyMin, hoyCambios, top };
}

function render() {
  const { total, hoyMin, hoyCambios, top } = estadisticas();
  $('resumen').textContent = estado.entradas.length
    ? `Hoy: ${hoyCambios} cambios de canal y ${fmtDur(hoyMin)} visto. En total: ${fmtDur(total)} en ${estado.entradas.length} ratos.`
    : 'Todavía no has puesto ningún canal con el diario activo.';

  const topUl = $('top');
  topUl.replaceChildren();
  if (!top.length) {
    const li = document.createElement('li');
    li.className = 'vacio';
    li.textContent = 'Cuando veas algo, aparece aquí.';
    topUl.append(li);
  }
  const max = top.length ? top[0][1] : 1;
  for (const [nombre, seg] of top) {
    const li = document.createElement('li');
    const fila = document.createElement('div');
    fila.className = 'top';
    const nom = document.createElement('span');
    nom.textContent = nombre;
    const dur = document.createElement('span');
    dur.textContent = fmtDur(seg);
    fila.append(nom, dur);
    const fondo = document.createElement('div');
    fondo.className = 'bar-fondo';
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.width = `${Math.max(3, Math.round((seg / max) * 100))}%`;
    fondo.append(bar);
    li.append(fila, fondo);
    topUl.append(li);
  }

  const lista = $('lista');
  lista.replaceChildren();
  const ultimas = estado.entradas.slice(-30).reverse();
  if (!ultimas.length) {
    const li = document.createElement('li');
    li.className = 'vacio';
    li.textContent = 'Sin cambios de canal todavía.';
    lista.append(li);
  }
  const t = ahora();
  for (const x of ultimas) {
    const li = document.createElement('li');
    const fila = document.createElement('div');
    fila.className = 'entry';
    const texto = document.createElement('span');
    texto.textContent = `${hhmm(x.i)}–${x.e === null ? 'ahora' : hhmm(x.e)} · ${x.n}`;
    const dur = document.createElement('span');
    dur.className = 'dur';
    dur.textContent = fmtDur((x.e === null ? t : x.e) - x.i);
    fila.append(texto, dur);
    li.append(fila);
    lista.append(li);
  }
}

function exportar() {
  const datos = {
    exportado: new Date().toISOString(),
    diasQueSeGuardan: estado.dias,
    entradas: estado.entradas.map((x) => ({
      canal: x.n, id: x.c, lista: x.k,
      desde: new Date(x.i * 1000).toISOString(),
      hasta: x.e === null ? null : new Date(x.e * 1000).toISOString(),
    })),
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(datos, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'diario-tele.json';
  a.click();
  URL.revokeObjectURL(url);
  tdt.ui.toast('Diario exportado a un archivo JSON');
}

(async () => {
  await tdt.ready;
  await cargar();
  tdt.on('channel.changed', (ev) => registrar(ev).catch((e) => console.warn(e)));
  tdt.on('config.changed', () => cargar().then(render).catch(() => {}));
  $('exportar').addEventListener('click', exportar);
  $('borrar').addEventListener('click', async () => {
    estado.entradas = [];
    estado.abierta = null;
    await tdt.storage.remove('entradas');
    await tdt.ui.toast('Diario borrado');
    render();
  });
  render();
  setInterval(render, POLL_MS); // solo repinta: la duración del canal en curso se calcula al vuelo
})().catch((error) => {
  $('resumen').textContent = error.message; // por ejemplo: permiso denegado: player.read
});
