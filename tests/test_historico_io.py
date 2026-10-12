"""
Pruebas del almacenamiento: Parquet mensual con un pendiente para la corrida.

Lo que hay que proteger acá es que no se pierda ni se corrompa una fila, que es
el único dato irrecuperable del proyecto: los precios de ayer no se pueden
volver a scrapear.

Las tres decisiones que estas pruebas fijan:

  1. El pendiente es la red de seguridad. append_historico() no escribe la
     partición —Parquet no se amplía, y reescribirlo 74 veces por corrida
     costaba 56 segundos— así que entre el append y el consolidar hay una
     ventana en la que las filas viven solo ahí. cargar_historico() tiene que
     verlas igual, y una corrida que se cae tiene que poder recuperarlas.
  2. El mes sale de la fecha de cada fila, no de la de hoy.
  3. El esquema evoluciona por reindex, no por migración: una partición vieja
     sin las columnas nuevas se lee igual, con esas columnas vacías.

    python tests/test_historico_io.py       # sin instalar nada
    python -m pytest tests/                 # si tenés pytest
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd

import historico_io
from historico_io import (CAMPOS_HISTORICO, append_historico, cargar_historico,
                          consolidar, escribir_particion, leer,
                          listar_fuentes, listar_particiones, particion)


def fila(dia, pid, **extra):
    base = dict.fromkeys(CAMPOS_HISTORICO, "")
    base.update(supermercado="wong", categoria="pollo", familia="pollo",
                nombre=f"Producto {pid}", producto_id=str(pid), sku_id=str(pid),
                precio_descuento="9.90", precio_regular="12.90",
                tiene_descuento="True", fecha_extraccion=f"{dia}T10:00:00")
    base.update(extra)
    return base


def en_un_directorio_limpio(fn):
    """Corre fn() con el cwd en un directorio vacío.

    historico_io trabaja con rutas relativas a data/, así que cambiar de
    directorio es lo que aísla una prueba de la otra y del histórico real.
    """
    def envuelta():
        previo = os.getcwd()
        tmp = tempfile.mkdtemp()
        try:
            os.chdir(tmp)
            Path("data").mkdir()
            return fn()
        finally:
            os.chdir(previo)
    envuelta.__name__ = fn.__name__
    envuelta.__doc__ = fn.__doc__
    return envuelta


# ── el pendiente ─────────────────────────────────────────────────────────────

@en_un_directorio_limpio
def test_lo_recien_agregado_se_ve_antes_de_consolidar():
    """La ventana entre el append y el consolidar no puede ser un agujero: si
    la corrida se cae en el medio, el dato tiene que estar."""
    append_historico([fila("2026-10-12", 1)])
    assert not listar_particiones(), "todavía no hay partición"
    assert len(cargar_historico()) == 1, "pero el dato ya se ve"


@en_un_directorio_limpio
def test_consolidar_deja_la_particion_y_se_lleva_el_pendiente():
    append_historico([fila("2026-10-12", 1), fila("2026-10-12", 2)])
    tocadas = consolidar()
    assert tocadas == [particion("2026-10")]
    assert not historico_io.PENDIENTE.exists()
    assert len(cargar_historico()) == 2


@en_un_directorio_limpio
def test_consolidar_sin_pendiente_no_hace_nada():
    """Se llama al principio de cada corrida, así que tiene que ser inocuo."""
    assert consolidar() == []
    append_historico([fila("2026-10-12", 1)])
    consolidar()
    assert consolidar() == [], "la segunda vez tampoco"
    assert len(cargar_historico()) == 1, "y no duplica lo ya consolidado"


@en_un_directorio_limpio
def test_consolidar_dos_veces_acumula_en_la_misma_particion():
    """Lo que pasa todos los días: la partición del mes ya existe."""
    append_historico([fila("2026-10-11", 1)])
    consolidar()
    append_historico([fila("2026-10-12", 1)])
    consolidar()
    assert len(cargar_historico()) == 2
    assert len(listar_particiones()) == 1


@en_un_directorio_limpio
def test_una_corrida_que_cruza_la_medianoche_va_a_los_dos_meses():
    """El mes sale de la fecha de cada fila, no de la de hoy: una corrida que
    arranca el 31 a las 23:50 escribe en dos meses."""
    append_historico([fila("2026-10-31", 1), fila("2026-11-01", 2)])
    tocadas = consolidar()
    assert tocadas == [particion("2026-10"), particion("2026-11")]
    assert len(cargar_historico()) == 2


@en_un_directorio_limpio
def test_una_fila_sin_fecha_usable_no_se_pierde():
    """Abajo de todo consolidar() borra el pendiente, así que una fila que no
    caiga en ningún grupo desaparecería para siempre."""
    append_historico([fila("2026-10-12", 1), fila("", 2, fecha_extraccion="")])
    consolidar()
    assert len(cargar_historico()) == 2


# ── el esquema ───────────────────────────────────────────────────────────────

@en_un_directorio_limpio
def test_una_particion_vieja_se_lee_con_las_columnas_nuevas_vacias():
    """Así evoluciona el esquema ahora: por reindex al leer y al escribir, sin
    reescribir particiones. Un vacío dice "esto no se capturaba todavía"."""
    vieja = pd.DataFrame([{"supermercado": "wong", "producto_id": "1",
                           "fecha_extraccion": "2026-09-01T10:00:00"}])
    vieja.to_parquet(particion("2026-09"), compression="zstd", index=False)
    df = cargar_historico()
    assert list(df.columns) == CAMPOS_HISTORICO
    assert df["precio_unidad"].isna().all()


@en_un_directorio_limpio
def test_pedir_columnas_que_la_particion_no_tiene_no_revienta():
    """Parquet es columnar y el dashboard aprovecha eso para leer 16 de 25
    columnas. Pedirle una que no existe es un error, no un vacío."""
    pd.DataFrame([{"supermercado": "wong", "producto_id": "1"}]).to_parquet(
        particion("2026-09"), compression="zstd", index=False)
    df = leer(particion("2026-09"), ["supermercado", "precio_unidad"])
    assert list(df.columns) == ["supermercado", "precio_unidad"]
    assert df["precio_unidad"].isna().all()


@en_un_directorio_limpio
def test_escribir_una_particion_fija_el_esquema_y_el_orden():
    escribir_particion(pd.DataFrame([{"producto_id": "1", "supermercado": "wong"}]),
                       particion("2026-10"))
    assert list(leer(particion("2026-10")).columns) == CAMPOS_HISTORICO


@en_un_directorio_limpio
def test_no_queda_ningun_tmp_despues_de_escribir():
    """La escritura es a un .tmp y después replace, para que una caída deje la
    partición anterior entera y no un Parquet truncado."""
    append_historico([fila("2026-10-12", 1)])
    consolidar()
    assert not list(Path("data").glob("*.tmp"))


# ── el histórico completo ────────────────────────────────────────────────────

@en_un_directorio_limpio
def test_sin_nada_devuelve_un_dataframe_vacio_con_el_esquema():
    df = cargar_historico()
    assert df.empty and list(df.columns) == CAMPOS_HISTORICO


@en_un_directorio_limpio
def test_las_fuentes_incluyen_el_pendiente_y_las_particiones_no():
    append_historico([fila("2026-10-11", 1)])
    consolidar()
    append_historico([fila("2026-10-12", 2)])
    assert len(listar_particiones()) == 1
    assert len(listar_fuentes()) == 2
    assert listar_fuentes()[-1] == historico_io.PENDIENTE


@en_un_directorio_limpio
def test_el_pendiente_vacio_no_cuenta_como_fuente():
    historico_io.PENDIENTE.write_text("", encoding="utf-8")
    assert listar_fuentes() == []
    assert consolidar() == []


@en_un_directorio_limpio
def test_el_texto_sobrevive_la_vuelta_completa():
    """Se guarda como texto a propósito: los consumidores parsean desde texto y
    distinguen el vacío ("no se midió") del cero. Un 6.50 que vuelve como 6.5
    rompería esa distinción sin avisar."""
    append_historico([fila("2026-10-12", 1, precio_descuento="6.50",
                           precio_normal="", stock="0")])
    consolidar()
    r = cargar_historico().iloc[0]
    assert r["precio_descuento"] == "6.50"
    assert r["stock"] == "0"
    assert r["precio_normal"] in ("", None) or pd.isna(r["precio_normal"])


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if not nombre.startswith("test_"):
            continue
        try:
            fn()
            print("  OK    %s" % nombre)
        except AssertionError as e:
            fallos += 1
            print("  FALLA %s: %s" % (nombre, e))
    print("\n%d fallo(s)" % fallos)
    sys.exit(1 if fallos else 0)
