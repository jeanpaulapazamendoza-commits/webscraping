"""
Pruebas del extractor VTEX, centradas en el precio con Tarjeta Oh!.

No tocan la red: usan respuestas de VTEX recortadas a lo que importa, con
valores tomados de productos reales de Plaza Vea el 8 de septiembre de 2026.

    python tests/test_extractor.py      # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

from vtex_extractor import detectar_descuento_oh, normalizar_producto


def teaser(nombre, descuento):
    """Un teaser de VTEX con la forma que devuelve Plaza Vea."""
    return {
        "Name": nombre,
        "Effects": {"Parameters": [
            {"Name": "PromotionalPriceTableItemsIds", "Value": "1"},
            {"Name": "PromotionalPriceTableItemsDiscount", "Value": str(descuento)},
        ]},
    }


def producto(price, full_selling_price, teasers=(), list_price=None):
    return {
        "productName": "Producto de prueba",
        "productId": "1",
        "linkText": "producto-de-prueba",
        "brand": "MARCA",
        "items": [{
            "itemId": "10",
            "images": [{"imageUrl": "http://x/img.jpg"}],
            "sellers": [{
                "sellerDefault": True,
                "sellerName": "Plaza Vea",
                "commertialOffer": {
                    "Price": price,
                    "ListPrice": list_price if list_price is not None else price,
                    "FullSellingPrice": full_selling_price,
                    "Teasers": list(teasers),
                    "PromotionTeasers": [],
                },
            }],
        }],
    }


def norm(p):
    return normalizar_producto(p, "plazavea", "pollo-entero", "https://www.plazavea.com.pe")


# ─── el caso que originó el arreglo ──────────────────────────────────────────

def test_producto_por_peso_usa_la_unidad_de_venta():
    """Pollo Entero Fresco con Menudencia, 1-sep-2026.

    S/ 8,90 el kilo, unidad de 2,20 kg (FullSellingPrice 19,58), promo Oh! de
    S/ 5,28 sobre la unidad. El precio con tarjeta es S/ 6,50 el kilo.
    Leyendo el 5,28 como fracción daba S/ -38,09.
    """
    r = norm(producto(8.90, 19.58, [teaser("TARJETA OH - PVEA", 5.28)]))
    assert r["precio_tarjeta"] == 6.50
    assert r["nombre_tarjeta"] == "Tarjeta Oh!"
    assert r["tarjeta_descuento_pct"] == 27.0
    assert r["precio_unidad"] == 19.58


def test_producto_por_unidad():
    """Sangrecita Criolla 500g: se vende por unidad, así que Price y
    FullSellingPrice coinciden y el descuento se resta directo."""
    r = norm(producto(6.40, 6.40, [teaser("Promo Oh-Pay", 0.40)]))
    assert r["precio_tarjeta"] == 6.00
    # 0,40 sobre 6,40 son 6,25%, y round() los deja en 6,2 (redondeo bancario)
    assert r["tarjeta_descuento_pct"] == 6.2


def test_nunca_devuelve_un_precio_negativo():
    """Si el descuento supera al precio de la unidad, el resultado no es un
    precio: mejor no registrar nada que guardar basura."""
    r = norm(producto(8.90, 19.58, [teaser("TARJETA OH", 25.0)]))
    assert r["precio_tarjeta"] is None
    assert r["nombre_tarjeta"] is None
    assert r["tarjeta_descuento_pct"] is None


def test_sin_full_selling_price_no_inventa():
    """Sin la base sobre la que VTEX expresa el descuento no hay forma de
    convertirlo, y estimarlo fue justamente el error original."""
    r = norm(producto(8.90, None, [teaser("TARJETA OH", 5.28)]))
    assert r["precio_tarjeta"] is None
    assert r["nombre_tarjeta"] is None


def test_sin_promo_no_hay_precio_de_tarjeta():
    r = norm(producto(8.90, 19.58, []))
    assert r["precio_tarjeta"] is None
    assert r["nombre_tarjeta"] is None
    assert r["precio_unidad"] == 19.58


def test_ignora_promos_que_no_son_de_la_tarjeta():
    r = norm(producto(10.0, 10.0, [teaser("Descuento de temporada", 2.0)]))
    assert r["precio_tarjeta"] is None


def test_se_queda_con_el_mejor_descuento():
    nombre, soles = detectar_descuento_oh(
        [teaser("Promo Oh-Pay", 1.20), teaser("TARJETA OH - PVEA", 2.40)], [])
    assert (nombre, soles) == ("Tarjeta Oh!", 2.40)


def test_acepta_la_serializacion_con_backing_field():
    """VTEX a veces devuelve las claves como <Name>k__BackingField."""
    raro = {
        "<Name>k__BackingField": "TARJETA OH - PVEA",
        "<Effects>k__BackingField": {"<Parameters>k__BackingField": [
            {"<Name>k__BackingField": "PromotionalPriceTableItemsDiscount",
             "<Value>k__BackingField": "2.40000"},
        ]},
    }
    assert detectar_descuento_oh([raro], []) == ("Tarjeta Oh!", 2.40)


# ─── el descuento online, que no cambió ──────────────────────────────────────

def test_descuento_online_sigue_saliendo_del_precio_tachado():
    r = norm(producto(7.50, 16.50, list_price=8.90))
    assert r["tiene_descuento"] is True
    assert r["descuento_pct"] == 15.7
    assert r["precio_regular"] == 8.90


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if not nombre.startswith("test_"):
            continue
        try:
            fn()
            print(f"  OK    {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}: {e}")
    print(f"\n{fallos} fallo(s)")
    sys.exit(1 if fallos else 0)
