/* Precios Supermercados Perú — toda la lógica corre en el navegador.
 *
 * No hay backend: la corrida diaria deja cuatro JSON en datos/ y acá se arman
 * la búsqueda, la comparación entre cadenas y el evolutivo. Son 291 KB
 * comprimidos, así que entra todo de una vez y buscar no cuesta una petición.
 */

const CADENAS = {
  tottus:   { nombre: 'Tottus',    color: 'var(--tottus)' },
  wong:     { nombre: 'Wong',      color: 'var(--wong)' },
  metro:    { nombre: 'Metro',     color: 'var(--metro)' },
  plazavea: { nombre: 'Plaza Vea', color: 'var(--plazavea)' },
};

const SUGERENCIAS = ['pollo fresco', 'palta', 'lomo fino', 'huevos', 'papa', 'manzana'];

// Palabras que describen presentación o cadena, no producto: estorban al
// agrupar. "con" y "sin" NO están acá a propósito: en este rubro distinguen de
// verdad — pollo "con menudencia" y "sin menudencia" son productos distintos y
// a precios distintos.
const RUIDO = new Set([
  'kg', 'kgs', 'und', 'unid', 'unidad', 'aprox', 'bolsa', 'empaque', 'paquete',
  'caja', 'pack', 'bandeja', 'gr', 'grs', 'lt', 'ltr', 'tottus', 'wong',
  'metro', 'plaza', 'vea', 'plazavea', 'del', 'por', 'los', 'las',
]);

const estado = {
  indice: [], series: {}, salud: null,
  cadenas: new Set(), familias: new Set(),
  soloDescuento: false, incluirAgotados: false,
  abierto: null,
};

/* ── utilidades ─────────────────────────────────────────────────────────── */

const normalizar = (s) => (s || '')
  .toLowerCase()
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[^a-z0-9 ]/g, ' ')
  .replace(/\s+/g, ' ')
  .trim();

const soles = (v) => v == null
  ? '—'
  : v.toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const el = (tag, clase, texto) => {
  const n = document.createElement(tag);
  if (clase) n.className = clase;
  if (texto != null) n.textContent = texto;
  return n;
};

/** La clave con la que se agrupan productos parecidos entre cadenas.
 *  Se queda con las palabras significativas, ordenadas, para que "Pollo Fresco
 *  con Menudencia San Fernando x kg" y "Pollo Entero Fresco con Menudencia x kg"
 *  caigan juntos. Agrupa por parecido de nombre; NO afirma que sean el mismo
 *  artículo, por eso cada fila muestra su nombre completo. */
function tokens(nombre) {
  return new Set(normalizar(nombre).split(' ')
    .filter((p) => p.length > 2 && !RUIDO.has(p) && !/^\d+$/.test(p)));
}

function claveGrupo(nombre) {
  const t = [...tokens(nombre)].sort();
  return t.join(' ') || normalizar(nombre);
}

/** Cuánto se parecen dos nombres: palabras compartidas sobre el total de
 *  palabras distintas entre ambos.
 *
 *  Se mide contra el TOTAL y no contra el nombre más corto, porque las palabras
 *  que sobran son justamente las que distinguen un producto de otro. Medido
 *  contra el más corto, "Pollo Fresco con Menudencia" daba 100% de parecido con
 *  "Pollo Fresco Sin Menudencia Artisan Libre de Antibióticos", que cuesta el
 *  doble. Así esas palabras de más cuentan en contra, que es lo correcto. */
function parecido(a, b) {
  let comunes = 0;
  a.forEach((t) => { if (b.has(t)) comunes += 1; });
  return comunes / Math.max(a.size + b.size - comunes, 1);
}

/** Une grupos cuyos nombres se parecen lo suficiente.
 *
 *  Solo corre cuando hay una búsqueda escrita: ahí el conjunto es chico y el
 *  término que escribió la persona ya acotó qué considera comparable. Sin
 *  búsqueda sería O(n²) sobre miles de productos, y además agruparía cosas que
 *  nadie pidió comparar.
 *
 *  Es parecido de NOMBRE, no identidad: por eso cada fila sigue mostrando su
 *  nombre completo y su cadena, para que se vea qué se está comparando. */
