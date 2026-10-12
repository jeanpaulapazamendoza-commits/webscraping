"""
Pruebas de correr dos veces el mismo día.

El cron de GitHub Actions es "best effort": encola y corre cuando puede. Medido
sobre 121 días de histórico, contra las 07:00 de Lima que pedía el horario
anterior: mediana 2,1 h de atraso, p75 3,9 h, p90 6,3 h y un máximo de 16,3 h.
Y 18 días no se capturaron nunca.

La respuesta son dos disparos de respaldo, y para que eso sea seguro hay que
poder correr dos veces el mismo día sin estropear nada. Eso es lo que prueban
estas:

  · El deduplicado pasó a ser por día y no por corrida. Antes una segunda
    corrida duplicaba el día entero, que es de dónde salen las filas repetidas
    del histórico.
  · --si-falta no hace nada si el día ya se capturó BIEN. "Ya hay datos de hoy"
    no alcanza: si la corrida anterior se cayó a mitad de camino hay datos de
    hoy y aun así falta la mitad, y ahí el respaldo tiene que correr.

    python tests/test_idempotencia.py   # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import salud
import scrape_all
from historico_io import CAMPOS_HISTORICO, append_historico, consolidar
from scrape_all import (claves_del_dia, clave_producto, dia_ya_capturado,
                        sin_repetidos)

HOY = "2026-10-12"


def fila(dia, pid, cadena="wong"):
    f = dict.fromkeys(CAMPOS_HISTORICO, "")
    f.update(supermercado=cadena, categoria="pollo", familia="pollo",
             nombre=f"Producto {pid}", producto_id=str(pid), sku_id=str(pid),
             url=f"https://x/{pid}", precio_descuento="9.90",
             precio_regular="9.90", tiene_descuento="False",
             fecha_extraccion=f"{dia}T10:00:00")
    return f


def en_un_directorio_limpio(fn):
    def envuelta():
        previo = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            Path("data").mkdir()
            return fn()
        finally:
            os.chdir(previo)
    envuelta.__name__ = fn.__name__
    envuelta.__doc__ = fn.__doc__
    return envuelta


def publicar_estado(problemas=(), fallidos=0):
    """Deja un data/_salud.json como el que escribiría una corrida."""
    datos = {"ultimo_dia": HOY, "problemas": list(problemas),
             "corrida": {"fallidos": fallidos}}
    salud.ARCHIVO_ESTADO.write_text(json.dumps(datos), encoding="utf-8")


# ── el deduplicado por día ───────────────────────────────────────────────────

@en_un_directorio_limpio
def test_las_claves_del_dia_salen_del_historico_y_del_pendiente():
    append_historico([fila(HOY, 1)])
    consolidar()
    append_historico([fila(HOY, 2)])      # sin consolidar, todavía pendiente
    assert len(claves_del_dia(HOY)) == 2


@en_un_directorio_limpio
def test_las_claves_de_otros_dias_no_cuentan():
    """Si contaran, el segundo día no capturaría nada: todos los productos ya
    estarían "vistos" desde ayer."""
    append_historico([fila("2026-10-11", 1), fila(HOY, 2)])
    consolidar()
    assert len(claves_del_dia(HOY)) == 1


@en_un_directorio_limpio
def test_sin_historico_no_hay_claves():
    assert claves_del_dia(HOY) == set()


@en_un_directorio_limpio
def test_la_segunda_corrida_no_duplica_lo_que_ya_capturo_la_primera():
    """El efecto que importa: antes esto duplicaba el día entero."""
    append_historico([fila(HOY, 1), fila(HOY, 2)])
    consolidar()
    # Segunda corrida: trae los dos de antes y uno nuevo.
    vistos = claves_del_dia(HOY)
    traidos = [fila(HOY, 1), fila(HOY, 2), fila(HOY, 3)]
    nuevos = sin_repetidos(traidos, vistos)
    assert [p["producto_id"] for p in nuevos] == ["3"]
    append_historico(nuevos)
    consolidar()
    assert len(claves_del_dia(HOY)) == 3


@en_un_directorio_limpio
def test_la_clave_distingue_cadenas_con_el_mismo_id():
    append_historico([fila(HOY, 1, "wong")])
    consolidar()
    vistos = claves_del_dia(HOY)
    assert sin_repetidos([fila(HOY, 1, "metro")], vistos), \
        "el mismo id en otra cadena es otro producto"


@en_un_directorio_limpio
def test_la_clave_del_historico_coincide_con_la_del_extractor():
    """Si no coincidieran, el deduplicado no reconocería nada de lo ya
    capturado y la segunda corrida duplicaría todo igual que antes."""
    f = fila(HOY, 1)
    append_historico([f])
    consolidar()
    assert clave_producto(f) in claves_del_dia(HOY)


# ── --si-falta ───────────────────────────────────────────────────────────────

@en_un_directorio_limpio
def test_si_no_hay_nada_del_dia_hay_que_correr():
    assert dia_ya_capturado(HOY) is None


@en_un_directorio_limpio
def test_si_el_dia_esta_capturado_y_limpio_no_hay_que_correr():
    append_historico([fila(HOY, 1)])
    consolidar()
    publicar_estado()
    assert dia_ya_capturado(HOY) is not None


@en_un_directorio_limpio
def test_si_la_corrida_anterior_dejo_problemas_hay_que_correr():
    """El caso que "ya hay datos de hoy" no distingue: la corrida se cayó a
    mitad de camino y falta la mitad del día."""
    append_historico([fila(HOY, 1)])
    consolidar()
    publicar_estado(problemas=["wong sin productos"])
    assert dia_ya_capturado(HOY) is None


@en_un_directorio_limpio
def test_si_la_corrida_anterior_tuvo_targets_fallidos_hay_que_correr():
    append_historico([fila(HOY, 1)])
    consolidar()
    publicar_estado(fallidos=3)
    assert dia_ya_capturado(HOY) is None


@en_un_directorio_limpio
def test_con_datos_pero_sin_estado_publicado_se_considera_capturado():
    """Caso raro —datos sin su _salud.json— pero hay que decidir algo: se
    confía en el dato, que es lo que no se puede recuperar."""
    append_historico([fila(HOY, 1)])
    consolidar()
    assert dia_ya_capturado(HOY) is not None


@en_un_directorio_limpio
def test_un_estado_corrupto_no_tira_la_corrida():
    append_historico([fila(HOY, 1)])
    consolidar()
    salud.ARCHIVO_ESTADO.write_text("{esto no es json", encoding="utf-8")
    assert dia_ya_capturado(HOY) is not None


@en_un_directorio_limpio
def test_datos_de_ayer_no_cuentan_como_el_dia_de_hoy():
    append_historico([fila("2026-10-11", 1)])
    consolidar()
    publicar_estado()
    assert dia_ya_capturado(HOY) is None


# ── el horario declarado ─────────────────────────────────────────────────────

def test_el_workflow_tiene_respaldos_y_ninguno_en_la_hora_en_punto():
    """Lo que el cron de GitHub castiga es el minuto 0, cuando se junta la cola
    de todo el mundo."""
    import yaml

    d = yaml.safe_load((RAIZ / ".github/workflows/scrape.yml")
                       .read_text(encoding="utf-8"))
    disparadores = d[True] if True in d else d["on"]
    crons = [c["cron"] for c in disparadores["schedule"]]
    assert len(crons) >= 2, "hace falta al menos un respaldo"
    for c in crons:
        assert c.split()[0] != "0", f"{c} cae en la hora en punto"


def test_el_workflow_corre_con_si_falta_solo_en_los_disparos_programados():
    """A mano tiene que correr siempre: si alguien lo aprieta, es porque quiere
    que corra."""
    texto = (RAIZ / ".github/workflows/scrape.yml").read_text(encoding="utf-8")
    assert "--si-falta" in texto
    assert "github.event_name == 'schedule'" in texto


def test_el_workflow_no_deja_correr_dos_a_la_vez():
    """Dos corridas simultáneas se pisarían el pendiente."""
    import yaml

    d = yaml.safe_load((RAIZ / ".github/workflows/scrape.yml")
                       .read_text(encoding="utf-8"))
    assert d["concurrency"]["cancel-in-progress"] is False


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
