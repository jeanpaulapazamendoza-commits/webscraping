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
    python scrape_all.py --no-json             # no regenerar el JSON del frontend
    python scrape_all.py --excel               # además generar el XLSX local

Output:
    - data/historico_YYYY-MM.parquet (partición mensual, vía historico_io.py)
    - data/_salud.json           (estado de la captura) y data/_badge.json
    - data/snapshots/*.json      (backup individual por categoría)
    - data/_run_log.txt          (log de la última corrida con éxitos/fallos)
    - Google Sheet (Histórico/Snapshot Actual/Resumen Competitivo), salvo --no-sheets
    - web/datos/*.json  (lo que consume el frontend), salvo --no-json
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
import salud
from historico_io import append_historico, consolidar

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
    """Los targets de la corrida.

    Se prefiere targets.lock.yaml, que genera descubrir.py desde el árbol de
    categorías de cada cadena. targets.yaml queda como respaldo: es la lista
    escrita a mano, de cuando las rutas se buscaban y se pegaban.
    """
    for ruta in ("targets.lock.yaml", "targets.yaml"):
        if Path(ruta).exists():
            with open(ruta, encoding="utf-8") as f:
                targets = (yaml.safe_load(f) or {}).get("targets") or []
            if targets:
                print(f"Targets desde {ruta}")
                return targets
    raise RuntimeError("No hay targets: falta targets.lock.yaml o targets.yaml")


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


def clave_producto(p: dict) -> tuple:
    """Identidad de un producto dentro de una corrida.

    `producto_id` es el ID de la cadena y es lo que corresponde comparar. La
    URL queda como respaldo para los pocos casos en que el listado no trae ID.
    """
    pid = str(p.get("producto_id") or "").strip()
    return (p.get("supermercado"), pid or str(p.get("url") or "").strip())


def sin_repetidos(productos: list[dict], vistos: set) -> list[dict]:
    """Los productos de este target que no aparecieron todavía en la corrida.

    `vistos` se va llenando entre targets, así que el orden manda: gana la
    primera aparición, y el lock pone los nodos finos primero para que la
    categoría que quede sea la más específica.
    """
    nuevos = []
    for p in productos:
        clave = clave_producto(p)
        if clave in vistos:
            continue
        vistos.add(clave)
        nuevos.append(p)
    return nuevos


def correr_un_target(target, delay):
    """Corre un solo target y devuelve sus productos."""
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
    # La familia viene del target; la categoría NO se pisa.
    #
    # El extractor de VTEX deriva la categoría de la ruta que trae el propio
    # producto, y es más fina que el target: pedir el departamento de carnes
    # devuelve 24 categorías distintas, no una. Sobrescribirla acá con el slug
    # del target tiraba esa precisión y dejaba una sola etiqueta por petición.
    for p in productos:
        if not p.get("categoria"):
            p["categoria"] = target["slug"]
        p["familia"] = target["familia"]
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
    parser.add_argument("--no-json", action="store_true",
                        help="No regenerar los JSON de web/datos")
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
        return 0

    DATA_DIR.mkdir(exist_ok=True)
    log_lines = [f"=== Corrida {ts_pretty} ===", f"Targets: {len(targets)}\n"]

    exitos = []
    fallos = []
    todos_los_productos = []
    # Un producto puede aparecer en más de un target: en Tottus se piden todos
    # los niveles del menú porque cada nodo lista solo lo suyo, y unos pocos
    # productos están en dos nodos a la vez. Nos quedamos con la primera
    # aparición, y el lock ordena los nodos finos primero para que la categoría
    # que gane sea la más específica.
    vistos = set()
    repetidos = 0

    # Lo primero: recoger lo que dejó una corrida que se cayó. El pendiente es
    # append-only y sobrevive al proceso, así que esas filas están a salvo pero
    # todavía no son partición.
    rescatadas = consolidar()
    if rescatadas:
        aviso = ("  Se consolidó el pendiente de una corrida anterior: "
                 + ", ".join(p.name for p in rescatadas))
        print(aviso)
        log_lines.append(aviso)

    t_inicio = time.time()

    for i, t in enumerate(targets, 1):
        marker = f"[{i:2}/{len(targets)}]"
        header = f"{marker} {t['supermercado']:9s} {t['familia']:9s} {t['slug']}"
        print(header)
        log_lines.append(header)

        try:
            productos = correr_un_target(t, args.delay)
            nuevos = sin_repetidos(productos, vistos)
            repetidos += len(productos) - len(nuevos)
            append_historico(nuevos)
            # El snapshot queda sin deduplicar a propósito: es el registro de
            # lo que devolvió esta categoría, y sirve justamente para ver la
            # superposición entre nodos cuando el lock cambia.
            guardar_snapshot(productos, t["supermercado"], t["slug"], ts)
            todos_los_productos.extend(nuevos)
            con_desc = sum(1 for prod in nuevos if prod.get("tiene_descuento"))
            ya = len(productos) - len(nuevos)
            msg = (f"        ✓ {len(nuevos)} productos ({con_desc} con descuento)"
                   + (f", {ya} ya vistos" if ya else ""))
            print(msg)
            log_lines.append(msg)
            exitos.append((t, len(nuevos)))
        except Exception as e:
            err = f"        ✗ ERROR: {type(e).__name__}: {e}"
            print(err)
            log_lines.append(err)
            log_lines.append(traceback.format_exc())
            fallos.append((t, str(e)))

    elapsed = time.time() - t_inicio
    total_productos = sum(n for _, n in exitos)

    # Recién acá el pendiente pasa a las particiones: una sola reescritura por
    # mes en vez de una por target.
    particiones = consolidar()

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
    if repetidos:
        resumen.append(f"  Repetidos omitidos:  {repetidos} "
                       f"(ya capturados por otra categoría)")
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

    print(f"\n  Histórico:  " + (", ".join(p.name for p in particiones)
                                 or f"{DATA_DIR}/historico_*.parquet"))
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

    # ── JSON para el frontend ─────────────────────────────────────────────────
    # Esto es lo que hace de "build": la web no calcula nada, solo lee estos
    # archivos. Va antes que el XLSX porque es parte del entregable diario.
    if exitos and not args.no_json:
        print(f"\n{'─'*70}")
        print("Construyendo el JSON del frontend...")
        print("─" * 70)
        try:
            import construir_json
            construir_json.construir()
        except Exception as e:
            print(f"  ⚠ No se pudo construir el JSON: {e}")

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

    # ── Semáforo ──────────────────────────────────────────────────────────────
    # Va al final y después de haber escrito todo: el código de salida nunca
    # decide si el dato se guarda. Primero se guarda, después se dictamina.
    return publicar_salud(targets, exitos, fallos, log_lines)


def publicar_salud(targets, exitos, fallos, log_lines) -> int:
    """Escribe el estado de la captura y devuelve el código de salida.

    Salir con error es la mitad del semáforo. La otra mitad, data/_badge.json,
    la escribe esta misma función, pero tiene un límite que conviene no
    olvidar: si la corrida no pasa, nadie lo reescribe y se queda diciendo que
    todo está bien. El badge que detecta "dejó de correr" es el de GitHub
    Actions, porque lo calcula GitHub; para que sirva, la corrida tiene que
    fallar cuando el resultado es malo, y eso es lo que pasa acá.
    """
    print(f"\n{'─'*70}")
    print("Estado de la captura")
    print("─" * 70)
    est, problemas, avisos = salud.revisar()

    # Lo que solo sabe esta corrida, y que el histórico no delata: que los
    # targets fallaron. Un día entero de fallos no mueve el último día del
    # histórico, así que sin esto pasaría en verde.
    if not exitos:
        problemas.append(f"ningún target trajo nada ({len(fallos)} fallos)")
    elif len(fallos) > len(targets) * 0.2:
        problemas.append(f"{len(fallos)} de {len(targets)} targets fallaron")

    for ruta in salud.publicar(est, problemas, avisos):
        print(f"  {ruta}")
    linea = (f"  último día {est['ultimo_dia']} · "
             f"{est.get('productos_ultimo_dia')} productos · "
             f"{est['dias_con_dato']} días de histórico")
    print(linea)
    log_lines.append(linea)
    for etiqueta, lista in (("⚠ problema", problemas), ("· aviso", avisos)):
        for texto in lista:
            print(f"  {etiqueta}: {texto}")
            log_lines.append(f"  {etiqueta}: {texto}")

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    if problemas:
        print("\n  La corrida sale con error para que el badge lo refleje. "
              "El dato ya quedó guardado.")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())