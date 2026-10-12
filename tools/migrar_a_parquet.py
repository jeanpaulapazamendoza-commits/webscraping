"""
Convierte las particiones del histórico de CSV a Parquet. Se corre una vez.

    python tools/migrar_a_parquet.py              # qué haría, sin tocar nada
    python tools/migrar_a_parquet.py --aplicar    # escribe los .parquet
    python tools/migrar_a_parquet.py --aplicar --borrar-csv

El borrado va en un flag aparte y solo después de verificar, fila por fila, que
el Parquet dice exactamente lo mismo que el CSV. Son 138 MB de histórico que no
se puede volver a scrapear: los precios de agosto no están más en ningún lado.
(Igual quedan en el historial de git, pero recuperarlos de ahí es un trabajo que
nadie quiere hacer un martes.)

La verificación compara el contenido con los nulos normalizados a cadena vacía,
porque esa es la única diferencia esperable entre los dos formatos: read_csv
devuelve NaN donde el campo está vacío y Parquet devuelve None.
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402

from historico_io import (CAMPOS_HISTORICO, DATA_DIR, escribir_particion,  # noqa: E402
                          leer)


def mismo_contenido(a: pd.DataFrame, b: pd.DataFrame) -> tuple[bool, str]:
    """Si dos particiones dicen lo mismo, y si no, en qué difieren."""
    a = a.reindex(columns=CAMPOS_HISTORICO).fillna("").astype(str)
    b = b.reindex(columns=CAMPOS_HISTORICO).fillna("").astype(str)
    if len(a) != len(b):
        return False, f"{len(a)} filas contra {len(b)}"
    distintas = (a.values != b.values)
    if distintas.any():
        col = CAMPOS_HISTORICO[distintas.any(axis=0).argmax()]
        fila = int(distintas.any(axis=1).argmax())
        return False, (f"{int(distintas.sum())} celdas distintas; "
                       f"la primera en la fila {fila}, columna {col}")
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="Escribe los .parquet. Sin esto solo informa.")
    ap.add_argument("--borrar-csv", action="store_true",
                    help="Borra cada .csv después de verificar su .parquet.")
    args = ap.parse_args()

    csvs = sorted(DATA_DIR.glob("historico_*.csv"))
    if not csvs:
        print("  No hay particiones en CSV. Nada que migrar.")
        return 0

    total_csv = total_pq = filas = 0
    fallos = []
    print(f"  {'partición':30s}{'CSV':>10}{'Parquet':>10}{'filas':>10}  estado")
    for ruta in csvs:
        mes = ruta.stem.replace("historico_", "")
        destino = DATA_DIR / f"historico_{mes}.parquet"
        df = leer(ruta).reindex(columns=CAMPOS_HISTORICO)
        peso_csv = ruta.stat().st_size
        total_csv += peso_csv
        filas += len(df)

        if not args.aplicar:
            print(f"  {ruta.name:30s}{peso_csv/1048576:>9.1f}M{'—':>10}"
                  f"{len(df):>10,}  se convertiría")
            continue

        escribir_particion(df, destino)
        ok, motivo = mismo_contenido(df, leer(destino))
        peso_pq = destino.stat().st_size
        total_pq += peso_pq
        estado = "ok" if ok else f"DIFIERE: {motivo}"
        if not ok:
            fallos.append((ruta.name, motivo))
        elif args.borrar_csv:
            ruta.unlink()
            estado = "ok, CSV borrado"
        print(f"  {ruta.name:30s}{peso_csv/1048576:>9.1f}M{peso_pq/1048576:>9.1f}M"
              f"{len(df):>10,}  {estado}")

    print(f"\n  {filas:,} filas en {len(csvs)} particiones")
    if args.aplicar and total_pq:
        print(f"  {total_csv/1048576:.1f} MB de CSV -> {total_pq/1048576:.1f} MB "
              f"de Parquet ({total_pq/total_csv*100:.0f}%)")
    if fallos:
        print(f"\n  ⚠ {len(fallos)} partición(es) no coinciden. No se borró "
              f"ningún CSV de esas, y sus .parquet hay que revisarlos a mano.")
        return 1
    if not args.aplicar:
        print("  Nada escrito. Volvé a correrlo con --aplicar.")
    elif not args.borrar_csv:
        print("  Los CSV siguen ahí. Con --borrar-csv se van (después de "
              "verificar, igual que ahora).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
