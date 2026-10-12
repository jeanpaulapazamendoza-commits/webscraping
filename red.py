"""
La política de red compartida por los extractores: timeouts y reintentos.

Hasta ahora cada extractor usaba una `requests.Session()` pelada, así que un
500 de paso o una conexión cortada mataba la extracción entera de esa
categoría. Medido sobre el histórico: 137 extracciones perdidas en 121 días, en
días que por lo demás corrieron bien. Son ~1,5% de las extracciones, que es
justo el perfil de una falla transitoria.

Hay dos capas de reintento y hacen cosas distintas:

  1. Acá, en el transporte: urllib3 repite la MISMA petición cuando el fallo es
     de red o un 5xx, con espera creciente. Es lo que arregla el 500 de paso
     sin que el extractor se entere.
  2. En scrape_all, por target: si la extracción se cae igual —a mitad de la
     paginación, o porque la respuesta no era el JSON esperado— se vuelve a
     correr desde la página 1.

Lo que NO se hace es devolver resultados parciales. Si falla la página 5 de 8,
guardar las primeras 4 registraría 48 productos donde había 78, y los 30 que
faltan parecerían haber salido de catálogo: un dato truncado es peor que uno
ausente, porque el ausente se ve y el truncado no. Así que la página que no se
pudo leer hace fallar el target, y el hueco queda a la vista en el semáforo.

Uso:
    from red import crear_sesion
    session = crear_sesion(HEADERS)
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Cuatro intentos por petición. Con más, una categoría que de verdad ya no
# existe se tarda una eternidad en dar la cara; con menos, una racha de dos
# fallos seguidos ya pierde el target.
INTENTOS = 4

# Espera creciente entre reintentos. urllib3 no espera antes del primero y
# después hace factor * 2^(n-1), así que con factor 1 las esperas son 0, 2 y 4:
# medido, 6 segundos en el peor caso, contra una corrida de ~170 s.
ESPERA = 1.0

# Los 5xx y el 429. Van también los de Cloudflare (520, 522, 524) porque las
# cuatro cadenas están detrás de un CDN y esos códigos los emite el CDN, no la
# tienda. Un 404 no está: si la ruta no existe, repetirla no la va a crear.
CODIGOS_A_REPETIR = (429, 500, 502, 503, 504, 520, 522, 524)

# Un timeout de lectura corto haría fallar categorías grandes que simplemente
# tardan; 30 s es lo que ya usaban los extractores.
TIMEOUT = 30

# Los timeouts de lectura se reintentan UNA vez, no las tres.
#
# No es simetría rota por capricho: un error de conexión o un 503 se resuelven
# casi siempre al segundo intento y cuestan milisegundos, pero un timeout de
# lectura significa que el servidor aceptó la conexión y se quedó colgado, y
# repetirlo cuesta 30 segundos cada vez. Con tres reintentos, una petición
# colgada son 120 s, y el reintento por target de scrape_all lo multiplica por
# tres: seis minutos para un solo target. Con uno, el peor caso se parte al
# medio y lo que de verdad cuelga se descubre rápido.
REINTENTOS_DE_LECTURA = 1

# Techo para el Retry-After que manda el servidor. Ver ReintentoAcotado.
ESPERA_MAXIMA = 30


class ReintentoAcotado(Retry):
    """Retry que no deja que un Retry-After enorme cuelgue la corrida.

    Respetar Retry-After es lo correcto —cuando una cadena pide bajar el ritmo,
    conviene hacerle caso antes de que nos bloquee— pero urllib3 lo obedece sin
    techo. Un 429 con `Retry-After: 3600` dormiría una hora por petición, y el
    job de GitHub Actions se corta a las 6: la corrida entera se perdería
    esperando. Acotarlo deja la cortesía sin el riesgo.
    """

    def get_retry_after(self, response):
        espera = super().get_retry_after(response)
        return None if espera is None else min(espera, ESPERA_MAXIMA)


def crear_sesion(headers: dict | None = None) -> requests.Session:
    """Una sesión que repite sola las fallas transitorias."""
    politica = ReintentoAcotado(
        total=INTENTOS - 1,
        read=REINTENTOS_DE_LECTURA,
        backoff_factor=ESPERA,
        status_forcelist=CODIGOS_A_REPETIR,
        # Solo GET: es lo único que hacemos, y es idempotente. Dejar que
        # repita un POST sería pedir problemas el día que haya uno.
        allowed_methods=frozenset(["GET", "HEAD"]),
        # Si la respuesta trae Retry-After, se respeta en vez de inventar una
        # espera: cuando una cadena nos pide bajar el ritmo, conviene hacerle
        # caso antes de que nos bloquee.
        respect_retry_after_header=True,
        # Agotados los intentos, se levanta la excepción. Sin esto urllib3
        # devolvería la última respuesta fallida como si fuera buena.
        raise_on_status=True,
    )
    sesion = requests.Session()
    adaptador = HTTPAdapter(max_retries=politica)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    if headers:
        sesion.headers.update(headers)
    return sesion
