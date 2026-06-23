"""
Verificador rápido de URLs antes de scrapear.

Hace UN solo request a cada URL y te dice si va a funcionar.
NO guarda nada, NO scrapea — solo valida.

Uso:
    python verificar_urls.py URL1 URL2 URL3 ...

    # o, más cómodo, una por línea:
    python verificar_urls.py `
        "https://www.wong.pe/carnes-aves-y-pescados/pollo" `
        "https://www.wong.pe/frutas-y-verduras/frutas" `
        "https://www.tottus.com.pe/tottus-pe/lista/CATG...../Pollo"

Salida: tabla con el estado de cada URL y cuántos productos hay disponibles.
"""

import sys
import re
import json
import requests
from urllib.parse import urlparse


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "es-PE,es;q=0.9",
}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def detectar_plataforma(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "tottus.com.pe" in host:
        return "tottus"
    if "wong.pe" in host or "metro.pe" in host or "plazavea.com.pe" in host:
        return "vtex"
    return "desconocida"


def verificar_tottus(url: str, session) -> tuple[str, str]:
    """Devuelve (estado, detalle)."""
    r = session.get(url, timeout=15)
    if r.status_code != 200:
        return "❌ FAIL", f"HTTP {r.status_code}"
    m = NEXT_DATA_RE.search(r.text)
    if not m:
        return "❌ FAIL", "No __NEXT_DATA__ en HTML"
    try:
        data = json.loads(m.group(1))
        pp = data["props"]["pageProps"]
        total = pp["pagination"]["count"]
        nombre = pp.get("breadCrumbData", [{}])[-1].get("displayName", "?")
        return "✅ OK", f"{total} productos · '{nombre}'"
    except Exception as e:
        return "⚠ RARO", f"Estructura inesperada: {e}"


def verificar_vtex(url: str, session) -> tuple[str, str]:
    """Devuelve (estado, detalle)."""
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.strip("/")
    # 1) confirmar que el path es una categoría
    pagetype_url = f"{host}/api/catalog_system/pub/portal/pagetype/{path}"
    r = session.get(pagetype_url, timeout=15)
    if r.status_code != 200:
        return "❌ FAIL", f"pagetype HTTP {r.status_code}"
    try:
        meta = r.json()
    except json.JSONDecodeError:
        return "❌ FAIL", "pagetype no devolvió JSON"
    pt = meta.get("pageType")
    if pt not in ("Category", "Department", "SubCategory"):
        return "❌ FAIL", f"pageType={pt} (no es categoría)"
    nombre = meta.get("name", "?")

    # 2) pedir 1 producto para confirmar que hay catálogo
    search_url = f"{host}/api/catalog_system/pub/products/search/{path}?_from=0&_to=0"
    r = session.get(search_url, timeout=15)
    if r.status_code not in (200, 206):
        return "⚠ RARO", f"search HTTP {r.status_code} (pero pagetype OK)"
    try:
        productos = r.json()
    except json.JSONDecodeError:
        return "⚠ RARO", "search no devolvió JSON"
    if not isinstance(productos, list) or not productos:
        return "⚠ VACÍA", f"'{nombre}' (pageType={pt}) — sin productos en path"
    return "✅ OK", f"'{nombre}' · al menos 1 producto disponible"


def verificar(url: str, session) -> tuple[str, str, str]:
    plataforma = detectar_plataforma(url)
    if plataforma == "tottus":
        estado, detalle = verificar_tottus(url, session)
    elif plataforma == "vtex":
        estado, detalle = verificar_vtex(url, session)
    else:
        estado, detalle = "❌ FAIL", f"Plataforma desconocida ({urlparse(url).netloc})"
    return plataforma, estado, detalle


def main():
    if len(sys.argv) < 2:
        print("Uso: python verificar_urls.py URL1 [URL2 URL3 ...]")
        sys.exit(1)

    urls = sys.argv[1:]
    s = requests.Session()
    s.headers.update(HEADERS)

    print(f"Verificando {len(urls)} URL{'s' if len(urls)>1 else ''}...\n")
    for i, url in enumerate(urls, 1):
        plataforma, estado, detalle = verificar(url, s)
        print(f"[{i}/{len(urls)}] {estado}  ({plataforma})")
        print(f"        {url}")
        print(f"        → {detalle}\n")


if __name__ == "__main__":
    main()
## version v1

