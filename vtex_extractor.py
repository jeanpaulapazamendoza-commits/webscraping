"""
Extractor de precios para sitios VTEX (Wong, Metro, Plaza Vea).

Usa el endpoint de búsqueda por path de VTEX:
    /api/catalog_system/pub/products/search/<path>?_from=X&_to=Y

Este endpoint incluye automáticamente productos de todas las subcategorías,
así que sirve tanto para categorías padre como para hojas.

VTEX devuelve hasta 50 productos por request. Paginamos incrementando _from/_to
hasta que la respuesta venga vacía.

Uso:
    python vtex_extractor.py "URL" --super wong     --categoria carne-de-res
    python vtex_extractor.py "URL" --super metro    --categoria pollo
    python vtex_extractor.py "URL" --super plazavea --categoria frutas

URLs validadas:
    https://www.wong.pe/carnes-aves-y-pescados/res-y-otras-carnes
    https://www.metro.pe/carnes-aves-y-pescados/res-y-otras-carnes
    https://www.plazavea.com.pe/carnes-aves-y-pescados/res
"""

import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

PAGE_SIZE = 50  # límite de VTEX por request


# ── Parseo de URL ───────────────────────────────────────────────────────────

def host_from_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def path_from_url(url: str) -> str:
    """Devuelve el path de la URL sin slashes a los costados."""
    return urlparse(url).path.strip("/")


def resolver_category_id(host: str, path: str,
                          session: requests.Session) -> str:
    """
    Usa el endpoint VTEX 'pagetype' para mapear path → ID de categoría.
    Es una llamada que el propio sitio hace internamente para resolver rutas.
    """
    endpoint = f"{host}/api/catalog_system/pub/portal/pagetype/{path}"
    r = session.get(endpoint, timeout=30)
    r.raise_for_status()
    data = r.json()
    # La respuesta tiene la forma:
    # {"id": "12345", "pageType": "Category", "name": "...", ...}
    if data.get("pageType") not in ("Category", "Department", "SubCategory"):
        raise RuntimeError(
            f"El path '{path}' no es una categoría según VTEX.\n"
            f"  pageType recibido: {data.get('pageType')}\n"
            f"  Verificá que la URL apunte a una página de listado de productos."
        )
    cat_id = str(data.get("id") or "")
    if not cat_id:
        raise RuntimeError(f"VTEX devolvió pageType={data.get('pageType')} pero sin id")
    return cat_id


def extraer_id_o_resolver(url: str, session: requests.Session) -> tuple[str, str]:
    """Devuelve (host, category_id). Si la URL termina en -NNNNN usa ese ID
    directamente; si no, hace lookup contra el endpoint pagetype."""
    host = host_from_url(url)
    path = path_from_url(url)

    # Intento 1: ID embebido al final del path (estilo legacy)
    last = path.split("/")[-1]
    m = re.search(r"-(\d+)$", last)
    if m:
        return host, m.group(1)

    # Intento 2: query string ?cat=N
    qs = parse_qs(urlparse(url).query)
    if "cat" in qs:
        return host, qs["cat"][0]

    # Intento 3: resolver vía pagetype (el caso de wong.pe/metro.pe/plazavea.com.pe modernos)
    print(f"  Resolviendo ID de categoría via pagetype API...")
    cat_id = resolver_category_id(host, path, session)
    return host, cat_id


# ── Fetch ───────────────────────────────────────────────────────────────────

def fetch_pagina(host: str, path: str, page: int,
                 session: requests.Session) -> list[dict]:
    """Devuelve hasta 50 productos de una página usando búsqueda por path.

    Este endpoint es el que VTEX usa internamente para resolver URLs amigables,
    e incluye automáticamente los productos de todas las subcategorías.
    """
    _from = page * PAGE_SIZE
    _to   = _from + PAGE_SIZE - 1
    url = (f"{host}/api/catalog_system/pub/products/search/"
           f"{path}?_from={_from}&_to={_to}")
    r = session.get(url, timeout=30)
    # VTEX: 200 = última página, 206 = hay más
    if r.status_code not in (200, 206):
        r.raise_for_status()
    try:
        data = r.json()
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Respuesta no es JSON (status {r.status_code}). Primeros 200 chars:\n"
            f"{r.text[:200]}"
        )
    if not isinstance(data, list):
        raise RuntimeError(f"Esperaba lista de productos, recibí: {type(data).__name__}")
    return data


