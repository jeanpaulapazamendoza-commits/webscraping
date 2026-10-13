/* Panel de precios — el tablero.
 *
 * Corre sobre los mismos JSON que el buscador, que ya están en memoria: no hay
 * una segunda carga ni un backend que consultar. Todo lo que se ve acá se
 * calcula en el navegador a partir del índice de hoy y del log de cambios, así
 * que cualquier filtro afecta a TODAS las métricas y no solo a la tabla.
 *
 * La decisión de fondo: no se usa resumen.json, que trae agregados por día ×
 * cadena × familia ya calculados. Serviría para dos de los cuatro gráficos y
 * sería más rápido, pero solo se puede filtrar por familia. Recalcular desde
 * las series cuesta unos cientos de milisegundos y a cambio los filtros de
 * categoría, marca, texto y rango de precio funcionan sobre todo el tablero.
 *
 * Los colores de cadena son identidad, no rango: cada cadena tiene el suyo y no
 * rota según cuántas series haya en pantalla.
 */

/* ── estado ─────────────────────────────────────────────────────────────── */

const PF = {
  // Una lista de términos, no un texto suelto. Cada uno es {t, v} donde t es
  // 'texto' (una frase: todas sus palabras tienen que estar) o 'prod' (un
  // producto concreto, por id). Entre términos la condición es O: con
  // "pollo fresco", "bife angosto" y "picaña" entran los tres grupos y se
  // exportan juntos, que con un único campo de texto era imposible.
  terminos: [],
  // 'o' (alcanza con cumplir uno) o 'y' (hay que cumplirlos todos). Con O se
  // juntan canastas distintas --pollo fresco, bife angosto, picaña-- para
  // exportarlas de una; con Y se afina una sola búsqueda --"pollo" y "tottus"--.
  modo: 'o',
  cadenas: new Set(),
  familias: new Set(),
  categorias: new Set(),
  marcas: new Set(),
  // El rango es un par de fechas, no una cantidad de días hacia atrás: así se
  // puede exportar un mes cerrado, o la quincena de una promoción, y no solo
  // "los últimos N". Los atajos de abajo lo único que hacen es completarlas.
  desde: null,
  hasta: null,
  soloDescuento: false,
  precioMax: null,
  // Los productos tildados en la tabla, para compararlos entre sí. Se guarda el
  // id y no el objeto: el índice se recalcula y los objetos cambian.
  seleccion: new Set(),
};

// Ocho series es el techo. No se inventa un color para la novena: los colores
// son los de las cadenas y repetirlos ya obliga a distinguir por trazo.
const TOPE_SELECCION = 8;

const GUARDADAS = 'panel-combinaciones-v1';

// Las series diarias se recalculan solo cuando cambia el rango, no con cada
// tecla del buscador: expandir 3.665 productos × 140 días es lo único caro acá.
const cacheSeries = { rango: null, mapa: new Map() };

/* ── utilidades ─────────────────────────────────────────────────────────── */

const pct = (v, dec = 0) => (v == null || !isFinite(v) ? '—' : `${(v * 100).toFixed(dec)}%`);

function mediana(valores) {
  if (!valores.length) return null;
  const o = Float64Array.from(valores).sort();
  const m = o.length >> 1;
  return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2;
}

/** Los días del rango elegido, de más viejo a más nuevo. */
/** Los límites de lo que hay en el histórico. */
function limites() {
  return { min: estado.salud.primer_dia, max: estado.salud.ultimo_dia };
}

/** Suma días a una fecha ISO, sin pasar por la zona horaria del navegador.
 *
 *  El mediodía es a propósito: con T00:00 y un huso al oeste de Greenwich,
 *  toISOString() devuelve el día anterior. */
function sumarDias(iso, n) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

/** El rango vigente, recortado a lo que el histórico tiene. */
function rangoVigente() {
  const { min, max } = limites();
  let desde = PF.desde || sumarDias(max, -89);
  let hasta = PF.hasta || max;
  if (desde < min) desde = min;
  if (hasta > max) hasta = max;
  if (desde > hasta) desde = hasta;
  return { desde, hasta };
}

function diasDelPanel() {
  const { desde, hasta } = rangoVigente();
  const dias = [];
  for (let d = desde; d <= hasta; d = sumarDias(d, 1)) dias.push(d);
  return dias;
}

/** El precio de un producto día por día, como arreglo tipado.
 *
 *  Tipado y no una lista de objetos a propósito: son 3.665 productos × 140
 *  días, y un objeto por celda eran medio millón de asignaciones en cada
 *  recálculo. Con Float32Array son 2 MB y el recálculo baja a decenas de
 *  milisegundos. NaN marca "sin dato ese día", que no es lo mismo que cero.
 */
function serieNumerica(id, dias) {
  if (cacheSeries.mapa.has(id)) return cacheSeries.mapa.get(id);
  const precios = new Float32Array(dias.length).fill(NaN);
  const promos = new Uint8Array(dias.length);
  const puntos = estado.series[id];
  if (puntos) {
    let i = 0; let p = NaN; let pr = 0;
    for (let d = 0; d < dias.length; d += 1) {
      while (i < puntos.length && puntos[i][0] <= dias[d]) {
        p = puntos[i][1] == null ? NaN : puntos[i][1];
        pr = puntos[i][3] ? 1 : 0;
        i += 1;
      }
      precios[d] = p; promos[d] = pr;
    }
  }
  const salida = { precios, promos };
  cacheSeries.mapa.set(id, salida);
  return salida;
}

function invalidarCache(dias) {
  const clave = dias[0] + '|' + dias[dias.length - 1];
  if (cacheSeries.rango !== clave) {
    cacheSeries.rango = clave;
    cacheSeries.mapa.clear();
  }
}

/* ── filtrado ───────────────────────────────────────────────────────────── */

/** Si un producto satisface un término. */
function cumple(p, termino) {
  if (termino.t === 'prod') return p.id === termino.id;
  const texto = normalizar(`${p.nombre} ${p.marca || ''} ${p.categoria}`);
  return termino.palabras.every((w) => texto.includes(w));
}

function productosDelPanel() {
  return estado.indice.filter((p) => {
    if (PF.cadenas.size && !PF.cadenas.has(p.cadena)) return false;
    if (PF.familias.size && !PF.familias.has(p.familia)) return false;
    if (PF.categorias.size && !PF.categorias.has(p.categoria)) return false;
    if (PF.marcas.size && !PF.marcas.has(p.marca || '—')) return false;
    if (PF.soloDescuento && !p.descuento) return false;
    if (PF.precioMax != null && (p.precio == null || p.precio > PF.precioMax)) return false;
    if (PF.terminos.length && !pasaLosTerminos(p)) return false;
    return true;
  });
}

