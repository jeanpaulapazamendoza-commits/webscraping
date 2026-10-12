# Precios Supermercados Perú

Pipeline automatizado que scrapea los precios de carnes y frutas/verduras en 4
supermercados peruanos (Tottus, Wong, Metro, Plaza Vea) —unos 4.000 productos por día
repartidos en 223 categorías—, todos los días, y los muestra en un dashboard web
(Streamlit) con histórico de evolución. El histórico completo también se replica en
un Google Sheet en la nube.

## Stack

- **Scraping**: Python + `requests` (sin Firecrawl — usa los endpoints JSON nativos
  de cada plataforma: Tottus vía `__NEXT_DATA__` de Next.js, Wong/Metro/Plaza Vea vía
  la API REST de VTEX)
- **Schedule**: GitHub Actions (cron diario + disparo manual)
- **Storage**: `data/historico_YYYY-MM.csv`, versionado en git y particionado por mes
  (vía `historico_io.py`), más réplica completa en Google Sheets
- **Dashboard**: Streamlit, hosteado gratis en Streamlit Community Cloud

## Estructura del repo

```
.
├── .github/
│   └── workflows/
│       ├── scrape.yml          # Cron diario + workflow_dispatch
│       └── descubrir.yml       # Cron semanal: vuelve a resolver el lock
├── categorias.yaml              # QUÉ vigilar: departamentos y reglas de familia
├── descubrir.py                 # Resuelve categorias.yaml contra cada catálogo
├── targets.lock.yaml            # Generado: las 74 rutas concretas de hoy
├── targets.yaml                 # La lista a mano anterior; respaldo del lock
├── tottus_extractor.py          # Next.js __NEXT_DATA__
├── vtex_extractor.py            # Wong, Metro y Plaza Vea (API VTEX)
├── historico_io.py              # Carga/escritura del histórico particionado por mes
├── scrape_all.py                # Orquestador: corre todos los targets y exporta
├── export_sheets.py             # Exporta Histórico/Snapshot/Resumen a Google Sheets
├── normalizar.py                # Limpieza y normalización compartida
├── build_excel.py               # (Opcional, manual) XLSX local para Power BI
├── app.py                       # Dashboard Streamlit
├── data/
│   ├── historico_YYYY-MM.csv    # Append-only, una partición por mes
│   └── snapshots/                # Backup JSON por corrida (gitignored)
└── requirements.txt
```

## Cómo correrlo

```bash
python scrape_all.py                      # los 74 targets del lock
python scrape_all.py --super tottus        # solo un supermercado
python scrape_all.py --familia res         # solo una familia
python scrape_all.py --dry-run             # solo lista qué haría, sin ejecutar
python scrape_all.py --no-sheets           # sin exportar a Google Sheets
python scrape_all.py --excel               # además generar el XLSX local
```

## Qué se vigila

Las URLs de las categorías no se escriben a mano. `categorias.yaml` declara los
departamentos a seguir y las reglas para asignar familia; `descubrir.py` las
resuelve contra el árbol de categorías que publica cada cadena y deja las rutas
concretas en `targets.lock.yaml`, que es lo que lee la corrida diaria.

```bash
python descubrir.py              # muestra qué cambiaría, sin escribir
python descubrir.py --aplicar    # reescribe targets.lock.yaml
```

Agregar una categoría nueva es agregar un departamento o un término a
`categorias.yaml`, no buscar una URL y pegarla. Una vez por semana el workflow
`descubrir.yml` vuelve a resolverlo solo, porque los menús cambian: aparecen
subcategorías y los CATG de Tottus se mueven de rama.

Las dos cadenas se piden distinto, y la diferencia es medida:

- **VTEX** (Wong, Metro, Plaza Vea) se pide por nivel. La búsqueda por ruta ya
  devuelve todo lo que cuelga debajo, así que pedir nivel 2 cubre el
  departamento entero con ~7 peticiones en vez de ~300, y la categoría de cada
  producto se deriva de su propia ruta, más fina que el target que lo trajo.
- **Tottus** se pide completo, a todos los niveles, porque cada nodo lista
  *solo* los productos asignados a él. Medido sobre "Frutas y Verduras": nivel 2
  son 177 productos, nivel 3 son 112, y comparten apenas 15. "Frutas Cítricas"
  tiene 7 productos y su hija "Fresas y Arándanos" 12, ninguno de los cuales
  aparece en la madre. Quedarse con un nivel perdía el otro.

Como los nodos de Tottus se superponen un poco, `scrape_all.py` descarta por
`producto_id` lo que ya vio en la corrida y conserva la primera aparición; el
lock ordena los nodos finos primero para que la categoría que quede sea la más
específica. Contra la lista a mano anterior: 4.002 productos por día en vez de
3.647 (+355, ninguno perdido) y 223 categorías distintas en vez de 185.

## La web (Vercel)

