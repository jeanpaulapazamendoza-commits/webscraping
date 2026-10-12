"""
Pruebas de las etapas que corren después de la captura.

El dato ya está en el histórico cuando estas etapas arrancan, así que ninguna
puede tirar la corrida. Pero antes tampoco podían avisar: las tres —Sheets, el
JSON del frontend y el XLSX— iban envueltas en un `try/except` que imprimía un
⚠ y seguía de largo. Dos consecuencias:

  · La exportación a Sheets avisaba TODOS LOS DÍAS que no había credenciales,
    con exactamente el mismo aspecto que tendría un error de verdad. "No está
    configurado" y "se rompió" son cosas distintas y ahora se informan
    distinto.
  · Si el JSON del frontend fallaba, la web seguía publicando los datos de
    ayer y nadie se enteraba. Eso ahora hace que la corrida salga con error,
    porque deja un entregable desactualizado.

Y la otra mitad: el JSON se publica entero o no se publica. Antes se escribía
pieza por pieza, así que un fallo en series.json dejaba un índice nuevo con las
series de ayer, y la web mostraba productos sin historia.

    python tests/test_etapas.py         # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import construir_json
import export_sheets
import scrape_all
from scrape_all import clasificar_etapas, correr_etapa


# ── los tres resultados de una etapa ─────────────────────────────────────────

def test_una_etapa_que_sale_bien():
    r = correr_etapa("Etapa", lambda: None, critica=True)
    assert r["estado"] == "ok"


def test_una_etapa_que_falla_guarda_el_error():
    def rota():
        raise ValueError("se rompió el serializador")

    r = correr_etapa("Etapa", rota, critica=True)
    assert r["estado"] == "falló"
    assert "ValueError" in r["error"] and "serializador" in r["error"]
    assert r["critica"] is True


def test_una_etapa_sin_configurar_se_omite_y_no_es_un_fallo():
    """La diferencia que esto vino a hacer: hoy la réplica en Sheets no está
    configurada, y eso no es un error que nadie tenga que arreglar."""
    r = correr_etapa("Etapa", lambda: "falta GOOGLE_SHEET_ID", critica=False)
    assert r["estado"] == "omitido"
    assert r["motivo"] == "falta GOOGLE_SHEET_ID"


# ── qué llega al semáforo ────────────────────────────────────────────────────

def test_el_json_roto_es_un_problema():
    """Deja la web publicando los datos de ayer sin decirlo, así que la corrida
    tiene que salir con error."""
    problemas, avisos = clasificar_etapas([
        {"etapa": "Construyendo el JSON...", "estado": "falló",
         "critica": True, "error": "ValueError: NaN"}])
    assert len(problemas) == 1 and not avisos
    assert "NaN" in problemas[0]


def test_sheets_roto_es_solo_un_aviso():
    """El dato está igual en el repo y en la web, y un error de la API de
    Google se arregla solo en la corrida siguiente."""
    problemas, avisos = clasificar_etapas([
        {"etapa": "Exportando a Google Sheets...", "estado": "falló",
         "critica": False, "error": "APIError: 503"}])
    assert not problemas and len(avisos) == 1


def test_lo_omitido_no_ensucia_ni_problemas_ni_avisos():
    problemas, avisos = clasificar_etapas([
        {"etapa": "Exportando a Google Sheets...", "estado": "omitido",
         "motivo": "no hay credenciales"},
        {"etapa": "Construyendo el JSON...", "estado": "ok"}])
    assert not problemas and not avisos


def test_varias_etapas_rotas_se_informan_todas():
    problemas, avisos = clasificar_etapas([
        {"etapa": "A", "estado": "falló", "critica": True, "error": "x"},
        {"etapa": "B", "estado": "falló", "critica": False, "error": "y"},
        {"etapa": "C", "estado": "ok"}])
    assert len(problemas) == 1 and len(avisos) == 1


# ── Sheets: no configurado contra roto ───────────────────────────────────────

def test_sin_credenciales_levanta_sin_configurar_y_no_un_error_cualquiera():
    """Si fuera un RuntimeError pelado, la corrida no podría distinguirlo de un
    fallo de la API y avisaría todos los días como si algo estuviera mal."""
    for var in ("GOOGLE_CREDS_JSON", "GOOGLE_CREDS_FILE",
                "GOOGLE_OAUTH_CLIENT_FILE"):
        os.environ.pop(var, None)
    previo = os.getcwd()
    try:
        # En un directorio vacío, para que no encuentre un service_account.json
        # del repo de quien corra las pruebas.
        os.chdir(tempfile.mkdtemp())
        try:
            export_sheets._autenticar()
            assert False, "tendría que haber levantado SinConfigurar"
        except export_sheets.SinConfigurar:
            pass
    finally:
        os.chdir(previo)


def test_sin_configurar_sigue_siendo_un_runtime_error():
    """Para no romper a quien ya atrapaba RuntimeError."""
    assert issubclass(export_sheets.SinConfigurar, RuntimeError)


# ── el JSON se publica entero o nada ─────────────────────────────────────────

def _historico_de_juguete(tmp):
    from historico_io import CAMPOS_HISTORICO, append_historico, consolidar

    fila = dict.fromkeys(CAMPOS_HISTORICO, "")
    fila.update(supermercado="wong", categoria="pollo", familia="pollo",
                nombre="Pollo", producto_id="1", sku_id="1",
                precio_descuento="9.90", precio_regular="9.90",
                tiene_descuento="False",
                fecha_extraccion="2026-10-12T10:00:00")
    Path("data").mkdir(exist_ok=True)
    append_historico([fila])
    consolidar()


def test_si_falla_la_serializacion_no_se_publica_nada():
    """Antes se escribía pieza por pieza: un fallo en series.json dejaba un
    índice nuevo con las series de ayer, y la web mostraba productos sin
    historia. Mejor no publicar nada que publicar la mitad."""
    previo = os.getcwd()
    tmp = Path(tempfile.mkdtemp())
    original = construir_json.serializar
    llamadas = {"n": 0}

    def serializar_que_falla_en_la_segunda(datos):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise ValueError("Out of range float values are not JSON compliant")
        return original(datos)

    try:
        os.chdir(tmp)
        _historico_de_juguete(tmp)
        construir_json.serializar = serializar_que_falla_en_la_segunda
        try:
            construir_json.construir(Path("web/datos"))
            assert False, "tendría que haber propagado el error"
        except ValueError:
            pass
        assert not list(Path("web/datos").glob("*.json")) \
            if Path("web/datos").exists() else True, \
            "no debería haber quedado ningún archivo publicado"
    finally:
        construir_json.serializar = original
        os.chdir(previo)


def test_cuando_sale_bien_publica_los_cuatro():
    previo = os.getcwd()
    tmp = Path(tempfile.mkdtemp())
    try:
        os.chdir(tmp)
        _historico_de_juguete(tmp)
        pesos = construir_json.construir(Path("web/datos"))
        assert set(pesos) == {"indice.json", "series.json", "resumen.json",
                              "salud.json"}
        for nombre in pesos:
            json.loads((Path("web/datos") / nombre).read_text(encoding="utf-8"))
    finally:
        os.chdir(previo)


def test_un_nan_en_los_datos_sigue_reventando():
    """La guarda que ya existía: Python escribe NaN sin chistar y JSON.parse
    rechaza el archivo entero."""
    try:
        construir_json.serializar({"x": float("nan")})
        assert False, "tendría que haber levantado ValueError"
    except ValueError:
        pass


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
