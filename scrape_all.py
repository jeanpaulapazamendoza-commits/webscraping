"""
Orquestador: corre todas las extracciones definidas en targets.yaml
y consolida en data/historico_YYYY-MM.csv (particionado por mes).

Uso:
    python scrape_all.py                       # scrapea las 77 categorías
    python scrape_all.py --super tottus        # solo tottus
    python scrape_all.py --familia frutas      # solo familia=frutas
    python scrape_all.py --super tottus --familia res  # combinaciones
    python scrape_all.py --dry-run             # solo muestra qué haría
    python scrape_all.py --delay 2             # 2 segundos entre requests (default: 1)
    python scrape_all.py --no-sheets           # no exportar a Google Sheets
    python scrape_all.py --excel               # además generar el XLSX local

Output:
    - data/historico_YYYY-MM.csv (append-only, partición mensual, vía historico_io.py)
    - data/snapshots/*.json      (backup individual por categoría)
    - data/_run_log.txt          (log de la última corrida con éxitos/fallos)
    - Google Sheet (Histórico/Snapshot Actual/Resumen Competitivo), salvo --no-sheets
"""

import sys
import time
import json
import argparse
import traceback
from pathlib import Path

# Fix Windows terminal encoding (cp1252 → utf-8) para caracteres de caja ╔═╗
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime

import yaml

# Importar los extractores (deben estar en la misma carpeta)
from tottus_extractor import extraer as extraer_tottus
from vtex_extractor import extraer as extraer_vtex
from historico_io import append_historico

EXTRACTORES = {
    "tottus":   extraer_tottus,
    "wong":     extraer_vtex,
    "metro":    extraer_vtex,
    "plazavea": extraer_vtex,
}

DATA_DIR      = Path("data")
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LOG_FILE      = DATA_DIR / "_run_log.txt"


