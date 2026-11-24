// Apagado al acabar: silencia cuando se acaba el programa en curso o a los X minutos del ajuste. Solo player.read/control,
// epg.read, storage y ui.toast. Un plugin no puede apagar el servidor (eso es del núcleo): silencia y avisa.

const $ = (id) => document.getElementById(id);
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

let temporizador = null; // {fin: segundos unix, timer: id, tipo: 'programa'|'minutos'}

async function estadoActual() {
  const s = await tdt.player.state();
  const activa = s.tiles.find((t) => t.slot === s.focus) || s.tiles[0];
  if (!activa || !activa.id) return { nombre: 'Nada en pantalla', epg: null };
  let epg = null;
  try { epg = await tdt.epg.now(activa.id); } catch { /* sin guía */ }
  return { nombre: activa.name, epg };
}

function pintar(act) {
  const epg = act.epg && act.epg.now;
  $('estado').textContent = epg
    ? `${act.nombre}: «${epg.now.title}», acaba a las ${hhmm(epg.now.end)}`
    : `${act.nombre}: sin guía`;
  $('finPrograma').disabled = !epg;
}

function cancelar(mensaje) {
  if (temporizador) clearTimeout(temporizador.timer);
  temporizador = null;
  $('cancelar').disabled = true;
  $('aviso').textContent = mensaje || '';
}

async function silenciar(tipo) {
  temporizador = null;
  $('cancelar').disabled = true;
  $('aviso').textContent = '';
  await tdt.player.mute(true);
  await tdt.ui.toast(tipo === 'programa' ? 'Se acabó el programa: silencio puesto' : 'Tiempo agotado: silencio puesto');
}

(async () => {
  await tdt.ready;
  const conf = await tdt.config.all();
  const minutos = Number(conf.minutos) || 30;

  const act = await estadoActual();
  pintar(act);
  tdt.on('player', async () => pintar(await estadoActual())); // channel.changed / programme changes
  tdt.on('channel.changed', async () => { cancelar(); pintar(await estadoActual()); });

  $('finPrograma').addEventListener('click', async () => {
    const a = await estadoActual();
    const fin = a.epg && a.epg.now ? a.epg.now.end : Math.floor(Date.now() / 1000) + 60;
    cancelar();
    temporizador = { fin, tipo: 'programa', timer: setTimeout(() => silenciar('programa'), Math.max(1, fin - Math.floor(Date.now() / 1000)) * 1000) };
    $('cancelar').disabled = false;
    $('aviso').textContent = `Silencio a las ${hhmm(fin)} (fin del programa)`;
    await tdt.storage.set('pendiente', { fin, tipo: 'programa' });
  });

  $('temporizador').addEventListener('click', async () => {
    cancelar();
    const fin = Math.floor(Date.now() / 1000) + minutos * 60;
    temporizador = { fin, tipo: 'minutos', timer: setTimeout(() => silenciar('minutos'), minutos * 60 * 1000) };
    $('cancelar').disabled = false;
    $('aviso').textContent = `Silencio a las ${hhmm(fin)} (a los ${minutos} minutos)`;
    await tdt.storage.set('pendiente', { fin, tipo: 'minutos' });
  });

  $('cancelar').addEventListener('click', async () => {
    cancelar('Cancelado.');
    await tdt.storage.remove('pendiente');
  });

  // si el panel se cerró con un temporizador puesto, se cumple igualmente (y se limpia al volver a abrir)
  const pendiente = await tdt.storage.get('pendiente');
  if (pendiente && pendiente.fin > Math.floor(Date.now() / 1000)) {
    temporizador = { fin: pendiente.fin, tipo: pendiente.tipo, timer: setTimeout(() => silenciar(pendiente.tipo), (pendiente.fin - Math.floor(Date.now() / 1000)) * 1000) };
    $('cancelar').disabled = false;
    $('aviso').textContent = `Sigue puesto: silencio a las ${hhmm(pendiente.fin)}`;
  }
})().catch((error) => {
  $('estado').textContent = error.message; // por ejemplo: permiso denegado: player.read
});
