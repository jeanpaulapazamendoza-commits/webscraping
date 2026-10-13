"""
Pruebas de descubrir.py: cómo se eligen las categorías a vigilar.

Todo corre sin red. Los árboles de cada cadena se inyectan a mano, con la
forma exacta que tienen los reales, porque lo que hay que probar no es que la
petición funcione sino las tres decisiones que se tomaron midiendo:

  1. VTEX se pide por nivel, porque la búsqueda por ruta ya incluye todo lo que
     cuelga debajo. Tottus se pide completo, porque cada nodo lista solo lo
     suyo: "Frutas Cítricas" tiene 7 productos y su hija "Fresas y Arándanos"
     12, ninguno de los cuales aparece en la madre.
  2. El mismo CATG cuelga de varias ramas, así que se filtra por departamento
     ANTES de deduplicar. Al revés, "Cebolla, Tomate, Ajo y Ajíes" perdía
     contra su propia aparición bajo "Pescados y Mariscos", y con ella 16
     productos de verdura que no se capturaban ningún día.
  3. Las reglas de familia se comparan por palabra. Con subcadenas, "res"
     reconocía a "Packs Ahorradores" y dejaba un pack de pollo como res.

    python tests/test_descubrir.py      # sin instalar nada
    python -m pytest tests/             # si tenés pytest
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

import yaml

import descubrir
from descubrir import (ambiguo, coincide, excluido, familia_de, podar,
                       resolver, revisar_cambio)
from scrape_all import sin_repetidos

REGLAS = yaml.safe_load((RAIZ / "categorias.yaml").read_text(encoding="utf-8"))["familias"]


# ── el árbol de prueba ───────────────────────────────────────────────────────

def nodo_vtex(ruta, nombre, hoja=False):
    return {"ruta": ruta, "nombre": nombre, "nivel": ruta.count("/") + 1, "hoja": hoja}


ARBOL_VTEX = [
    nodo_vtex("carnes-aves-y-pescados", "Carnes, Aves y Pescados"),
    nodo_vtex("carnes-aves-y-pescados/pollo", "Pollo"),
    nodo_vtex("carnes-aves-y-pescados/pollo/pollo-entero", "Pollo Entero", hoja=True),
    nodo_vtex("carnes-aves-y-pescados/res", "Res", hoja=True),
    nodo_vtex("frutas-y-verduras", "Frutas y Verduras"),
    nodo_vtex("frutas-y-verduras/ensaladas", "Ensaladas", hoja=True),
    nodo_vtex("mascotas", "Mascotas"),
    nodo_vtex("mascotas/perros", "Perros", hoja=True),
    nodo_vtex("abarrotes", "Abarrotes", hoja=True),
    # Una rama profunda, como los chorizos: cuelgan de fiambres y no de carnes.
    nodo_vtex("quesos-y-fiambres", "Quesos y Fiambres"),
    nodo_vtex("quesos-y-fiambres/embutidos", "Embutidos"),
    nodo_vtex("quesos-y-fiambres/embutidos/chorizos", "Chorizos", hoja=True),
    nodo_vtex("quesos-y-fiambres/embutidos/salchichas", "Salchichas", hoja=True),
    nodo_vtex("quesos-y-fiambres/quesos-duros", "Quesos Duros", hoja=True),
]


def nodo_tottus(cid, nombre, padres):
    return {"ruta": cid + "/" + nombre.replace(" ", "-"), "nombre": nombre,
            "nivel": len(padres) + 1, "padres": padres}


ARBOL_TOTTUS = [
    nodo_tottus("CATG1", "Pollo", ["Carnes"]),
    nodo_tottus("CATG2", "Packs Ahorradores", ["Carnes", "Pollo"]),
    nodo_tottus("CATG3", "Frutas Cítricas", ["Frutas y Verduras"]),
    nodo_tottus("CATG4", "Fresas y Arándanos", ["Frutas y Verduras", "Frutas Cítricas"]),
    nodo_tottus("CATG5", "Ensaladas", ["Frutas y Verduras"]),
    # El mismo CATG en dos ramas: la aparición de afuera es más superficial.
    nodo_tottus("CATG6", "Cebolla, Tomate, Ajo y Ajíes", ["Pescados y Mariscos"]),
    nodo_tottus("CATG6", "Cebolla, Tomate, Ajo y Ajíes",
                ["Frutas y Verduras", "Mundo Hortalizas"]),
    # Dos categorías distintas con el mismo nombre, bajo padres distintos.
    nodo_tottus("CATG7", "Elaborados", ["Carnes", "Pollo"]),
    nodo_tottus("CATG8", "Elaborados", ["Carnes", "Cerdo"]),
    nodo_tottus("CATG9", "Bazar", ["Carnes"]),
    nodo_tottus("CATG10", "Galletas", ["Abarrotes"]),
    # "Chorizos" cuelga de un departamento que no se pide, y tiene productos
    # propios además de los de sus hijos.
    nodo_tottus("CATG11", "Chorizos", ["Huevos y Fiambres"]),
    nodo_tottus("CATG12", "Parrilleros", ["Huevos y Fiambres", "Chorizos"]),
]

DECLARACION = {
    "departamentos": {"vtex": ["carnes-aves-y-pescados", "frutas-y-verduras",
                               "quesos-y-fiambres/embutidos/chorizos"],
                      "tottus": ["Carnes", "Frutas y Verduras", "Chorizos"]},
    "nivel_vtex": 2,
    "excluir": ["mascotas", "bazar"],
    "familias": REGLAS,
}


def resolver_con_arbol(declaracion=None, heredadas=None):
    """resolver() contra los árboles de prueba, sin red y sin targets.yaml."""
    originales = (descubrir.arbol_vtex, descubrir.arbol_tottus,
                  descubrir.familias_heredadas)
    descubrir.arbol_vtex = lambda sesion, host: list(ARBOL_VTEX)
    descubrir.arbol_tottus = lambda sesion: list(ARBOL_TOTTUS)
    descubrir.familias_heredadas = lambda: dict(heredadas or {})
    try:
        return resolver(declaracion or DECLARACION, sesion=None)
    finally:
        (descubrir.arbol_vtex, descubrir.arbol_tottus,
         descubrir.familias_heredadas) = originales


# ── coincidencia de términos ─────────────────────────────────────────────────

def test_un_termino_no_coincide_por_el_final_de_otra_palabra():
    """El caso que estaba mal: «ahorradores» termina en "res"."""
    assert not coincide("packs-ahorradores", "res")
    assert coincide("packs-ahorradores-pollo", "pollo")


def test_un_termino_en_singular_reconoce_el_plural():
    assert coincide("frutas-citricas", "fruta")
    assert coincide("manzanas-peras-uvas-y-membrillos", "manzana")
    assert coincide("ajies", "aji")


def test_un_termino_con_guion_se_busca_completo():
    assert coincide("aves-y-huevos-frescos", "aves-y-huevos")
    assert not coincide("carnes-aves-y-pescados", "aves-y-huevos")


# ── familias ─────────────────────────────────────────────────────────────────

def test_la_familia_puesta_a_mano_le_gana_a_las_reglas():
    """Las categorías que ya estaban en targets.yaml conservan su familia: una
    heurística no debe pisar criterio humano."""
    assert familia_de("jugos", "Jugos", REGLAS, {"jugos": "frutas"}) == "frutas"


def test_la_familia_se_hereda_del_padre_cuando_el_nombre_no_dice_nada():
    """"Cortes Caseros" solo se entiende colgando de "Pavo"."""
    assert familia_de("cortes-caseros", "Cortes Caseros", REGLAS, {},
                      ["Carnes", "Pavo"], ["carnes"]) == "pavo"


def test_el_nombre_propio_le_gana_al_del_padre():
    assert familia_de("cerdo", "Cerdo", REGLAS, {},
                      ["Carnes", "Arma Tu Parrilla"], ["carnes"]) == "cerdo"


def test_el_departamento_declarado_no_decide_la_familia():
    """Sin esto "Ensaladas" heredaba de "Frutas y Verduras" y quedaba como
    fruta, porque `frutas` es la primera regla que ese nombre reconoce."""
    deps = ["carnes", "frutas-y-verduras"]
    assert familia_de("ensaladas", "Ensaladas", REGLAS, {},
                      ["Frutas y Verduras"], deps) == "verduras"


def test_lo_que_ninguna_regla_reconoce_queda_como_otros():
    """Mejor un "otros" visible en el informe que una familia inventada."""
    assert familia_de("jugos", "Jugos", REGLAS, {},
                      ["Frutas y Verduras"], ["frutas-y-verduras"]) == "otros"


# ── poda y exclusiones ───────────────────────────────────────────────────────

def test_podar_quita_lo_que_ya_cubre_un_ancestro():
    """En VTEX pedir la madre ya trae a la hija: pedir las dos duplicaba."""
    assert podar(["a/b", "a", "a/b/c", "d"]) == ["a", "d"]


def test_podar_no_confunde_un_prefijo_con_un_ancestro():
    assert podar(["carne", "carnes-frias"]) == ["carne", "carnes-frias"]


def test_excluir_vale_por_slug_y_por_nombre():
    assert excluido({"ruta": "mascotas/perros", "nombre": "Perros"}, ["mascotas"])
    assert excluido({"ruta": "CATG9/Bazar", "nombre": "Bazar"}, ["bazar"])
    assert not excluido({"ruta": "carnes/pollo", "nombre": "Pollo"}, ["mascotas"])


# ── resolución completa ──────────────────────────────────────────────────────

def test_vtex_se_pide_por_nivel_y_tottus_completo():
    """La asimetría es medida, no estética: la búsqueda por ruta de VTEX
    incluye los descendientes y la de Tottus no."""
    targets = resolver_con_arbol()
    vtex = sorted(t["slug"] for t in targets if t["supermercado"] == "plazavea")
    assert vtex == ["chorizos", "ensaladas", "pollo", "res"], vtex
    assert "pollo-entero" not in vtex, "el nivel 3 ya viene dentro del nivel 2"

    tottus = {t["slug"] for t in targets if t["supermercado"] == "tottus"}
    assert "frutas-citricas" in tottus
    assert "fresas-y-arandanos" in tottus, "en Tottus la madre no cubre a la hija"


def test_una_hoja_mas_arriba_del_nivel_igual_se_pide():
    """Si una rama de VTEX termina antes del nivel pedido, hay que pedirla o se
    pierde entera."""
    wong = [t["slug"] for t in resolver_con_arbol() if t["supermercado"] == "wong"]
    assert "res" in wong


def test_el_departamento_se_filtra_antes_de_deduplicar_el_catg():
    """El caso de "Cebolla, Tomate, Ajo y Ajíes": su aparición bajo "Pescados y
    Mariscos" es más superficial, y si gana, el nodo queda afuera."""
    tottus = [t for t in resolver_con_arbol() if t["supermercado"] == "tottus"]
    cebolla = [t for t in tottus if "cebolla" in t["slug"]]
    assert len(cebolla) == 1, "esperaba una, salieron %d" % len(cebolla)
    assert cebolla[0]["familia"] == "verduras"


