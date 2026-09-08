"""
Carga y escritura del histórico de precios, particionado por mes.

Un solo data/historico.csv crece sin límite y eventualmente supera el límite
de 100MB por archivo de GitHub (a 77 categorías/día, ~37MB/mes). Particionar
por mes mantiene cada archivo chico indefinidamente sin perder histórico:
cargar_historico() concatena todas las particiones, así que el resto del
pipeline (app.py, export_sheets.py) ve el mismo DataFrame de siempre.

Uso:
    from historico_io import cargar_historico, append_historico
"""

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")

CAMPOS_HISTORICO = [
    "supermercado", "categoria", "familia", "nombre",
    "producto_id", "sku_id", "marca", "url", "imagen", "vendedor",
    "precio_tarjeta", "nombre_tarjeta", "tarjeta_descuento_pct",
    "precio_internet", "precio_unidad", "precio_normal",
    "precio_descuento", "precio_regular",
    "tiene_descuento", "descuento_pct", "fecha_extraccion",
]


def _particion_actual() -> Path:
    mes = datetime.now().strftime("%Y-%m")
    return DATA_DIR / f"historico_{mes}.csv"


def listar_particiones() -> list[Path]:
    return sorted(DATA_DIR.glob("historico_*.csv"))


def cargar_historico() -> pd.DataFrame:
    """Carga y concatena todas las particiones mensuales del histórico."""
    particiones = listar_particiones()
    if not particiones:
        return pd.DataFrame(columns=CAMPOS_HISTORICO)
    return pd.concat(
        (pd.read_csv(p, dtype=str, encoding="utf-8-sig") for p in particiones),
        ignore_index=True,
    )


def _cabecera(destino: Path) -> list[str] | None:
    """Columnas de una partición existente, o None si no existe o está vacía."""
    if not destino.exists() or destino.stat().st_size == 0:
        return None
    with open(destino, encoding="utf-8", newline="") as f:
        return next(csv.reader(f), None)


def migrar_esquema(destino: Path) -> None:
    """Reescribe una partición para que use las columnas actuales.

    Agregar un campo a CAMPOS_HISTORICO sin esto desalinea el CSV en silencio:
    DictWriter escribiría N+1 valores bajo una cabecera de N, y todo lo que
    viniera después de la columna nueva quedaría corrido un lugar. Las filas
    viejas quedan con la columna nueva vacía, que es la verdad: ese dato no se
    capturaba todavía.
    """
    tmp = destino.with_name(destino.name + ".tmp")
    with open(destino, encoding="utf-8", newline="") as f_in, \
         open(tmp, "w", encoding="utf-8", newline="") as f_out:
        lector = csv.DictReader(f_in)
        escritor = csv.DictWriter(f_out, fieldnames=CAMPOS_HISTORICO,
                                  extrasaction="ignore")
        escritor.writeheader()
        for fila in lector:
            escritor.writerow({c: fila.get(c, "") for c in CAMPOS_HISTORICO})
    tmp.replace(destino)


def append_historico(rows: list[dict]) -> Path:
    """Agrega filas a la partición del mes actual (append-only). Devuelve la ruta."""
    destino = _particion_actual()
    if not rows:
        return destino
    DATA_DIR.mkdir(exist_ok=True)
    cabecera = _cabecera(destino)
    if cabecera is not None and cabecera != CAMPOS_HISTORICO:
        migrar_esquema(destino)
    with open(destino, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        if cabecera is None:
            w.writeheader()
        w.writerows(rows)
    return destino
