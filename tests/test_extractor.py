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

from vtex_extractor import (STOCK_SIN_LIMITE, detectar_promo_tarjeta,
                             normalizar_producto)


def teaser(nombre, descuento, parametro="PromotionalPriceTableItemsDiscount"):
    """Un teaser de VTEX. Por defecto con el formato de Plaza Vea: un monto en
    soles. Wong y Metro usan PercentualDiscount, que es un porcentaje."""
    return {
        "Name": nombre,
        "Effects": {"Parameters": [
            {"Name": "PromotionalPriceTableItemsIds", "Value": "1"},
            {"Name": parametro, "Value": str(descuento)},
        ]},
    }


def producto(price, full_selling_price, teasers=(), list_price=None,
             disponible=True, cantidad=100):
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
                    "IsAvailable": disponible,
                    "AvailableQuantity": cantidad,
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


def test_se_queda_con_la_promo_que_deja_el_precio_mas_bajo():
    r = norm(producto(10.0, 10.0, [teaser("TARJETA OH - PVEA", 1.00),
                                   teaser("Promo Oh-Pay", 2.50)]))
    assert r["precio_tarjeta"] == 7.50, "la mejor para el cliente"


def test_acepta_la_serializacion_con_backing_field():
    """VTEX a veces devuelve las claves como <Name>k__BackingField."""
    raro = {
        "<Name>k__BackingField": "TARJETA OH - PVEA",
        "<Effects>k__BackingField": {"<Parameters>k__BackingField": [
            {"<Name>k__BackingField": "PromotionalPriceTableItemsDiscount",
             "<Value>k__BackingField": "2.40000"},
        ]},
    }
    tarjeta, precio, pct, origen = detectar_promo_tarjeta([raro], [], 10.0, 10.0)
    assert (tarjeta, precio) == ("Tarjeta Oh!", 7.60)
    assert origen == "TARJETA OH - PVEA|PromotionalPriceTableItemsDiscount"


# ─── las otras cadenas ───────────────────────────────────────────────────────

def test_wong_usa_un_porcentaje_directo_sobre_el_precio():
    """Wong y Metro publican "5% con TC Wong" con el parámetro
    PercentualDiscount, que no pasa por FullSellingPrice."""
    r = norm(producto(17.90, 17.90, [teaser(
        "[TCENCO] Oct26 - Supermercado Con 5% Dscto Con TC Wong del 07al31",
        5, parametro="PercentualDiscount")]))
    assert r["precio_tarjeta"] == 17.0
    assert r["tarjeta_descuento_pct"] == 5.0
    assert r["nombre_tarjeta"] == "TC Wong"


def test_metro_tiene_su_propia_etiqueta():
    r = norm(producto(9.50, 9.50, [teaser(
        "[TCENCO] Oct26 - Supermercado Con 5% Dscto Con TC Metro del 01al31",
        5, parametro="PercentualDiscount")]))
    assert r["nombre_tarjeta"] == "TC Metro"
    assert r["precio_tarjeta"] == 9.03


def test_el_porcentaje_no_se_lee_como_monto():
    """Los dos formatos no se pueden mezclar: leer el 5 de PercentualDiscount
    como si fueran 5 soles daría 12,90 en vez de 17,00."""
    r = norm(producto(17.90, 17.90, [teaser("TC Wong", 5, parametro="PercentualDiscount")]))
    assert r["precio_tarjeta"] == 17.0


def test_se_guarda_de_donde_salio_el_numero():
    """Es lo que faltó para reconstruir el error del precio Oh! sin volver a
    scrapear: qué teaser y qué parámetro produjeron el precio."""
    r = norm(producto(10.0, 10.0, [teaser("Promo Oh-Pay", 2.00)]))
    assert r["tarjeta_origen"] == "Promo Oh-Pay|PromotionalPriceTableItemsDiscount"


def test_las_promos_de_una_tienda_puntual_se_ignoran():
    """Los teasers "...-Lurin-Oculto" son de una tienda concreta y el scraper
    corre sin región, así que lo que corresponde informar es el precio general."""
    r = norm(producto(10.0, 10.0, [teaser("Promo-Tarjeta-Oh-Lurin-Oculto", 5.00)]))
    assert r["precio_tarjeta"] is None


def test_un_parametro_desconocido_no_inventa_un_precio():
    """Si VTEX agrega un tercer formato, se registra como sin promo en vez de
    calcularlo con la fórmula equivocada."""
    r = norm(producto(10.0, 10.0, [teaser("TARJETA OH", 3, parametro="PromotionalPrice")]))
    assert r["precio_tarjeta"] is None
    assert r["nombre_tarjeta"] is None


# ─── el descuento online, que no cambió ──────────────────────────────────────

def test_descuento_online_sigue_saliendo_del_precio_tachado():
    r = norm(producto(7.50, 16.50, list_price=8.90))
    assert r["tiene_descuento"] is True
    assert r["descuento_pct"] == 15.7
    assert r["precio_regular"] == 8.90


# ─── disponibilidad ──────────────────────────────────────────────────────────

def test_captura_disponibilidad_y_stock():
    r = norm(producto(8.90, 19.58, cantidad=317))
    assert r["disponible"] is True
    assert r["stock"] == 317


def test_un_agotado_se_marca_pero_conserva_su_precio():
    """No se descarta la fila: perderla impide saber cuándo volvió el stock, y
    el precio publicado sigue siendo un dato real del catálogo."""
    r = norm(producto(7.90, 7.90, disponible=False, cantidad=0))
    assert r["disponible"] is False
    assert r["stock"] == 0
    assert r["precio_internet"] == 7.90


def test_el_centinela_no_se_guarda_como_stock():
    """VTEX pone 99999 para decir que no lleva la cuenta, típico de lo que se
    vende por peso. Medido: 326 de 934 productos disponibles lo traen."""
    for cantidad in (99999, 99997, 99695, STOCK_SIN_LIMITE):
        r = norm(producto(8.90, 19.58, cantidad=cantidad))
        assert r["stock"] is None, f"{cantidad} debería leerse como desconocido"
        assert r["disponible"] is True, "sin contador no significa sin stock"


def test_stock_justo_por_debajo_del_centinela_si_se_guarda():
    r = norm(producto(8.90, 19.58, cantidad=STOCK_SIN_LIMITE - 1))
    assert r["stock"] == STOCK_SIN_LIMITE - 1


def test_la_disponibilidad_sale_del_mismo_seller_que_el_precio():
    """Si una cadena suma vendedores, precio y disponibilidad tienen que venir
    del mismo, no de dos distintos."""
    p = producto(8.90, 19.58)
    oferta = p["items"][0]["sellers"][0]
    otro = {"sellerDefault": False, "sellerName": "Otro",
            "commertialOffer": {"Price": 99.0, "ListPrice": 99.0,
                                "FullSellingPrice": 99.0, "IsAvailable": False,
                                "AvailableQuantity": 0, "Teasers": [],
                                "PromotionTeasers": []}}
    p["items"][0]["sellers"] = [otro, oferta]      # el default va segundo
    r = norm(p)
    assert r["precio_internet"] == 8.90, "tomó el precio del seller default"
    assert r["disponible"] is True, "y la disponibilidad del MISMO seller"


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
