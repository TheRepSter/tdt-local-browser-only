// Agenda: qué dan ahora y después en los canales de tu agenda, con recordatorios (te avisa cuando empiece un programa
// marcado; un clic en el canal lo pone). Solo usa la API del SDK con los permisos del manifest: channels.read, epg.read,
// player.read/control, storage, ui.toast. Nada de red propia.

const $ = (id) => document.getElementById(id);
const MAX_CANALES = 10; // epg.now es por canal: más canales serían demasiadas llamadas
const POLL_MS = 30000; // vigila los recordatorios cada 30 s

const estado = {
  canales: [], // [{id, name}]
  ahora: {}, // channel id -> {channel, now, next}
  recordatorios: [], // [{channel, titulo, start, avisado}]
  avisarMin: 5,
};

const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

async function guardar() {
  await tdt.storage.set('canales', estado.canales);
  await tdt.storage.set('recordatorios', estado.recordatorios.map(({ channel, titulo, start }) => ({ channel, titulo, start })));
}

async function cargar() {
  estado.canales = (await tdt.storage.get('canales')) || [];
  estado.recordatorios = ((await tdt.storage.get('recordatorios')) || []).map((r) => ({ ...r, avisado: false }));
  const conf = await tdt.config.all();
  estado.avisarMin = Number(conf.avisarMin) || 0;
}

async function refrescar() {
  const lista = $('lista');
  lista.replaceChildren();
  if (!estado.canales.length) {
    const li = document.createElement('li');
    li.className = 'vacio';
    li.textContent = 'Añade canales con el buscador de arriba.';
    lista.append(li);
  }
  for (const c of estado.canales) {
    let epg = null;
    try { epg = await tdt.epg.now(c.id); } catch { /* sin guía */ }
    estado.ahora[c.id] = epg;
    const li = document.createElement('li');
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'star';
    star.title = 'Recordar el programa que dan ahora';
    const marcado = estado.recordatorios.some((r) => r.channel === c.id && epg && epg.now && r.titulo === epg.now.title && r.start === epg.now.start);
    star.textContent = marcado ? '★' : '☆';
    if (marcado) star.classList.add('marcado');
    star.addEventListener('click', () => marcar(c, epg, marcado));
    const nombre = document.createElement('span');
    nombre.className = 'canal';
    nombre.textContent = c.name;
    nombre.title = 'Poner este canal';
    nombre.addEventListener('click', () => tdt.player.play(c.id).catch((e) => tdt.ui.toast(e.message)));
    li.append(star, nombre);
    if (epg && epg.now) {
      const p = document.createElement('span');
      p.className = 'prog';
      p.textContent = `${hhmm(epg.now.start)}–${hhmm(epg.now.end)} · ${epg.now.title}` + (epg.next ? `  →  ${hhmm(epg.next.start)} ${epg.next.title}` : '');
      li.append(p);
    }
    lista.append(li);
  }
  pintarRecordatorios();
}

function pintarRecordatorios() {
  const caja = $('recordatorios');
  caja.replaceChildren();
  const activos = estado.recordatorios.filter((r) => !r.avisado && r.start + 3600 > Date.now() / 1000);
  if (!activos.length) return;
  const h = document.createElement('h2');
  h.textContent = 'Recordatorios';
  caja.append(h);
  const ul = document.createElement('ul');
  for (const r of activos) {
    const li = document.createElement('li');
    li.textContent = `${hhmm(r.start)} · ${r.titulo} (${nombreCanal(r.channel)})`;
    const quita = document.createElement('button');
    quita.type = 'button';
    quita.textContent = '✕';
    quita.title = 'Quitar el recordatorio';
    quita.addEventListener('click', async () => {
      estado.recordatorios = estado.recordatorios.filter((x) => x !== r);
      await guardar();
      pintarRecordatorios();
      refrescar();
    });
    li.append(quita);
    ul.append(li);
  }
  caja.append(ul);
}

const nombreCanal = (id) => (estado.canales.find((c) => c.id === id) || {}).name || id;

async function marcar(c, epg, yaMarcado) {
  if (!epg || !epg.now) return tdt.ui.toast('Ese canal no tiene guía ahora mismo');
  if (yaMarcado) {
    estado.recordatorios = estado.recordatorios.filter((r) => !(r.channel === c.id && r.titulo === epg.now.title && r.start === epg.now.start));
  } else {
    estado.recordatorios.push({ channel: c.id, titulo: epg.now.title, start: epg.now.start, avisado: false });
    await tdt.ui.toast(`Recordado: «${epg.now.title}», a las ${hhmm(epg.now.start)}`);
  }
  await guardar();
  refrescar();
}

async function vigilar() {
  const ahora = Math.floor(Date.now() / 1000);
  for (const r of estado.recordatorios) {
    if (r.avisado || r.start > ahora + estado.avisarMin * 60) continue;
    try {
      const epg = await tdt.epg.now(r.channel);
      if (epg && epg.now && epg.now.title === r.titulo && epg.now.start === r.start && epg.now.start <= ahora) {
        r.avisado = true;
        await tdt.ui.toast(`▶ Empieza «${r.titulo}» en ${nombreCanal(r.channel)}`);
        await guardar();
        pintarRecordatorios();
      }
    } catch { /* sin guía un ciclo */ }
  }
  setTimeout(vigilar, POLL_MS);
}

(async () => {
  await tdt.ready;
  await cargar();
  $('add').addEventListener('click', async () => {
    const q = $('buscar').value.trim();
    if (!q) return;
    const res = await tdt.channels.search(q, { kind: 'tv', limit: 5 });
    const c = res.channels.find((x) => x.hasGuide) || res.channels[0];
    if (!c) return tdt.ui.toast('No hay ningún canal con ese nombre');
    if (!estado.canales.some((x) => x.id === c.id)) {
      estado.canales.push({ id: c.id, name: c.name });
      if (estado.canales.length > MAX_CANALES) estado.canales.shift();
      await guardar();
    }
    $('buscar').value = '';
    refrescar();
  });
  refrescar();
  vigilar();
})().catch((error) => {
  $('lista').textContent = error.message; // por ejemplo: permiso denegado: epg.read
});
