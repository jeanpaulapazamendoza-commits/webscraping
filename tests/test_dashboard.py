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


def test_la_descarga_no_se_construye_al_cargar():
    """Lo que tumbaba la app en Streamlit Cloud.

    st.download_button exige los bytes en el momento de dibujarse, así que
    tenerlo siempre listo obligaba a armar el histórico completo como CSV en
    CADA carga de página: 832 MB de los 1.147 MB de pico, sobre los 690 MB que
    Streamlit Community Cloud le da a la app. Ahora primero se pide y después
    se descarga.
    """
    at = _base()
    assert not at.get("download_button"), (
        "no debería haber ningún download_button antes de pedir el archivo"
    )
    assert any("Generar" in b.label for b in at.button), (
        "debería ofrecer un botón para generar el archivo"
    )


def test_generar_el_archivo_habilita_la_descarga():
    at = _correr(lambda a: None)
    boton = next(i for i, b in enumerate(at.button) if "Generar" in b.label)
    at.button[boton].click().run()
    assert not at.exception
    assert at.get("download_button"), "tras generar debería aparecer la descarga"


def test_el_rango_por_defecto_no_es_todo_el_historico():
    """Con 120 días el archivo pasa los 130 MB y armarlo no entra en memoria."""
    at = _base()
    desde, hasta = at.date_input[0].value, at.date_input[1].value
    assert (hasta - desde).days <= 30, (
        f"el rango por defecto es de {(hasta - desde).days} días, demasiado"
    )


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