function fusionarParecidos(grupos) {
  // Alto a propósito: solo se juntan nombres casi idénticos. Agrupar de menos
  // deja dos filas adyacentes que la persona compara de un vistazo; agrupar de
  // más afirma que dos productos distintos son el mismo y les pone una brecha
  // de precio que no significa nada.
  const UMBRAL = 0.7;
  const salida = [];
  grupos.forEach((g) => {
    const suyos = tokens(g.items[0].nombre);
    const destino = salida.find((o) => parecido(suyos, o.tokens) >= UMBRAL);
    if (destino) {
      destino.items.push(...g.items);
      destino.tokens = new Set([...destino.tokens].filter((t) => suyos.has(t)));
    } else {
      salida.push({ items: [...g.items], tokens: suyos });
    }
  });
  return salida.map((o) => o.items);
}

/* ── carga ──────────────────────────────────────────────────────────────── */

async function cargar() {
  const [indice, series, salud] = await Promise.all([
    fetch('datos/indice.json').then((r) => r.json()),
    fetch('datos/series.json').then((r) => r.json()),
    fetch('datos/salud.json').then((r) => r.json()),
  ]);
  estado.indice = indice.productos.map((p) => ({ ...p, busq: normalizar(p.nombre) }));
  estado.series = series;
  estado.salud = salud;
}

/* ── frescura del dato ──────────────────────────────────────────────────── */

function pintarFrescura() {
  const s = estado.salud;
  const cont = document.getElementById('frescura');
  const dias = Math.round((Date.now() - new Date(s.ultimo_dia + 'T12:00:00')) / 86400000);
  const cuando = dias <= 0 ? 'hoy' : dias === 1 ? 'ayer' : `hace ${dias} días`;
  cont.classList.toggle('vieja', dias >= 2);
  cont.append(
    el('span', 'punto'),
    el('span', null, 'Última foto '),
    el('span', 'frescura-dato', cuando),
  );
  cont.title = `${s.ultimo_dia} a las ${s.hora_de_la_foto} · ${s.dias_con_dato} días de histórico`;

  document.getElementById('pie-dato').textContent =
    `${s.productos.toLocaleString('es-PE')} productos · ${s.dias_con_dato} días de histórico `
    + `desde el ${s.primer_dia} · última foto el ${s.ultimo_dia} a las ${s.hora_de_la_foto}.`;
}

/* ── filtros ────────────────────────────────────────────────────────────── */

function pintarFiltros() {
  const cadenas = [...new Set(estado.indice.map((p) => p.cadena))].sort();
  const cont = document.getElementById('chips-cadena');
  cadenas.forEach((c) => {
    const b = el('button', 'chip');
    b.type = 'button';
    b.setAttribute('aria-pressed', 'false');
    const punto = el('span', 'punto-cadena');
    punto.style.background = CADENAS[c]?.color || 'var(--apagado)';
    b.append(punto, document.createTextNode(CADENAS[c]?.nombre || c));
    b.onclick = () => {
      estado.cadenas.has(c) ? estado.cadenas.delete(c) : estado.cadenas.add(c);
      b.setAttribute('aria-pressed', String(estado.cadenas.has(c)));
      render();
    };
    cont.append(b);
  });

  const familias = [...new Set(estado.indice.map((p) => p.familia).filter(Boolean))].sort();
  const cf = document.getElementById('chips-familia');
  familias.forEach((f) => {
    const b = el('button', 'chip', f);
    b.type = 'button';
    b.setAttribute('aria-pressed', 'false');
    b.onclick = () => {
      estado.familias.has(f) ? estado.familias.delete(f) : estado.familias.add(f);
      b.setAttribute('aria-pressed', String(estado.familias.has(f)));
      render();
    };
    cf.append(b);
  });

  document.getElementById('solo-descuento').onchange = (e) => {
    estado.soloDescuento = e.target.checked; render();
  };
  document.getElementById('incluir-agotados').onchange = (e) => {
    estado.incluirAgotados = e.target.checked; render();
  };

  const sug = document.getElementById('sugerencias');
  SUGERENCIAS.forEach((t) => {
    const b = el('button', null, t);
    b.type = 'button';
    b.onclick = () => { entrada.value = t; render(); entrada.focus(); };
    sug.append(b);
  });
}

/* ── búsqueda ───────────────────────────────────────────────────────────── */