def test_los_nombres_repetidos_se_desambiguan_con_el_padre():
    """Dos "Elaborados" con el mismo slug mezclarían en una sola serie lo que
    pasa con el pollo y lo que pasa con el cerdo."""
    slugs = [t["slug"] for t in resolver_con_arbol() if t["supermercado"] == "tottus"]
    assert len(slugs) == len(set(slugs)), "slugs repetidos: %s" % slugs
    assert "pollo-elaborados" in slugs
    assert "cerdo-elaborados" in slugs


def test_los_nodos_finos_quedan_primero():
    """scrape_all se queda con la primera aparición de cada producto, así que
    el orden del lock es lo que decide qué categoría gana."""
    tottus = [t for t in resolver_con_arbol() if t["supermercado"] == "tottus"]
    niveles = [t["nivel"] for t in tottus]
    assert niveles == sorted(niveles, reverse=True), niveles


def test_lo_excluido_no_aparece_ni_lo_de_otros_departamentos():
    todos = {t["slug"] for t in resolver_con_arbol()}
    assert "perros" not in todos and "bazar" not in todos
    assert "abarrotes" not in todos and "galletas" not in todos


def test_cada_target_trae_lo_que_scrape_all_necesita():
    esperadas = {"supermercado", "nivel", "familia", "slug", "url"}
    for t in resolver_con_arbol():
        assert set(t) == esperadas, t
        assert t["url"].startswith("https://")
        assert t["slug"] and t["familia"]


