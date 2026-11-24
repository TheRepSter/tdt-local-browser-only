// El mosaico: elige hasta 4 canales y los pone en multivisión con player.multi (el audio rota según el ajuste
// «rotar»); «Volver a un solo canal» pone el canal de la casilla activa. Los canales elegidos se guardan en storage.
// Permisos del manifest: player.read, player.control, channels.read, storage.

const $ = (id) => document.getElementById(id);

const MAX_CANALES = 4; // player.multi admite de 1 a 4 canales

const estado = {
  canales: [], // [{id, name}]
  rotar: 20,
  mode: 'single',
  casillas: 0,
};

async function guardar() {
  await tdt.storage.set('canales', estado.canales);
}

async function cargar() {
  estado.canales = (await tdt.storage.get('canales')) || [];
  const conf = await tdt.config.all();
  estado.rotar = Number(conf.rotar) || 0; // 0 = sin rotación: el usuario elige la casilla con el foco
  await refrescarEstado();
}

async function refrescarEstado() {
  try {
    const st = await tdt.player.state();
    estado.mode = st.mode;
    estado.casillas = Array.isArray(st.tiles) ? st.tiles.length : 0;
  } catch { /* sin permiso o sin reproductor: se queda como estaba */ }
  render();
}

function render() {
  const lista = $('lista');
  lista.replaceChildren();
  if (!estado.canales.length) {
    const li = document.createElement('li');
    li.className = 'vacio';
    li.textContent = 'Añade entre 1 y 4 canales con el buscador de arriba.';
    lista.append(li);
  }
  estado.canales.forEach((c, i) => {
    const li = document.createElement('li');
    const nom = document.createElement('span');
    nom.textContent = `${i + 1}. ${c.name}`;
    const quita = document.createElement('button');
    quita.type = 'button';
    quita.className = 'quita';
    quita.textContent = '✕';
    quita.title = `Quitar ${c.name} del mosaico`;
    quita.addEventListener('click', async () => {
      estado.canales.splice(i, 1);
      await guardar();
      render();
    });
    li.append(nom, quita);
    lista.append(li);
  });

  $('estado').textContent = estado.mode === 'multi'
    ? `Ahora mismo: mosaico de ${estado.casillas} casilla(s), audio cada ${estado.rotar || '—'} s.`
    : 'Ahora mismo: un solo canal en pantalla.';
  $('poner').disabled = estado.mode === 'multi' || !estado.canales.length;
  $('salir').disabled = estado.mode !== 'multi';
}

(async () => {
  await tdt.ready;
  await cargar();

  $('add').addEventListener('click', async () => {
    const q = $('buscar').value.trim();
    if (!q) return;
    const res = await tdt.channels.search(q, { limit: 5 });
    const c = res.channels[0];
    if (!c) {
      $('estado').textContent = 'No hay ningún canal con ese nombre.';
      return;
    }
    if (estado.canales.some((x) => x.id === c.id)) {
      $('estado').textContent = `«${c.name}» ya está en el mosaico.`;
      return;
    }
    if (estado.canales.length >= MAX_CANALES) {
      $('estado').textContent = `El mosaico admite ${MAX_CANALES} canales como mucho: quita uno primero.`;
      return;
    }
    estado.canales.push({ id: c.id, name: c.name });
    await guardar();
    $('buscar').value = '';
    render();
  });

  $('poner').addEventListener('click', async () => {
    try {
      await tdt.player.multi(estado.canales.map((c) => c.id), estado.rotar || null);
    } catch (e) {
      $('estado').textContent = e.message; // por ejemplo: permiso denegado: player.control
    }
    await refrescarEstado();
  });

  $('salir').addEventListener('click', async () => {
    try {
      const st = await tdt.player.state();
      const tile = (st.tiles || []).find((t) => t.slot === st.focus) || (st.tiles || [])[0];
      if (tile) await tdt.player.play(tile.id); // poner un solo canal saca del modo multivisión
    } catch (e) {
      $('estado').textContent = e.message;
    }
    await refrescarEstado();
  });

  tdt.on('player', () => refrescarEstado().catch(() => {})); // el estado (single/multi) se repinta solo
  tdt.on('config.changed', () => cargar().catch(() => {}));
})().catch((error) => {
  $('estado').textContent = error.message;
});
