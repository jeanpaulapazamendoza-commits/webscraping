# Precios Supermercados Perú

Pipeline automatizado que scrapea precios de 4 supermercados peruanos (Tottus, Wong,
Metro, Plaza Vea) en 77 categorías, todos los días, y los muestra en un dashboard web
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
│       └── scrape.yml          # Cron diario + workflow_dispatch
├── targets.yaml                 # 77 combinaciones supermercado × categoría
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
python scrape_all.py                      # las 77 categorías
python scrape_all.py --super tottus        # solo un supermercado
python scrape_all.py --familia res         # solo una familia
python scrape_all.py --dry-run             # solo lista qué haría, sin ejecutar
python scrape_all.py --no-sheets           # sin exportar a Google Sheets
python scrape_all.py --excel               # además generar el XLSX local
```

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
