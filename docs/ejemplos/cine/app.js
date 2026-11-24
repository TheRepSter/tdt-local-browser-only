// Modo cine: oscurece la pantalla (el fondo del cuerpo, con el % del ajuste «oscurecer»), fija el volumen al del ajuste
// «volumen» y muestra qué estás viendo. Al salir devuelve el volumen y el silencio que tenías antes. Solo player.read/control,
// epg.read, storage y ui.toast. La pantalla la cierra la aplicación (la ✕ de la cabecera del panel).

const $ = (id) => document.getElementById(id);
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

async function pintar() {
  const s = await tdt.player.state();
  const activa = s.tiles.find((t) => t.slot === s.focus) || s.tiles[0];
  $('que').firstChild.textContent = activa ? activa.name : 'Modo cine';
  let prog = '';
  if (activa && activa.id) {
    try {
      const epg = await tdt.epg.now(activa.id);
      if (epg && epg.now) prog = `«${epg.now.title}», hasta las ${hhmm(epg.now.end)}`;
    } catch { /* sin guía */ }
  }
  $('prog').textContent = prog;
  $('vol').value = String(Math.round((s.vol.m ? 0 : s.vol.v) * 100));
  $('mudo').setAttribute('aria-pressed', String(s.vol.m));
  $('mudo').textContent = s.vol.m ? 'Sin sonido' : 'Sonido';
}

(async () => {
  await tdt.ready;
  const conf = await tdt.config.all();
  const osc = Math.min(90, Math.max(0, Number(conf.oscurecer) || 0)) / 100;
  document.body.style.setProperty('--osc', String(osc));

  // al entrar: recuerda el volumen/silencio anterior y fija el del ajuste
  const s0 = await tdt.player.state();
  await tdt.storage.set('antes', { v: s0.vol.v, m: s0.vol.m });
  const vol = Math.min(100, Math.max(0, Number(conf.volumen) || 0)) / 100;
  await tdt.player.volume(vol);
  if (s0.vol.m) await tdt.player.mute(false);

  await pintar();
  tdt.on('player', () => pintar().catch(() => {}));

  $('vol').addEventListener('change', () => tdt.player.volume(Number($('vol').value) / 100).catch(() => {}));
  $('mudo').addEventListener('click', async () => {
    const s = await tdt.player.state();
    await tdt.player.mute(!s.vol.m);
    pintar();
  });
  $('salir').addEventListener('click', async () => {
    const antes = (await tdt.storage.get('antes')) || { v: 1, m: false };
    await tdt.player.volume(antes.v);
    await tdt.player.mute(antes.m);
    await tdt.ui.toast('Modo cine: volumen como lo tenías. Cierra la pantalla con la ✕ de la cabecera.');
  });
})().catch((error) => {
  $('prog').textContent = error.message; // por ejemplo: permiso denegado: player.control
});
