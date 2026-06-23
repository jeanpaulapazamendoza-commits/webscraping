# Arquitectura propuesta: Scraping de precios sin Firecrawl

> **Contexto:** análisis de precios de 4 supermercados peruanos × 3 categorías cada uno (carne de res, pollo, frutas), ejecutado 2 veces por semana, con histórico de precios.

---

## Descubrimiento clave

**Tottus está construido con Next.js**, lo que confirmamos en tu propio HTML extraído (`tottus_raw.html`): contiene un `<script id="__NEXT_DATA__">` con **675,899 caracteres de JSON** que incluye absolutamente todos los datos de la página — productos, precios, paginación, todo.

Eso significa que **no se necesita JavaScript rendering** ni Firecrawl: con un `requests.get()` plano sobre la URL de la categoría, se obtienen los datos crudos.

### La estructura de precios es semántica y limpia

Donde Firecrawl te daba strings como `"S/ 23.90"` que tenías que parsear con regex, el JSON nativo viene así:

```json
"prices": [
  {"type": "cmrPrice",      "price": ["23.90"], "icons": "cmr-icon"},
  {"type": "internetPrice", "price": ["24.90"], "icons": ""},
  {"type": "normalPrice",   "price": ["25.50"], "crossed": true}
]
```

Los tipos vienen etiquetados: `cmrPrice` (descuento CMR), `internetPrice` (precio online), `normalPrice` (precio original tachado). Cero ambigüedad.

### Mapa de stacks por supermercado

| Super | Grupo | Stack | Endpoint nativo |
|-------|-------|-------|-----------------|
| **Tottus** | Falabella | Next.js | `__NEXT_DATA__` en HTML + `/_next/data/{buildId}/...json` para paginación |
| **Wong** | Cencosud | VTEX | `/api/catalog_system/pub/products/search/?fq=C:{id}&_from=0&_to=49` |
| **Metro** | Cencosud | VTEX | Mismo endpoint VTEX |
| **Plaza Vea** | SPSA | Por confirmar (inspeccionar Network) | Probablemente VTEX o custom |

Los tres stacks tienen APIs nativas accesibles sin Firecrawl. La única incógnita es Plaza Vea — hay que inspeccionar manualmente.

---

## Tradeoff: ¿qué se gana, qué se pierde?

| Aspecto | Firecrawl | Approach nativo |
|---------|-----------|-----------------|
| **Costo** | $19+/mes (~750 páginas) | **$0** |
| **Velocidad** | 5–10s por página (renderiza JS) | ~0.5s por página |
| **Confiabilidad** | Depende de selectores CSS | Depende de schema de la API |
| **Riqueza de datos** | Solo lo que el schema captura | Todo (marca, vendedor, stock, badges, sponsor, etc.) |
| **Mantenimiento** | Bajo (Firecrawl maneja anti-bot) | Medio (si cambia el JSON shape, hay que ajustar) |
| **Bloqueos** | Firecrawl rota IPs | Tu IP residencial — riesgo si scrapeas muy seguido |

**El riesgo principal del approach nativo** es que Tottus/VTEX cambien el shape del JSON o agreguen anti-bot. Mitigación: el script falla con error claro, y siempre podés volver a Firecrawl puntualmente si pasa.

**Recomendación:** approach nativo como primario, Firecrawl como fallback (instalado pero no usado a menos que la API rompa).

---

## Arquitectura propuesta

```
supermercados-precios/
├── extractors/
│   ├── base.py              # Clase abstracta Extractor
│   ├── tottus.py            # Next.js
│   ├── vtex.py              # Wong + Metro (mismo código)
│   └── plazavea.py          # Por definir
├── procesar/
│   ├── normalizar.py        # Schema único: supermercado, categoria, nombre, precios, fecha
│   └── exportar_sheets.py   # Sheets con histórico
├── config/
│   └── targets.yaml         # 4 super × 3 categorías = 12 URLs
├── .tmp/                    # Outputs crudos por corrida
└── run.py                   # Orquestador
```

### Schema unificado de salida

Todos los extractores devuelven la misma estructura para que el resto del pipeline no sepa de qué supermercado vino:

```python
{
    "supermercado":     "tottus" | "wong" | "metro" | "plazavea",
    "categoria":        "carne-de-res" | "pollo" | "frutas",
    "nombre":           str,
    "producto_id":      str,    # ID interno del super
    "marca":            str,
    "url":              str,
    "imagen":           str,
    "vendedor":         str,    # importante para detectar marketplace
    "precio_descuento": float,
    "precio_regular":   float,
    "precio_normal":    float | None,
    "tiene_descuento":  bool,
    "descuento_pct":    float | None,
    "fecha_extraccion": ISO timestamp,
}
```

### Google Sheets con histórico

Dos pestañas por supermercado/categoría — o mejor: dos pestañas globales:

- **`snapshot_actual`**: se sobreescribe cada corrida. Es la vista "hoy".
- **`historial`**: append-only. Cada producto agrega una fila por corrida. Ideal para gráficos de evolución de precio.

El histórico crece ~12 categorías × ~80 productos × 2 corridas/semana = **~1,920 filas/semana = ~100k filas/año**. Google Sheets aguanta hasta 10M celdas (~900k filas con 10 columnas). Holgura sobrada para 5–7 años.

---

## Paso a paso recomendado

### 1. Validar el POC de Tottus en tu PC (15 min)

```bash
pip install requests
python tottus_extractor.py "https://www.tottus.com.pe/tottus-pe/lista/CATG16918/Carne-de-Res"
```

Esperás ver:
- "Productos totales: 86" (no 69 como antes)
- Output en `.tmp/tottus_carne-de-res_YYYYMMDD_HHMM.json`

Si falla con 403, probás:
1. Subir el `delay` a 3-5 segundos
2. Hacer la primera request manualmente con curl y guardar las cookies
3. Como último recurso: Playwright local (browser headless con tu IP)

### 2. Inspeccionar las APIs de Wong/Metro/Plaza Vea (30 min)

Abrí cada categoría en Chrome, F12 → Network → filtrá por XHR/Fetch. Buscás llamadas que devuelvan JSON con productos. Para VTEX deberías ver llamadas a `/api/catalog_system/pub/products/search`. Me mandás un screenshot o el cURL copiado y armamos el extractor.

### 3. Generalizar a 4 supermercados (1-2 horas con mi ayuda)

Cada extractor implementa la misma interfaz `extraer(url) -> list[dict_normalizado]`. El orquestador no distingue.

### 4. Implementar histórico en Sheets (30 min)

Modificar tu `procesar_precios_tottus.py` para hacer `ws.append_rows()` en lugar de `ws.clear()` en la pestaña histórico.

### 5. Programar con cron / GitHub Actions (15 min)

2 veces por semana: martes y viernes 7am, por ejemplo. Si lo hacés con GitHub Actions tenés logs gratis y no necesitás dejar tu PC prendida.

---

## Costo total estimado

| Item | Firecrawl | Approach nativo |
|------|-----------|-----------------|
| Suscripción mensual | $19–49 | **$0** |
| Google Sheets API | $0 | $0 |
| GitHub Actions (si se usa) | $0 (cuota gratuita) | $0 |
| **Total/mes** | **$19–49** | **$0** |
| **Ahorro anual** | — | **$228–588** |

---

## Próximo paso concreto

**Correr el POC `tottus_extractor.py` localmente** y reportar:
1. ¿Sale 200 o 403?
2. ¿Cuántos productos extrajo?
3. ¿El JSON resultante se ve completo?

Con eso confirmamos el approach y avanzamos a Wong/Metro.
