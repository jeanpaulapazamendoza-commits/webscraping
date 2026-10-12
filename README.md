# Precios Supermercados Perú

[![Scraping diario](https://github.com/jeanpaulapazamendoza-commits/webscraping/actions/workflows/scrape.yml/badge.svg)](https://github.com/jeanpaulapazamendoza-commits/webscraping/actions/workflows/scrape.yml)
[![Estado de los datos](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjeanpaulapazamendoza-commits%2Fwebscraping%2Fmain%2Fdata%2F_badge.json)](data/_salud.json)

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
- **Storage**: `data/historico_YYYY-MM.parquet`, versionado en git y particionado por
  mes (vía `historico_io.py`), más réplica completa en Google Sheets
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
├── red.py                       # Timeouts y reintentos compartidos
├── historico_io.py              # Carga/escritura del histórico particionado por mes
├── salud.py                     # Estado de la captura: atraso, huecos, badge
├── scrape_all.py                # Orquestador: corre todos los targets y exporta
├── export_sheets.py             # Exporta Histórico/Snapshot/Resumen a Google Sheets
├── normalizar.py                # Limpieza y normalización compartida
├── build_excel.py               # (Opcional, manual) XLSX local para Power BI
├── app.py                       # Dashboard Streamlit
├── data/
│   ├── historico_YYYY-MM.parquet # Una partición por mes
│   ├── _pendiente.csv            # Lo que la corrida en curso no consolidó (gitignored)
│   ├── _salud.json               # Estado de la captura; _badge.json para el badge
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

## Dónde vive el histórico

Parquet, una partición por mes. Medido sobre los 446.220 registros que había al
migrar: **138 MB de CSV son 5,5 MB de Parquet**, el 4%. Lo que más pesaba eran
las columnas de baja cardinalidad —supermercado, categoría, nombre, marca: las
mismas pocas cadenas repetidas cientos de miles de veces— y Parquet guarda cada
valor una vez y un entero por fila.

Como el histórico se versiona en git, lo que importa no es solo el tamaño del
árbol sino cuánto crece el repo. También medido, con 20 días de commits: el CSV
que crece por append deja **216 KB por día** en `.git`, el Parquet reescrito
entero **170 KB**. Parquet gana en los dos lados.

Se guarda todo como texto, igual que cuando era CSV. Tipar las columnas no
ahorra nada (5,5 MB en los dos casos: el diccionario de Parquet comprime "6.50"
tan bien como un float) y en cambio obligaría a revisar cada consumidor, que hoy
parsea desde texto y distingue el vacío —"no se midió"— del cero.

El precio de Parquet es que no se puede ampliar: agregar una fila es reescribir
el archivo. Como `append_historico()` se llama una vez por target, 74 veces por
corrida, reescribir la partición costaba 56 segundos y subiendo. Así que la
corrida agrega a `data/_pendiente.csv`, donde agregar al final sale gratis, y al
terminar `consolidar()` lo pliega en las particiones de una sola vez. El
pendiente también es la red de seguridad: si la corrida se cae, queda en disco y
la próxima lo recupera.

```bash
python tools/migrar_a_parquet.py --aplicar   # la conversión, ya corrida
```

## Cuando una extracción falla

Medido sobre el histórico: **137 extracciones perdidas en 121 días**, en días
que por lo demás corrieron bien. Son ~1,5% del total y están repartidas entre
las cuatro cadenas, que es el perfil de una falla transitoria y no de una
categoría rota. Hasta ahora cada extractor usaba una `requests.Session()`
pelada, así que un 500 de paso perdía la categoría entera de ese día.

Hay dos capas de reintento, y hacen cosas distintas:

1. **En el transporte** (`red.py`): urllib3 repite la *misma* petición ante un
   fallo de red, un 429 o un 5xx —incluidos los 520/522/524 del CDN—, con
   espera creciente de 1, 2 y 4 segundos. Un 404 no se reintenta: si la ruta no
   existe, repetirla no la va a crear.
2. **Por target** (`scrape_all.py`): lo que sobrevive a la capa de abajo —una
   caída a mitad de la paginación, una respuesta que no era el JSON esperado—
   se vuelve a correr desde la página 1, hasta 3 intentos. Volver a correr es
   seguro porque la extracción solo lee, y el deduplicado por `producto_id` se
   encarga de lo que llegue dos veces.

Los targets que salieron bien **recién al segundo intento** se informan en el
resumen y quedan en `data/_salud.json`: son la señal temprana, porque el que
hoy necesita dos intentos es el que mañana falla.

### Nada de resultados parciales

Lo que **no** se hace es guardar lo que se alcanzó a leer. Si falla la página 5
de 8, registrar las primeras 4 guardaría 48 productos donde había 78, y los 30
que faltan parecerían haber salido de catálogo: indistinguible de una baja
real. Un dato truncado es peor que uno ausente, porque el ausente se ve en el
semáforo y el truncado no.

Eso hacía el extractor de Tottus, que atrapaba el error y cortaba el bucle: la
corrida informaba éxito con la mitad de la categoría. Ahora la página que no se
pudo leer hace fallar el target, y además:

- **Tottus verifica la completitud.** La categoría declara cuántos productos
  tiene antes de paginar, así que se compara contra lo obtenido. Con margen del
  10%: el catálogo se mueve mientras recorremos las páginas y el declarado
  difiere del obtenido de a uno (medido, en 2 de las 5 categorías de más de una
  página). El margen tolera eso y sigue atrapando la pérdida de una página.
- **VTEX avisa si no entra en el techo de páginas.** Llegar a `max_paginas` con
  la última página llena significa que quedaron productos sin leer, y antes se
  cortaba en silencio. Hoy la categoría más grande usa 8 de 20 páginas
  (`wong/verduras`, 381 productos).

## Estado de la captura

```bash
python salud.py          # qué trajo la última corrida y qué días faltan
```

Escribe `data/_salud.json` (atraso, días con dato, huecos, productos por cadena)
y `data/_badge.json`, que es lo que dibuja el badge de arriba. Existe porque un
día ausente no se distinguía de uno normal: el cron podía dejar de correr y el
histórico seguía creciendo sin que nada fallara.

La distinción importante es entre **problema** y **aviso**. Problema es lo que se
le puede reclamar a la corrida y todavía se puede arreglar —el dato está viejo,
una cadena no trajo nada, el total se cayó más del 30% contra el día anterior— y
hace que `scrape_all.py` **salga con error**. Aviso es lo que hay que ver pero ya
no se puede arreglar: los días que faltan. Los precios de un día perdido no están
en ningún lado, así que marcarlos en rojo dejaría el semáforo trabado una semana
sin que nadie pueda hacer nada.

Y una limitación que conviene tener clara: `_badge.json` lo escribe la corrida,
así que **si la corrida no pasa, nadie lo reescribe y se queda en verde**. El
badge que detecta "dejó de correr" es el primero, el de GitHub Actions, porque lo
calcula GitHub. Para que sirva, la corrida tiene que fallar cuando el resultado
es malo, y por eso sale con error. El dato se guarda y se commitea igual: el
código de salida dictamina, no decide si se escribe.

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
