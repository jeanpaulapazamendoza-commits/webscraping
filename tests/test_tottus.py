"""
Pruebas del extractor de Tottus, centradas en el precio de referencia.

Tottus publica hasta tres precios por producto y marca con `crossed: true` el
que muestra tachado. El extractor deducía la referencia del nombre del tipo, y
cuando había precio CMR usaba el precio internet: eso medía el descuento de la
tarjeta contra el precio web en vez de contra el de lista. El sitio anunciaba
35% donde el pipeline guardaba 8,9%.

    python tests/test_tottus.py         # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

from tottus_extractor import (descuento_del_sitio, normalizar_producto,
                              precio_tachado)


def precio(tipo, valor, tachado=False):
    p = {"type": tipo, "price": [str(valor)]}
    if tachado:
        p["crossed"] = True
    return p


def producto(precios, badge=None, nombre="Producto"):
    p = {"displayName": nombre, "productId": "1", "skuId": "2", "brand": "M",
         "url": "u", "mediaUrls": ["i"], "sellerName": "Tottus", "prices": precios}
    if badge is not None:
        p["discountBadge"] = {"label": badge}
    return p


def norm(p):
    return normalizar_producto(p, "carne-de-pollo", "tottus")


# ─── el caso que originó el arreglo ──────────────────────────────────────────

def test_la_referencia_es_el_precio_tachado_no_el_internet():
    """Piernitas de Pollo Importado, medido en vivo: el sitio anuncia 35%.

    Con los tres precios presentes, la regla anterior comparaba el CMR (5,10)
    contra el precio internet (5,60) y daba 8,9%. El precio tachado es 7,90.
    """
    r = norm(producto([
        precio("cmrPrice", 5.10),
        precio("internetPrice", 5.60),
        precio("normalPrice", 7.90, tachado=True),
    ], badge="-35%"))
    assert r["precio_regular"] == 7.90, "la referencia es el tachado"
    assert r["precio_descuento"] == 5.10
    assert r["descuento_pct"] == 35.4
    assert r["descuento_pct_sitio"] == 35.0
    assert abs(r["descuento_pct"] - r["descuento_pct_sitio"]) < 1.5


def test_sin_precio_tachado_cae_al_normal_y_despues_al_internet():
    solo_normal = norm(producto([
        precio("cmrPrice", 8.90), precio("normalPrice", 11.50),
    ]))
    assert solo_normal["precio_regular"] == 11.50

    solo_inet = norm(producto([
        precio("cmrPrice", 8.90), precio("internetPrice", 9.90),
    ]))
    assert solo_inet["precio_regular"] == 9.90


def test_precio_regular_ya_no_queda_nulo():
    """Antes quedaba vacío en el 67% de las filas de Tottus por la cadena de
    condicionales; sin referencia no hay descuento que calcular."""
    r = norm(producto([precio("internetPrice", 9.90)]))
    assert r["precio_regular"] == 9.90
    assert r["tiene_descuento"] is False
    assert r["descuento_pct"] == 0.0


def test_sin_descuento_la_referencia_iguala_al_precio():
    r = norm(producto([
        precio("cmrPrice", 12.90), precio("internetPrice", 12.90),
    ]))
    assert r["tiene_descuento"] is False


# ─── el badge del sitio ──────────────────────────────────────────────────────

def test_lee_el_badge_tal_cual_lo_publica_el_sitio():
    assert descuento_del_sitio({"discountBadge": {"label": "-18%"}}) == 18.0
    assert descuento_del_sitio({"discountBadge": {"label": "-8%"}}) == 8.0


def test_sin_badge_no_inventa_un_cero():
    """Vacío significa que ese producto no tenía etiqueta de descuento, que no
    es lo mismo que un descuento de cero."""
    assert descuento_del_sitio({}) is None
    assert descuento_del_sitio({"discountBadge": {"label": ""}}) is None
    assert norm(producto([precio("internetPrice", 9.90)]))["descuento_pct_sitio"] is None


def test_un_badge_ilegible_no_rompe_la_corrida():
    assert descuento_del_sitio({"discountBadge": {"label": "oferta"}}) is None


# ─── el lector del precio tachado ────────────────────────────────────────────

def test_precio_tachado_ignora_los_no_marcados():
    assert precio_tachado([
        precio("cmrPrice", 5.10),
        precio("internetPrice", 5.60),
        precio("normalPrice", 7.90, tachado=True),
    ]) == 7.90
    assert precio_tachado([precio("internetPrice", 5.60)]) is None


def test_precio_tachado_tolera_coma_decimal():
    assert precio_tachado([{"type": "normalPrice", "price": ["7,90"], "crossed": True}]) == 7.90


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