`web/` es un sitio estático sin build ni dependencias: tres archivos (HTML, CSS
y JS) que leen los JSON que deja la corrida diaria en `web/datos/`. No hay
backend, ni base de datos, ni funciones de servidor — el navegador baja 291 KB
comprimidos y con eso tiene búsqueda, comparación entre cadenas y el evolutivo
completo.

Para verla en local:

```bash
cd web && python -m http.server 8777
```

Para desplegarla en Vercel: importar el repo y poner **Root Directory = `web`**.
Framework Preset queda en *Other*; no hay comando de build. Cada push del bot
con el dato del día redespliega solo.

Los JSON los genera `construir_json.py`, que `scrape_all.py` llama al final de
cada corrida:

```bash
python construir_json.py            # regenerar a mano
python scrape_all.py --no-json      # saltearlo en una corrida
```

## Tests

Pruebas de humo del dashboard: corren `app.py` entera con el harness oficial de
Streamlit, sin levantar servidor, contra el histórico real del repo.

```bash
python tests/test_dashboard.py      # dashboard, sin instalar nada
python tests/test_extractor.py      # extractor VTEX, sin red
python -m pytest tests/             # las dos, si tenés pytest
```

`test_dashboard.py` cubre los casos que ya rompieron la app: filtros que no
dejan ninguna fila, que el snapshot sea del último día y no de la última vez
que se vio cada producto, y que el contador del encabezado cuente días y no
timestamps.

`test_extractor.py` cubre el precio con Tarjeta Oh! de Plaza Vea, que VTEX
expresa como un monto en soles sobre la unidad de venta y no como un
porcentaje — leerlo mal producía precios negativos.

## Setup

### 1. Google Sheets (entregable en la nube)

1. [Google Cloud Console](https://console.cloud.google.com/) → crear o elegir un
   proyecto → habilitar la **Google Sheets API**.
2. IAM & Admin → Service Accounts → crear una → generar una key en formato JSON y
   descargarla.
3. Crear un Google Sheet (o usar uno existente) y compartirlo como **Editor** con el
   `client_email` que aparece dentro del JSON.
4. Copiar el ID del Sheet (de la URL: `.../d/<ID>/edit`).
5. **Local**: guardar el JSON como `service_account.json` en la raíz del repo
   (gitignored) y definir la variable de entorno `GOOGLE_SHEET_ID`.
6. **CI**: en GitHub → Settings → Secrets and variables → Actions, crear los secrets
   `GOOGLE_CREDS_JSON` (contenido completo del JSON) y `GOOGLE_SHEET_ID`.

Si no hay credenciales configuradas, `scrape_all.py` sigue corriendo igual: solo
imprime un warning y omite el push a Sheets.

### 2. GitHub Actions (cron diario)

Ya está en `.github/workflows/scrape.yml`, corre todos los días a las 07:00 Lima
(`12:00 UTC`). También se puede disparar a mano desde la pestaña **Actions** →
"Scraping de precios (diario)" → **Run workflow**.

### 3. Deploy del dashboard (Streamlit Community Cloud)

1. https://share.streamlit.io → "New app"
2. Conectar la cuenta de GitHub y seleccionar este repo
3. Entry point: `app.py`
4. Deploy → da una URL pública tipo `https://<nombre>.streamlit.app`

## El histórico: CSV + Google Sheets

- **CSV particionado por mes** (`data/historico_YYYY-MM.csv`, vía `historico_io.py`)
  es la fuente de verdad que lee el dashboard y queda versionada en git. Se particiona
  por mes para no superar el límite de 100MB por archivo de GitHub (a 77
  categorías/día, cada partición mensual pesa ~37MB).
- **Google Sheets** tiene 3 pestañas, actualizadas en cada corrida:
  - **Histórico**: append-only, día a día, sin rotación automática. Google Sheets
    tiene un límite de 10M celdas — al ritmo actual se alcanzaría en ~4.3 meses.
    Cuando se llene hay que archivar el Sheet a mano y crear uno nuevo (apuntando
    `GOOGLE_SHEET_ID` al nuevo). El pipeline detecta el error de límite, lo loguea y
    sigue sin romper la corrida.
  - **Snapshot Actual**: último precio por producto (se sobreescribe cada corrida).
  - **Resumen Competitivo**: agregación por supermercado × categoría (se sobreescribe
    cada corrida).

## Mantenimiento

- **Si un extractor falla**: revisar `data/_run_log.txt` o el log de Actions. Lo más
  común es que el sitio cambió el schema del JSON.
- **Si el Sheet "Histórico" se llena** (10M celdas): crear un Sheet nuevo, actualizar
  el secret/variable `GOOGLE_SHEET_ID`, y archivar el viejo.
- `build_excel.py` queda disponible como utilidad manual opcional
  (`python build_excel.py` o `python scrape_all.py --excel`) para generar un XLSX
  local con formato (Power BI / Plaza Vea).

## Costos

| Item | Costo |
|------|-------|
| GitHub Actions (repo público) | $0 |
| Google Sheets API | $0 |
| Streamlit Community Cloud | $0 |
| **Total** | **$0** |
