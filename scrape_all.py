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
    python scrape_all.py --si-falta            # no hacer nada si el día ya está

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
from historico_io import append_historico, consolidar, leer, listar_fuentes

EXTRACTORES = {
    "tottus":   extraer_tottus,
    "wong":     extraer_vtex,
    "metro":    extraer_vtex,
    "plazavea": extraer_vtex,
}

DATA_DIR      = Path("data")
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LOG_FILE      = DATA_DIR / "_run_log.txt"

# Tres intentos por target, además de los reintentos de red que hace red.py.
# Medido sobre el histórico: 137 extracciones perdidas en 121 días, ~1,5% de
# las extracciones, y en días que por lo demás corrieron bien. Ese perfil es de
# falla transitoria, así que el segundo intento debería alcanzar casi siempre.
INTENTOS_POR_TARGET = 3
# Espera creciente entre intentos: 5 s y después 10 s. Si una cadena está
# teniendo un mal momento, volver a golpearla de inmediato no ayuda.
ESPERA_ENTRE_INTENTOS = 5.0


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


def correr_etapa(nombre: str, fn, critica: bool) -> dict:
    """Corre una etapa posterior a la captura y devuelve cómo le fue.

    Tres resultados, no dos, porque antes "no está configurado" y "se rompió"
    daban el mismo ⚠ y era imposible distinguirlos: la exportación a Sheets
    avisaba todos los días que no había credenciales, con el mismo aspecto que
    tendría un error de verdad.

      · "ok"       salió bien.
      · "omitido"  falta la configuración. Es un estado legítimo, no un fallo.
                   La etapa lo informa devolviendo el motivo.
      · "falló"    estaba configurada y se rompió. Esto hay que mirarlo.

    `critica` dice si un fallo deja un entregable desactualizado. El JSON del
    frontend lo es: si no se reconstruye, la web sigue publicando los datos de
    ayer sin decir que son de ayer. La réplica en Sheets no, porque el dato
    está igual en el repo y en la web, y un error de la API de Google se
    arregla solo en la corrida siguiente.
    """
    print(f"\n{'─'*70}")
    print(nombre)
    print("─" * 70)
    try:
        motivo = fn()
    except Exception as e:
        print(f"  ⚠ falló: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"etapa": nombre, "estado": "falló", "critica": critica,
                "error": f"{type(e).__name__}: {e}"}
    if motivo:
        print(f"  · omitido: {motivo}")
        return {"etapa": nombre, "estado": "omitido", "motivo": str(motivo)}
    return {"etapa": nombre, "estado": "ok"}


def clasificar_etapas(etapas: list[dict]) -> tuple[list[str], list[str]]:
    """Reparte los fallos de las etapas entre problemas y avisos.

    Las omitidas no cuentan para ninguno de los dos: que la réplica en Sheets
    no esté configurada es un estado legítimo —hoy es el estado real— y no algo
    que haya que arreglar.
    """
    problemas, avisos = [], []
    for e in etapas:
        if e.get("estado") != "falló":
            continue
        texto = f"{e['etapa'].rstrip('. ')}: {e['error']}"
        (problemas if e.get("critica") else avisos).append(texto)
    return problemas, avisos


def correr_con_reintentos(target, delay, intentos=INTENTOS_POR_TARGET):
    """Corre un target, reintentando desde cero si falla.

    Es la segunda capa de reintento. La primera está en red.py y repite la
    misma petición cuando el fallo es de red o un 5xx; esta se ocupa de lo que
    sobrevive a eso: una caída a mitad de la paginación, una respuesta que no
    era el JSON esperado, una categoría que devolvió menos de lo que declaraba.
    En esos casos no alcanza con repetir una petición, hay que empezar de nuevo.

    Volver a correr es seguro: la extracción solo lee, y sin_repetidos() se
    encarga de que un producto que llegó dos veces se escriba una.

    Devuelve (productos, intentos_usados) para que la corrida pueda informar
    cuántos targets necesitaron ayuda — si eso sube, algo está pasando aunque
    no falle nada.
    """
    for intento in range(1, intentos + 1):
        try:
            return correr_un_target(target, delay), intento
        except Exception as e:
            if intento == intentos:
                raise
            espera = ESPERA_ENTRE_INTENTOS * intento
            print(f"        ↻ intento {intento}/{intentos} falló "
                  f"({type(e).__name__}: {e}); reintento en {espera:.0f}s")
            time.sleep(espera)


def claves_del_dia(dia: str) -> set:
    """Las claves de los productos que ya se capturaron ese día.

    Con esto el deduplicado pasa a ser por día y no por corrida, y volver a
    correr el mismo día se vuelve idempotente: lo que ya está no se escribe
    otra vez, y lo que falta sí. Antes una segunda corrida duplicaba todo el
    día, que es por lo que el histórico tiene filas repetidas.

    Es lo que habilita los disparos de respaldo del cron: el segundo puede
    correr tranquilo sabiendo que, si el primero ya trajo todo, no va a
    duplicar nada, y si el primero trajo la mitad, va a completar la otra.
    """
    vistos = set()
    columnas = ["supermercado", "producto_id", "url", "fecha_extraccion"]
    for ruta in listar_fuentes():
        # Parquet es columnar, así que pedir 4 de 25 columnas no lee el resto:
        # recorrer el histórico entero para esto cuesta décimas de segundo.
        df = leer(ruta, columnas)
        del_dia = df[df["fecha_extraccion"].astype(str).str.startswith(dia)]
        vistos.update(clave_producto(f) for f in del_dia.to_dict("records"))
    return vistos


