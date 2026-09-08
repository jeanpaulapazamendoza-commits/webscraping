"""
Pruebas del export a Google Sheets.

No tocan la red ni necesitan credenciales: usan una hoja simulada que registra
lo que se le manda. Cubren el caso que corrompió la hoja Histórico: append_rows()
escribe por POSICIÓN, así que una columna nueva en el medio del esquema corre
todas las siguientes.

    python tests/test_sheets.py         # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import pandas as pd

import export_sheets
from export_sheets import alinear_a_cabecera, append_historico_sheet
from normalizar import normalizar_df

# Cabecera tal como quedó la hoja antes de que el esquema ganara precio_unidad.
CABECERA_VIEJA = [
    "Supermercado", "Categoría", "Familia", "Nombre Producto", "ID Producto",
    "SKU", "Marca", "Precio Efectivo (S/)", "Precio Tarjeta (S/)",
    "Nombre Tarjeta", "Descuento Tarjeta %", "Precio Internet (S/)",
    "Precio Normal (S/)", "Precio Descuento (S/)", "Precio Regular (S/)",
    "Tiene Descuento", "Descuento %", "Fecha Extracción", "URL", "URL Imagen",
    "Vendedor",
]


class HojaFalsa:
    """Worksheet de gspread simulada: guarda lo que le mandan."""

    def __init__(self, cabecera=()):
        self.cabecera = list(cabecera)
        self.filas_agregadas = []
        self.updates = []

    def row_values(self, n):
        return list(self.cabecera) if n == 1 else []

    def append_rows(self, filas, value_input_option=None):
        self.filas_agregadas.extend(filas)

    def update(self, values, range_name=None, value_input_option=None):
        self.updates.append((range_name, values))
        if range_name == "A1":
            self.cabecera = list(values[0])


class LibroFalso:
    def __init__(self, hoja):
        self.hoja = hoja

    def worksheet(self, nombre):
        return self.hoja


FILA = dict(
    supermercado="plazavea", categoria="pollo-entero", familia="pollo",
    nombre="Pollo Entero Fresco con Menudencia x kg", producto_id="9253",
    sku_id="9357", marca="PLAZA VEA", url="u", imagen="i", vendedor="Plaza Vea",
    precio_tarjeta=6.50, nombre_tarjeta="Tarjeta Oh!", tarjeta_descuento_pct=27.0,
    precio_internet=8.90, precio_unidad=19.58, precio_normal=None,
    precio_descuento=8.90, precio_regular=8.90, tiene_descuento=False,
    descuento_pct=None, fecha_extraccion="2026-09-08T10:00:00",
)


def _subir(hoja, filas):
    append_historico_sheet(LibroFalso(hoja), filas)


def test_la_columna_nueva_va_al_final_y_no_corre_las_demas():
    """El caso que rompía: 'Precio Unidad (S/)' entra en la posición 12 del
    esquema, pero la hoja ya tenía 21 columnas fijadas."""
    hoja = HojaFalsa(CABECERA_VIEJA)
    _subir(hoja, [FILA])

    assert hoja.cabecera[:21] == CABECERA_VIEJA, "las columnas viejas no se deben mover"
    assert hoja.cabecera[21] == "Precio Unidad (S/)", "la nueva va al final"

    fila = hoja.filas_agregadas[0]
    assert len(fila) == len(hoja.cabecera) == 22
    por_nombre = dict(zip(hoja.cabecera, fila))
    assert por_nombre["Precio Internet (S/)"] == "8.9"
    assert por_nombre["Precio Unidad (S/)"] == "19.58"
    assert por_nombre["Vendedor"] == "Plaza Vea"
    assert por_nombre["Fecha Extracción"] == "2026-09-08"


def test_sin_columnas_nuevas_no_toca_la_cabecera():
    hoja = HojaFalsa(CABECERA_VIEJA + ["Precio Unidad (S/)"])
    _subir(hoja, [FILA])
    assert hoja.updates == [], "no debería reescribir la fila 1 si no hace falta"
    assert len(hoja.filas_agregadas[0]) == 22


def test_hoja_vacia_escribe_la_cabecera():
    hoja = HojaFalsa([])
    _subir(hoja, [FILA])
    assert hoja.filas_agregadas[0][0] == "Supermercado", "la primera fila es la cabecera"
    assert len(hoja.filas_agregadas) == 2


def test_una_columna_que_la_corrida_no_trae_queda_vacia_no_corrida():
    """Si la hoja tiene una columna que la corrida no trae, esa celda queda
    vacía EN SU LUGAR y no desplaza a las siguientes."""
    df = normalizar_df(pd.DataFrame([FILA])).drop(columns=["Marca"])
    alineado, final = alinear_a_cabecera(df, CABECERA_VIEJA)
    assert final[:21] == CABECERA_VIEJA
    fila = alineado.where(pd.notna(alineado), "").astype(str).values.tolist()[0]
    por_nombre = dict(zip(final, fila))
    assert por_nombre["Marca"] == ""
    assert por_nombre["Vendedor"] == "Plaza Vea", "lo de después no se corrió"


def test_alinear_a_cabecera_conserva_el_orden_existente():
    df = normalizar_df(pd.DataFrame([FILA]))
    alineado, final = alinear_a_cabecera(df, CABECERA_VIEJA)
    assert final[:21] == CABECERA_VIEJA
    assert list(alineado.columns) == final


def test_falla_ruidosamente_si_los_anchos_no_cuadran():
    """Red de seguridad: antes de mandar nada, ancho de fila == ancho de cabecera."""
    hoja = HojaFalsa(CABECERA_VIEJA)
    original = export_sheets.alinear_a_cabecera
    try:
        # simulamos un desalineo: devolvemos una cabecera mas larga que el df
        export_sheets.alinear_a_cabecera = lambda df, cab: (
            df, list(cab) + ["Fantasma", "Fantasma2", "Fantasma3"])
        try:
            _subir(hoja, [FILA])
        except RuntimeError as e:
            assert "no coinciden con la cabecera" in str(e)
        else:
            raise AssertionError("debería haber fallado")
    finally:
        export_sheets.alinear_a_cabecera = original
    assert hoja.filas_agregadas == [], "no debe subir nada si detecta desalineo"


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
