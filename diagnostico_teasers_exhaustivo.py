"""
Check exhaustivo: Wong y Metro realmente NO usan Teasers?

Escanea ~50 productos por sitio buscando teasers/promociones por tarjeta.
Si encuentra alguno, lo muestra. Si no encuentra ninguno en 50 productos,
podemos asumir con alta confianza que Wong/Metro no exponen tarjeta en VTEX.

Uso:
    python diagnostico_teasers_exhaustivo.py
"""

import requests
import json
import sys

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-PE,es;q=0.9",
}

# Probar varias categorías por sitio (50 productos cada una)
TARGETS = [
    ("WONG",     "https://www.wong.pe/api/catalog_system/pub/products/search/carnes-aves-y-pescados/res-y-otras-carnes?_from=0&_to=49"),
    ("WONG",     "https://www.wong.pe/api/catalog_system/pub/products/search/frutas-y-verduras/frutas?_from=0&_to=49"),
    ("METRO",    "https://www.metro.pe/api/catalog_system/pub/products/search/carnes-aves-y-pescados/res-y-otras-carnes?_from=0&_to=49"),
    ("METRO",    "https://www.metro.pe/api/catalog_system/pub/products/search/frutas-y-verduras/frutas?_from=0&_to=49"),
    # Plaza Vea como control — debería tener muchos
    ("PLAZAVEA", "https://www.plazavea.com.pe/api/catalog_system/pub/products/search/frutas-y-verduras/frutas/paltas?_from=0&_to=49"),
]


def main():
    s = requests.Session()
    s.headers.update(HEADERS)

    resumen_global = {}

    for site, url in TARGETS:
        print(f"\n━━━ {site} ━━━")
        print(f"URL: {url[:90]}...")
        try:
            r = s.get(url, timeout=20)
            if r.status_code not in (200, 206):
                print(f"  Status: {r.status_code} (skip)")
                continue
            data = r.json()
            print(f"  Productos: {len(data)}")
        except Exception as e:
            print(f"  Error: {e}")
            continue

        con_teasers = 0
        nombres_tarjetas = set()
        ejemplo = None

        for p in data:
            items = p.get("items") or []
            if not items:
                continue
            for seller in items[0].get("sellers") or []:
                offer = seller.get("commertialOffer") or {}
                teasers = offer.get("Teasers") or []
                promo_teasers = offer.get("PromotionTeasers") or []
                if teasers or promo_teasers:
                    con_teasers += 1
                    if ejemplo is None:
                        ejemplo = (p.get("productName"), teasers, promo_teasers)
                    for t in promo_teasers or teasers:
                        name = t.get("Name", "?")
                        nombres_tarjetas.add(name)

        print(f"  Productos con Teasers/PromotionTeasers: {con_teasers}/{len(data)}")
        if nombres_tarjetas:
            print(f"  Nombres de promociones encontradas:")
            for n in sorted(nombres_tarjetas):
                print(f"    - {n}")
        if ejemplo:
            nom, t, pt = ejemplo
            print(f"\n  Ejemplo: {nom}")
            print(f"    PromotionTeasers[0]:")
            if pt:
                print(json.dumps(pt[0], indent=4, ensure_ascii=False)[:700])

        resumen_global.setdefault(site, []).append((con_teasers, len(data), nombres_tarjetas))

    # ── Resumen final ─────────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("  CONCLUSIÓN")
    print("═" * 70)
    for site, runs in resumen_global.items():
        total_con = sum(c for c, _, _ in runs)
        total = sum(t for _, t, _ in runs)
        todas_tarjetas = set()
        for _, _, nt in runs:
            todas_tarjetas.update(nt)
        print(f"\n  {site}: {total_con}/{total} productos con teasers")
        if todas_tarjetas:
            print(f"    Tarjetas/promos:")
            for t in sorted(todas_tarjetas):
                print(f"      - {t}")
        else:
            print(f"    ✓ Sin teasers — no requiere parseo especial")


if __name__ == "__main__":
    main()

    #version01
    