def dia_ya_capturado(dia: str) -> str | None:
    """Por qué no hace falta correr hoy, o None si hace falta.

    "Ya hay datos de hoy" no alcanza: si la corrida anterior se cayó a mitad de
    camino, hay datos de hoy y aun así falta la mitad. Así que se mira también
    el estado que publicó: si tuvo problemas o targets fallidos, se vuelve a
    correr, y el deduplicado por día se encarga de no repetir lo que ya está.
    """
    if not any(k[0] for k in claves_del_dia(dia)):
        return None
    estado = salud.ARCHIVO_ESTADO
    if not estado.exists():
        return f"ya hay datos del {dia}"
    try:
        previo = json.loads(estado.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"ya hay datos del {dia}"
    corrida = previo.get("corrida") or {}
    if previo.get("problemas") or corrida.get("fallidos"):
        return None
    return (f"ya hay datos del {dia} y la corrida que los trajo no dejó "
            f"problemas")


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
    parser.add_argument("--si-falta", action="store_true",
                        help="No hacer nada si el día ya se capturó bien. "
                             "Para los disparos de respaldo del cron.")
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
    hoy = datetime.now().strftime("%Y-%m-%d")

    if args.si_falta:
        motivo = dia_ya_capturado(hoy)
        if motivo:
            print(f"Nada que hacer: {motivo}.")
            return 0
        print("Falta capturar el día, se corre.\n")

    log_lines = [f"=== Corrida {ts_pretty} ===", f"Targets: {len(targets)}\n"]

    exitos = []
    fallos = []
    # Los que salieron bien pero no de primera. Se informan porque son la señal
    # temprana: un target que hoy necesita dos intentos es el que mañana falla.
    reintentados = []
    todos_los_productos = []
    # Un producto puede aparecer en más de un target: en Tottus se piden todos
    # los niveles del menú porque cada nodo lista solo lo suyo, y unos pocos
    # productos están en dos nodos a la vez. Nos quedamos con la primera
    # aparición, y el lock ordena los nodos finos primero para que la categoría
    # que gane sea la más específica.
    #
    # Se arranca con lo que ya se capturó hoy, no vacío: así volver a correr el
    # mismo día completa lo que falta en vez de duplicar lo que está.
    vistos = claves_del_dia(hoy)
    if vistos:
        aviso = f"  Ya había {len(vistos)} productos capturados hoy; no se repiten."
        print(aviso)
        log_lines.append(aviso)
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
            productos, intentos = correr_con_reintentos(t, args.delay)
            if intentos > 1:
                reintentados.append((t, intentos))
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
    if reintentados:
        resumen.append(f"  Salvados a reintento: {len(reintentados)}")
        for t, n in reintentados:
            resumen.append(f"    - {t['supermercado']}/{t['slug']}: "
                           f"bien al intento {n}")
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

    # ── Etapas posteriores a la captura ───────────────────────────────────────
    # El dato ya está en el histórico, así que ninguna de estas puede tirar la
    # corrida. Pero tampoco pueden fallar en silencio, que es lo que pasaba:
    # las tres iban envueltas en un try/except que imprimía un ⚠ y seguía.
    etapas = []

    def exportar_sheets():
        import export_sheets
        try:
            export_sheets.exportar(rows_nuevas=todos_los_productos)
        except export_sheets.SinConfigurar as e:
            # El import queda adentro a propósito, para que el pipeline corra
            # sin gspread instalado. Por eso el motivo vuelve como valor y no
            # como excepción: correr_etapa no puede conocer esa clase.
            return str(e)

    def construir_el_json():
        import construir_json
        construir_json.construir()

    def generar_xlsx():
        import subprocess
        r = subprocess.run([sys.executable, "build_excel.py"], check=False)
        if r.returncode != 0:
            raise RuntimeError(f"build_excel.py salió con código {r.returncode}")

    if exitos and not args.no_sheets:
        etapas.append(correr_etapa(
            "Exportando a Google Sheets...", exportar_sheets, critica=False))
    if exitos and not args.no_json:
        etapas.append(correr_etapa(
            "Construyendo el JSON del frontend...", construir_el_json,
            critica=True))
    if exitos and args.excel and Path("build_excel.py").exists():
        etapas.append(correr_etapa(
            "Generando XLSX normalizado para Power BI...", generar_xlsx,
            critica=False))

    # ── Semáforo ──────────────────────────────────────────────────────────────
    # Va al final y después de haber escrito todo: el código de salida nunca
    # decide si el dato se guarda. Primero se guarda, después se dictamina.
    return publicar_salud(targets, exitos, fallos, reintentados, etapas,
                          log_lines)


def publicar_salud(targets, exitos, fallos, reintentados, etapas,
                   log_lines) -> int:
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

    graves, leves = clasificar_etapas(etapas)
    problemas += graves
    avisos += leves

    corrida = {
        "targets": len(targets),
        "exitosos": len(exitos),
        "fallidos": len(fallos),
        "salvados_a_reintento": len(reintentados),
        "fallos": [{"cadena": t["supermercado"], "categoria": t["slug"],
                    "error": e} for t, e in fallos],
        "reintentos": [{"cadena": t["supermercado"], "categoria": t["slug"],
                        "intentos": n} for t, n in reintentados],
        "etapas": etapas,
    }
    for ruta in salud.publicar(est, problemas, avisos, corrida):
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