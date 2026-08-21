"""
Pruebas de humo del dashboard.

Corren app.py entera con AppTest, el harness oficial de Streamlit, sin levantar
ningún servidor. Cubren los escenarios que ya rompieron la app alguna vez, para
que no vuelvan en silencio.

    python tests/test_dashboard.py      # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)                       # app.py lee data/ con rutas relativas
sys.path.insert(0, str(RAIZ))

import pandas as pd
from streamlit.testing.v1 import AppTest

from historico_io import cargar_historico

TIMEOUT = 300


def _correr(ajustes=None):
    """Corre la app, opcionalmente tocando widgets del sidebar, y la devuelve."""
    at = AppTest.from_file("app.py", default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, f"la app falló al arrancar: {at.exception[0]}"
    if ajustes:
        ajustes(at)
        at.run()
    return at


_BASE = None


def _base():
    """Corrida sin tocar filtros. Se reusa: cargar el histórico cuesta ~20s y
    varias pruebas miran la misma pantalla inicial."""
    global _BASE
    if _BASE is None:
        _BASE = _correr()
    return _BASE


def _esperado():
    """Lo que el snapshot debería mostrar, calculado aparte de la app."""
    df = cargar_historico()
    df["ts"] = pd.to_datetime(df["fecha_extraccion"])
    ultimo = df["ts"].dt.date.max()
    del_dia = df[df["ts"].dt.date == ultimo]
    return {
        "dias": df["ts"].dt.date.nunique(),
        "productos": len(del_dia.groupby(["supermercado", "producto_id"])),
    }


def test_arranca_sin_excepciones():
    at = _base()
    assert at.title[0].value.endswith("Precios Supermercados Perú")
    assert len(at.tabs) == 5


def test_sin_supermercados_avisa_en_vez_de_romper():
    """B1: deseleccionar todo dejaba dff vacío y el .max() tiraba NaTType."""
    at = _correr(lambda a: a.sidebar.multiselect[0].set_value([]))
    assert not at.exception
    assert at.warning, "debería avisar que no hay datos con esos filtros"
    assert not at.metric, "no debería intentar calcular KPIs sin filas"


def test_sin_familias_avisa_en_vez_de_romper():
    at = _correr(lambda a: a.sidebar.multiselect[1].set_value([]))
    assert not at.exception
    assert at.warning


def test_snapshot_es_del_ultimo_dia():
    """B3: antes arrastraba la última fila de cada producto visto alguna vez."""
    at = _base()
    esperado = _esperado()
    assert int(at.metric[0].value.replace(",", "")) == esperado["productos"]


def test_el_contador_cuenta_dias_no_timestamps():
    """B4: nunique() sobre el timestamp daba miles de 'snapshots'."""
    at = _base()
    dias = int(re.match(r"(\d+) día", at.caption[0].value).group(1))
    assert dias == _esperado()["dias"]


def test_precio_minimo_no_es_cero():
    """VTEX publica Price=0 sin stock; cero no es un precio."""
    at = _base()
    assert float(at.metric[2].value) > 0


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