# ── el deduplicado de la corrida ─────────────────────────────────────────────
#
# Es la otra mitad de la decisión de pedir todos los niveles de Tottus: si los
# nodos se superponen, alguien tiene que quedarse con una sola fila por
# producto y por día. Medido en Tottus: 1.002 filas crudas, 564 productos.

def test_el_deduplicado_se_queda_con_la_primera_aparicion():
    vistos = set()
    fino = [{"supermercado": "tottus", "producto_id": "7", "categoria": "fresas-y-arandanos"}]
    grueso = [{"supermercado": "tottus", "producto_id": "7", "categoria": "frutas"}]
    assert sin_repetidos(fino, vistos) == fino
    assert sin_repetidos(grueso, vistos) == [], "la segunda aparición se descarta"


def test_el_mismo_id_en_dos_cadenas_son_dos_productos():
    """Los IDs son por cadena: sin el supermercado en la clave, un producto de
    Wong taparía al de Metro que casualmente comparte número."""
    vistos = set()
    a = [{"supermercado": "wong", "producto_id": "7"}]
    b = [{"supermercado": "metro", "producto_id": "7"}]
    assert sin_repetidos(a, vistos) and sin_repetidos(b, vistos)


def test_sin_producto_id_se_usa_la_url():
    vistos = set()
    uno = [{"supermercado": "tottus", "producto_id": "", "url": "https://a"}]
    otro = [{"supermercado": "tottus", "producto_id": "", "url": "https://b"}]
    assert sin_repetidos(uno, vistos) and sin_repetidos(otro, vistos)
    assert sin_repetidos(list(uno), vistos) == []