/** Si un producto pasa la lista de términos, según el modo.
 *
 *  Los productos fijados --los que se eligieron uno por uno de las
 *  sugerencias-- entran siempre, en los dos modos. No son una condición de
 *  búsqueda sino una elección explícita, y con Y pedir que un producto sea a la
 *  vez otro producto no deja pasar nada.
 */
function pasaLosTerminos(p) {
  const fijados = PF.terminos.filter((x) => x.t === 'prod');
  if (fijados.some((x) => x.id === p.id)) return true;
  const texto = PF.terminos.filter((x) => x.t === 'texto');
  if (!texto.length) return false;
  return PF.modo === 'y'
    ? texto.every((x) => cumple(p, x))
    : texto.some((x) => cumple(p, x));
}

/** Cuántos productos trae cada término por sí solo, para mostrarlo en su chip. */
function conteoPorTermino() {
  return PF.terminos.map((x) => estado.indice.filter((p) => cumple(p, x)).length);
}

/* ── cálculo ────────────────────────────────────────────────────────────── */

function calcularPanel() {
  const dias = diasDelPanel();
  invalidarCache(dias);
  const items = productosDelPanel();

  const cadenas = [...new Set(items.map((p) => p.cadena))]
    .sort((a, b) => Object.keys(CADENAS).indexOf(a) - Object.keys(CADENAS).indexOf(b));

  // Acumuladores por cadena y día.
  const precios = {}; const enPromo = {}; const conDato = {};
  cadenas.forEach((c) => {
    precios[c] = dias.map(() => []);
    enPromo[c] = new Int32Array(dias.length);
    conDato[c] = new Int32Array(dias.length);
  });

  const filas = [];
  items.forEach((p) => {
    const { precios: ps, promos } = serieNumerica(p.id, dias);
    let min = Infinity; let max = -Infinity; let minDia = null;
    let n = 0; let nPromo = 0; const vivos = [];
    for (let d = 0; d < dias.length; d += 1) {
      const v = ps[d];
      if (!isFinite(v)) continue;
      precios[p.cadena][d].push(v);
      conDato[p.cadena][d] += 1;
      if (promos[d]) { enPromo[p.cadena][d] += 1; nPromo += 1; }
      vivos.push(v); n += 1;
      if (v < min) { min = v; minDia = dias[d]; }
      if (v > max) max = v;
    }
    if (!n) return;
    const primero = vivos[0]; const ultimo = vivos[vivos.length - 1];
    filas.push({
      p,
      hoy: ultimo,
      mediana: mediana(vivos),
      min, max, minDia,
      dias: n,
      promo: nPromo / n,
      variacion: primero ? (ultimo - primero) / primero : null,
      enMinimo: ultimo <= min + 0.009,
    });
  });

  const serieMediana = cadenas.map((c) => ({
    cadena: c,
    valores: dias.map((_, d) => mediana(precios[c][d])),
  }));
  const seriePromo = cadenas.map((c) => ({
    cadena: c,
    valores: dias.map((_, d) => (conDato[c][d] ? enPromo[c][d] / conDato[c][d] : null)),
  }));

  return {
    dias, items, cadenas, filas, serieMediana, seriePromo,
    barato: quienEstaMasBarato(items, dias),
    kpis: resumirKpis(filas, dias, cadenas, precios),
  };
}

/** En cuántas comparaciones gana cada cadena.
 *
 *  Se compara dentro de grupos de productos parecidos que tengan al menos dos
 *  cadenas, que es el mismo agrupamiento del buscador. Comparar la mediana de
 *  cada cadena contra la de la otra sería más barato y estaría mal: cada una
 *  vende un surtido distinto, así que esa diferencia mide el surtido, no el
 *  precio. Acá cada comparación es entre productos que se parecen.
 */
function quienEstaMasBarato(items, dias) {
  const grupos = agrupar(items, false).filter((g) => g.cadenas > 1);
  const gana = {}; let total = 0; let empates = 0;
  grupos.forEach((g) => {
    const series = g.items.map((x) => ({ cadena: x.cadena, s: serieNumerica(x.id, dias).precios }));
    for (let d = 0; d < dias.length; d += 1) {
      let mejor = null; let empate = false;
      series.forEach(({ cadena, s }) => {
        const v = s[d];
        if (!isFinite(v)) return;
        if (!mejor || v < mejor.v - 0.009) { mejor = { cadena, v }; empate = false; }
        else if (Math.abs(v - mejor.v) <= 0.009 && cadena !== mejor.cadena) empate = true;
      });
      // Los empates no se adjudican: si dos cadenas tienen el mismo precio,
      // ninguna "está más barata" y contarlo para una sería inventar.
      if (!mejor) continue;
      total += 1;
      if (empate) empates += 1;
      else gana[mejor.cadena] = (gana[mejor.cadena] || 0) + 1;
    }
  });
  return {
    grupos: grupos.length,
    total,
    empates,
    filas: Object.entries(gana)
      .map(([cadena, n]) => ({ cadena, n, parte: total ? n / total : 0 }))
      .sort((a, b) => b.n - a.n),
  };
}

function resumirKpis(filas, dias, cadenas, precios) {
  const hoy = filas.map((f) => f.hoy).filter((v) => v != null);
  const medianaHoy = mediana(hoy);
  // La canasta: la mediana de la mediana diaria, para que un día con pocos
  // productos no pese igual que uno completo.
  const porDia = dias.map((_, d) => {
    const todos = cadenas.flatMap((c) => precios[c][d]);
    return mediana(todos);
  }).filter((v) => v != null);
  const inicio = porDia[0]; const fin = porDia[porDia.length - 1];
  return {
    productos: filas.length,
    medianaHoy,
    canastaVar: inicio ? (fin - inicio) / inicio : null,
    enPromo: filas.length ? filas.filter((f) => f.promo > 0.5).length / filas.length : null,
    enMinimo: filas.filter((f) => f.enMinimo).length,
    serieCanasta: porDia,
  };
}

/* ── gráficos ───────────────────────────────────────────────────────────── */

const SVGNS = 'http://www.w3.org/2000/svg';
const svgEl = (t, attrs) => {
  const n = document.createElementNS(SVGNS, t);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  return n;
};

/** Gráfico de líneas con cruz y globo, como el de una herramienta de BI.
 *
 *  `formato` decide cómo se escribe un valor (soles o porcentaje); el eje y el
 *  globo usan el mismo, así que nunca se contradicen. */
