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

# VTEX usa un número enorme en AvailableQuantity para decir "no llevo la cuenta",
# típico de lo que se vende por peso. Medido sobre 1.042 productos de las tres
# cadenas: 326 traen exactamente 99999, y aparecen también 99997 y 99695, que es
# el mismo centinela ya descontado por ventas. Guardar eso como stock real sería
# inventar un número; por encima de este umbral se registra como desconocido.
STOCK_SIN_LIMITE = 99_000


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


# ── Promociones de tarjeta ──────────────────────────────────────────────────

# Cómo nombra cada cadena su tarjeta dentro del teaser. Medido sobre los
# teasers reales de las tres cadenas VTEX: Plaza Vea los llama "Promo Oh-Pay" y
# "TARJETA OH - PVEA"; Wong y Metro usan "[TCENCO] ... Con TC Wong/Metro ...".
TARJETAS = (
    (re.compile(r"tarjeta\s*oh|oh-?pay|\boh\b", re.I), "Tarjeta Oh!"),
    (re.compile(r"tc\s*wong", re.I),  "TC Wong"),
    (re.compile(r"tc\s*metro", re.I), "TC Metro"),
)

# Los teasers con "oculto" en el nombre son promociones de una tienda puntual
# (aparecen como "Promo-Tarjeta-Oh-Lurin-Oculto"). El scraper corre sin región,
# así que lo que corresponde informar es el precio general. Medido: en 14 de 15
# productos valen lo mismo que la pública, y en el restante valen menos.
OCULTO = re.compile(r"oculto", re.I)


def _precio_desde_monto(valor: float, price: float, full_price: float | None):
    """PromotionalPriceTableItemsDiscount: un MONTO EN SOLES sobre la unidad.

    Es el formato de Plaza Vea. El descuento viene expresado sobre
    FullSellingPrice (el artículo entero), mientras que Price puede ser por kilo,
    así que hay que llevarlo a la misma base antes de restarlo.
    """
    if not full_price:
        return None
    return price * (1 - valor / full_price)


def _precio_desde_porcentaje(valor: float, price: float, full_price: float | None):
    """PercentualDiscount: un PORCENTAJE directo sobre el precio.

    Es el formato de Wong y Metro, que publican "5% con TC Wong". Acá no
    interviene FullSellingPrice: el porcentaje se aplica sobre Price tal cual.
    """
    return price * (1 - valor / 100)


# Un formato por nombre de parámetro. Son cálculos distintos y no se pueden
# mezclar: leer el monto en soles como si fuera un porcentaje fue exactamente el
# error que dejó 2.300 precios de tarjeta negativos en el histórico.
FORMULAS = {
    "PromotionalPriceTableItemsDiscount": _precio_desde_monto,
    "PercentualDiscount": _precio_desde_porcentaje,
}


def _nombre(obj: dict) -> str:
    """VTEX serializa las claves de dos formas según el endpoint."""
    return obj.get("Name") or obj.get("<Name>k__BackingField") or ""


def _parametros(teaser: dict) -> list:
    efectos = teaser.get("Effects") or teaser.get("<Effects>k__BackingField") or {}
    return efectos.get("Parameters") or efectos.get("<Parameters>k__BackingField") or []


def detectar_promo_tarjeta(teasers: list, promo_teasers: list, price: float,
                           full_price: float | None) -> tuple:
    """Busca la mejor promoción de tarjeta en los teasers de VTEX.

    Devuelve (nombre_tarjeta, precio_tarjeta, descuento_pct, origen), o cuatro
    None si no hay ninguna aplicable.

    `origen` guarda qué teaser y qué parámetro produjeron el número, con la
    forma "nombre del teaser|parámetro". Es lo que faltó para reconstruir el
    error del precio Oh! sin volver a scrapear.

    Si hay varias promociones se queda con la que deja el precio más bajo, que
    es lo que el cliente termina pagando.
    """
    mejor = None
    for teaser in (promo_teasers or []) + (teasers or []):
        nombre_teaser = _nombre(teaser)
        if not nombre_teaser or OCULTO.search(nombre_teaser):
            continue

        tarjeta = next((etiqueta for patron, etiqueta in TARJETAS
                        if patron.search(nombre_teaser)), None)
        if not tarjeta:
            continue

        for parametro in _parametros(teaser):
            formula = FORMULAS.get(_nombre(parametro))
            if not formula:
                continue
            crudo = parametro.get("Value") or parametro.get("<Value>k__BackingField")
            try:
                valor = float(crudo)
            except (TypeError, ValueError):
                continue
            if not valor:
                continue

            candidato = formula(valor, price, full_price)
            # Red de seguridad: si no es un precio plausible, preferimos no
            # registrar nada antes que guardar basura.
            if candidato is None or not 0 < candidato < price:
                continue
            if mejor is None or candidato < mejor[1]:
                mejor = (tarjeta, round(candidato, 2),
                         round((1 - candidato / price) * 100, 1),
                         f"{nombre_teaser}|{_nombre(parametro)}")
            break

    return mejor if mejor else (None, None, None, None)


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
    # Precio de la UNIDAD de venta. En los productos por peso, Price es por kilo
    # y este es el del artículo entero: un pollo de 2,2 kg a S/ 8,90/kg tiene
    # FullSellingPrice 19,58. Lo guardamos porque es la base sobre la que VTEX
    # expresa los descuentos de la promo Oh!.
    full_price = offer.get("FullSellingPrice")

    # Disponibilidad. Sale del MISMO offer del que sale el precio, para que no
    # queden de vendedores distintos si alguna cadena suma sellers más adelante.
    #
    # Importa más de lo que parece: un producto agotado se publica con su precio
    # normal, así que sin este dato entra al histórico como si fuera comprable.
    # En Plaza Vea eso es cerca de un tercio del catálogo, mientras que en Wong y
    # Metro es casi cero, así que el sesgo cae sobre una sola cadena y ensucia
    # justo la comparación entre cadenas.
    disponible = offer.get("IsAvailable")
    cantidad = offer.get("AvailableQuantity")
    stock = None
    if isinstance(cantidad, (int, float)) and cantidad < STOCK_SIN_LIMITE:
        stock = int(cantidad)

    # Heurística VTEX: si ListPrice > Price hay descuento online (sin tarjeta)
    tiene_desc = bool(list_price and price and float(list_price) > float(price))
    pct = round((1 - price / list_price) * 100, 1) if tiene_desc else None

    # ── Promoción de tarjeta, para las tres cadenas ───────────────────────
    # Antes esto estaba dentro de un `if supermercado == "plazavea"`, así que las
    # columnas de tarjeta salían vacías para Wong y Metro aunque las dos
    # publiquen su propia promoción. Hoy ambas tienen un 5% con su tarjeta.
    nombre_tarjeta = precio_tarjeta = tarjeta_pct = tarjeta_origen = None
    if price:
        nombre_tarjeta, precio_tarjeta, tarjeta_pct, tarjeta_origen = (
            detectar_promo_tarjeta(
                offer.get("Teasers") or [],
                offer.get("PromotionTeasers") or [],
                price, full_price,
            )
        )

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
        "disponible":   disponible,
        "stock":        stock,
        # Precio con tarjeta del supermercado (CMR Tottus, Oh! Plaza Vea)
        "precio_tarjeta":         precio_tarjeta,
        "nombre_tarjeta":         nombre_tarjeta,
        "tarjeta_descuento_pct":  tarjeta_pct,
        "tarjeta_origen":         tarjeta_origen,
        "precio_internet":        price,
        "precio_unidad":          full_price,
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