# ── Detección de tarjeta (Plaza Vea Oh!) ────────────────────────────────────

def detectar_tarjeta_oh(teasers: list, promo_teasers: list) -> tuple[str | None, float | None]:
    """Busca promos del ecosistema Oh! (Plaza Vea) en los teasers de VTEX.

    Devuelve (nombre_tarjeta, descuento_pct) o (None, None) si no hay.

    Heurística: cualquier teaser cuyo Name contenga "oh" (case-insensitive)
    se considera promoción Oh!. Si hay varias, se queda con la del descuento
    más alto.

    El descuento se lee del parámetro PromotionalPriceTableItemsDiscount
    (es una fracción 0-1, ej. 0.156 = 15.6%).
    """
    candidatos = (promo_teasers or []) + (teasers or [])
    mejor_pct = None
    for t in candidatos:
        # Nombre puede venir como "Name" o "<Name>k__BackingField"
        name = t.get("Name") or t.get("<Name>k__BackingField") or ""
        if "oh" not in name.lower():
            continue

        # Buscar el parámetro de descuento dentro de Effects.Parameters
        effects = t.get("Effects") or t.get("<Effects>k__BackingField") or {}
        params  = effects.get("Parameters") or effects.get("<Parameters>k__BackingField") or []
        for param in params:
            p_name = param.get("Name") or param.get("<Name>k__BackingField") or ""
            if p_name == "PromotionalPriceTableItemsDiscount":
                p_val = param.get("Value") or param.get("<Value>k__BackingField")
                try:
                    pct = float(p_val) * 100
                    if mejor_pct is None or pct > mejor_pct:
                        mejor_pct = pct
                except (TypeError, ValueError):
                    pass
                break

    if mejor_pct is None:
        return None, None
    return "Tarjeta Oh!", round(mejor_pct, 1)


# ── Normalización ───────────────────────────────────────────────────────────

def normalizar_producto(p: dict, supermercado: str, categoria: str,
                         host: str) -> dict | None:
    """Adapta un producto VTEX al schema unificado del histórico."""
    items = p.get("items") or []
    if not items:
        return None

    item = items[0]  # primer SKU del producto
    sellers = item.get("sellers") or []
    if not sellers:
        return None
    # Preferir seller default si existe
    seller = next((s for s in sellers if s.get("sellerDefault")), sellers[0])
    offer  = seller.get("commertialOffer") or {}

    price      = offer.get("Price")
    list_price = offer.get("ListPrice")

    # Heurística VTEX: si ListPrice > Price hay descuento online (sin tarjeta)
    tiene_desc = bool(list_price and price and float(list_price) > float(price))
    pct = round((1 - price / list_price) * 100, 1) if tiene_desc else None

    # ── Detección de tarjeta (solo Plaza Vea hoy) ─────────────────────────
    nombre_tarjeta = None
    tarjeta_pct    = None
    precio_tarjeta = None
    if supermercado == "plazavea":
        teasers       = offer.get("Teasers") or []
        promo_teasers = offer.get("PromotionTeasers") or []
        nombre_tarjeta, tarjeta_pct = detectar_tarjeta_oh(teasers, promo_teasers)
        if tarjeta_pct is not None and price:
            # Aproximación: aplicamos el descuento de la promo Oh! sobre el
            # precio internet. El precio real puede diferir un poco porque VTEX
            # usa una tabla de precios promocional que no expone públicamente.
            precio_tarjeta = round(price * (1 - tarjeta_pct / 100), 2)

    link_text = p.get("linkText") or ""
    url_producto = f"{host}/{link_text}/p" if link_text else ""

    img_url = ""
    images = item.get("images") or []
    if images:
        img_url = images[0].get("imageUrl", "")

    return {
        "supermercado": supermercado,
        "categoria":    categoria,
        "nombre":       (p.get("productName") or "").strip(),
        "producto_id":  str(p.get("productId") or ""),
        "sku_id":       str(item.get("itemId") or ""),
        "marca":        p.get("brand") or "",
        "url":          url_producto,
        "imagen":       img_url,
        "vendedor":     seller.get("sellerName") or "",
        # Precio con tarjeta del supermercado (CMR Tottus, Oh! Plaza Vea)
        "precio_tarjeta":         precio_tarjeta,
        "nombre_tarjeta":         nombre_tarjeta,
        "tarjeta_descuento_pct":  tarjeta_pct,
        "precio_internet":        price,
        "precio_normal":          list_price if tiene_desc else None,
        "precio_descuento":       price,
        "precio_regular":         list_price if tiene_desc else price,
        "tiene_descuento":        tiene_desc,
        "descuento_pct":          pct,
        "fecha_extraccion":       datetime.now().isoformat(timespec="seconds"),
    }


