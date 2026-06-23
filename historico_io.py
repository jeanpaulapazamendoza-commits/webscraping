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
    "precio_internet", "precio_normal",
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


def append_historico(rows: list[dict]) -> Path:
    """Agrega filas a la partición del mes actual (append-only). Devuelve la ruta."""
    destino = _particion_actual()
    if not rows:
        return destino
    DATA_DIR.mkdir(exist_ok=True)
    es_nuevo = not destino.exists() or destino.stat().st_size == 0
    with open(destino, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        if es_nuevo:
            w.writeheader()
        w.writerows(rows)
    return destino
