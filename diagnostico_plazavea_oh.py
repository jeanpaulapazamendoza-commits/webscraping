"""
Diagnóstico de precio Oh!/SIP en Plaza Vea.

Busca en la respuesta de VTEX un producto conocido y muestra TODAS las claves
de su oferta para identificar dónde viene el precio Oh!/SIP que no estamos
capturando.

Uso:
    python diagnostico_plazavea_oh.py
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

# Producto que el usuario reportó: Palta Fuerte x kg, SKU 64576
URL_API = ("https://www.plazavea.com.pe/api/catalog_system/pub/products/search"
           "/frutas-y-verduras/frutas/paltas?_from=0&_to=10")


def main():
    s = requests.Session()
    s.headers.update(HEADERS)

    print(f"GET {URL_API}\n")
    r = s.get(URL_API, timeout=20)
    print(f"Status: {r.status_code}")

    if r.status_code not in (200, 206):
        print(r.text[:500])
        sys.exit(1)

    data = r.json()
    print(f"Productos en respuesta: {len(data)}\n")

    # Buscar la Palta Fuerte específicamente
    palta = None
    for p in data:
        nombre = p.get("productName", "").lower()
        if "palta" in nombre and "fuerte" in nombre:
            palta = p
            break

    if not palta:
        print("⚠ No se encontró 'Palta Fuerte' en los primeros 10 productos.")
        print("Productos encontrados:")
        for p in data:
            print(f"  - {p.get('productName')}")
        return

    print("━" * 70)
    print(f"PRODUCTO ENCONTRADO: {palta['productName']}")
    print(f"productId: {palta.get('productId')}")
    print("━" * 70)

    items = palta.get("items") or []
    if not items:
        print("⚠ Sin items")
        return

    item = items[0]
    print(f"\nitemId (SKU): {item.get('itemId')}")
    print(f"Cantidad de sellers: {len(item.get('sellers') or [])}\n")

    for i, seller in enumerate(item.get("sellers") or [], 1):
        print(f"━━━ Seller {i}: {seller.get('sellerName')} ━━━")
        offer = seller.get("commertialOffer") or {}

        # Mostrar TODAS las claves para no perder nada
        print(f"\nTodas las claves de commertialOffer:")
        for k in sorted(offer.keys()):
            v = offer[k]
            # Truncar valores largos
            v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float, bool, str)) else str(v)
            if len(v_str) > 200:
                v_str = v_str[:200] + "..."
            print(f"  {k}: {v_str}")

        # En particular, buscar promociones / teasers que podrían tener Oh!/SIP
        print(f"\n── Análisis de promociones ──")
        for key in ["Teasers", "PromotionTeasers", "DiscountHighLight",
                    "GiftSkuIds", "Installments"]:
            v = offer.get(key)
            if v:
                print(f"\n{key}:")
                print(json.dumps(v, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()