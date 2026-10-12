"""
Pruebas del semáforo.

La distinción que estas pruebas cuidan es entre problema y aviso, porque es lo
que decide si la corrida sale con error:

  · Problema es lo que se le puede reclamar a la corrida y todavía se puede
    arreglar: que el dato esté viejo, que una cadena no traiga nada, que el
    total se caiga contra el día anterior. Eso pone el badge en rojo.
  · Aviso es lo que hay que ver pero ya no se puede arreglar: los días que
    faltan. Los precios de un día perdido no están en ningún lado, así que
    marcarlos en rojo dejaría el semáforo trabado una semana sin que nadie
    pueda hacer nada.

Y la limitación de fondo: este archivo lo escribe la corrida, así que si la
corrida no pasa, nadie lo reescribe y se queda diciendo que todo está bien. Esa
mitad la cubre el badge de GitHub Actions, no esto.

    python tests/test_salud.py          # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import pandas as pd

import salud
from salud import avisos, badge, estado, huecos, problemas, publicar

HOY = date(2026, 10, 12)


def filas(*dias_y_cadenas):
    """Un histórico de juguete: ("2026-10-12", "wong", 3) son 3 productos."""
    out = []
    for dia, cadena, n in dias_y_cadenas:
        for i in range(n):
            out.append({"supermercado": cadena, "producto_id": f"{cadena}{i}",
                        "categoria": "pollo", "familia": "pollo",
                        "fecha_extraccion": f"{dia}T10:00:00"})
    return pd.DataFrame(out)


# ── huecos ───────────────────────────────────────────────────────────────────

def test_sin_huecos_cuando_los_dias_son_seguidos():
    assert huecos(["2026-10-10", "2026-10-11", "2026-10-12"]) == []


def test_un_dia_salteado_aparece():
    assert huecos(["2026-10-10", "2026-10-12"]) == ["2026-10-11"]


def test_los_huecos_solo_se_buscan_entre_el_primero_y_el_ultimo():
    """Antes del primer día no falta nada: ahí no había proyecto todavía."""
    assert huecos(["2026-10-12"]) == []
    assert huecos([]) == []


def test_un_bloque_largo_sale_entero():
    assert len(huecos(["2026-06-04", "2026-06-19"])) == 14


# ── el estado ────────────────────────────────────────────────────────────────

def test_el_estado_describe_la_ultima_foto_y_no_el_historico():
    """por_cadena es de la última corrida: si una cadena falta ahí, es que esa
    corrida no la trajo. Esa es la señal que se busca."""
    est = estado(filas(("2026-10-11", "wong", 5), ("2026-10-11", "metro", 5),
                       ("2026-10-12", "metro", 4)), hoy=HOY)
    assert est["ultimo_dia"] == "2026-10-12"
    assert set(est["por_cadena"]) == {"metro"}
    assert est["productos_ultimo_dia"] == 4
    assert est["productos_dia_anterior"] == 10


def test_el_estado_cuenta_el_atraso_contra_hoy():
    est = estado(filas(("2026-10-09", "wong", 1)), hoy=HOY)
    assert est["atraso_dias"] == 3


def test_el_estado_de_un_historico_vacio_no_revienta():
    est = estado(pd.DataFrame(columns=["supermercado", "producto_id",
                                       "categoria", "fecha_extraccion"]),
                 hoy=HOY)
    assert est["ultimo_dia"] is None
    assert problemas(est, hoy=HOY) == ["no hay histórico todavía"]


# ── problemas contra avisos ──────────────────────────────────────────────────

def test_un_dia_de_atraso_es_normal():
    """La corrida es diaria y el badge se mira a cualquier hora."""
    est = estado(filas(("2026-10-11", "wong", 100)), hoy=HOY)
    assert problemas(est, hoy=HOY) == []


def test_tres_dias_de_atraso_es_un_problema():
    est = estado(filas(("2026-10-09", "wong", 100)), hoy=HOY)
    assert any("atraso" in p for p in problemas(est, hoy=HOY))


def test_un_derrumbe_contra_el_dia_anterior_es_un_problema():
    """La señal de que un extractor se rompió: el catálogo se mueve de a
    decenas por día, no de a cientos."""
    est = estado(filas(("2026-10-11", "wong", 100), ("2026-10-12", "wong", 20)),
                 hoy=HOY)
    assert any("contra" in p for p in problemas(est, hoy=HOY))


def test_una_merma_chica_no_es_un_problema():
    est = estado(filas(("2026-10-11", "wong", 100), ("2026-10-12", "wong", 95)),
                 hoy=HOY)
    assert problemas(est, hoy=HOY) == []


def test_los_dias_faltantes_son_aviso_y_no_problema():
    """El punto de todo esto: que un día faltante se vea. Pero no frena nada,
    porque ya no se puede recuperar."""
    est = estado(filas(("2026-10-10", "wong", 100), ("2026-10-12", "wong", 100)),
                 hoy=HOY)
    assert est["dias_faltantes"] == ["2026-10-11"]
    assert problemas(est, hoy=HOY) == []
    assert len(avisos(est, hoy=HOY)) == 1


def test_el_aviso_distingue_los_huecos_recientes():
    """Un hueco de hace dos semanas se cuenta pero no se destaca: ya no hay
    nada que mirar ahí."""
    # El 01, y después del 03 al 12 seguidos: el único hueco es el 02.
    dias = [("2026-10-01", "wong", 100)]
    dias += [(f"2026-10-{d:02d}", "wong", 100) for d in range(3, 13)]
    est = estado(filas(*dias), hoy=HOY)
    assert est["dias_faltantes"] == ["2026-10-02"]
    texto = avisos(est, hoy=HOY)[0]
    assert "1 día(s)" in texto
    assert "de la última semana" not in texto, "el 02 quedó fuera de la ventana"


# ── el badge ─────────────────────────────────────────────────────────────────

def test_el_badge_tiene_exactamente_lo_que_pide_shields():
    """shields.io valida el esquema: estas cuatro claves y nada más."""
    est = estado(filas(("2026-10-12", "wong", 100)), hoy=HOY)
    b = badge(est, [], [])
    assert set(b) == {"schemaVersion", "label", "message", "color"}
    assert b["schemaVersion"] == 1


def test_el_badge_se_pone_verde_amarillo_y_rojo():
    est = estado(filas(("2026-10-12", "wong", 100)), hoy=HOY)
    assert badge(est, [], [])["color"] == "brightgreen"
    assert badge(est, [], ["falta un día"])["color"] == "yellow"
    assert badge(est, ["wong sin productos"], [])["color"] == "red"


def test_el_badge_dice_cuando_es_el_dato():
    assert "hoy" in badge(estado(filas(("2026-10-12", "wong", 7)), hoy=HOY),
                          [], [])["message"]
    assert "ayer" in badge(estado(filas(("2026-10-11", "wong", 7)), hoy=HOY),
                           [], [])["message"]
    assert "hace 3 días" in badge(estado(filas(("2026-10-09", "wong", 7)),
                                         hoy=HOY), [], [])["message"]


def test_el_badge_sin_historico_no_miente():
    b = badge({"ultimo_dia": None}, [])
    assert b["color"] == "lightgrey" and "sin histórico" in b["message"]


# ── lo que se publica ────────────────────────────────────────────────────────

def test_se_publican_los_dos_archivos_y_son_json_valido():
    est = estado(filas(("2026-10-12", "wong", 100)), hoy=HOY)
    tmp = Path(tempfile.mkdtemp())
    rutas = publicar(est, [], [], destino=tmp)
    assert [r.name for r in rutas] == ["_salud.json", "_badge.json"]
    for r in rutas:
        json.loads(r.read_text(encoding="utf-8"))


def test_el_estado_publicado_lleva_los_problemas_y_los_avisos():
    """Para que el resumen del job y quien abra el archivo vean el motivo, no
    solo el color."""
    est = estado(filas(("2026-10-09", "wong", 100)), hoy=HOY)
    tmp = Path(tempfile.mkdtemp())
    publicar(est, problemas(est, hoy=HOY), avisos(est, hoy=HOY), destino=tmp)
    guardado = json.loads((tmp / "_salud.json").read_text(encoding="utf-8"))
    assert guardado["problemas"], "el atraso tiene que quedar escrito"
    assert "avisos" in guardado


def test_el_historico_de_verdad_tiene_estado():
    """Contra los datos reales del repo, no un fixture: que las columnas que el
    cálculo espera sigan estando."""
    est, fallas, notas = salud.revisar()
    assert est["ultimo_dia"] and est["dias_con_dato"] > 0
    assert est["productos_ultimo_dia"] > 0
    assert isinstance(fallas, list) and isinstance(notas, list)


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
