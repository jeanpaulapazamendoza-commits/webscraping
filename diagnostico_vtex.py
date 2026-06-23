"""
Diagnóstico VTEX: investiga por qué la búsqueda viene vacía aunque el path
resuelva a una category ID.

Probará tres approaches para ver cuál funciona:
  1. Búsqueda por path directo (sin resolver ID)
  2. Listar el árbol de categorías hijas
  3. Inspeccionar el formato exacto de respuesta del pagetype

Uso:
    python diagnostico_vtex.py "URL_DE_LA_CATEGORIA"
"""

import sys
import json
import requests
from urllib.parse import urlparse


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}


def host_from_url(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def path_from_url(url):
    return urlparse(url).path.strip("/")


def main():
    if len(sys.argv) < 2:
        print("Uso: python diagnostico_vtex.py <URL>")
        sys.exit(1)

    url = sys.argv[1]
    host = host_from_url(url)
    path = path_from_url(url)
    s = requests.Session()
    s.headers.update(HEADERS)

    print(f"Host: {host}")
    print(f"Path: {path}\n")

    # ── 1. pagetype lookup completo ─────────────────────────────────
    print("━" * 70)
    print("1. RESPUESTA COMPLETA DEL pagetype")
    print("━" * 70)
    pagetype_url = f"{host}/api/catalog_system/pub/portal/pagetype/{path}"
    r = s.get(pagetype_url, timeout=30)
    print(f"GET {pagetype_url}")
    print(f"Status: {r.status_code}")
    try:
        data = r.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        cat_id = str(data.get("id") or "")
    except Exception as e:
        print(f"Error parseando JSON: {e}")
        print(r.text[:500])
        cat_id = ""

    if not cat_id:
        print("\n⚠ No hay ID de categoría — no podemos seguir investigando.")
        return

    # ── 2. Búsqueda por path directo (sin resolver ID) ──────────────
    print("\n" + "━" * 70)
    print("2. BÚSQUEDA POR PATH DIRECTO")
    print("━" * 70)
    search_path = f"{host}/api/catalog_system/pub/products/search/{path}?_from=0&_to=2"
    r = s.get(search_path, timeout=30)
    print(f"GET {search_path}")
    print(f"Status: {r.status_code}")
    try:
        data = r.json()
        print(f"Productos devueltos: {len(data) if isinstance(data, list) else 'no es lista'}")
        if isinstance(data, list) and data:
            p0 = data[0]
            print(f"\n  Primer producto:")
            print(f"    productName: {p0.get('productName')}")
            print(f"    productId:   {p0.get('productId')}")
            print(f"    brand:       {p0.get('brand')}")
            print(f"    categoryId:  {p0.get('categoryId')}")
            items = p0.get("items") or []
            if items:
                sellers = items[0].get("sellers") or []
                if sellers:
                    offer = sellers[0].get("commertialOffer") or {}
                    print(f"    Price:       {offer.get('Price')}")
                    print(f"    ListPrice:   {offer.get('ListPrice')}")
    except Exception as e:
        print(f"Error: {e}")
        print(r.text[:300])

    # ── 3. Árbol de categorías hijas ────────────────────────────────
    print("\n" + "━" * 70)
    print(f"3. CATEGORÍAS HIJAS de ID {cat_id}")
    print("━" * 70)
    tree_url = f"{host}/api/catalog_system/pub/category/tree/3"
    r = s.get(tree_url, timeout=30)
    print(f"GET {tree_url}")
    print(f"Status: {r.status_code}")
    try:
        tree = r.json()

        def buscar_y_mostrar(nodos, target_id, nivel=0):
            for n in nodos:
                if str(n.get("id")) == target_id:
                    print(f"\n  ✓ Encontrada: {n.get('name')} (id={n.get('id')})")
                    hijas = n.get("children") or []
                    if hijas:
                        print(f"  Tiene {len(hijas)} subcategorías:")
                        for h in hijas:
                            print(f"    - id={h.get('id')}  name={h.get('name')}  url={h.get('url')}")
                    else:
                        print(f"  Sin subcategorías (es hoja)")
                    return True
                if buscar_y_mostrar(n.get("children") or [], target_id, nivel + 1):
                    return True
            return False

        if not buscar_y_mostrar(tree, cat_id):
            print(f"  ⚠ ID {cat_id} no encontrada en el árbol (profundidad 3)")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
    