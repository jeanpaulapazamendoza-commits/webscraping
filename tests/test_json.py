"""
Pruebas de construir_json.py, lo que alimenta al frontend.

La propiedad que importa es que el log de cambios sea equivalente a la serie
completa: guarda solo los días en que el precio se movió, así que si la
reconstrucción perdiera algo, el gráfico mentiría sin que nada falle.

    python tests/test_json.py           # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

from historico_io import CAMPOS_HISTORICO  # noqa: E402


def _fila(dia, clave, precio, **extra):
    base = dict.fromkeys(CAMPOS_HISTORICO, "")
    cadena, pid = clave.split("|")
    base.update(supermercado=cadena, producto_id=pid, sku_id=pid,
                nombre=f"Producto {pid}", categoria="pollo", familia="pollo",
                marca="M", url="u",
                precio_descuento="" if precio is None else str(precio),
                precio_regular="" if precio is None else str(precio),
                tiene_descuento="False",
                fecha_extraccion=f"{dia}T10:00:00")
    base.update(extra)
    return base


def _construir(filas):
    """Escribe un histórico de juguete y devuelve los cuatro JSON."""
    import construir_json

    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    with (tmp / "data" / "historico_2026-10.csv").open("w", encoding="utf-8",
                                                      newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)

    previo = os.getcwd()
    try:
        os.chdir(tmp)
        construir_json.construir(Path("web/datos"))
        return {p.stem: json.loads(p.read_text(encoding="utf-8"))
                for p in (tmp / "web" / "datos").glob("*.json")}
    finally:
        os.chdir(previo)


def _reconstruir(puntos, dias):
    """Lo que hace el frontend: arrastrar el último valor hacia adelante."""
    salida, i, actual = {}, 0, None
    for d in dias:
        while i < len(puntos) and puntos[i][0] <= d:
            actual = puntos[i][1]
            i += 1
        if actual is not None:
            salida[d] = actual
    return salida


# ─── la propiedad central ────────────────────────────────────────────────────

def test_el_log_de_cambios_reconstruye_la_serie_completa():
    dias = [f"2026-10-{d:02d}" for d in range(1, 11)]
    precios = [9.9, 9.9, 9.9, 7.5, 7.5, 9.9, 9.9, 8.0, 8.0, 8.0]
    salida = _construir([_fila(d, "wong|1", p) for d, p in zip(dias, precios)])

    serie = salida["series"]["wong|1"]
    assert len(serie) == 4, f"solo 4 cambios de precio, no {len(serie)}: {serie}"
    assert _reconstruir(serie, dias) == dict(zip(dias, precios))


def test_un_precio_que_nunca_cambia_deja_un_solo_punto():
    dias = [f"2026-10-{d:02d}" for d in range(1, 8)]
    salida = _construir([_fila(d, "wong|1", 9.9) for d in dias])
    assert salida["series"]["wong|1"] == [["2026-10-01", 9.9]]
    assert _reconstruir(salida["series"]["wong|1"], dias) == {d: 9.9 for d in dias}


# ─── limpieza del dato ───────────────────────────────────────────────────────

def test_un_precio_cero_no_es_un_precio():
    """VTEX publica Price=0 cuando el producto está sin precio. Tomarlo como
    una ganga de S/ 0 arruinaría cualquier mínimo.

    El día que el precio deja de publicarse entra como un punto nulo, no se
    omite: omitirlo haría que el frontend arrastrara el último precio hacia
    adelante y afirmara un precio que ya no existe.
    """
    salida = _construir([
        _fila("2026-10-01", "wong|1", 9.9),
        _fila("2026-10-02", "wong|1", 0),
    ])
    assert salida["series"]["wong|1"] == [["2026-10-01", 9.9], ["2026-10-02", None]]
    assert salida["indice"]["productos"][0]["precio"] is None


def test_el_corte_de_precio_no_se_arrastra():
    dias = ["2026-10-01", "2026-10-02", "2026-10-03"]
    salida = _construir([
        _fila(dias[0], "wong|1", 9.9),
        _fila(dias[1], "wong|1", 0),
        _fila(dias[2], "wong|1", 0),
    ])
    rec = _reconstruir(salida["series"]["wong|1"], dias)
    assert rec[dias[0]] == 9.9
    # Sin precio el día queda fuera de la serie: en el gráfico es un hueco, no
    # una línea plana arrastrando el último valor conocido.
    assert dias[1] not in rec and dias[2] not in rec


def test_el_mismo_producto_dos_veces_en_un_dia_cuenta_una():
    """La búsqueda por path de VTEX incluye las subcategorías, así que un
    producto puede llegar por dos targets en la misma corrida."""
    salida = _construir([
        _fila("2026-10-01", "wong|1", 9.9, categoria="res"),
        _fila("2026-10-01", "wong|1", 9.9, categoria="cortes-premium"),
    ])
    assert len(salida["indice"]["productos"]) == 1
    assert salida["salud"]["filas"] == 1


def test_sin_dato_de_stock_no_se_inventa_un_false():
    salida = _construir([_fila("2026-10-01", "wong|1", 9.9)])
    assert salida["indice"]["productos"][0]["disponible"] is None

    con = _construir([_fila("2026-10-02", "wong|1", 9.9, disponible="False")])
    assert con["indice"]["productos"][0]["disponible"] is False


# ─── lo que el frontend espera encontrar ─────────────────────────────────────

def test_el_indice_trae_el_ultimo_dia_y_sus_campos():
    salida = _construir([
        _fila("2026-10-01", "wong|1", 9.9),
        _fila("2026-10-02", "wong|1", 7.5),
    ])
    idx = salida["indice"]
    assert idx["dia"] == "2026-10-02"
    p = idx["productos"][0]
    assert p["precio"] == 7.5 and p["cadena"] == "wong" and p["id"] == "wong|1"
    for campo in ("nombre", "categoria", "familia", "marca", "url",
                  "tarjeta", "tarjeta_nombre", "disponible", "unidad"):
        assert campo in p, f"falta {campo} en el índice"


def test_salud_dice_de_cuando_es_el_dato():
    salida = _construir([
        _fila("2026-10-01", "wong|1", 9.9),
        _fila("2026-10-03", "metro|2", 8.0),
    ])
    s = salida["salud"]
    assert s["ultimo_dia"] == "2026-10-03"
    assert s["primer_dia"] == "2026-10-01"
    assert s["dias_con_dato"] == 2
    # por_cadena describe la ÚLTIMA corrida, no el histórico: si una cadena
    # falta ahí, es que esa corrida no la trajo. Esa es la señal que se busca.
    assert set(s["por_cadena"]) == {"metro"}, "el 03 solo trajo metro"


def test_el_resumen_agrega_por_dia_cadena_y_familia():
    salida = _construir([
        _fila("2026-10-01", "wong|1", 10.0),
        _fila("2026-10-01", "wong|2", 20.0),
        _fila("2026-10-01", "metro|3", 5.0),
    ])
    filas = {(r["cadena"], r["familia"]): r for r in salida["resumen"]}
    assert filas[("wong", "pollo")]["n"] == 2
    assert filas[("wong", "pollo")]["mediana"] == 15.0
    assert filas[("metro", "pollo")]["minimo"] == 5.0


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