function filtrar() {
  const terminos = normalizar(entrada.value).split(' ').filter(Boolean);
  return estado.indice.filter((p) => {
    if (estado.cadenas.size && !estado.cadenas.has(p.cadena)) return false;
    if (estado.familias.size && !estado.familias.has(p.familia)) return false;
    if (estado.soloDescuento && !p.descuento) return false;
    // Solo se descarta lo marcado explícitamente como agotado: null significa
    // que ese día la cadena no publicaba el dato, no que no haya stock.
    if (!estado.incluirAgotados && p.disponible === false) return false;
    return terminos.every((t) => p.busq.includes(t));
  });
}

function agrupar(productos, hayBusqueda) {
  const mapa = new Map();
  productos.forEach((p) => {
    const k = claveGrupo(p.nombre);
    if (!mapa.has(k)) mapa.set(k, []);
    mapa.get(k).push(p);
  });
  let grupos = [...mapa.values()];
  if (hayBusqueda && grupos.length <= 120) {
    grupos = fusionarParecidos(grupos.map((items) => ({ items })));
  }
  return grupos
    .map((items) => {
      const conPrecio = items.filter((p) => p.precio != null);
      const precios = conPrecio.map((p) => p.precio);
      const min = precios.length ? Math.min(...precios) : null;
      const max = precios.length ? Math.max(...precios) : null;
      items.sort((a, b) => (a.precio ?? Infinity) - (b.precio ?? Infinity));
      return {
        items, min, max,
        cadenas: new Set(items.map((p) => p.cadena)).size,
        brecha: min && max && min > 0 ? Math.round((max / min - 1) * 100) : 0,
      };
    })
    .sort((a, b) => (b.cadenas - a.cadenas) || (b.items.length - a.items.length));
}

/* ── evolutivo ──────────────────────────────────────────────────────────── */

/** El log de cambios trae solo los días en que el precio se movió. Acá se
 *  reconstruye la escalera. Un punto nulo corta la serie: el producto dejó de
 *  tener precio publicado, y arrastrar el último valor afirmaría un precio que
 *  ya no existe. */
/** La serie día por día, arrastrando el último valor conocido.
 *
 *  Cada día devuelve {p, r, promo} o null si todavía no hay dato: `p` es lo que
 *  se paga, `r` el precio de lista. La distancia entre los dos es el descuento,
 *  y es lo que hace legible el gráfico — el pollo entero de Plaza Vea cambia de
 *  precio 40 veces en 121 días, y sin la referencia eso es ruido. */
function serieDiaria(id, dias) {
  const puntos = estado.series[id];
  if (!puntos || !puntos.length) return [];
  const salida = [];
  let i = 0; let actual = null;
  dias.forEach((d) => {
    while (i < puntos.length && puntos[i][0] <= d) {
      const [, p, r, promo] = puntos[i];
      actual = p == null ? null : { p, r, promo: !!promo };
      i += 1;
    }
    salida.push(actual);
  });
  return salida;
}

/** Los números de una serie: lo que contesta "¿compro hoy o espero?". */
function metricasDeItem(valores, dias) {
  const conDato = [];
  valores.forEach((v, i) => { if (v) conDato.push({ ...v, dia: dias[i] }); });
  if (!conDato.length) return null;
  const precios = conDato.map((v) => v.p).sort((a, b) => a - b);
  const mediana = precios.length % 2
    ? precios[(precios.length - 1) / 2]
    : (precios[precios.length / 2 - 1] + precios[precios.length / 2]) / 2;
  const minimo = conDato.reduce((a, b) => (b.p < a.p ? b : a));
  const maximo = conDato.reduce((a, b) => (b.p > a.p ? b : a));
  const enPromo = conDato.filter((v) => v.promo);
  const hoy = conDato[conDato.length - 1];
  return {
    hoy: hoy.p,
    dias: conDato.length,
    mediana,
    minimo: minimo.p,
    minimoDia: minimo.dia,
    maximo: maximo.p,
    diasEnPromo: enPromo.length,
    // Cuánto se aparta hoy de su propia mediana. Es la lectura que importa:
    // un precio no es caro o barato en absoluto, lo es contra su historia.
    contraMediana: mediana ? (hoy.p - mediana) / mediana : null,
  };
}

/** Cuántos días fue más barata cada cadena.
 *
 *  La tabla de arriba dice quién está más barato HOY, que un día de promo da
 *  vuelta. Esto dice quién lo estuvo de verdad: medido sobre el pollo entero,
 *  Plaza Vea ganó 90 de 120 días. */
