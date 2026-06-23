"""
Extractor de precios Tottus — SIN Firecrawl.

Usa el JSON embebido en <script id="__NEXT_DATA__"> que Next.js incluye
en cada página, y el endpoint paralelo /_next/data/{buildId}/...json
para las páginas siguientes.

Uso:
    python tottus_extractor.py "https://www.tottus.com.pe/tottus-pe/lista/CATG16918/Carne-de-Res"

Salida:
    .tmp/tottus_<categoria>_<fecha>.json   (datos crudos normalizados)
"""

import re
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


# ── Extracción ──────────────────────────────────────────────────────────────

def fetch_next_data(url: str, session: requests.Session, page: int = 1) -> dict:
    """Carga la página (con ?page=N si page>1) y extrae __NEXT_DATA__."""
    if page > 1:
        sep = "&" if "?" in url else "?"
        fetch_url = f"{url}{sep}page={page}"
    else:
        fetch_url = url
    r = session.get(fetch_url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    # Detectar si nos redirigieron a /notFound
    if "/notFound" in r.url:
        raise RuntimeError(f"Página {page} no existe (redirect a notFound)")
    m = NEXT_DATA_RE.search(r.text)
    if not m:
        raise RuntimeError(f"__NEXT_DATA__ no encontrado en página {page}")
    return json.loads(m.group(1))


# ── Normalización ───────────────────────────────────────────────────────────

def precio_por_tipo(prices: list, tipo: str) -> float | None:
    for p in prices:
        if p.get("type") == tipo and p.get("price"):
            try:
                return float(p["price"][0].replace(",", "."))
            except (ValueError, IndexError):
                pass
    return None


def normalizar_producto(p: dict, categoria: str, supermercado: str) -> dict:
    prices = p.get("prices", [])
    p_cmr   = precio_por_tipo(prices, "cmrPrice")       # con tarjeta CMR
    p_inet  = precio_por_tipo(prices, "internetPrice")  # precio online sin tarjeta
    p_norm  = precio_por_tipo(prices, "normalPrice")    # precio original (tachado)

    # El precio "descuento" es el CMR si existe, si no el internet, si no el normal
    p_desc = p_cmr or p_inet or p_norm
    p_reg  = p_inet if p_cmr else (p_norm if p_inet else None)

    # Si hay precio CMR, calcular el porcentaje de descuento de la tarjeta
    # vs el precio internet (precio sin tarjeta)
    tarjeta_pct = None
    if p_cmr and p_inet and p_inet > p_cmr:
        tarjeta_pct = round((1 - p_cmr / p_inet) * 100, 1)

    return {
        "supermercado": supermercado,
        "categoria":    categoria,
        "nombre":       p.get("displayName", "").strip(),
        "producto_id":  str(p.get("productId", "")),
        "sku_id":       str(p.get("skuId", "")),
        "marca":        p.get("brand", ""),
        "url":          p.get("url", ""),
        "imagen":       (p.get("mediaUrls") or [None])[0],
        "vendedor":     p.get("sellerName", ""),
        # Precio con tarjeta del supermercado (Tottus = CMR)
        "precio_tarjeta":         p_cmr,
        "nombre_tarjeta":         "CMR" if p_cmr else None,
        "tarjeta_descuento_pct":  tarjeta_pct,
        "precio_internet":        p_inet,
        "precio_normal":          p_norm,
        "precio_descuento":       p_desc,
        "precio_regular":         p_reg,
        "tiene_descuento":        bool(p_reg and p_desc and p_desc < p_reg),
        "descuento_pct":          round((1 - p_desc / p_reg) * 100, 1)
                                  if p_reg and p_desc and p_reg > 0 else None,
        "fecha_extraccion":       datetime.now().isoformat(timespec="seconds"),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def slug_path_from_url(url: str) -> str:
    # https://www.tottus.com.pe/tottus-pe/lista/CATG16918/Carne-de-Res
    # → "CATG16918/Carne-de-Res"
    parts = urlparse(url).path.strip("/").split("/")
    # parts = ['tottus-pe', 'lista', 'CATG16918', 'Carne-de-Res']
    idx = parts.index("lista")
    return "/".join(parts[idx + 1:])


def categoria_from_url(url: str) -> str:
    return slug_path_from_url(url).split("/")[-1].lower()


def extraer(url: str, supermercado: str = "tottus", delay: float = 1.5) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"[1] Cargando página 1: {url}")
    next_data = fetch_next_data(url, session, page=1)
    pp        = next_data["props"]["pageProps"]
    pagination = pp["pagination"]
    total      = pagination["count"]
    per_page   = pagination["perPage"]
    total_paginas = (total + per_page - 1) // per_page

    print(f"    Total productos: {total}  ({total_paginas} páginas de {per_page})")

    categoria = categoria_from_url(url)
    todos = [normalizar_producto(p, categoria, supermercado) for p in pp["results"]]
    print(f"    Página 1: +{len(pp['results'])} productos")

    # Páginas siguientes: misma URL HTML + ?page=N
    for page in range(2, total_paginas + 1):
        time.sleep(delay)
        print(f"[{page}] Cargando página {page}...")
        try:
            data = fetch_next_data(url, session, page=page)
            results = data["props"]["pageProps"]["results"]
            todos.extend(normalizar_producto(p, categoria, supermercado) for p in results)
            print(f"    Página {page}: +{len(results)} productos")
        except Exception as e:
            print(f"    ⚠ Error en página {page}: {e}")
            break

    return todos


def guardar(productos: list[dict], categoria: str, supermercado: str) -> Path:
    out_dir = Path(".tmp")
    out_dir.mkdir(exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"{supermercado}_{categoria}_{fecha}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(productos, f, ensure_ascii=False, indent=2)
    return out


# Schema canónico del histórico — orden importa para append a CSV existente
CAMPOS_HISTORICO = [
    "supermercado", "categoria", "nombre", "producto_id", "sku_id",
    "marca", "url", "imagen", "vendedor",
    "precio_cmr", "precio_internet", "precio_normal",
    "precio_descuento", "precio_regular",
    "tiene_descuento", "descuento_pct", "fecha_extraccion",
]


def append_a_historico(productos: list[dict]) -> tuple[Path, bool]:
    """Append-only al CSV maestro. Crea data/ y el archivo si no existen."""
    import csv
    historico = Path("data") / "historico.csv"
    historico.parent.mkdir(exist_ok=True)
    es_nuevo = not historico.exists()
    with open(historico, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        if es_nuevo:
            w.writeheader()
        w.writerows(productos)
    return historico, es_nuevo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL de la categoría en Tottus")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Segundos entre requests (default: 1.5)")
    parser.add_argument("--no-historico", action="store_true",
                        help="No agregar al CSV histórico (solo guardar JSON)")
    args = parser.parse_args()

    productos = extraer(args.url, delay=args.delay)
    out = guardar(productos, categoria_from_url(args.url), "tottus")

    con_desc = sum(1 for p in productos if p["tiene_descuento"])
    precios  = [p["precio_descuento"] for p in productos if p["precio_descuento"]]

    print("\n=== Resumen ===")
    print(f"  Productos totales: {len(productos)}")
    print(f"  Con descuento:     {con_desc}")
    if precios:
        print(f"  Precio mín:  S/ {min(precios):.2f}")
        print(f"  Precio máx:  S/ {max(precios):.2f}")
        print(f"  Precio prom: S/ {sum(precios)/len(precios):.2f}")
    print(f"\n  Snapshot JSON: {out}")

    if not args.no_historico:
        hist_path, es_nuevo = append_a_historico(productos)
        accion = "creado" if es_nuevo else "actualizado"
        print(f"  Histórico CSV {accion}: {hist_path}  (+{len(productos)} filas)")


if __name__ == "__main__":
    main()


# Ejemplo de uso:actualiazdo v2

