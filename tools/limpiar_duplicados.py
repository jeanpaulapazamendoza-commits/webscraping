"""
Deja una sola lectura por producto y día en el histórico. Se corre una vez.

    python tools/limpiar_duplicados.py              # qué haría, sin tocar nada
    python tools/limpiar_duplicados.py --aplicar    # reescribe las particiones

De dónde salen los repetidos: durante meses el deduplicado fue por CORRIDA, no
por día. Si el día se capturaba dos veces --el cron y un disparo manual, como el
2026-10-12 a las 12 y a las 15-- la segunda corrida volvía a escribir el día
entero. Medido antes de limpiar: 23.828 filas repetidas sobre 446.220, el 5,3%,
y un solo día con 7.643 filas para 3.665 productos.

Eso ya no vuelve a pasar: scrape_all arranca con las claves capturadas hoy y no
las repite. Esto es para lo que quedó escrito antes de ese arreglo.

QUÉ FILA SE CONSERVA

La última por fecha_extraccion, que es exactamente la que ya elige
construir_json al armar los JSON de la web. Esa coincidencia no es casual ni
decorativa: si acá se eligiera otra, limpiar cambiaría los precios publicados, y
una limpieza que mueve los números no es una limpieza. El script lo verifica
antes de escribir.
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402

from historico_io import (CAMPOS_HISTORICO, escribir_particion,  # noqa: E402
                          leer, listar_particiones)


def deduplicar(df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por (día, producto): la última lectura.

    Mismo criterio que construir_json._preparar(): orden estable por
    fecha_extraccion y se conserva la última de cada grupo.
    """
    d = df.copy()
    d["_orden"] = range(len(d))
    d["_ts"] = pd.to_datetime(d["fecha_extraccion"], errors="coerce", format="mixed")
    d["_dia"] = d["_ts"].dt.strftime("%Y-%m-%d")
    d["_clave"] = d["supermercado"].astype(str) + "|" + d["producto_id"].astype(str)

    # Las filas sin fecha usable no se tocan: no se puede decir cuál es la
    # última de un grupo cuyo orden no se conoce, y descartarlas sería perder
    # dato por una duda nuestra.
    sin_fecha = d[d["_ts"].isna()]
    con_fecha = d[d["_ts"].notna()]

    quedan = (con_fecha.sort_values("_ts", kind="stable")
                       .groupby(["_dia", "_clave"], as_index=False, sort=False)
                       .tail(1))
    salida = pd.concat([quedan, sin_fecha]).sort_values("_orden")
    return salida.drop(columns=["_orden", "_ts", "_dia", "_clave"])


def mismo_contenido_publicado(antes: pd.DataFrame, despues: pd.DataFrame) -> tuple:
    """Si lo que la web publica queda igual después de limpiar.

    La web ya deduplica al construir los JSON, así que limpiar el histórico no
    puede moverle un precio. Si esto falla, el criterio de acá y el de
    construir_json se separaron y hay que mirarlos antes de escribir nada.
    """
    def publicado(df):
        d = df.copy()
        d["_ts"] = pd.to_datetime(d["fecha_extraccion"], errors="coerce", format="mixed")
        d = d[d["_ts"].notna()]
        d["_dia"] = d["_ts"].dt.strftime("%Y-%m-%d")
        d["_clave"] = d["supermercado"].astype(str) + "|" + d["producto_id"].astype(str)
        d = d.sort_values("_ts", kind="stable").groupby(["_dia", "_clave"],
                                                        as_index=False, sort=False).tail(1)
        # Todas las columnas, no solo las de precio: el índice de la web
        # publica también nombre, marca, tarjeta, stock y unidad, y una
        # comprobación que mira tres columnas da un ok que no vale.
        return (d.set_index(["_dia", "_clave"])[CAMPOS_HISTORICO]
                 .fillna("").astype(str).sort_index())
    a, b = publicado(antes), publicado(despues)
    if len(a) != len(b):
        return False, f"{len(a)} lecturas publicadas contra {len(b)}"
    if not a.index.equals(b.index):
        return False, "cambian los pares (día, producto)"
    distintas = (a.values != b.values).sum()
    return (distintas == 0), f"{distintas} celdas publicadas cambiarían"


# Lo que esta comprobación NO cubre, y conviene saberlo: el ORDEN de las claves
# en los JSON sí cambia, porque construir_json agrupa con sort=False y el orden
# sigue a la primera aparición en el DataFrame. Al sacar filas, cambia cuál
# aparece primero. Verificado comparando los JSON antes y después: mismas 4.423
# series con los mismos puntos y mismos 3.665 productos con los mismos campos,
# solo en otro orden. El navegador los lee por clave, así que no lo nota.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="Reescribe las particiones. Sin esto solo informa.")
    args = ap.parse_args()

    particiones = listar_particiones()
    if not particiones:
        print("  No hay histórico que limpiar.")
        return 0

    total_antes = total_despues = 0
    problemas = []
    print(f"  {'partición':30s}{'filas':>10}{'se van':>9}{'quedan':>10}  estado")
    for ruta in particiones:
        df = leer(ruta).reindex(columns=CAMPOS_HISTORICO)
        limpio = deduplicar(df)
        total_antes += len(df)
        total_despues += len(limpio)
        sobran = len(df) - len(limpio)

        ok, motivo = mismo_contenido_publicado(df, limpio)
        estado = "ok" if ok else f"PARA: {motivo}"
        if not ok:
            problemas.append((ruta.name, motivo))
        elif args.aplicar and sobran:
            escribir_particion(limpio, ruta)
            estado = "ok, reescrita"
        elif args.aplicar:
            estado = "ok, sin cambios"
        print(f"  {ruta.name:30s}{len(df):>10,}{sobran:>9,}{len(limpio):>10,}  {estado}")

    sobran = total_antes - total_despues
    print(f"\n  {total_antes:,} filas · se van {sobran:,} ({sobran / total_antes * 100:.1f}%) "
          f"· quedan {total_despues:,}")
    if problemas:
        print(f"\n  ⚠ {len(problemas)} partición(es) cambiarían lo que publica la web. "
              f"No se escribió ninguna de esas.")
        return 1
    if not args.aplicar:
        print("  Nada escrito. Volvé a correrlo con --aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