function grafLineas({ dias, series, formato, titulo, idUnico }) {
  const angosto = window.innerWidth < 760;
  const W = angosto ? 380 : 720;
  const H = angosto ? 230 : 250;
  const M = angosto ? { t: 14, r: 46, b: 24, l: 40 } : { t: 16, r: 92, b: 26, l: 52 };
  const caja = el('div', 'graf');
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'graf-svg', role: 'img' });
  svg.setAttribute('aria-label', titulo);
  caja.append(svg);

  const vivos = series.flatMap((s) => s.valores).filter((v) => v != null && isFinite(v));
  if (!vivos.length) {
    caja.append(el('p', 'graf-vacio', 'Sin datos para este filtro.'));
    return caja;
  }
  let lo = Math.min(...vivos); let hi = Math.max(...vivos);
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  const margen = (hi - lo) * 0.12; lo -= margen; hi += margen;
  if (formato === 'pct') { lo = Math.max(0, lo); hi = Math.min(1, hi); }

  const x = (i) => M.l + (i / Math.max(dias.length - 1, 1)) * (W - M.l - M.r);
  const y = (v) => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
  const escribir = (v) => (formato === 'pct' ? pct(v) : soles(v));

  marcasDeEje(lo, hi).forEach((v) => {
    svg.append(svgEl('line', { class: 'graf-grilla', x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    const t = svgEl('text', { x: M.l - 6, y: y(v) + 3.5, 'text-anchor': 'end', class: 'graf-eje-txt' });
    t.textContent = escribir(v);
    svg.append(t);
  });

  const cuantas = Math.min(angosto ? 3 : 6, dias.length);
  for (let k = 0; k < cuantas; k += 1) {
    const i = Math.round((k / Math.max(cuantas - 1, 1)) * (dias.length - 1));
    const [, mes, d] = dias[i].split('-');
    const t = svgEl('text', {
      x: x(i), y: H - 8, class: 'graf-eje-txt',
      'text-anchor': k === 0 ? 'start' : k === cuantas - 1 ? 'end' : 'middle',
    });
    t.textContent = `${d}/${mes}`;
    svg.append(t);
  }
  svg.append(svgEl('line', { class: 'graf-eje', x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b }));

  series.forEach(({ cadena, valores, etiqueta, trazo }) => {
    let d = ''; let trazando = false;
    valores.forEach((v, i) => {
      if (v == null || !isFinite(v)) { trazando = false; return; }
      d += `${trazando ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
      trazando = true;
    });
    if (!d) return;
    const attrs = { class: 'graf-serie', d, stroke: CADENAS[cadena]?.color || 'var(--apagado)' };
    if (trazo) attrs['stroke-dasharray'] = trazo;
    svg.append(svgEl('path', attrs));
    // Etiqueta directa al final: con cuatro series o menos, la identidad no
    // depende solo del color.
    if (!angosto) {
      for (let i = valores.length - 1; i >= 0; i -= 1) {
        if (valores[i] == null || !isFinite(valores[i])) continue;
        const t = svgEl('text', {
          x: x(i) + 7, y: y(valores[i]) + 3.5, class: 'graf-etiqueta',
          fill: CADENAS[cadena]?.color || 'var(--apagado)',
        });
        // Recorte por caracteres y no por palabras: dos productos de la
        // misma cadena suelen compartir las primeras dos ("Pollo Fresco…")
        // y la etiqueta dejaba de distinguirlos.
        // Cadena primero y después el producto: el nombre solo no dice de
        // dónde viene, y con dos cadenas vendiendo lo mismo hace falta.
        const corto = etiqueta && etiqueta.length > 15
          ? etiqueta.slice(0, 14) + '…' : etiqueta;
        t.textContent = etiqueta
          ? `${CADENAS[cadena]?.nombre || cadena} · ${corto}`
          : (CADENAS[cadena]?.nombre || cadena);
        svg.append(t);
        break;
      }
    }
  });

  // ── cruz y globo ──
  const guia = svgEl('line', { class: 'graf-guia', y1: M.t, y2: H - M.b, x1: 0, x2: 0, opacity: 0 });
  svg.append(guia);
  const puntos = series.map(() => {
    const c = svgEl('circle', { r: 4, class: 'graf-punto', opacity: 0 });
    svg.append(c);
    return c;
  });
  const globo = el('div', 'graf-globo');
  globo.hidden = true;
  caja.append(globo);

  const mover = (ev) => {
    const r = svg.getBoundingClientRect();
    const frac = (((ev.clientX - r.left) / r.width) * W - M.l) / (W - M.l - M.r);
    const i = Math.max(0, Math.min(dias.length - 1, Math.round(frac * (dias.length - 1))));
    guia.setAttribute('x1', x(i)); guia.setAttribute('x2', x(i)); guia.setAttribute('opacity', 1);
    const [a, mes, d] = dias[i].split('-');
    globo.replaceChildren(el('b', null, `${d}/${mes}/${a.slice(2)}`));
    series.forEach((s, k) => {
      const v = s.valores[i];
      const punto = puntos[k];
      if (v == null || !isFinite(v)) { punto.setAttribute('opacity', 0); return; }
      punto.setAttribute('cx', x(i)); punto.setAttribute('cy', y(v));
      punto.setAttribute('fill', CADENAS[s.cadena]?.color || 'var(--apagado)');
      punto.setAttribute('opacity', 1);
      const linea = el('span');
      const bolita = el('i');
      bolita.style.background = CADENAS[s.cadena]?.color || 'var(--apagado)';
      linea.append(bolita,
        el('span', 'globo-n', s.etiqueta
          ? `${CADENAS[s.cadena]?.nombre || s.cadena} · ${s.etiqueta.slice(0, 30)}`
          : (CADENAS[s.cadena]?.nombre || s.cadena)),
        el('b', null, escribir(v)));
      globo.append(linea);
    });
    globo.hidden = false;
    const izq = (x(i) / W) * 100;
    globo.style.left = `${izq}%`;
    globo.classList.toggle('a-la-izquierda', izq > 58);
  };
  const salir = () => {
    guia.setAttribute('opacity', 0);
    puntos.forEach((p) => p.setAttribute('opacity', 0));
    globo.hidden = true;
  };
  caja.addEventListener('mousemove', mover);
  caja.addEventListener('mouseleave', salir);
  return caja;
}

/** Barras horizontales con el valor escrito al lado. */
function grafBarras({ filas, titulo, formato, divergente }) {
  const caja = el('div', 'graf graf-barras');
  if (!filas.length) {
    caja.append(el('p', 'graf-vacio', 'Sin datos para este filtro.'));
    return caja;
  }
  const tope = Math.max(...filas.map((f) => Math.abs(f.valor))) || 1;
  const lista = el('div', 'barras');
  filas.forEach((f) => {
    const fila = el('div', 'barra-fila');
    const etiqueta = el('span', 'barra-etq');
    if (f.cadena) {
      const i = el('i');
      i.style.background = CADENAS[f.cadena]?.color || 'var(--apagado)';
      etiqueta.append(i);
    }
    // La cadena va primero y no se encoge: el nombre del producto es lo que
    // se recorta cuando no entra, porque saber de qué supermercado viene no
    // puede quedar afuera.
    if (f.cadenaNombre) etiqueta.append(el('em', 'barra-cadena', f.cadenaNombre));
    etiqueta.append(el('span', 'barra-nombre', f.nombre));
    etiqueta.title = f.cadenaNombre ? `${f.cadenaNombre} · ${f.nombre}` : f.nombre;

    const pista = el('span', 'barra-pista');
    const barra = el('span', 'barra');
    barra.style.width = `${(Math.abs(f.valor) / tope) * 100}%`;
    barra.style.background = divergente
      ? (f.valor >= 0 ? 'var(--caro)' : 'var(--barato)')
      // Gris cuando la barra no es una cadena: el color de cadena es
      // identidad y teñir "Empate" de azul lo haría pasar por Metro.
      : (CADENAS[f.cadena]?.color || 'var(--linea-fuerte)');
    pista.append(barra);

    const valor = el('span', 'barra-val',
      formato === 'pct' ? pct(f.valor, 0) : `S/ ${soles(f.valor)}`);
    if (divergente) valor.classList.add(f.valor >= 0 ? 'sube' : 'baja');
    fila.append(etiqueta, pista, valor);
    fila.title = `${f.cadenaNombre ? f.cadenaNombre + ' · ' : ''}${f.nombre}: `
      + `${formato === 'pct' ? pct(f.valor, 1) : soles(f.valor)}`;
    lista.append(fila);
  });
  caja.append(lista);
  return caja;
}

/** Las series de los productos tildados, listas para el gráfico.
 *
 *  Dos productos de la misma cadena comparten color, así que el segundo y los
 *  siguientes van punteados: si no, son dos líneas idénticas. */
function seriesDeSeleccion(dias) {
  const porId = new Map(estado.indice.map((p) => [p.id, p]));
  const vistos = {};
  const TRAZOS = ['', '6 4', '2 3', '9 3 2 3'];
  return [...PF.seleccion].slice(0, TOPE_SELECCION).map((id) => {
    const p = porId.get(id);
    if (!p) return null;
    const k = vistos[p.cadena] = (vistos[p.cadena] ?? -1) + 1;
    const { precios } = serieNumerica(id, dias);
    return {
      cadena: p.cadena,
      etiqueta: p.nombre,
      trazo: TRAZOS[Math.min(k, TRAZOS.length - 1)],
      valores: Array.from(precios, (v) => (isFinite(v) ? v : null)),
    };
  }).filter(Boolean);
}


function bloque(titulo, subtitulo, contenido) {
  const b = el('section', 'bloque');
  const cab = el('div', 'bloque-cab');
  cab.append(el('h3', null, titulo));
  if (subtitulo) cab.append(el('p', null, subtitulo));
  b.append(cab, contenido);
  return b;
}

/* ── filtros: la interfaz ───────────────────────────────────────────────── */

/* ── el buscador de términos ────────────────────────────────────────────── */

// Se construye una sola vez y se reusa: reconstruirlo en cada repintado le
// quitaba el foco al campo y se perdía la palabra a medio escribir.
let nodoBuscador = null;
let nodoChips = null;
let nodoSugerencias = null;
let nodoEntrada = null;

const MAX_SUGERENCIAS = 7;

function agregarTermino(termino) {
  const yaEsta = PF.terminos.some(
    (x) => x.t === termino.t && (x.id || x.v) === (termino.id || termino.v));
  if (!yaEsta) PF.terminos.push(termino);
  // Un producto agregado a mano entra también al gráfico de comparación: es
  // justamente para eso que uno busca un producto puntual.
  if (termino.t === 'prod' && PF.seleccion.size < TOPE_SELECCION) {
    PF.seleccion.add(termino.id);
  }
  nodoEntrada.value = '';
  cerrarSugerencias();
  pintarPanel({ conservarFiltros: true });
}

function cerrarSugerencias() {
  nodoSugerencias.replaceChildren();
  nodoSugerencias.hidden = true;
}

function pintarSugerencias() {
  const crudo = nodoEntrada.value.trim();
  if (!crudo) { cerrarSugerencias(); return; }
  const palabras = normalizar(crudo).split(' ').filter(Boolean);
  if (!palabras.length) { cerrarSugerencias(); return; }

  const opciones = [];
  const comoTexto = { t: 'texto', v: crudo, palabras };
  const cuantos = estado.indice.filter((p) => cumple(p, comoTexto)).length;
  opciones.push({
    clase: 'sug-texto',
    etiqueta: `Buscar «${crudo}»`,
    nota: `${cuantos.toLocaleString('es-PE')} producto(s)`,
    accion: () => agregarTermino(comoTexto),
  });

  estado.indice
    .filter((p) => cumple(p, comoTexto))
    .sort((a, b) => (a.precio ?? Infinity) - (b.precio ?? Infinity))
    .slice(0, MAX_SUGERENCIAS)
    .forEach((p) => opciones.push({
      clase: 'sug-prod',
      cadena: p.cadena,
      etiqueta: p.nombre,
      nota: `${CADENAS[p.cadena]?.nombre || p.cadena} · S/ ${soles(p.precio)}`,
      accion: () => agregarTermino({ t: 'prod', v: p.nombre, id: p.id }),
    }));

  nodoSugerencias.replaceChildren(...opciones.map((o) => {
    const b = el('button', `sug ${o.clase}`);
    b.type = 'button';
    if (o.cadena) {
      const i = el('i');
      i.style.background = CADENAS[o.cadena]?.color || 'var(--apagado)';
      b.append(i);
    }
    b.append(el('span', 'sug-txt', o.etiqueta), el('em', null, o.nota));
    b.addEventListener('mousedown', (ev) => { ev.preventDefault(); o.accion(); });
    return b;
  }));
  nodoSugerencias.hidden = false;
}

function pintarChips() {
  const conteos = conteoPorTermino();
  nodoChips.replaceChildren();
  if (!PF.terminos.length) {
    nodoChips.append(el('span', 'chips-vacio',
      'Sin términos: entran todos los productos. Escribí uno y agregalo.'));
    return;
  }

  // El modo se ofrece recién con dos términos de texto: con uno solo, O e Y
  // dan lo mismo y el control sería una pregunta sin consecuencia.
  if (PF.terminos.filter((x) => x.t === 'texto').length > 1) {
    const modo = el('span', 'modo-terminos');
    modo.append(el('span', 'modo-txt', 'Entra lo que cumpla'));
    [['cualquiera', 'o'], ['todos', 'y']].forEach(([etq, valor]) => {
      const b = el('button', 'modo-op' + (PF.modo === valor ? ' activo' : ''));
      b.type = 'button';
      b.textContent = etq;
      b.title = valor === 'o'
        ? 'Cualquiera: junta canastas distintas (pollo fresco, bife angosto…)'
        : 'Todos: afina una sola búsqueda (pollo + tottus)';
      b.addEventListener('click', () => {
        PF.modo = valor;
        pintarPanel({ conservarFiltros: true });
      });
      modo.append(b);
    });
    nodoChips.append(modo);
  }
  PF.terminos.forEach((x, i) => {
    const c = el('span', `termino ${x.t === 'prod' ? 'es-prod' : ''}`);
    if (x.t === 'prod') {
      const ind = estado.indice.find((p) => p.id === x.id);
      const punto = el('i');
      punto.style.background = CADENAS[ind?.cadena]?.color || 'var(--apagado)';
      c.append(punto);
    }
    if (x.t === 'prod') {
      const ind = estado.indice.find((p) => p.id === x.id);
      c.append(el('span', 'termino-cadena',
        CADENAS[ind?.cadena]?.nombre || ind?.cadena || ''));
    }
    c.append(el('span', 'termino-txt', x.v), el('em', null, String(conteos[i])));
    const quitar = el('button');
    quitar.type = 'button';
    quitar.textContent = '×';
    quitar.setAttribute('aria-label', `Quitar ${x.v}`);
    quitar.addEventListener('click', () => {
      if (x.t === 'prod') PF.seleccion.delete(x.id);
      PF.terminos.splice(i, 1);
      pintarPanel({ conservarFiltros: true });
    });
    c.append(quitar);
    c.title = x.t === 'prod' ? `Producto: ${x.v}` : `Búsqueda: ${x.v}`;
    nodoChips.append(c);
  });
  const limpiar = el('button', 'termino-limpiar', 'Quitar todos');
  limpiar.type = 'button';
  limpiar.addEventListener('click', () => {
    PF.terminos.forEach((x) => { if (x.t === 'prod') PF.seleccion.delete(x.id); });
    PF.terminos = [];
    pintarPanel({ conservarFiltros: true });
  });
  nodoChips.append(limpiar);
}

function buscador() {
  if (nodoBuscador) { pintarChips(); return nodoBuscador; }

  nodoBuscador = el('div', 'campo campo-ancho buscador-terminos');
  nodoBuscador.append(el('label', null, 'Términos de búsqueda'));

  const fila = el('div', 'buscador-fila');
  nodoEntrada = el('input');
  nodoEntrada.type = 'search';
  nodoEntrada.placeholder = 'pollo fresco, bife angosto, picaña…';
  nodoEntrada.autocomplete = 'off';
  nodoEntrada.addEventListener('input', pintarSugerencias);
  nodoEntrada.addEventListener('focus', pintarSugerencias);
  nodoEntrada.addEventListener('blur', () => setTimeout(cerrarSugerencias, 120));
  nodoEntrada.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const crudo = nodoEntrada.value.trim();
      const palabras = normalizar(crudo).split(' ').filter(Boolean);
      if (palabras.length) agregarTermino({ t: 'texto', v: crudo, palabras });
    } else if (ev.key === 'Escape') cerrarSugerencias();
    else if (ev.key === 'Backspace' && !nodoEntrada.value && PF.terminos.length) {
      const fuera = PF.terminos.pop();
      if (fuera.t === 'prod') PF.seleccion.delete(fuera.id);
      pintarPanel({ conservarFiltros: true });
    }
  });
  const agregar = el('button', 'buscador-agregar', 'Agregar');
  agregar.type = 'button';
  agregar.addEventListener('click', () => {
    const crudo = nodoEntrada.value.trim();
    const palabras = normalizar(crudo).split(' ').filter(Boolean);
    if (palabras.length) agregarTermino({ t: 'texto', v: crudo, palabras });
  });
  fila.append(nodoEntrada, agregar);

  nodoSugerencias = el('div', 'sugerencias-panel');
  nodoSugerencias.hidden = true;
  const envoltura = el('div', 'buscador-envoltura');
  envoltura.append(fila, nodoSugerencias);

  nodoChips = el('div', 'terminos');
  nodoBuscador.append(envoltura, nodoChips);
  pintarChips();
  return nodoBuscador;
}


function opcionesDe(campo) {
  const cuenta = new Map();
  estado.indice.forEach((p) => {
    const v = campo === 'marca' ? (p.marca || '—') : p[campo];
    if (v) cuenta.set(v, (cuenta.get(v) || 0) + 1);
  });
  return [...cuenta.entries()].sort((a, b) => b[1] - a[1]);
}

function campoMulti(titulo, campo, conjunto, tope) {
  const caja = el('div', 'campo');
  caja.append(el('label', null, titulo));
  const chips = el('div', 'campo-chips');
  opcionesDe(campo).slice(0, tope).forEach(([valor, n]) => {
    const b = el('button', 'chip');
    b.type = 'button';
    b.append(document.createTextNode(valor), el('em', null, String(n)));
    if (conjunto.has(valor)) b.classList.add('activo');
    b.addEventListener('click', () => {
      if (conjunto.has(valor)) conjunto.delete(valor); else conjunto.add(valor);
      pintarPanel();
    });
    chips.append(b);
  });
  caja.append(chips);
  return caja;
}

function pintarFiltrosPanel() {
  const cont = document.getElementById('panel-campos');
  cont.replaceChildren();

  cont.append(buscador());

  const rango = el('div', 'campo');
  rango.append(el('label', null, 'Período'));
  const { min, max } = limites();
  const vig = rangoVigente();

  const fechas = el('div', 'rango-fechas');
  const campo = (etq, valor, alCambiar) => {
    const caja = el('label', 'rango-campo');
    caja.append(el('span', null, etq));
    const i = el('input');
    i.type = 'date';
    i.min = min; i.max = max; i.value = valor;
    i.addEventListener('change', () => {
      if (i.value) alCambiar(i.value);
      pintarPanel();
    });
    caja.append(i);
    return caja;
  };
  fechas.append(
    campo('Desde', vig.desde, (v) => { PF.desde = v; }),
    campo('Hasta', vig.hasta, (v) => { PF.hasta = v; }),
  );
  rango.append(fechas);

  // Los atajos no son otro estado: escriben las dos fechas. Así el selector
  // siempre muestra el rango real y no hay dos fuentes de verdad.
  const chips = el('div', 'campo-chips');
  const ATAJOS = [
    ['30 días', () => ({ desde: sumarDias(max, -29), hasta: max })],
    ['90 días', () => ({ desde: sumarDias(max, -89), hasta: max })],
    ['Este mes', () => ({ desde: max.slice(0, 8) + '01', hasta: max })],
    ['Mes pasado', () => {
      const primeroDeEste = max.slice(0, 8) + '01';
      const finAnterior = sumarDias(primeroDeEste, -1);
      return { desde: finAnterior.slice(0, 8) + '01', hasta: finAnterior };
    }],
    ['Todo', () => ({ desde: min, hasta: max })],
  ];
  ATAJOS.forEach(([etq, calcular]) => {
    const b = el('button', 'chip');
    b.type = 'button';
    b.textContent = etq;
    const r = calcular();
    if (r.desde === vig.desde && r.hasta === vig.hasta) b.classList.add('activo');
    b.addEventListener('click', () => {
      PF.desde = r.desde; PF.hasta = r.hasta;
      pintarPanel();
    });
    chips.append(b);
  });
  rango.append(chips);
  rango.append(el('p', 'rango-nota',
    `${diasDelPanel().length} días · el histórico va del ${min} al ${max}`));
  cont.append(rango);

  cont.append(campoMulti('Cadena', 'cadena', PF.cadenas, 8));
  cont.append(campoMulti('Familia', 'familia', PF.familias, 12));
  cont.append(campoMulti('Categoría', 'categoria', PF.categorias, 18));
  cont.append(campoMulti('Marca', 'marca', PF.marcas, 18));

  const otros = el('div', 'campo');
  otros.append(el('label', null, 'Otros'));
  const ochips = el('div', 'campo-chips');
  const bd = el('button', 'chip');
  bd.type = 'button';
  bd.textContent = 'Solo con descuento';
  if (PF.soloDescuento) bd.classList.add('activo');
  bd.addEventListener('click', () => { PF.soloDescuento = !PF.soloDescuento; pintarPanel(); });
  ochips.append(bd);
  const tope = el('label', 'chip chip-num');
  tope.append(document.createTextNode('Precio hasta S/'));
  const num = el('input');
  num.type = 'number'; num.min = '0'; num.step = '1';
  num.value = PF.precioMax ?? '';
  num.addEventListener('change', () => {
    PF.precioMax = num.value === '' ? null : Number(num.value);
    pintarPanel();
  });
  tope.append(num);
  ochips.append(tope);
  otros.append(ochips);
  cont.append(otros);
}

/* ── combinaciones guardadas ────────────────────────────────────────────── */

function leerGuardadas() {
  try { return JSON.parse(localStorage.getItem(GUARDADAS) || '{}'); } catch { return {}; }
}
function escribirGuardadas(obj) {
  // Envuelto porque en una ventana privada o con el almacenamiento bloqueado
  // esto lanza, y perder un filtro guardado no puede tirar el tablero.
  try { localStorage.setItem(GUARDADAS, JSON.stringify(obj)); } catch { /* sin guardar */ }
}
function instantanea() {
  return {
    terminos: PF.terminos, modo: PF.modo,
    desde: PF.desde, hasta: PF.hasta,
    soloDescuento: PF.soloDescuento,
    precioMax: PF.precioMax, seleccion: [...PF.seleccion],
    cadenas: [...PF.cadenas], familias: [...PF.familias],
    categorias: [...PF.categorias], marcas: [...PF.marcas],
  };
}
function aplicar(snap) {
  // Los términos guardados vienen sin `palabras`, que es derivada: se
  // recalcula al cargarlos para no depender de lo que había en el disco.
  PF.terminos = (snap.terminos || []).map((x) => (x.t === 'prod' ? x : {
    ...x, palabras: normalizar(x.v).split(' ').filter(Boolean),
  }));
  PF.modo = snap.modo === 'y' ? 'y' : 'o';
  PF.desde = snap.desde || null;
  PF.hasta = snap.hasta || null;
  PF.soloDescuento = !!snap.soloDescuento;
  PF.precioMax = snap.precioMax ?? null;
  PF.seleccion = new Set(snap.seleccion || []);
  ['cadenas', 'familias', 'categorias', 'marcas'].forEach((k) => {
    PF[k] = new Set(snap[k] || []);
  });
}
function pintarGuardadas() {
  const sel = document.getElementById('pf-guardadas');
  const guardadas = leerGuardadas();
  sel.replaceChildren(el('option', null, 'Combinaciones guardadas…'));
  Object.keys(guardadas).sort().forEach((n) => {
    const o = el('option', null, n);
    o.value = n;
    sel.append(o);
  });
}

/* ── exportación ────────────────────────────────────────────────────────── */

const COLUMNAS_CSV = [
  ['cadena', (f) => CADENAS[f.p.cadena]?.nombre || f.p.cadena],
  ['producto', (f) => f.p.nombre],
  ['marca', (f) => f.p.marca || ''],
  ['categoria', (f) => f.p.categoria],
  ['familia', (f) => f.p.familia],
  ['precio_hoy', (f) => f.hoy?.toFixed(2) ?? ''],
  ['precio_mediana', (f) => f.mediana?.toFixed(2) ?? ''],
  ['precio_minimo', (f) => f.min?.toFixed(2) ?? ''],
  ['fecha_minimo', (f) => f.minDia || ''],
  ['precio_maximo', (f) => f.max?.toFixed(2) ?? ''],
  ['dias_con_dato', (f) => f.dias],
  ['pct_dias_en_promo', (f) => (f.promo * 100).toFixed(1)],
  ['variacion_periodo_pct', (f) => (f.variacion == null ? '' : (f.variacion * 100).toFixed(1))],
  ['en_minimo_historico', (f) => (f.enMinimo ? 'si' : 'no')],
  ['url', (f) => f.p.url || ''],
];

function exportarCsv(calc) {
  const celda = (v) => {
    const s = String(v ?? '');
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lineas = [COLUMNAS_CSV.map(([n]) => n).join(';')];
  calc.filas.forEach((f) => lineas.push(COLUMNAS_CSV.map(([, fn]) => celda(fn(f))).join(';')));
  // Punto y coma y BOM: Excel en español abre así el archivo en columnas. Con
  // coma mete todo en la primera columna y sin BOM rompe los acentos.
  const texto = '﻿' + lineas.join('\r\n') + '\r\n';
  const blob = new Blob([texto], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `precios_${calc.dias[0]}_a_${calc.dias[calc.dias.length - 1]}.csv`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ── render ─────────────────────────────────────────────────────────────── */

const TOPE_TABLA = 200;
let ultimoCalculo = null;

function pintarKpis(k, dias) {
  const cont = document.getElementById('panel-kpis');
  const tarjetas = [
    { t: 'Productos', v: k.productos.toLocaleString('es-PE'),
      n: `en ${dias.length} días` },
    { t: 'Precio mediano hoy', v: `S/ ${soles(k.medianaHoy)}`,
      n: 'del conjunto filtrado' },
    { t: 'La canasta', v: k.canastaVar == null ? '—' : pct(k.canastaVar, 1),
      n: 'cambio en el período', estado: k.canastaVar == null ? '' : (k.canastaVar > 0 ? 'sube' : 'baja') },
    { t: 'Casi siempre en promo', v: pct(k.enPromo),
      n: 'más de la mitad de los días' },
    { t: 'En mínimo histórico', v: k.enMinimo.toLocaleString('es-PE'),
      n: 'hoy están en su piso' },
  ];
  cont.replaceChildren(...tarjetas.map((x) => {
    const c = el('div', 'kpi');
    c.append(el('span', 'kpi-t', x.t));
    const v = el('strong', 'kpi-v', x.v);
    if (x.estado) v.classList.add(x.estado);
    c.append(v, el('span', 'kpi-n', x.n));
    return c;
  }));
}

function pintarGraficos(c) {
  const cont = document.getElementById('panel-graficos');
  const movimientos = c.filas
    .filter((f) => f.variacion != null && Math.abs(f.variacion) > 0.001 && f.dias > 5)
    .sort((a, b) => b.variacion - a.variacion);
  const extremos = [...movimientos.slice(0, 5), ...movimientos.slice(-5).reverse()]
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .map((f) => ({
      nombre: f.p.nombre.slice(0, 34),
      // Sin esto la barra solo tenía el punto de color, y dos productos con el
      // mismo nombre en cadenas distintas eran indistinguibles.
      cadenaNombre: CADENAS[f.p.cadena]?.nombre || f.p.cadena,
      cadena: f.p.cadena,
      valor: f.variacion,
    }));

  const bloques = [];
  if (PF.seleccion.size) {
    const series = seriesDeSeleccion(c.dias);
    bloques.push(bloque(
      `Comparar ${series.length} producto(s) seleccionado(s)`,
      'Los que agregaste como término o tildaste en la tabla. Dos productos de '
      + 'la misma cadena comparten color, así que el segundo va punteado.',
      grafLineas({ dias: c.dias, series, formato: 'soles',
                   titulo: 'Histórico de los productos seleccionados' })));
  }

  const enlaces = pintarEnlaces(c);
  if (enlaces) bloques.push(enlaces);

  cont.replaceChildren(
    ...bloques,
    bloque('Precio mediano por cadena',
      'Cada cadena vende un surtido distinto, así que esto sirve para ver cómo '
      + 'se mueve cada una en el tiempo, no para decir cuál es más barata. '
      + 'Para eso está el panel de abajo, que compara producto contra producto.',
      grafLineas({ dias: c.dias, series: c.serieMediana, formato: 'soles',
                   titulo: 'Precio mediano por cadena' })),
    bloque('Intensidad promocional',
      'Qué parte del surtido filtrado está con descuento cada día.',
      grafLineas({ dias: c.dias, series: c.seriePromo, formato: 'pct',
                   titulo: 'Porcentaje de productos en promoción por cadena' })),
    bloque('Quién está más barato',
      `Sobre ${c.barato.grupos.toLocaleString('es-PE')} grupos de productos parecidos `
      + 'presentes en más de una cadena, día por día. Cuando dos cadenas tienen '
      + 'el mismo precio no gana ninguna: eso es el empate.',
      // Sin grupos presentes en más de una cadena no hay nada que comparar
      // --pasa al filtrar por una sola cadena--, y una barra de "Empate 0%"
      // dice menos que decirlo.
      !c.barato.grupos ? el('p', 'graf-vacio',
        'El filtro no deja productos parecidos en más de una cadena, así que no '
        + 'hay comparación posible.')
      : grafBarras({
        // El empate va como una barra más, si no las otras suman 42% y parece
        // que falta algo. Que dos cadenas tengan el mismo precio la mitad de
        // los días es un resultado, no un hueco.
        filas: [
          ...c.barato.filas.map((f) => ({
            nombre: CADENAS[f.cadena]?.nombre || f.cadena, cadena: f.cadena, valor: f.parte,
          })),
          { nombre: 'Empate', valor: c.barato.total ? c.barato.empates / c.barato.total : 0 },
        ], formato: 'pct',
      })),
    bloque('Mayores subidas y bajas',
      'Variación entre el primer y el último día del período, por producto.',
      grafBarras({ filas: extremos, formato: 'pct', divergente: true })),
  );
}

/** El enlace a la ficha del producto en la web de la cadena.
 *
 *  Va en cada fila y no solo en un lugar: el tablero sirve para encontrar el
 *  producto, pero la decisión de compra se toma mirando la ficha real --el
 *  gramaje, la foto, si hay stock--, y hasta ahora había que buscarlo a mano.
 */
function celdaEnlace(p) {
  const td = el('td', 'ir');
  if (!p.url) return td;
  const a = el('a', 'ir-tienda', '↗');
  a.href = p.url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.title = `Abrir en ${CADENAS[p.cadena]?.nombre || p.cadena}`;
  a.setAttribute('aria-label', `Abrir ${p.nombre} en ${CADENAS[p.cadena]?.nombre || p.cadena}`);
  td.append(a);
  return td;
}

/** Los enlaces de lo que se está comparando, juntos y con su precio. */
function pintarEnlaces(c) {
  const porId = new Map(estado.indice.map((p) => [p.id, p]));
  const elegidos = [...PF.seleccion].map((id) => porId.get(id)).filter(Boolean);
  if (!elegidos.length) return null;
  const lista = el('div', 'enlaces');
  elegidos.forEach((p) => {
    const fila = el('a', 'enlace-fila');
    if (p.url) {
      fila.href = p.url;
      fila.target = '_blank';
      fila.rel = 'noopener noreferrer';
    } else {
      fila.classList.add('sin-enlace');
    }
    const i = el('i');
    i.style.background = CADENAS[p.cadena]?.color || 'var(--apagado)';
    const izq = el('span', 'enlace-txt');
    izq.append(el('b', null, CADENAS[p.cadena]?.nombre || p.cadena),
               el('span', null, p.nombre));
    fila.append(i, izq,
      el('span', 'enlace-precio', `S/ ${soles(p.precio)}`),
      el('span', 'enlace-ir', p.url ? 'Abrir ↗' : 'sin enlace'));
    lista.append(fila);
  });
  return bloque('Ir a la tienda',
    'Los productos que estás comparando, con el enlace a su ficha en la web de '
    + 'cada cadena. Abren en una pestaña nueva.',
    lista);
}

function pintarTabla(c) {
  const tabla = document.getElementById('panel-tabla');
  const cab = el('tr');
  // La cadena va escrita, no solo en el color del punto: en la tabla aparecen
  // productos con el mismo nombre en dos cadenas distintas y el punto obliga a
  // recordar el código de colores para distinguirlos.
  [['', 'tilde'], ['Cadena', 'cad'], ['Producto', ''], ['Hoy', 'num'],
    ['Mediana', 'num'], ['Mínimo', 'num'], ['Máximo', 'num'],
    ['En promo', 'num'], ['Período', 'num'], ['', 'ir']]
    .forEach(([t, cl]) => cab.append(el('th', cl, t)));
  const cuerpo = el('tbody');
  const orden = [...c.filas].sort((a, b) => (b.variacion ?? 0) - (a.variacion ?? 0));
  orden.slice(0, TOPE_TABLA).forEach((f) => {
    const tr = el('tr');
    const tdTilde = el('td', 'tilde');
    const caja = el('input');
    caja.type = 'checkbox';
    caja.checked = PF.seleccion.has(f.p.id);
    caja.setAttribute('aria-label', `Comparar ${f.p.nombre}`);
    caja.addEventListener('change', () => {
      if (caja.checked) {
        if (PF.seleccion.size >= TOPE_SELECCION) { caja.checked = false; return; }
        PF.seleccion.add(f.p.id);
      } else PF.seleccion.delete(f.p.id);
      pintarPanel({ conservarFiltros: true });
    });
    tdTilde.append(caja);
    const tdCadena = el('td', 'cad');
    const i = el('i');
    i.style.background = CADENAS[f.p.cadena]?.color || 'var(--apagado)';
    tdCadena.append(i, document.createTextNode(CADENAS[f.p.cadena]?.nombre || f.p.cadena));
    const td = el('td', 'prod');
    td.append(el('span', 'tb-nombre', f.p.nombre));
    if (f.enMinimo) td.append(el('em', 'marca-min', 'mínimo'));
    tr.append(tdTilde, tdCadena, td,
      el('td', 'num', `S/ ${soles(f.hoy)}`),
      el('td', 'num', `S/ ${soles(f.mediana)}`),
      el('td', 'num', `S/ ${soles(f.min)}`),
      el('td', 'num', `S/ ${soles(f.max)}`),
      el('td', 'num', pct(f.promo)));
    const v = el('td', 'num');
    if (f.variacion != null) {
      const e = el('b', f.variacion > 0 ? 'sube' : f.variacion < 0 ? 'baja' : null,
        `${f.variacion > 0 ? '+' : ''}${(f.variacion * 100).toFixed(1)}%`);
      v.append(e);
    } else v.textContent = '—';
    tr.append(v, celdaEnlace(f.p));
    cuerpo.append(tr);
  });
  const cabecera = el('thead');
  cabecera.append(cab);
  tabla.replaceChildren(cabecera, cuerpo);

  document.getElementById('panel-tabla-titulo').textContent =
    `Productos (${c.filas.length.toLocaleString('es-PE')})`;
  const mas = document.getElementById('panel-mas');
  mas.hidden = c.filas.length <= TOPE_TABLA;
  mas.textContent = `Se muestran los ${TOPE_TABLA} de mayor variación. `
    + 'El CSV baja la lista completa.';
}

function pintarPanel({ conservarFiltros = false } = {}) {
  // Al tipear no se repintan los filtros: reconstruirlos le saca el foco al
  // campo y se pierde la palabra a medio escribir.
  if (conservarFiltros) pintarChips();
  else pintarFiltrosPanel();
  const c = calcularPanel();
  ultimoCalculo = c;
  pintarKpis(c.kpis, c.dias);
  pintarGraficos(c);
  pintarTabla(c);
}

/* ── arranque ───────────────────────────────────────────────────────────── */

function montarPanel() {
  document.querySelectorAll('.vista').forEach((b) => {
    b.addEventListener('click', () => {
      const cual = b.dataset.vista;
      document.querySelectorAll('.vista').forEach((x) => {
        const act = x === b;
        x.classList.toggle('activa', act);
        if (act) x.setAttribute('aria-current', 'page'); else x.removeAttribute('aria-current');
      });
      document.getElementById('vista-explorar').hidden = cual !== 'explorar';
      document.getElementById('vista-panel').hidden = cual !== 'panel';
      if (cual === 'panel' && !ultimoCalculo) pintarPanel();
    });
  });

  document.getElementById('pf-csv').addEventListener('click', () => {
    if (ultimoCalculo) exportarCsv(ultimoCalculo);
  });
  document.getElementById('pf-limpiar').addEventListener('click', () => {
    aplicar({});
    pintarPanel();
  });
  document.getElementById('pf-guardar').addEventListener('click', () => {
    const nombre = prompt('Nombre para esta combinación de filtros:');
    if (!nombre) return;
    const g = leerGuardadas();
    g[nombre.trim()] = instantanea();
    escribirGuardadas(g);
    pintarGuardadas();
  });
  document.getElementById('pf-guardadas').addEventListener('change', (ev) => {
    const g = leerGuardadas();
    if (g[ev.target.value]) { aplicar(g[ev.target.value]); pintarPanel(); }
  });
  pintarGuardadas();
}

// app.js carga los JSON y recién ahí hay algo que calcular.
document.addEventListener('datos-listos', montarPanel);
