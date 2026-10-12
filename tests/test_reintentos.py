"""
Pruebas de los reintentos y de las guardas contra el dato truncado.

Hasta ahora los extractores usaban una `requests.Session()` pelada: un 500 de
paso o una conexión cortada perdía la extracción entera de esa categoría.
Medido sobre el histórico: 137 extracciones perdidas en 121 días que por lo
demás corrieron bien, ~1,5% del total, que es el perfil de una falla
transitoria.

Hay dos capas de reintento y estas pruebas cubren las dos, más la decisión de
no guardar resultados parciales:

  1. red.py repite la misma petición cuando el fallo es de red o un 5xx. Se
     prueba contra un servidor local de verdad, no mirando la configuración:
     una política bien escrita pero montada solo en https tampoco sirve, y eso
     no se ve leyendo el objeto Retry.
  2. scrape_all reintenta el target completo, para lo que sobrevive a la capa
     de abajo: una caída a mitad de la paginación, o una categoría que
     devolvió menos de lo que declaraba.
  3. Un target que falla NO guarda lo que alcanzó a leer. Antes el extractor de
     Tottus atrapaba el error y cortaba el bucle, así que un fallo en la página
     3 de 5 devolvía dos páginas y la corrida informaba éxito: los productos
     que faltaban parecían haber salido de catálogo.

    python tests/test_reintentos.py     # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import contextlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import requests

import red
import scrape_all
import tottus_extractor
import vtex_extractor
from red import crear_sesion


# ── un servidor que falla a pedido ───────────────────────────────────────────

class Servidor:
    """Un HTTP local que responde la secuencia de códigos que se le pida.

    Cuenta las peticiones que recibe, que es lo que hay que medir: si la
    sesión reintenta, el servidor ve más de una.
    """

    def __init__(self, codigos):
        self.codigos = list(codigos)
        self.recibidas = 0
        prueba = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                prueba.recibidas += 1
                i = min(prueba.recibidas - 1, len(prueba.codigos) - 1)
                codigo = prueba.codigos[i]
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def sin_esperas(fn):
    """Corre fn() con las esperas de los reintentos en cero.

    Las esperas son reales en producción —1, 2 y 4 segundos— y no hace falta
    pagarlas en una prueba: lo que se verifica es que reintente, no cuánto
    duerme.
    """
    def envuelta():
        antes_red = red.ESPERA
        antes_scrape = scrape_all.ESPERA_ENTRE_INTENTOS
        red.ESPERA = 0
        scrape_all.ESPERA_ENTRE_INTENTOS = 0
        try:
            return fn()
        finally:
            red.ESPERA = antes_red
            scrape_all.ESPERA_ENTRE_INTENTOS = antes_scrape
    envuelta.__name__ = fn.__name__
    envuelta.__doc__ = fn.__doc__
    return envuelta


# ── capa 1: la sesión ────────────────────────────────────────────────────────

@sin_esperas
def test_un_500_de_paso_se_reintenta_y_sale_bien():
    """El caso que perdía 137 extracciones."""
    with Servidor([503, 503, 200]) as s:
        r = crear_sesion().get(s.url, timeout=5)
        assert r.status_code == 200
        assert s.recibidas == 3, f"esperaba 3 peticiones, hubo {s.recibidas}"


@sin_esperas
def test_un_500_permanente_termina_fallando():
    """Reintentar no puede volverse insistir para siempre: si la cadena está
    caída, el target tiene que fallar y el hueco quedar a la vista."""
    with Servidor([503]) as s:
        try:
            crear_sesion().get(s.url, timeout=5)
            assert False, "tendría que haber levantado excepción"
        except requests.exceptions.RetryError:
            pass
        assert s.recibidas == red.INTENTOS, s.recibidas


@sin_esperas
def test_un_404_no_se_reintenta():
    """Si la ruta no existe, repetirla no la va a crear. Reintentar un 404 son
    tres peticiones de más por cada categoría que salió del catálogo."""
    with Servidor([404]) as s:
        r = crear_sesion().get(s.url, timeout=5)
        assert r.status_code == 404
        assert s.recibidas == 1, s.recibidas


@sin_esperas
def test_el_200_de_primera_no_reintenta_nada():
    with Servidor([200]) as s:
        assert crear_sesion().get(s.url, timeout=5).status_code == 200
        assert s.recibidas == 1


def test_la_politica_esta_montada_en_los_dos_esquemas():
    """Montarla solo en https funcionaría en producción y dejaría las pruebas
    sin cobertura; montarla solo en http, al revés. Van las dos."""
    s = crear_sesion()
    for esquema in ("https://", "http://"):
        assert s.get_adapter(esquema).max_retries.total == red.INTENTOS - 1


def test_los_headers_se_aplican():
    assert crear_sesion({"User-Agent": "x"}).headers["User-Agent"] == "x"


@sin_esperas
def test_un_503_sigue_teniendo_los_tres_reintentos():
    """Acotar los timeouts de lectura no puede acotar también los 5xx, que son
    el caso que esto vino a arreglar."""
    with Servidor([503, 503, 503, 200]) as s:
        assert crear_sesion().get(s.url, timeout=5).status_code == 200
        assert s.recibidas == red.INTENTOS


def test_un_timeout_de_lectura_se_reintenta_una_sola_vez():
    """Repetir un cuelgue cuesta 30 s cada vez y casi nunca ayuda: con tres
    reintentos, una petición colgada son 120 s, y el reintento por target lo
    multiplica por tres."""
    politica = crear_sesion().get_adapter("https://").max_retries
    assert politica.read == red.REINTENTOS_DE_LECTURA
    assert politica.read < politica.total, "tiene que ser más estricto que el total"


def respuesta_con(retry_after=None):
    r = requests.Response()
    if retry_after is not None:
        r.headers["Retry-After"] = retry_after
    return r


def test_un_retry_after_enorme_se_acota():
    """urllib3 obedece el Retry-After sin techo: un `Retry-After: 3600`
    dormiría una hora por petición, y el job de Actions se corta a las 6."""
    politica = crear_sesion().get_adapter("https://").max_retries
    assert politica.get_retry_after(respuesta_con("3600")) == red.ESPERA_MAXIMA


def test_un_retry_after_razonable_se_respeta_tal_cual():
    """Acotar no es ignorar: si la cadena pide 5 segundos, se le dan 5."""
    politica = crear_sesion().get_adapter("https://").max_retries
    assert politica.get_retry_after(respuesta_con("5")) == 5


def test_sin_retry_after_no_inventa_una_espera():
    """Sin la cabecera manda el backoff, no un número fijo."""
    politica = crear_sesion().get_adapter("https://").max_retries
    assert politica.get_retry_after(respuesta_con()) is None


# ── capa 2: el target completo ───────────────────────────────────────────────

def extractor_que_falla(veces, resultado=None):
    """Un extractor que levanta excepción las primeras `veces` llamadas."""
    estado = {"llamadas": 0}

    def extractor(*a, **k):
        estado["llamadas"] += 1
        if estado["llamadas"] <= veces:
            raise RuntimeError("caída a mitad de la paginación")
        return list(resultado if resultado is not None else [{"producto_id": "1"}])

    extractor.estado = estado
    return extractor


@contextlib.contextmanager
def con_extractor_falso(extractor):
    """Reemplaza el extractor de wong por uno de prueba."""
    original = scrape_all.EXTRACTORES["wong"]
    scrape_all.EXTRACTORES["wong"] = extractor
    try:
        yield
    finally:
        scrape_all.EXTRACTORES["wong"] = original


TARGET = {"supermercado": "wong", "slug": "verduras", "familia": "verduras",
          "url": "https://www.wong.pe/frutas-y-verduras/verduras"}


@sin_esperas
def test_el_target_se_reintenta_desde_cero():
    """Lo que la capa de red no puede arreglar: si la extracción se cayó en la
    página 5, no alcanza con repetir una petición, hay que empezar de nuevo."""
    ext = extractor_que_falla(1)
    with con_extractor_falso(ext):
        productos, intentos = scrape_all.correr_con_reintentos(TARGET, delay=0)
    assert intentos == 2, f"salió al intento {intentos}"
    assert len(productos) == 1
    assert ext.estado["llamadas"] == 2


@sin_esperas
def test_un_target_roto_de_verdad_falla_despues_de_los_intentos():
    ext = extractor_que_falla(99)
    with con_extractor_falso(ext):
        try:
            scrape_all.correr_con_reintentos(TARGET, delay=0)
            assert False, "tendría que haber levantado excepción"
        except RuntimeError:
            pass
    assert ext.estado["llamadas"] == scrape_all.INTENTOS_POR_TARGET


@sin_esperas
def test_el_que_sale_de_primera_informa_un_intento():
    """Para que la corrida pueda contar cuántos targets necesitaron ayuda."""
    with con_extractor_falso(extractor_que_falla(0)):
        _, intentos = scrape_all.correr_con_reintentos(TARGET, delay=0)
    assert intentos == 1


# ── capa 3: nada de resultados parciales ─────────────────────────────────────

def test_tottus_falla_si_obtuvo_menos_de_lo_que_la_categoria_declaraba():
    """La guarda contra el truncamiento silencioso. Antes de esto, un fallo en
    la página 3 de 5 devolvía dos páginas y la corrida informaba éxito."""
    paginas = {
        1: {"props": {"pageProps": {
            "pagination": {"count": 100, "perPage": 48},
            "results": [{"productId": str(i), "displayName": f"P{i}",
                         "prices": []} for i in range(48)]}}},
    }

    def fetch_falso(url, session, page=1):
        if page not in paginas:
            raise RuntimeError("la página 2 no respondió")
        return paginas[page]

    original = tottus_extractor.fetch_next_data
    tottus_extractor.fetch_next_data = fetch_falso
    try:
        tottus_extractor.extraer("https://www.tottus.com.pe/tottus-pe/lista/CATG1/Verduras", delay=0)
        assert False, "tendría que haber fallado en vez de devolver 48 de 100"
    except RuntimeError as e:
        assert "no respondió" in str(e) or "de los 100" in str(e), e
    finally:
        tottus_extractor.fetch_next_data = original


def test_tottus_tolera_que_el_catalogo_se_mueva_mientras_pagina():
    """El declarado y el obtenido difieren de a uno: medido, en 2 de las 5
    categorías de varias páginas. Un margen estrecho fallaría todos los días."""
    def fetch_falso(url, session, page=1):
        # Declara 50 y entrega 49: un producto se dio de baja en el medio.
        if page == 1:
            return {"props": {"pageProps": {
                "pagination": {"count": 50, "perPage": 48},
                "results": [{"productId": str(i), "displayName": f"P{i}",
                             "prices": []} for i in range(48)]}}}
        return {"props": {"pageProps": {
            "results": [{"productId": "48", "displayName": "P48", "prices": []}]}}}

    original = tottus_extractor.fetch_next_data
    tottus_extractor.fetch_next_data = fetch_falso
    try:
        assert len(tottus_extractor.extraer("https://www.tottus.com.pe/tottus-pe/lista/CATG1/Verduras", delay=0)) == 49
    finally:
        tottus_extractor.fetch_next_data = original


def test_vtex_falla_si_la_categoria_no_entra_en_el_techo_de_paginas():
    """Cortar en silencio a las 20 páginas registraría una categoría truncada,
    que parece una baja masiva de catálogo. Hoy la más grande usa 8."""
    llena = [{"productId": str(i), "productName": f"P{i}", "items": [
        {"itemId": str(i), "sellers": [{"sellerDefault": True, "sellerName": "s",
         "commertialOffer": {"Price": 10, "ListPrice": 10, "IsAvailable": True}}]}],
        "categories": ["/Frutas y Verduras/Verduras/"]}
        for i in range(vtex_extractor.PAGE_SIZE)]

    original = vtex_extractor.fetch_pagina
    vtex_extractor.fetch_pagina = lambda *a, **k: list(llena)
    try:
        vtex_extractor.extraer("https://www.wong.pe/a/b", "wong", "verduras",
                               delay=0, max_paginas=3)
        assert False, "tendría que haber fallado en vez de truncar"
    except RuntimeError as e:
        assert "no entra en 3 páginas" in str(e), e
    finally:
        vtex_extractor.fetch_pagina = original


def test_vtex_termina_normal_cuando_la_ultima_pagina_viene_corta():
    """El caso de todos los días: no hay que confundirlo con el truncamiento."""
    def paginas(host, path, page, session):
        if page == 0:
            return [{"productId": str(i), "productName": f"P{i}", "items": [
                {"itemId": str(i), "sellers": [{"sellerDefault": True,
                 "sellerName": "s", "commertialOffer": {
                     "Price": 10, "ListPrice": 10, "IsAvailable": True}}]}],
                "categories": ["/Frutas y Verduras/Verduras/"]}
                for i in range(3)]
        return []

    original = vtex_extractor.fetch_pagina
    vtex_extractor.fetch_pagina = paginas
    try:
        r = vtex_extractor.extraer("https://www.wong.pe/a/b", "wong", "verduras",
                                   delay=0, max_paginas=3)
        assert len(r) == 3
    finally:
        vtex_extractor.fetch_pagina = original


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