function masBaratoPorDia(series, dias) {
  const cuenta = {};
  let conDato = 0;
  dias.forEach((_, i) => {
    let mejor = null;
    series.forEach(({ p, valores }) => {
      const v = valores[i];
      if (!v) return;
      if (!mejor || v.p < mejor.precio) mejor = { cadena: p.cadena, precio: v.p };
    });
    if (!mejor) return;
    conDato += 1;
    cuenta[mejor.cadena] = (cuenta[mejor.cadena] || 0) + 1;
  });
  const orden = Object.entries(cuenta).sort((a, b) => b[1] - a[1]);
  return orden.length ? { cadena: orden[0][0], dias: orden[0][1], total: conDato } : null;
}

/** Marcas de eje en números redondos.
 *
 *  Antes salían del rango crudo y daban cosas como 10,99 · 8,96 · 6,94, que no
 *  se leen de un vistazo. */
function marcasDeEje(lo, hi, cuantas = 4) {
  const bruto = (hi - lo) / (cuantas - 1);
  const mag = 10 ** Math.floor(Math.log10(bruto));
  const paso = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((p) => p >= bruto) || mag * 10;
  const inicio = Math.ceil(lo / paso) * paso;
  const marcas = [];
  for (let v = inicio; v <= hi + 1e-9; v += paso) marcas.push(Math.round(v * 100) / 100);
  return marcas;
}

function diasDelRango(items) {
  const fechas = new Set();
  items.forEach((p) => (estado.series[p.id] || []).forEach(([d]) => fechas.add(d)));
  if (!fechas.size) return [];
  const orden = [...fechas].sort();
  const dias = [];
  const fin = new Date(estado.salud.ultimo_dia + 'T12:00:00');
  for (let d = new Date(orden[0] + 'T12:00:00'); d <= fin; d.setDate(d.getDate() + 1)) {
    dias.push(d.toISOString().slice(0, 10));
  }
  return dias;
}

/** Color y trazo de cada línea.
 *
 *  El color lo da la cadena, pero un grupo puede traer dos productos de la
 *  misma: ahí dos líneas del mismo color son indistinguibles. La segunda y las
 *  siguientes van punteadas. */
function estilosDeSerie(items) {
  const vistos = {};
  const TRAZOS = ['', '6 4', '2 3', '9 3 2 3'];
  return items.map((p) => {
    const i = vistos[p.cadena] = (vistos[p.cadena] ?? -1) + 1;
    return {
      color: CADENAS[p.cadena]?.color || 'var(--apagado)',
      trazo: TRAZOS[Math.min(i, TRAZOS.length - 1)],
      repetida: i > 0,
    };
  });
}

/** El gráfico, con el precio de lista de fondo y lectura al pasar el mouse.
 *
 *  Dos líneas por producto: la sólida es lo que se paga y la tenue punteada el
 *  precio de lista. El hueco entre las dos es el descuento, así que las rachas
 *  de promo se ven sin marcar día por día — que con el pollo entero, en promo
 *  el 57% de los días, sería una mancha de puntos sin información. */