# ── la guarda del lock ───────────────────────────────────────────────────────
#
# El descubrimiento corre solo una vez por semana. Un lock que se encoge sin
# que nadie mire es el peor de los fallos posibles acá: no rompe nada, la
# corrida diaria sigue andando con menos categorías, y el hueco recién se nota
# meses después en una serie cortada.

def targets_de(cadena, n):
    return [{"supermercado": cadena, "slug": str(i)} for i in range(n)]


def test_la_guarda_no_dice_nada_cuando_el_lock_crece_o_no_cambia():
    assert revisar_cambio(targets_de("wong", 10), targets_de("wong", 10)) == []
    assert revisar_cambio(targets_de("wong", 20), targets_de("wong", 10)) == []
    assert revisar_cambio(targets_de("wong", 3), []) == [], "la primera vez no compara"


def test_la_guarda_tolera_un_recorte_chico():
    """Que una cadena saque una subcategoría es normal y no debe frenar nada."""
    assert revisar_cambio(targets_de("wong", 9), targets_de("wong", 10)) == []


def test_la_guarda_avisa_cuando_el_recorte_es_grande():
    assert revisar_cambio(targets_de("wong", 5), targets_de("wong", 10))


def test_la_guarda_avisa_cuando_una_cadena_desaparece():
    """Lo que pasa si el árbol de una cadena falla: el resto sigue bien y la
    cadena caída se iría del lock en silencio."""
    antes = targets_de("wong", 10) + targets_de("metro", 5)
    problemas = revisar_cambio(targets_de("metro", 5), antes)
    assert len(problemas) == 1 and "wong" in problemas[0]