# ── Loop de extracción ──────────────────────────────────────────────────────

def extraer(url: str, supermercado: str, categoria: str,
            delay: float = 1.0, max_paginas: int = 20) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    host = host_from_url(url)
    path = path_from_url(url)

    print(f"[{supermercado}] Host: {host}")
    print(f"           Categoría: {categoria} (path: {path})")

    todos = []
    for page in range(max_paginas):
        if page > 0:
            time.sleep(delay)
        print(f"  Página {page+1}...", end="", flush=True)
        productos_raw = fetch_pagina(host, path, page, session)
        if not productos_raw:
            print(" sin productos (fin)")
            break
        normalizados = [
            n for n in (
                normalizar_producto(p, supermercado, categoria, host)
                for p in productos_raw
            ) if n
        ]
        todos.extend(normalizados)
        print(f" +{len(normalizados)} (total: {len(todos)})")
        if len(productos_raw) < PAGE_SIZE:
            break

    return todos


# ── Persistencia ────────────────────────────────────────────────────────────

CAMPOS_HISTORICO = [
    "supermercado", "categoria", "nombre", "producto_id", "sku_id",
    "marca", "url", "imagen", "vendedor",
    "precio_cmr", "precio_internet", "precio_normal",
    "precio_descuento", "precio_regular",
    "tiene_descuento", "descuento_pct", "fecha_extraccion",
]


def guardar_snapshot(productos: list[dict], super_name: str, categoria: str) -> Path:
    out_dir = Path(".tmp")
    out_dir.mkdir(exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"{super_name}_{categoria}_{fecha}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(productos, f, ensure_ascii=False, indent=2)
    return out


def append_a_historico(productos: list[dict]) -> tuple[Path, bool]:
    import csv
    historico = Path("data") / "historico.csv"
    historico.parent.mkdir(exist_ok=True)
    es_nuevo = not historico.exists() or historico.stat().st_size == 0
    with open(historico, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        if es_nuevo:
            w.writeheader()
        w.writerows(productos)
    return historico, es_nuevo


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL de la categoría")
    parser.add_argument("--super", dest="supermercado", required=True,
                        choices=["wong", "metro", "plazavea"],
                        help="Identificador del supermercado")
    parser.add_argument("--categoria", required=True,
                        help="Nombre canónico de la categoría (carne-de-res, pollo, frutas, ...)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Segundos entre requests (default: 1.0)")
    parser.add_argument("--no-historico", action="store_true",
                        help="No agregar al CSV histórico")
    args = parser.parse_args()

    productos = extraer(args.url, args.supermercado, args.categoria, args.delay)

    if not productos:
        print("\n⚠ No se extrajo ningún producto. Verificá el ID de categoría.")
        return

    out = guardar_snapshot(productos, args.supermercado, args.categoria)

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

## Version v3
