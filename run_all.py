"""
Orquestador: corre todos los extractores y hace append al histórico.

Lee config/targets.yaml (4 super × 3 categorías), corre cada extractor
y consolida los resultados en data/historico.csv.

Uso:
    python run_all.py
"""

import csv
import sys
import json
import yaml
from pathlib import Path
from datetime import datetime

from extractors.tottus import extraer as extraer_tottus
# from extractors.vtex import extraer as extraer_vtex   # cuando esté listo

EXTRACTORES = {
    "tottus":   extraer_tottus,
    # "wong":     extraer_vtex,
    # "metro":    extraer_vtex,
    # "plazavea": extraer_vtex,
}

DATA_DIR = Path("data")
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
HISTORICO = DATA_DIR / "historico.csv"

CAMPOS = [
    "supermercado", "categoria", "nombre", "producto_id", "sku_id",
    "marca", "url", "imagen", "vendedor",
    "precio_cmr", "precio_internet", "precio_normal",
    "precio_descuento", "precio_regular",
    "tiene_descuento", "descuento_pct", "fecha_extraccion",
]


def cargar_targets() -> list[dict]:
    with open("config/targets.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["targets"]


def append_historico(rows: list[dict]) -> None:
    """Append-only al CSV maestro."""
    DATA_DIR.mkdir(exist_ok=True)
    existe = HISTORICO.exists()
    with open(HISTORICO, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
        if not existe:
            w.writeheader()
        w.writerows(rows)


def guardar_snapshot(rows: list[dict], super_name: str, categoria: str) -> Path:
    """Guarda snapshot individual con timestamp (para auditoría / debug)."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = SNAPSHOTS_DIR / f"{super_name}_{categoria}_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return out


def main():
    targets = cargar_targets()
    total_rows = 0
    errores = []

    print(f"=== Corrida {datetime.now().isoformat(timespec='seconds')} ===")
    print(f"Targets: {len(targets)}\n")

    for t in targets:
        super_name = t["supermercado"]
        categoria  = t["categoria"]
        url        = t["url"]
        extractor  = EXTRACTORES.get(super_name)

        if not extractor:
            print(f"⚠ Saltando {super_name} — extractor no implementado")
            continue

        print(f"▶ {super_name} / {categoria}")
        try:
            rows = extractor(url)
            append_historico(rows)
            guardar_snapshot(rows, super_name, categoria)
            print(f"  ✓ {len(rows)} productos\n")
            total_rows += len(rows)
        except Exception as e:
            print(f"  ✗ Error: {e}\n")
            errores.append((super_name, categoria, str(e)))

    print(f"=== Total: {total_rows} filas agregadas al histórico ===")
    if errores:
        print(f"\n⚠ {len(errores)} errores:")
        for s, c, e in errores:
            print(f"  - {s}/{c}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
