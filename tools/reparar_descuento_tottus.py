"""
Recalcula el descuento de las filas de Tottus anteriores a 47392de.

Hasta ese commit el extractor tomaba el precio internet como referencia cuando
el producto tenía precio CMR, en vez del precio de lista que el sitio muestra
tachado. El descuento quedaba sistemáticamente corto: "Bisteck Tottus" figura
con 5,0% donde el real era 56,9%.

Se puede reparar hacia atrás porque `precio_normal` sí se venía guardando. La
reconstrucción usa exactamente la misma regla que el extractor nuevo, con las
columnas que hay en el histórico:

    precio_regular  = precio_normal, o precio_internet si no hay normal
    tiene_descuento = precio_descuento < precio_regular
    descuento_pct   = (1 - precio_descuento / precio_regular) * 100

Solo toca filas de Tottus. Las de VTEX usan otra regla, que no cambió. Las
filas sin precio_normal ni precio_internet quedan intactas: sin referencia no
hay descuento que calcular.

Uso:
    python tools/reparar_descuento_tottus.py            # muestra qué haría
    python tools/reparar_descuento_tottus.py --aplicar  # escribe
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402

from historico_io import (CAMPOS_HISTORICO, escribir_particion,  # noqa: E402
                          leer, listar_particiones)


def _num(valor):
    """El histórico guarda todo como texto; devuelve float o None."""
    if valor is None or valor == "":
        return None
    try:
        v = float(valor)
    except ValueError:
        return None
    return v


def recalcular(fila: dict) -> dict | None:
    """Devuelve los campos corregidos, o None si la fila no cambia."""
    if fila.get("supermercado") != "tottus":
        return None

    desc = _num(fila.get("precio_descuento"))
    normal = _num(fila.get("precio_normal"))
    internet = _num(fila.get("precio_internet"))

    referencia = normal if normal else internet
    if not desc or not referencia or referencia <= 0:
        return None

    pct = round((1 - desc / referencia) * 100, 1)
    nuevo = {
        "precio_regular": f"{referencia:g}",
        "tiene_descuento": str(desc < referencia),
        "descuento_pct": f"{pct:g}",
    }
    if all(fila.get(k, "") == v for k, v in nuevo.items()):
        return None
    return nuevo


def procesar(ruta: Path, aplicar: bool) -> dict:
    # El histórico pasó a Parquet, así que la partición se lee y se escribe por
    # historico_io en vez de con csv.DictReader. La lógica de abajo sigue
    # trabajando con filas como diccionarios de texto, que es como las leía
    # antes; fillna("") reproduce lo que daba read_csv sobre un campo vacío.
    filas = leer(ruta).reindex(columns=CAMPOS_HISTORICO).fillna("") \
                      .astype(str).to_dict("records")
    cambios = 0
    deltas = []
    regular_vacio = 0
    for fila in filas:
        nuevo = recalcular(fila)
        if not nuevo:
            continue
        antes = _num(fila.get("descuento_pct")) or 0.0
        delta = float(nuevo["descuento_pct"]) - antes
        if abs(delta) > 0.05:
            deltas.append(delta)
        if not fila.get("precio_regular"):
            regular_vacio += 1
        fila.update(nuevo)
        cambios += 1

    if aplicar and cambios:
        escribir_particion(pd.DataFrame(filas), ruta)

    return {"filas": len(filas), "cambios": cambios, "deltas": deltas,
            "regular_vacio": regular_vacio}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="Escribe los cambios. Sin esto solo informa.")
    args = ap.parse_args()

    total = tocadas = vacias = 0
    todos = []
    print(f"{'Partición':24s}{'Filas':>9}{'Tocadas':>9}{'Cambia el %':>13}")
    for ruta in listar_particiones():
        r = procesar(ruta, args.aplicar)
        total += r["filas"]
        tocadas += r["cambios"]
        vacias += r["regular_vacio"]
        todos += r["deltas"]
        print(f"  {ruta.name:22s}{r['filas']:>9,}{r['cambios']:>9,}{len(r['deltas']):>13,}")

    print(f"\n  {'TOTAL':22s}{total:>9,}{tocadas:>9,}{len(todos):>13,}")
    print(f"\n  Qué cambia en esas {tocadas:,} filas:")
    print(f"    · {vacias:,} tenían precio_regular vacío y ahora lo tienen")
    print(f"    · {len(todos):,} cambian el descuento_pct — son las del error")
    if todos:
        todos.sort()
        n = len(todos)
        print(f"\n  De las {n:,} que cambian el descuento:")
        print(f"    sube en {sum(1 for d in todos if d > 0):,}, "
              f"baja en {sum(1 for d in todos if d < 0):,}")
        print(f"    corrección media {sum(todos)/n:+.1f} pp · "
              f"mediana {todos[n//2]:+.1f} pp · mayor {todos[-1]:+.1f} pp")
    print("\n" + ("Escrito." if args.aplicar else
                  "Nada escrito. Volvé a correrlo con --aplicar para que tome efecto."))


if __name__ == "__main__":
    main()
