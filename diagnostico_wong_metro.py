"""
Diagnóstico de precios con tarjeta en Wong y Metro.

Toma un producto de carne (de los que ya extrajimos antes) y muestra
TODAS las claves del objeto commertialOffer + análisis de Teasers/PromotionTeasers.

El objetivo es ver si Wong/Metro exponen su tarjeta Bonus de la misma forma
que Plaza Vea expone Oh!/SIP, o si usan otra estructura.

Uso:
    python diagnostico_wong_metro.py
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

# Categorías ya probadas que sabemos tienen catálogo activo
SITIOS = [
    {
        "nombre": "WONG",
        "url": "https://www.wong.pe/api/catalog_system/pub/products/search/carnes-aves-y-pescados/res-y-otras-carnes?_from=0&_to=4",
    },
    {
        "nombre": "METRO",
        "url": "https://www.metro.pe/api/catalog_system/pub/products/search/carnes-aves-y-pescados/res-y-otras-carnes?_from=0&_to=4",
    },
]


def imprimir_oferta(producto):
    print(f"\n  Producto: {producto.get('productName')}")
    print(f"  productId: {producto.get('productId')}")
    items = producto.get("items") or []
    if not items:
        print("  ⚠ Sin items")
        return
    item = items[0]
    sellers = item.get("sellers") or []
    if not sellers:
        print("  ⚠ Sin sellers")
        return

    for i, seller in enumerate(sellers, 1):
        print(f"\n  ── Seller {i}: {seller.get('sellerName')} ──")
        offer = seller.get("commertialOffer") or {}

        # Precios principales
        print(f"  Price:                {offer.get('Price')}")
        print(f"  ListPrice:            {offer.get('ListPrice')}")
        print(f"  PriceWithoutDiscount: {offer.get('PriceWithoutDiscount')}")

        # Mostrar todas las claves para no perder nada
        print(f"\n  Todas las claves de commertialOffer:")
        for k in sorted(offer.keys()):
            v = offer[k]
            if isinstance(v, (int, float, bool, str)):
                v_str = str(v)
            else:
                v_str = json.dumps(v, ensure_ascii=False)
            if len(v_str) > 120:
                v_str = v_str[:120] + "..."
            print(f"    {k}: {v_str}")

        # Promociones / Teasers
        for key in ["Teasers", "PromotionTeasers"]:
            v = offer.get(key)
            if v:
                print(f"\n  {key} (cantidad: {len(v) if isinstance(v, list) else '?'}):")
                print(json.dumps(v, indent=2, ensure_ascii=False)[:1500])
                if len(json.dumps(v)) > 1500:
                    print("    ... (truncado)")


def main():
    s = requests.Session()
    s.headers.update(HEADERS)

    for sitio in SITIOS:
        print("\n" + "═" * 70)
        print(f"  {sitio['nombre']}")
        print("═" * 70)
        print(f"GET {sitio['url'][:90]}...")

        try:
            r = s.get(sitio["url"], timeout=20)
            print(f"Status: {r.status_code}")
            if r.status_code not in (200, 206):
                print(r.text[:300])
                continue

            data = r.json()
            print(f"Productos en respuesta: {len(data)}")

            # Buscar uno que tenga promoción (precios distintos)
            con_promo = None
            for p in data:
                items = p.get("items") or []
                if not items: continue
                sellers = items[0].get("sellers") or []
                if not sellers: continue
                offer = sellers[0].get("commertialOffer") or {}
                price = offer.get("Price")
                list_price = offer.get("ListPrice")
                teasers = offer.get("Teasers") or []
                promo_teasers = offer.get("PromotionTeasers") or []
                # Preferir producto con Teasers (promociones activas)
                if teasers or promo_teasers:
                    con_promo = p
                    break

            if con_promo:
                print(f"\n✓ Producto con promoción encontrado:")
                imprimir_oferta(con_promo)
            else:
                # Si no hay promo activa, mostrar el primer producto igual
                print(f"\n⚠ Ningún producto con Teasers/PromotionTeasers en esta respuesta.")
                print(f"   Mostrando el primer producto igual:")
                imprimir_oferta(data[0])

        except Exception as e:
            print(f"✗ Error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()