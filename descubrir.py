"""
Resuelve qué categorías scrapear, contra el árbol que publica cada cadena.

Antes `targets.yaml` era una lista de 77 URLs escritas a mano. Eso tenía dos
costos: dejaba afuera la mayor parte del catálogo (entre el 6% y el 27% de las
categorías de alimentos según la cadena) y duplicaba productos, porque pedía una
categoría y también una hija suya — y la búsqueda por ruta de VTEX ya incluye
todo lo que cuelga debajo.

Acá se declara QUÉ vigilar (`categorias.yaml`) y este script resuelve DÓNDE está
en cada sitio, poda los solapamientos y escribe `targets.lock.yaml`.

El lock existe para que la corrida diaria sea reproducible: el descubrimiento se
corre aparte, no todos los días, así una categoría que aparece o desaparece se ve
como un diff que alguien revisa, en vez de cambiar en silencio qué se está
midiendo.

Uso:
    python descubrir.py              # muestra qué cambiaría
    python descubrir.py --aplicar    # reescribe targets.lock.yaml
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

from red import TIMEOUT, crear_sesion
import yaml

DECLARACION = Path("categorias.yaml")
LOCK = Path("targets.lock.yaml")
TARGETS_VIEJOS = Path("targets.yaml")

HOSTS_VTEX = {
    "plazavea": "www.plazavea.com.pe",
    "wong": "www.wong.pe",
    "metro": "www.metro.pe",
}
TOTTUS_SEMILLA = "https://www.tottus.com.pe/tottus-pe/lista/CATG16919/Carne-de-Pollo"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def slug(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", str(texto).lower())
    sin_tildes = sin_tildes.encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sin_tildes)).strip("-")


# ── árboles ─────────────────────────────────────────────────────────────────

def arbol_vtex(sesion, host: str) -> list:
    """Todos los nodos del catálogo, con su ruta y profundidad."""
    raiz = sesion.get(
        f"https://{host}/api/catalog_system/pub/category/tree/4", timeout=30).json()

    def recorrer(nodos, padre=""):
        salida = []
        for n in nodos:
            s = n["url"].rstrip("/").split("/")[-1]
            ruta = f"{padre}/{s}" if padre else s
            hijos = n.get("children") or []
            salida.append({"ruta": ruta, "nombre": n["name"],
                           "nivel": ruta.count("/") + 1, "hoja": not hijos})
            salida += recorrer(hijos, ruta)
        return salida

    return recorrer(raiz)


def arbol_tottus(sesion) -> list:
    """El menú de navegación, que Tottus incrusta en cualquier página.

    No hay API de categorías; el árbol completo viaja dentro del
    __NEXT_DATA__ de cualquier listado, así que alcanza con una sola petición.
    """
    html = sesion.get(TOTTUS_SEMILLA, headers={"Accept": "text/html"}, timeout=30).text
    datos = json.loads(NEXT_DATA.search(html).group(1))
    menu = datos["props"]["appCtx"]["sisNavigationMenu"]

    salida = []

    def enlace(obj):
        """El (nombre, href) del nodo, o (None, None) si no es uno.

        El menú no es homogéneo: en el primer nivel `item_url` es un objeto
        {title, href}, y a partir del segundo es el href pelado, con el nombre
        en `item_name`. Leer solo la primera forma dejaba fuera el árbol entero
        salvo los 33 departamentos.
        """
        url = obj.get("item_url")
        if isinstance(url, dict):
            return url.get("title") or obj.get("item_name"), url.get("href")
        if isinstance(url, str):
            return obj.get("item_name") or obj.get("title"), url
        return None, None

    def recorrer(obj, titulos):
        if isinstance(obj, dict):
            titulo, href = enlace(obj)
            m = re.search(r"/lista/(CATG\d+)/([^/?#]+)", href or "")
            if m and titulo:
                salida.append({
                    "ruta": f"{m.group(1)}/{m.group(2)}",
                    "nombre": titulo,
                    "nivel": len(titulos) + 1,
                    "padres": list(titulos),
                    "hoja": True,
                })
            nuevos = titulos + [titulo] if titulo else titulos
            for k, v in obj.items():
                if k != "item_url":
                    recorrer(v, nuevos)
        elif isinstance(obj, list):
            for v in obj:
                recorrer(v, titulos)
    recorrer(menu, [])

    # Se devuelven todas las apariciones, repetidas incluidas: el mismo CATG
    # cuelga de varias ramas y cuál es la buena depende de qué departamentos se
    # estén pidiendo, que acá no se saben. Deduplica `resolver`.
    return salida


# ── selección ───────────────────────────────────────────────────────────────

def excluido(nodo: dict, patrones: list) -> bool:
    texto = f"{nodo['ruta']} {nodo['nombre']}".lower()
    return any(p.lower() in texto for p in patrones)


def podar(rutas: list) -> list:
    """Quita los nodos que ya están cubiertos por un ancestro de la lista.

    Es lo que generaba 14.234 filas duplicadas: targets.yaml pedía
    `res-y-otras-carnes` y también `res-y-otras-carnes/cortes-premium`, y la
    búsqueda por ruta de VTEX devuelve la segunda dentro de la primera.
    """
    orden = sorted(rutas, key=lambda r: (r.count("/"), r))
    elegidas = []
    for r in orden:
        if not any(r == e or r.startswith(e + "/") for e in elegidas):
            elegidas.append(r)
    return elegidas


def coincide(texto: str, termino: str) -> bool:
    """Si un término de categorias.yaml aplica a un texto en kebab-case.

    Se compara por palabra y no por subcadena. Con `termino in texto`, "res"
    reconocía a "Packs Ahorradores" por el final de «ahorradores» y dejaba un
    pack de pollo etiquetado como res. Se acepta el prefijo para que un término
    en singular tome el plural — "fruta" reconoce «frutas» —, y los términos
    que ya traen guion ("aves-y-huevos") se buscan completos.
    """
    t = slug(termino)
    if "-" in t:
        return t in texto
    return any(palabra.startswith(t) for palabra in texto.split("-"))


def ambiguo(texto: str, reglas: dict) -> bool:
    """Si un nombre nombra más de una familia y por lo tanto no decide nada.

    "Frutas y Verduras" es el caso: hereda a todo lo que cuelga de él la
    primera de las dos que aparezca en las reglas, que es puro azar del orden.
    """
    cuantas = sum(1 for terminos in reglas.values()
                  if any(coincide(texto, t) for t in terminos))
    return cuantas > 1


def familia_de(slug_cat: str, nombre: str, reglas: dict, heredadas: dict,
               padres=(), departamentos=()) -> str:
    """La familia de una categoría.

    Las que ya estaban en targets.yaml conservan la que tenían asignada a mano;
    las reglas solo deciden sobre las nuevas, para no pisar criterio humano con
    una heurística.

    Se mira también el nombre de los padres: muchas subcategorías no dicen de
    qué son por sí solas — "Cortes Caseros" o "Elaborados" solo se entienden
    colgando de "Carne de Res" o "Carne de Cerdo".

    De los padres se descarta el que sea AMBIGUO, no todo departamento
    declarado. "Frutas y Verduras" nombra dos familias a la vez, así que
    heredar de él convertía a "Ensaladas" en fruta por el solo hecho de que
    `frutas` es la primera regla que ese nombre reconoce. Pero descartar todo
    departamento rompía el caso contrario: con "Chorizos" declarado como rama
    propia, quitarlo dejaba a sus hijos heredando de "Huevos y Fiambres" y los
    volvía pollo. Se descarta por ambiguo, que es el problema real.
    """
    if slug_cat in heredadas:
        return heredadas[slug_cat]
    # Unidos con guion y no con espacio: así todo queda en una sola cadena
    # kebab-case y `coincide` puede partirla en palabras.
    propio = f"{slug_cat}-{slug(nombre)}"
    heredado = "-".join(slug(p) for p in padres
                        if p and not ambiguo(slug(p), reglas))
    # Primero el nombre propio y recién después el del padre: si la categoría
    # se llama "Cerdo" dentro de "Carnes", manda lo que dice ella.
    for texto in (propio, heredado):
        for familia, terminos in reglas.items():
            if any(coincide(texto, t) for t in terminos):
                return familia
    return "otros"


def familias_heredadas() -> dict:
    if not TARGETS_VIEJOS.exists():
        return {}
    viejos = yaml.safe_load(TARGETS_VIEJOS.read_text(encoding="utf-8")) or {}
    return {t["slug"]: t["familia"] for t in viejos.get("targets", [])}


# ── resolución ──────────────────────────────────────────────────────────────

def resolver(declaracion: dict, sesion) -> list:
    excluir = declaracion.get("excluir") or []
    reglas = declaracion.get("familias") or {}
    heredadas = familias_heredadas()
    targets = []

    deps_vtex = (declaracion.get("departamentos") or {}).get("vtex") or []
    nivel_vtex = declaracion.get("nivel_vtex", 2)
    for cadena, host in HOSTS_VTEX.items():
        nodos = arbol_vtex(sesion, host)
        # El nivel se cuenta DESDE el departamento declarado, no desde la raíz
        # del catálogo. Con `carnes-aves-y-pescados`, que está en el nivel 1, y
        # nivel_vtex 2, el objetivo sigue siendo el nivel 2 de siempre. Pero así
        # también se puede declarar una rama profunda como
        # `embutidos-y-fiambres/embutidos/chorizos` y que se pida ella misma, que
        # con un nivel absoluto quedaba fuera por estar en el 3.
        def objetivo(n):
            for d in deps_vtex:
                if n["ruta"] == d or n["ruta"].startswith(d + "/"):
                    return d.count("/") + nivel_vtex
            return None

        bajo = [n for n in nodos
                if objetivo(n) is not None and not excluido(n, excluir)]
        # Un nodo del nivel pedido, o una hoja más arriba: si una rama termina
        # antes, igual hay que pedirla o se pierde entera.
        elegidos = [n for n in bajo
                    if n["nivel"] == objetivo(n)
                    or (n["hoja"] and n["nivel"] < objetivo(n))]
        for ruta in podar([n["ruta"] for n in elegidos]):
            nodo = next(n for n in bajo if n["ruta"] == ruta)
            s = ruta.split("/")[-1]
            targets.append({
                "supermercado": cadena,
                "nivel": nodo["nivel"],
                "familia": familia_de(s, nodo["nombre"], reglas, heredadas,
                                      ruta.split("/")[:-1], deps_vtex),
                "slug": s,
                "url": f"https://{host}/{ruta}",
                "_id": ruta.replace("/", "-"),
            })

    deps_tottus = [slug(d) for d in
                   ((declaracion.get("departamentos") or {}).get("tottus") or [])]
    nodos = arbol_tottus(sesion)
    # Primero el departamento y recién después el deduplicado por CATG. Al
    # revés se perdían categorías enteras: "Cebolla, Tomate, Ajo y Ajíes"
    # también figura bajo "Pescados y Mariscos", y esa aparición es más
    # superficial, así que ganaba y dejaba al nodo fuera de los departamentos
    # pedidos — 16 productos de verdura que no se capturaban ningún día.
    dentro = {}
    for n in nodos:
        if excluido(n, excluir):
            continue
        # El propio nodo cuenta, no solo sus hijos: en Tottus cada nodo lista
        # únicamente sus productos, así que declarar "Chorizos" y quedarse solo
        # con "Especiales" y "Parrilleros" perdería lo que cuelga directo de él.
        padres = [slug(p) for p in n.get("padres", []) if p] + [slug(n["nombre"])]
        if not any(d in padres for d in deps_tottus):
            continue
        cid = n["ruta"].split("/")[0]
        if cid not in dentro or n["nivel"] < dentro[cid]["nivel"]:
            dentro[cid] = n
    for n in dentro.values():
        # Todos los niveles, no uno elegido: en Tottus cada nodo lista solo sus
        # propios productos. Medido sobre "Frutas y Verduras": nivel 2 son 177
        # productos, nivel 3 son 112, y comparten apenas 15 — 274 únicos. Ni el
        # padre cubre al hijo ni al revés, así que quedarse con un nivel pierde
        # el otro. La superposición la resuelve el deduplicado de scrape_all.
        s = slug(n["nombre"])
        targets.append({
            "supermercado": "tottus",
            "nivel": n["nivel"],
            "familia": familia_de(s, n["nombre"], reglas, heredadas,
                                  n.get("padres", []), deps_tottus),
            "slug": s,
            "url": f"https://www.tottus.com.pe/tottus-pe/lista/{n['ruta']}",
            "_padre": slug(n["padres"][-1]) if n.get("padres") else "",
            "_id": n["ruta"].split("/")[0].lower(),
        })

    # Dos categorías distintas pueden llamarse igual bajo padres distintos
    # ("Elaborados" cuelga de res y también de cerdo). Si se dejaran con el
    # mismo slug, la columna `categoria` del histórico mezclaría las dos.
    def agrupar():
        por_slug = {}
        for t in targets:
            por_slug.setdefault((t["supermercado"], t["slug"]), []).append(t)
        return {k: v for k, v in por_slug.items() if len(v) > 1}

    for (_, s), repetidos in agrupar().items():
        for t in repetidos:
            padre = t.get("_padre") or slug(t["url"].split("/")[-2])
            if padre:
                t["slug"] = f"{padre}-{s}"
    # "Cerdo" cuelga de Carnes, de Marca Tottus y de Arma Tu Parrilla: dos de
    # los tres dan el mismo prefijo. Lo que queda repetido se desempata con el
    # ID del nodo, que es único y estable.
    for _, repetidos in agrupar().items():
        for t in repetidos:
            t["slug"] = f"{t['slug']}-{t['_id']}" if t.get("_id") else t["slug"]
    for t in targets:
        t.pop("_padre", None)
        t.pop("_id", None)

    # Los nodos más profundos van primero: scrape_all se queda con la primera
    # aparición de cada producto, así que la categoría que gana es la más fina.
    targets.sort(key=lambda t: (t["supermercado"], -t.get("nivel", 0),
                                t["familia"], t["slug"]))
    return targets


# ── informe ─────────────────────────────────────────────────────────────────

def responde_con_productos(sesion, target: dict) -> bool:
    """Una petición por target, para no proponer rutas que no son listados.

    El menú de Tottus repite categorías con IDs distintos y algunas no
    resuelven a un listado: la página responde 200 pero sin productos. Sin este
    chequeo, el lock proponía 9 rutas así y la corrida diaria las registraba
    como fallos todos los días.
    """
    url = target["url"]
    try:
        if target["supermercado"] == "tottus":
            html = sesion.get(url, headers={"Accept": "text/html"}, timeout=25).text
            m = NEXT_DATA.search(html)
            if not m:
                return False
            pp = json.loads(m.group(1))["props"]["pageProps"]
            return bool(pp.get("pagination", {}).get("count"))
        ruta = "/".join(url.split("/")[3:])
        host = url.split("/")[2]
        r = sesion.get(
            f"https://{host}/api/catalog_system/pub/products/search/{ruta}?_from=0&_to=0",
            timeout=25)
        return r.status_code in (200, 206) and bool(r.json())
    except Exception:
        return False


def comparar(nuevos: list, anteriores: list) -> dict:
    clave = lambda t: (t["supermercado"], t["url"])
    a = {clave(t): t for t in anteriores}
    n = {clave(t): t for t in nuevos}
    return {
        "agregados": [n[k] for k in n.keys() - a.keys()],
        "quitados": [a[k] for k in a.keys() - n.keys()],
    }


def revisar_cambio(nuevos: list, anteriores: list, tolerancia=0.2) -> list:
    """Razones para no reescribir el lock sin que alguien mire.

    Esto corre solo una vez por semana. Si una cadena rehace su menú, o la
    petición del árbol falla a medias, el lock se encogería y la corrida
    diaria dejaría de capturar categorías enteras sin que nada falle: el
    histórico seguiría creciendo, con menos productos, y el hueco recién se
    vería meses después en una serie cortada.
    """
    if not anteriores:
        return []
    antes = Counter(t["supermercado"] for t in anteriores)
    ahora = Counter(t["supermercado"] for t in nuevos)
    problemas = []
    for cadena, n in sorted(antes.items()):
        m = ahora.get(cadena, 0)
        if m == 0:
            problemas.append(f"{cadena} se queda sin ningún target (tenía {n})")
        elif m < n * (1 - tolerancia):
            problemas.append(f"{cadena} baja de {n} a {m} targets "
                             f"({(1 - m / n) * 100:.0f}% menos)")
    return problemas


def cargar_lock() -> list:
    if not LOCK.exists():
        return []
    return (yaml.safe_load(LOCK.read_text(encoding="utf-8")) or {}).get("targets", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="Escribe targets.lock.yaml. Sin esto solo informa.")
    ap.add_argument("--forzar", action="store_true",
                    help="Escribe aunque el lock se encoja. Para cuando el "
                         "recorte es real y ya lo revisaste.")
    args = ap.parse_args()

    declaracion = yaml.safe_load(DECLARACION.read_text(encoding="utf-8"))
    # Con reintentos: una falla de paso acá saca una categoría del lock, y la
    # corrida diaria deja de capturarla sin que nada falle.
    sesion = crear_sesion({"User-Agent": UA, "Accept": "application/json",
                           "Accept-Language": "es-PE,es;q=0.9"})

    targets = resolver(declaracion, sesion)

    print(f"Verificando {len(targets)} rutas…")
    vivos, muertos = [], []
    for t in targets:
        (vivos if responde_con_productos(sesion, t) else muertos).append(t)
    targets = vivos
    if muertos:
        print(f"  {len(muertos)} descartadas por no devolver productos:")
        for t in muertos:
            print(f"    {t['supermercado']:9s} {t['slug']:30s} {t['url']}")
    print()

    cambios = comparar(targets, cargar_lock())

    por_cadena = {}
    for t in targets:
        por_cadena.setdefault(t["supermercado"], []).append(t)
    print(f"{'cadena':12s}{'targets':>9}   familias")
    for cadena in sorted(por_cadena):
        fams = sorted({t["familia"] for t in por_cadena[cadena]})
        print(f"  {cadena:10s}{len(por_cadena[cadena]):>9}   {', '.join(fams)}")
    print(f"\n  TOTAL {len(targets)} targets")

    sin_familia = [t for t in targets if t["familia"] == "otros"]
    if sin_familia:
        print(f"\n  ⚠ {len(sin_familia)} sin familia reconocida "
              f"(quedan como 'otros'; agregá un término a categorias.yaml):")
        for t in sin_familia[:10]:
            print(f"      {t['supermercado']:9s} {t['slug']}")

    if cambios["agregados"] or cambios["quitados"]:
        print(f"\n  Cambios contra el lock actual:")
        for t in sorted(cambios["agregados"], key=lambda x: (x["supermercado"], x["slug"]))[:25]:
            print(f"    + {t['supermercado']:9s} {t['familia']:9s} {t['slug']}")
        if len(cambios["agregados"]) > 25:
            print(f"      … y {len(cambios['agregados']) - 25} más")
        for t in sorted(cambios["quitados"], key=lambda x: (x["supermercado"], x["slug"])):
            print(f"    − {t['supermercado']:9s} {t['familia']:9s} {t['slug']}")
    elif cargar_lock():
        print("\n  Sin cambios contra el lock actual.")

    problemas = revisar_cambio(targets, cargar_lock())
    if problemas:
        print("\n  ⚠ El lock se encoge:")
        for p_ in problemas:
            print(f"      {p_}")
        if args.aplicar and not args.forzar:
            print("\n  No se escribió nada. Si el recorte es real, corré otra "
                  "vez con --forzar.")
            return 1

    if args.aplicar:
        LOCK.write_text(yaml.safe_dump(
            {"generado": datetime.now().isoformat(timespec="seconds"),
             "nota": "Generado por descubrir.py desde categorias.yaml. No editar a mano. "
                     "El orden importa: scrape_all se queda con la primera "
                     "aparición de cada producto y los nodos finos van primero.",
             "targets": targets},
            allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
        print(f"\n  Escrito {LOCK} con {len(targets)} targets.")
    else:
        print("\n  Nada escrito. Volvé a correrlo con --aplicar para que tome efecto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