function dibujarGrafico(series, dias) {
  // El viewBox se achica en el teléfono en vez de dejar que el de 760 se
  // reduzca a 345: ahí el factor de escala es 0,45 y el texto del eje, que son
  // 10,5 unidades, termina dibujado a 5 px. Con el lienzo angosto la escala
  // queda cerca de 1 y los números se leen.
  const angosto = window.innerWidth < 560;
  const W = angosto ? 380 : 760;
  const H = angosto ? 250 : 230;
  const M = angosto ? { t: 10, r: 10, b: 22, l: 36 } : { t: 12, r: 14, b: 24, l: 46 };
  const ns = 'http://www.w3.org/2000/svg';
  const caja = el('div', 'grafico-caja');
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'grafico');
  svg.setAttribute('role', 'img');
  caja.append(svg);

  const todos = series.flatMap((s) => s.valores)
    .filter(Boolean).flatMap((v) => [v.p, v.r]).filter((v) => v != null);
  if (!todos.length) return caja;

  let lo = Math.min(...todos); let hi = Math.max(...todos);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;

  const x = (i) => M.l + (i / Math.max(dias.length - 1, 1)) * (W - M.l - M.r);
  const y = (v) => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
  const crear = (t, attrs) => {
    const n = document.createElementNS(ns, t);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    return n;
  };

  marcasDeEje(lo, hi).forEach((v) => {
    svg.append(crear('line', { class: 'grilla', x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    const t = crear('text', { x: M.l - 6, y: y(v) + 3.5, 'text-anchor': 'end' });
    t.textContent = soles(v);
    svg.append(t);
  });

  // Cinco fechas repartidas: con dos, un rango de 121 días no se ubica. En el
  // teléfono entran tres sin pisarse.
  const cuantas = Math.min(angosto ? 3 : 5, dias.length);
  for (let k = 0; k < cuantas; k += 1) {
    const i = Math.round((k / Math.max(cuantas - 1, 1)) * (dias.length - 1));
    const t = crear('text', {
      x: x(i), y: H - 7,
      'text-anchor': k === 0 ? 'start' : k === cuantas - 1 ? 'end' : 'middle',
    });
    const [, m, d] = dias[i].split('-');
    t.textContent = `${d}/${m}`;
    svg.append(t);
  }
  svg.append(crear('line', { class: 'eje', x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b }));

  const trazar = (valores, campo) => {
    let d = ''; let dibujando = false;
    valores.forEach((v, i) => {
      const valor = v && v[campo];
      if (valor == null) { dibujando = false; return; }
      d += `${dibujando ? 'L' : 'M'}${x(i).toFixed(1)} ${y(valor).toFixed(1)}`;
      dibujando = true;
    });
    return d;
  };

  // El precio de lista va primero, para que quede por detrás. Solo se dibuja
  // si en algún momento se despega del precio: si nunca hubo descuento, las
  // dos líneas serían la misma y solo agregaría tinta.
  series.forEach(({ estilo, valores }) => {
    if (!valores.some((v) => v && v.r != null && v.r > v.p + 0.009)) return;
    const d = trazar(valores, 'r');
    if (d) svg.append(crear('path', { class: 'serie-lista', d, stroke: estilo.color }));
  });

  series.forEach(({ estilo, valores }) => {
    const d = trazar(valores, 'p');
    if (!d) return;
    const attrs = { class: 'serie', d, stroke: estilo.color };
    if (estilo.trazo) attrs['stroke-dasharray'] = estilo.trazo;
    svg.append(crear('path', attrs));
  });

  // ── lectura al pasar el mouse ──────────────────────────────────────────
  const guia = crear('line', {
    class: 'guia', y1: M.t, y2: H - M.b, x1: 0, x2: 0, opacity: 0,
  });
  svg.append(guia);
  const globo = el('div', 'globo');
  globo.hidden = true;
  caja.append(globo);

  const leer = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const frac = (px - M.l) / (W - M.l - M.r);
    const i = Math.max(0, Math.min(dias.length - 1, Math.round(frac * (dias.length - 1))));
    guia.setAttribute('x1', x(i)); guia.setAttribute('x2', x(i));
    guia.setAttribute('opacity', 1);

    const [, m, d] = dias[i].split('-');
    globo.replaceChildren(el('b', null, `${d}/${m}`));
    series.forEach(({ p, estilo, valores }) => {
      const v = valores[i];
      if (!v) return;
      const linea = el('span');
      const punto = el('i');
      punto.style.background = estilo.color;
      linea.append(punto, document.createTextNode(
        `${CADENAS[p.cadena]?.nombre || p.cadena}  S/ ${soles(v.p)}`,
      ));
      if (v.promo && v.r != null && v.r > v.p) {
        linea.append(el('em', null, ` antes S/ ${soles(v.r)}`));
      }
      globo.append(linea);
    });
    globo.hidden = false;
    // El globo salta de lado cerca del borde derecho para no salirse.
    const izq = (x(i) / W) * 100;
    globo.style.left = `${izq}%`;
    globo.classList.toggle('a-la-izquierda', izq > 62);
  };
  const salir = () => { guia.setAttribute('opacity', 0); globo.hidden = true; };
  caja.addEventListener('mousemove', leer);
  caja.addEventListener('mouseleave', salir);

  svg.setAttribute('aria-label',
    `Evolución de precio de ${series.length} producto(s) entre ${dias[0]} y ${dias[dias.length - 1]}.`);
  return caja;
}

function listaDeCambios(items) {
  const eventos = [];
  items.forEach((p) => {
    const puntos = estado.series[p.id] || [];
    for (let i = 1; i < puntos.length; i += 1) {
      const [dia, precio] = puntos[i];
      const previo = puntos[i - 1][1];
      if (precio == null || previo == null) continue;
      eventos.push({ dia, p, previo, precio });
    }
  });
  eventos.sort((a, b) => b.dia.localeCompare(a.dia));
  return eventos.slice(0, 6);
}

/** Los números del producto, debajo del gráfico.
 *
 *  El gráfico muestra la forma; esto contesta la pregunta. Una fila por
 *  producto con las mismas columnas, y arriba el dato que no estaba en ningún
 *  lado: quién fue más barato de verdad, no quién lo está hoy. */
function pintarMetricas(series, dias) {
  const caja = el('div', 'metricas');
  const ganador = masBaratoPorDia(series, dias);
  if (ganador && series.length > 1) {
    const pct = Math.round((ganador.dias / ganador.total) * 100);
    const p = el('p', 'ganador');
    const punto = el('i');
    punto.style.background = CADENAS[ganador.cadena]?.color || 'var(--apagado)';
    p.append(
      punto,
      el('b', null, CADENAS[ganador.cadena]?.nombre || ganador.cadena),
      document.createTextNode(` fue el más barato en ${ganador.dias} de ${ganador.total} días (${pct}%)`),
    );
    caja.append(p);
  }

  const tabla = el('table', 'tabla-metricas');
  const thead = el('thead');
  const filaCab = el('tr');
  // La columna vacía después de "Mínimo" es la fecha en que se tocó.
  [['', null], ['Hoy', 'num'], ['Mediana', 'num'], ['Mínimo', 'num'],
    ['', 'num-chico'], ['Máximo', 'num'], ['En promo', 'num']]
    .forEach(([t, clase]) => filaCab.append(el('th', clase, t)));
  thead.append(filaCab);
  const tbody = el('tbody');

  series.forEach(({ p, estilo, valores }) => {
    const m = metricasDeItem(valores, dias);
    if (!m) return;
    const tr = el('tr');
    const nombre = el('td');
    const punto = el('i');
    punto.style.background = estilo.color;
    let etiqueta = CADENAS[p.cadena]?.nombre || p.cadena;
    if (estilo.repetida) etiqueta += ` · ${p.nombre.split(' ').slice(-2).join(' ')}`;
    nombre.append(punto, document.createTextNode(etiqueta));

    const hoy = el('td', 'num');
    hoy.append(el('b', null, `S/ ${soles(m.hoy)}`));
    if (m.contraMediana != null && Math.abs(m.contraMediana) >= 0.02) {
      const sube = m.contraMediana > 0;
      hoy.append(el('em', sube ? 'caro' : 'barato',
        `${sube ? '+' : ''}${Math.round(m.contraMediana * 100)}%`));
    }

    const [, mesMin, diaMin] = m.minimoDia.split('-');
    tr.append(
      nombre,
      hoy,
      el('td', 'num', `S/ ${soles(m.mediana)}`),
      el('td', 'num', `S/ ${soles(m.minimo)}`),
      el('td', 'num-chico', `${diaMin}/${mesMin}`),
      el('td', 'num', `S/ ${soles(m.maximo)}`),
      el('td', 'num', m.dias ? `${Math.round((m.diasEnPromo / m.dias) * 100)}%` : '—'),
    );
    tbody.append(tr);
  });

  tabla.append(thead, tbody);
  caja.append(tabla);
  return caja;
}

function pintarDetalle(grupo) {
  const caja = el('div', 'detalle');
  const dias = diasDelRango(grupo.items);
  if (!dias.length) {
    caja.append(el('p', 'cambios', 'Todavía no hay histórico para este producto.'));
    return caja;
  }

  const cab = el('div', 'detalle-cab');
  // Días de calendario, no días con dato: hay 18 fechas sin corrida en el
  // histórico, así que se nombra el rango en vez de un conteo que no cuadra.
  const [, m1, d1] = dias[0].split('-');
  const [, m2, d2] = dias[dias.length - 1].split('-');
  cab.append(el('h3', null, `Evolución · ${d1}/${m1} a ${d2}/${m2}`));
  const estilos = estilosDeSerie(grupo.items);
  const leyenda = el('div', 'detalle-leyenda');
  grupo.items.forEach((p, k) => {
    const s = el('span');
    const i = el('i');
    i.style.background = estilos[k].color;
    // Punteada en la leyenda igual que en el gráfico, si no no se sabe cuál es.
    if (estilos[k].trazo) {
      i.style.background = `repeating-linear-gradient(90deg, ${estilos[k].color} 0 4px, transparent 4px 7px)`;
    }
    let etiqueta = CADENAS[p.cadena]?.nombre || p.cadena;
    if (estilos[k].repetida) {
      // Con dos productos de la misma cadena, el nombre de la cadena no alcanza.
      const corto = p.nombre.split(' ').slice(-2).join(' ');
      etiqueta += ` · ${corto}`;
    }
    s.append(i, document.createTextNode(etiqueta));
    leyenda.append(s);
  });
  cab.append(leyenda);

  // Rango: 121 días apretados en 760 px es parte de por qué el gráfico se leía
  // como ruido. El de 30 días es el que contesta "¿está caro esta semana?".
  const RANGOS = [['30 d', 30], ['90 d', 90], ['Todo', 0]];
  const selector = el('div', 'rangos');
  const lienzo = el('div', 'lienzo');
  let rango = dias.length > 120 ? 90 : 0;

  const redibujar = () => {
    const visibles = rango ? dias.slice(-rango) : dias;
    const series = grupo.items
      .map((p, i) => ({ p, estilo: estilos[i], valores: serieDiaria(p.id, visibles) }))
      .filter((s) => s.valores.some(Boolean));
    lienzo.replaceChildren(
      dibujarGrafico(series, visibles),
      pintarMetricas(series, visibles),
    );
    [...selector.children].forEach((b) => {
      b.classList.toggle('activo', Number(b.dataset.dias) === rango);
    });
  };

  RANGOS.forEach(([etiqueta, n]) => {
    if (n && dias.length <= n) return;
    const b = el('button', null, etiqueta);
    b.type = 'button';
    b.dataset.dias = n;
    b.addEventListener('click', (ev) => { ev.stopPropagation(); rango = n; redibujar(); });
    selector.append(b);
  });
  if (selector.children.length > 1) cab.append(selector);

  caja.append(cab, lienzo);
  redibujar();

  const cambios = listaDeCambios(grupo.items);
  if (cambios.length) {
    const c = el('div', 'cambios');
    c.append(el('span', null, 'Últimos cambios de precio:'));
    const ul = el('ul');
    cambios.forEach((e) => {
      const li = el('li');
      const [a, m, d] = e.dia.split('-');
      const subio = e.precio > e.previo;
      li.append(
        document.createTextNode(`${d}/${m} · ${CADENAS[e.p.cadena]?.nombre || e.p.cadena}: `),
        el('b', null, `S/ ${soles(e.previo)} → S/ ${soles(e.precio)}`),
        document.createTextNode(subio ? ' (subió)' : ' (bajó)'),
      );
      ul.append(li);
    });
    c.append(ul);
    caja.append(c);
  }

  const conUrl = grupo.items.find((p) => p.url);
  if (conUrl) {
    const a = el('a', 'ver-producto', 'Ver en la web de la cadena →');
    a.href = conUrl.url; a.target = '_blank'; a.rel = 'noopener';
    caja.append(a);
  }
  return caja;
}

/* ── render ─────────────────────────────────────────────────────────────── */

function pintarFila(p, grupo) {
  const b = el('button', 'fila');
  b.type = 'button';
  if (grupo.items.length > 1 && p.precio != null && p.precio === grupo.min) {
    b.classList.add('es-barato');
  }

  const tag = el('div', 'cadena-tag');
  const punto = el('span', 'punto-cadena');
  punto.style.background = CADENAS[p.cadena]?.color || 'var(--apagado)';
  tag.append(punto, document.createTextNode(CADENAS[p.cadena]?.nombre || p.cadena));

  const info = el('div', 'fila-info');
  info.append(el('div', 'fila-nombre', p.nombre));
  const etqs = el('div', 'fila-etiquetas');
  if (p.descuento) etqs.append(el('span', 'etq etq-descuento', `−${Math.round(p.descuento)}%`));
  if (p.tarjeta) {
    etqs.append(el('span', 'etq etq-tarjeta',
      `${p.tarjeta_nombre || 'Tarjeta'}: S/ ${soles(p.tarjeta)}`));
  }
  if (p.disponible === false) etqs.append(el('span', 'etq etq-agotado', 'Sin stock'));
  if (p.marca) etqs.append(el('span', 'etq', p.marca));
  if (etqs.children.length) info.append(etqs);

  const col = el('div', 'fila-precio');
  const precio = el('div', 'precio');
  precio.append(el('span', 'moneda', 'S/ '), document.createTextNode(soles(p.precio)));
  col.append(precio);
  if (p.regular && p.precio && p.regular > p.precio) {
    col.append(el('div', 'precio-antes', `S/ ${soles(p.regular)}`));
  }
  if (grupo.items.length > 1 && p.precio != null && grupo.min) {
    const dif = Math.round((p.precio / grupo.min - 1) * 100);
    col.append(el('div', `delta ${dif === 0 ? 'ok' : 'mal'}`,
      dif === 0 ? 'más barato' : `+${dif}%`));
  }

  b.append(tag, info, col);
  b.onclick = () => {
    estado.abierto = estado.abierto === grupo.clave ? null : grupo.clave;
    render();
  };
  return b;
}

function render() {
  const productos = filtrar();
  const grupos = agrupar(productos, Boolean(entrada.value.trim()))
    .map((g) => ({ ...g, clave: g.items[0].id }));

  document.getElementById('limpiar').hidden = !entrada.value;
  const cont = document.getElementById('resultados');
  cont.textContent = '';

  const vacio = document.getElementById('vacio');
  if (!productos.length) {
    vacio.hidden = false;
    vacio.textContent = entrada.value
      ? `No hay productos para "${entrada.value}" con estos filtros. `
        + 'Puede que esa categoría todavía no se esté vigilando.'
      : 'No hay productos con estos filtros.';
    document.getElementById('conteo').textContent = '';
    return;
  }
  vacio.hidden = true;

  const enVarias = grupos.filter((g) => g.cadenas > 1).length;
  document.getElementById('conteo').textContent =
    `${productos.length.toLocaleString('es-PE')} producto${productos.length === 1 ? '' : 's'}`
    + ` · ${grupos.length} grupo${grupos.length === 1 ? '' : 's'}`
    + (enVarias ? ` · ${enVarias} comparable${enVarias === 1 ? '' : 's'} entre cadenas` : '');

  grupos.slice(0, 60).forEach((g) => {
    const caja = el('div', 'grupo');
    if (g.items.length > 1) {
      const cab = el('div', 'grupo-cab');
      cab.append(el('h2', null, g.items[0].nombre));
      const meta = el('span', 'grupo-meta');
      meta.append(document.createTextNode(
        `${g.cadenas} cadena${g.cadenas === 1 ? '' : 's'}`));
      if (g.brecha > 0) {
        meta.append(document.createTextNode(' · '),
                    el('span', 'brecha', `brecha ${g.brecha}%`));
      }
      cab.append(meta);
      caja.append(cab);
    }
    if (estado.abierto === g.clave) caja.append(pintarDetalle(g));
    g.items.forEach((p) => caja.append(pintarFila(p, g)));
    cont.append(caja);
  });

  if (grupos.length > 60) {
    cont.append(el('p', 'conteo',
      `Se muestran los primeros 60 grupos de ${grupos.length}. Afiná la búsqueda para ver el resto.`));
  }
}

/* ── arranque ───────────────────────────────────────────────────────────── */

const entrada = document.getElementById('q');

(async function iniciar() {
  const cont = document.getElementById('resultados');
  cont.append(el('p', 'cargando', 'Cargando precios…'));
  try {
    await cargar();
  } catch (e) {
    cont.textContent = '';
    cont.append(el('p', 'vacio',
      'No se pudieron cargar los datos. Probá recargar la página.'));
    return;
  }
  pintarFrescura();
  pintarFiltros();
  entrada.oninput = () => { estado.abierto = null; render(); };
  document.getElementById('formulario').onsubmit = (e) => e.preventDefault();
  document.getElementById('limpiar').onclick = () => {
    entrada.value = ''; estado.abierto = null; render(); entrada.focus();
  };
  render();
  // El panel vive en panel.js y trabaja sobre estos mismos datos; se entera
  // acá en vez de volver a pedirlos.
  document.dispatchEvent(new CustomEvent('datos-listos'));
}());