# ── ramas declaradas en profundidad ──────────────────────────────────────────

def test_una_rama_profunda_se_pide_tal_cual():
    """Los chorizos no están en carnes sino colgando de fiambres, y pedir el
    departamento entero traía 1.607 productos de queso y jamón. Declarando la
    rama exacta el nivel se cuenta desde ahí, no desde la raíz."""
    vtex = [t for t in resolver_con_arbol() if t["supermercado"] == "wong"]
    slugs = {t["slug"] for t in vtex}
    assert "chorizos" in slugs
    assert "salchichas" not in slugs, "solo se pidió la rama de chorizos"
    assert "quesos-duros" not in slugs, "el resto del departamento queda afuera"


def test_la_rama_profunda_no_cambia_el_nivel_de_las_otras():
    """El nivel relativo tiene que dar lo mismo que antes para un departamento
    declarado en el nivel 1."""
    vtex = sorted(t["slug"] for t in resolver_con_arbol()
                  if t["supermercado"] == "metro")
    assert "pollo" in vtex and "pollo-entero" not in vtex


def test_en_tottus_el_nodo_declarado_cuenta_ademas_de_sus_hijos():
    """Cada nodo de Tottus lista solo sus propios productos: quedarse con los
    hijos de "Chorizos" perdería lo que cuelga directo de él."""
    slugs = {t["slug"] for t in resolver_con_arbol() if t["supermercado"] == "tottus"}
    assert "chorizos" in slugs, "falta el nodo declarado"
    assert "parrilleros" in slugs, "falta su hijo"


def test_los_chorizos_quedan_en_su_propia_familia():
    """Sin esto caían en `parrilla`, mezclados con hamburguesas y anticuchos,
    y la sección pedida no se podía filtrar."""
    for t in resolver_con_arbol():
        if "chorizo" in t["slug"] or t["slug"] == "parrilleros":
            assert t["familia"] == "chorizos", t


def test_un_padre_ambiguo_no_decide_pero_uno_claro_si():
    """La regla fina: se descarta el padre que nombra dos familias a la vez
    --"Frutas y Verduras"--, no todo departamento declarado. Descartarlos todos
    dejaba a los hijos de "Chorizos" heredando de "Huevos y Fiambres", que los
    volvía pollo."""
    assert ambiguo("frutas-y-verduras", REGLAS)
    assert not ambiguo("chorizos", REGLAS)
    assert familia_de("parrilleros", "Parrilleros", REGLAS, {},
                      ["Huevos y Fiambres", "Chorizos"]) == "chorizos"


# ── la declaración de verdad ─────────────────────────────────────────────────

def test_categorias_yaml_declara_lo_que_descubrir_lee():
    d = yaml.safe_load((RAIZ / "categorias.yaml").read_text(encoding="utf-8"))
    assert set(d["departamentos"]) == {"vtex", "tottus"}
    assert isinstance(d["nivel_vtex"], int)
    assert "nivel_tottus" not in d, "Tottus ya no se pide por nivel"
    assert d["familias"] and all(isinstance(v, list) for v in d["familias"].values())


def test_el_lock_es_consistente_con_el_esquema():
    lock = RAIZ / "targets.lock.yaml"
    if not lock.exists():
        return
    targets = yaml.safe_load(lock.read_text(encoding="utf-8"))["targets"]
    assert targets
    for t in targets:
        assert {"supermercado", "familia", "slug", "url"} <= set(t), t
    claves = [(t["supermercado"], t["slug"]) for t in targets]
    assert len(claves) == len(set(claves)), "hay slugs repetidos en el lock"


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
