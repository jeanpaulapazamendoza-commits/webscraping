"""
Pruebas de normalizar.py, centradas en el contrato del esquema de salida.

normalizar_df() tiene que devolver SIEMPRE las mismas columnas en el mismo
orden, pase lo que pase con la entrada. Cuando devolvía solo la intersección
con lo que traía el lote, dos cosas se rompieron: el CSV de descarga que une
particiones viejas (sin precio_unidad) con nuevas, y el append posicional al
Google Sheet. En los dos casos la fecha terminaba debajo de un encabezado de
precio.

    python tests/test_normalizar.py     # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import pandas as pd

from historico_io import CAMPOS_HISTORICO
from normalizar import ESQUEMA_SALIDA, construir_resumen, normalizar_df

# Una fila como la escriben los extractores hoy (21 campos).
FILA_NUEVA = dict(
    supermercado="plazavea", categoria="pollo-entero", familia="pollo",
    nombre="Pollo Entero Fresco con Menudencia x kg", producto_id="9253",
    sku_id="9357", marca="PLAZA VEA", url="u", imagen="i", vendedor="Plaza Vea",
    precio_tarjeta="6.50", nombre_tarjeta="Tarjeta Oh!", tarjeta_descuento_pct="27.0",
    precio_internet="8.90", precio_unidad="19.58", precio_normal="",
    precio_descuento="8.90", precio_regular="8.90", tiene_descuento="False",
    descuento_pct="", fecha_extraccion="2026-09-08T10:00:00",
)
# La misma fila como la escribían antes de septiembre: sin precio_unidad.
FILA_VIEJA = {k: v for k, v in FILA_NUEVA.items() if k != "precio_unidad"}
FILA_VIEJA["fecha_extraccion"] = "2026-05-27T07:52:00"


def test_el_esquema_de_salida_es_fijo():
    nueva = normalizar_df(pd.DataFrame([FILA_NUEVA]))
    vieja = normalizar_df(pd.DataFrame([FILA_VIEJA]))
    assert list(nueva.columns) == ESQUEMA_SALIDA
    assert list(vieja.columns) == ESQUEMA_SALIDA, (
        "una partición sin precio_unidad tiene que dar el mismo esquema"
    )


def test_dos_lotes_distintos_se_pueden_concatenar_sin_desalinear():
    """El caso real: el CSV de descarga une mayo con septiembre."""
    vieja = normalizar_df(pd.DataFrame([FILA_VIEJA]))
    nueva = normalizar_df(pd.DataFrame([FILA_NUEVA]))
    junto = pd.concat([vieja, nueva], ignore_index=True)
    assert list(junto.columns) == ESQUEMA_SALIDA
    # Lo que fallaba: la fecha caía bajo un encabezado de precio.
    assert junto["Fecha Extracción"].tolist() == ["2026-05-27", "2026-09-08"]
    assert junto["Precio Internet (S/)"].tolist() == [8.90, 8.90]
    assert junto["Vendedor"].tolist() == ["Plaza Vea", "Plaza Vea"]


def test_la_columna_que_no_existia_sale_vacia_no_corrida():
    vieja = normalizar_df(pd.DataFrame([FILA_VIEJA]))
    assert pd.isna(vieja["Precio Unidad (S/)"].iloc[0]), (
        "antes de septiembre ese dato no se capturaba: vacío es la verdad"
    )
    # y lo que viene después NO se movió de lugar
    assert vieja["Precio Normal (S/)"].isna().iloc[0]
    assert vieja["Precio Descuento (S/)"].iloc[0] == 8.90


def test_el_esquema_cubre_todo_el_historico():
    """Si mañana se agrega un campo al histórico, esta prueba lo recuerda."""
    from normalizar import COL_RENAME
    sin_mapear = [c for c in CAMPOS_HISTORICO if COL_RENAME.get(c, c) not in ESQUEMA_SALIDA]
    assert not sin_mapear, (
        f"estos campos del histórico no llegan al esquema de salida: {sin_mapear}"
    )


def test_el_resumen_sigue_funcionando():
    df = normalizar_df(pd.DataFrame([FILA_NUEVA, FILA_VIEJA]))
    r = construir_resumen(df)
    assert "Precio Promedio (S/)" in r.columns
    assert len(r) >= 1


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