def cargar_targets() -> list[dict]:
    with open("targets.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["targets"]


def filtrar(targets, super_filter=None, familia_filter=None):
    out = targets
    if super_filter:
        out = [t for t in out if t["supermercado"] == super_filter]
    if familia_filter:
        out = [t for t in out if t["familia"] == familia_filter]
    return out


def enriquecer_con_familia(productos, familia):
    """Agrega la columna 'familia' a cada producto extraído."""
    for p in productos:
        p["familia"] = familia
    return productos


def guardar_snapshot(rows, super_name, slug, ts):
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOTS_DIR / f"{super_name}_{slug}_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return out


def correr_un_target(target, delay):
    """Corre un solo target y devuelve (n_productos, error_msg | None)."""
    extractor = EXTRACTORES[target["supermercado"]]
    if target["supermercado"] == "tottus":
        # Tottus extractor recibe (url, delay)
        productos = extractor(target["url"], delay=delay)
    else:
        # VTEX extractor recibe (url, supermercado, categoria, delay)
        productos = extractor(
            target["url"],
            target["supermercado"],
            target["slug"],
            delay=delay,
        )
    # Asegurar campo 'categoria' (los extractores ya lo ponen, pero por las dudas)
    for p in productos:
        p["categoria"] = target["slug"]
        p["familia"]   = target["familia"]
    return productos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--super",    dest="super_filter",   help="Solo este supermercado")
    parser.add_argument("--familia",  dest="familia_filter", help="Solo esta familia")
    parser.add_argument("--delay", type=float, default=1.0, help="Segundos entre requests")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar qué haría, sin ejecutar")
    parser.add_argument("--no-sheets", action="store_true",
                        help="No exportar a Google Sheets al final")
    parser.add_argument("--excel", action="store_true",
                        help="Además generar el XLSX local vía build_excel.py")
    args = parser.parse_args()

    targets = cargar_targets()
    targets = filtrar(targets, args.super_filter, args.familia_filter)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    ts_pretty = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"╔{'═'*68}╗")
    print(f"║  ORQUESTADOR DE PRECIOS — {ts_pretty}{' '*23}║")
    print(f"╚{'═'*68}╝")
    print(f"\nTargets a procesar: {len(targets)}")
    if args.super_filter:
        print(f"  Filtro super:    {args.super_filter}")
    if args.familia_filter:
        print(f"  Filtro familia:  {args.familia_filter}")
    print()

    if args.dry_run:
        print("--- DRY RUN — no se ejecuta nada ---\n")
        for i, t in enumerate(targets, 1):
            print(f"  [{i:2}/{len(targets)}] {t['supermercado']:9s} {t['familia']:9s} {t['slug']}")
        return

    DATA_DIR.mkdir(exist_ok=True)
    log_lines = [f"=== Corrida {ts_pretty} ===", f"Targets: {len(targets)}\n"]

    exitos = []
    fallos = []
    todos_los_productos = []
    t_inicio = time.time()

    for i, t in enumerate(targets, 1):
        marker = f"[{i:2}/{len(targets)}]"
        header = f"{marker} {t['supermercado']:9s} {t['familia']:9s} {t['slug']}"
        print(header)
        log_lines.append(header)

        try:
            productos = correr_un_target(t, args.delay)
            append_historico(productos)
            guardar_snapshot(productos, t["supermercado"], t["slug"], ts)
            todos_los_productos.extend(productos)
            con_desc = sum(1 for p in productos if p.get("tiene_descuento"))
            msg = f"        ✓ {len(productos)} productos ({con_desc} con descuento)"
            print(msg)
            log_lines.append(msg)
            exitos.append((t, len(productos)))
        except Exception as e:
            err = f"        ✗ ERROR: {type(e).__name__}: {e}"
            print(err)
            log_lines.append(err)
            log_lines.append(traceback.format_exc())
            fallos.append((t, str(e)))

    elapsed = time.time() - t_inicio
    total_productos = sum(n for _, n in exitos)

    resumen = [
        "",
        f"╔{'═'*68}╗",
        f"║  RESUMEN                                                           ║",
        f"╚{'═'*68}╝",
        f"  Tiempo total:        {elapsed:.1f}s ({elapsed/60:.1f} min)",
        f"  Targets exitosos:    {len(exitos)}/{len(targets)}",
        f"  Targets fallidos:    {len(fallos)}",
        f"  Productos totales:   {total_productos}",
    ]
    if fallos:
        resumen.append("\n  Fallos:")
        for t, e in fallos:
            resumen.append(f"    - {t['supermercado']}/{t['slug']}: {e}")

    for line in resumen:
        print(line)
        log_lines.append(line)

    # Escribir log
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"\n  Histórico:  {DATA_DIR}/historico_*.csv")
    print(f"  Snapshots:  {SNAPSHOTS_DIR}/")
    print(f"  Log:        {LOG_FILE}")

    # ── Export a Google Sheets (default) ──────────────────────────────────────
    if exitos and not args.no_sheets:
        print(f"\n{'─'*70}")
        print("Exportando a Google Sheets...")
        print("─" * 70)
        try:
            import export_sheets
            export_sheets.exportar(rows_nuevas=todos_los_productos)
        except Exception as e:
            print(f"  ⚠ No se pudo exportar a Google Sheets: {e}")

    # ── Generación opcional del XLSX para Power BI ─────────────────────────────
    if exitos and args.excel and Path("build_excel.py").exists():
        print(f"\n{'─'*70}")
        print("Generando XLSX normalizado para Power BI...")
        print("─" * 70)
        import subprocess
        try:
            r = subprocess.run(
                [sys.executable, "build_excel.py"],
                check=False,
                capture_output=False,
            )
            if r.returncode != 0:
                print(f"  ⚠ build_excel.py salió con código {r.returncode}")
        except Exception as e:
            print(f"  ⚠ No se pudo generar el XLSX: {e}")


if __name__ == "__main__":
    main()