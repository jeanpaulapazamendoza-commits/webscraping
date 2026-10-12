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
import streamlit as st
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


def test_las_filas_sin_el_dato_de_stock_no_se_pierden():
    """La trampa del filtro de disponibilidad.

    La columna existe desde octubre de 2026. Si el filtro descartara todo lo que
    no diga True, se llevaría puesto el histórico entero y el dashboard quedaría
    con un mes de datos. Solo se descarta lo marcado explícitamente como agotado.
    """
    at = _base()
    dias_vistos = int(re.match(r"(\d+) día", at.caption[0].value).group(1))
    assert dias_vistos == _esperado()["dias"], (
        f"el dashboard muestra {dias_vistos} días y el histórico tiene "
        f"{_esperado()['dias']}: el filtro se comió las filas sin el dato"
    )


def test_el_filtro_de_stock_sobre_datos_controlados():
    """Las tres situaciones, sobre un histórico de juguete.

    Se usa un fixture porque el histórico real todavía no tiene la columna
    poblada: recién se captura desde la primera corrida con el extractor nuevo.
    """
    import tempfile

    from historico_io import CAMPOS_HISTORICO, append_historico, consolidar

    base = dict.fromkeys(CAMPOS_HISTORICO, "")
    base.update(supermercado="wong", categoria="pollo", familia="pollo",
                marca="M", url="u", precio_internet="10", precio_descuento="10",
                precio_regular="10", tiene_descuento="False")
    filas = [
        {**base, "nombre": "Disponible", "producto_id": "1", "sku_id": "1",
         "disponible": "True", "stock": "5", "fecha_extraccion": "2026-10-12T10:00:00"},
        {**base, "nombre": "Agotado", "producto_id": "2", "sku_id": "2",
         "disponible": "False", "stock": "0", "fecha_extraccion": "2026-10-12T10:00:00"},
        {**base, "nombre": "Sin dato", "producto_id": "3", "sku_id": "3",
         "fecha_extraccion": "2026-10-12T10:00:00"},
    ]

    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "data"))

    app = os.path.join(RAIZ, "app.py")
    previo = os.getcwd()
    try:
        os.chdir(tmp)
        append_historico(filas)
        consolidar()
        # cargar_datos() está cacheada y las otras pruebas ya la llenaron con el
        # histórico real; sin limpiar, el fixture no se llega a leer.
        st.cache_data.clear()
        at = AppTest.from_file(app, default_timeout=TIMEOUT)
        at.run()
        assert not at.exception, at.exception
        por_defecto = int(at.metric[0].value.replace(",", ""))
        assert por_defecto == 2, (
            f"por defecto deberían verse el disponible y el sin dato, no {por_defecto}"
        )
        assert "1 filas sin stock fuera de la vista" in at.caption[0].value

        at.sidebar.checkbox[1].set_value(True).run()
        assert not at.exception
        assert int(at.metric[0].value.replace(",", "")) == 3, "con el interruptor, los tres"
        assert "incluye 1 filas sin stock" in at.caption[0].value
    finally:
        os.chdir(previo)
        st.cache_data.clear()


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
