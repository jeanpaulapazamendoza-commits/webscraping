"""
El estado de la captura: de cuándo es el dato y qué le falta.

Esto existe porque hasta ahora un día ausente no se distinguía de uno normal.
El cron podía dejar de correr, o correr y traer la mitad, y el histórico seguía
creciendo sin que nada fallara: el hueco recién se veía mirando una serie a ojo,
meses después.

Hay una trampa en publicar el propio estado, y conviene tenerla clara: el
archivo lo escribe la corrida, así que si la corrida no pasa, nadie lo
reescribe y se queda diciendo que todo está bien. Por eso el semáforo de verdad
son dos cosas juntas:

  1. Este archivo, que dice qué trajo la última corrida y qué días faltan.
  2. Que la corrida SALGA CON ERROR cuando el estado es malo, para que el badge
     de GitHub Actions se ponga en rojo. Ese lo calcula GitHub, no nosotros, así
     que también se pone en rojo si la corrida no corre.

Uso:
    from salud import estado, problemas, publicar
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
ARCHIVO_ESTADO = DATA_DIR / "_salud.json"
ARCHIVO_BADGE = DATA_DIR / "_badge.json"

# Un día de atraso es normal: la corrida es diaria y el badge se mira a
# cualquier hora. Dos ya es un día perdido.
ATRASO_TOLERADO = 1

# Si un día trae menos de esto comparado con el anterior, algo se rompió. El
# catálogo se mueve de a decenas de productos por día, no de a cientos: la caída
# del 30% es holgada a propósito, para que avise de una cadena caída y no de un
# lunes flojo.
CAIDA_TOLERADA = 0.70

# Un hueco viejo es un hecho histórico que ya no se puede arreglar; uno reciente
# es un problema que todavía se puede mirar.
VENTANA_HUECOS = 7


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega las columnas derivadas que necesita el cálculo.

    Pensado para recibir el histórico crudo de historico_io, pero también
    acepta uno que ya las tenga: construir_json.py llega con el suyo ya
    preparado y no hay que rehacerle el trabajo.
    """
    if {"dia", "clave", "ts"} <= set(df.columns):
        return df
    df = df.copy()
    df["ts"] = pd.to_datetime(df["fecha_extraccion"], errors="coerce",
                              format="mixed")
    df = df.dropna(subset=["ts"])
    df["dia"] = df["ts"].dt.strftime("%Y-%m-%d")
    df["clave"] = (df["supermercado"].astype(str) + "|"
                   + df["producto_id"].astype(str))
    return df


def huecos(dias: list[str]) -> list[str]:
    """Los días sin dato entre el primero y el último que sí tienen."""
    if len(dias) < 2:
        return []
    inicio = date.fromisoformat(dias[0])
    fin = date.fromisoformat(dias[-1])
    tiene = set(dias)
    faltan = []
    d = inicio
    while d <= fin:
        if d.isoformat() not in tiene:
            faltan.append(d.isoformat())
        d += timedelta(days=1)
    return faltan


def estado(df: pd.DataFrame, generado: str | None = None,
           hoy: date | None = None) -> dict:
    """Qué trajo la última corrida y qué le falta al histórico."""
    hoy = hoy or date.today()
    generado = generado or datetime.now().isoformat(timespec="seconds")
    df = preparar(df)
    if df.empty:
        return {"generado": generado, "ultimo_dia": None, "dias_con_dato": 0,
                "filas": 0, "productos": 0, "por_cadena": {},
                "dias_faltantes": [], "atraso_dias": None,
                "productos_dia_anterior": None}

    dias = sorted(df["dia"].unique())
    ultimo = dias[-1]
    foto = df[df["dia"] == ultimo]
    por_cadena = (foto.groupby("supermercado", observed=True)
                      .agg(productos=("clave", "nunique"),
                           categorias=("categoria", "nunique"))
                      .to_dict("index"))
    anterior = None
    if len(dias) > 1:
        anterior = int(df.loc[df["dia"] == dias[-2], "clave"].nunique())

    return {
        "generado": generado,
        "ultimo_dia": ultimo,
        "hora_de_la_foto": foto["ts"].max().strftime("%H:%M"),
        "dias_con_dato": len(dias),
        "primer_dia": dias[0],
        "filas": int(len(df)),
        "productos": int(df["clave"].nunique()),
        "productos_ultimo_dia": int(foto["clave"].nunique()),
        "productos_dia_anterior": anterior,
        "atraso_dias": (hoy - date.fromisoformat(ultimo)).days,
        "dias_faltantes": huecos(dias),
        "por_cadena": {k: {kk: int(vv) for kk, vv in v.items()}
                       for k, v in por_cadena.items()},
    }


def problemas(est: dict, hoy: date | None = None) -> list[str]:
    """Las razones para poner el semáforo en rojo. Vacío es que está bien.

    Acá va solo lo que se le puede reclamar a la corrida y todavía se puede
    hacer algo al respecto, porque esto es lo que la hace salir con error. Un
    día que se perdió la semana pasada no entra: los precios de ese día no
    están más en ningún lado, así que marcarlo en rojo dejaría el semáforo
    trabado una semana sin que nadie pueda hacer nada. Eso va en avisos().
    """
    if not est.get("ultimo_dia"):
        return ["no hay histórico todavía"]
    hoy = hoy or date.today()
    fallas = []

    atraso = est.get("atraso_dias")
    if atraso is not None and atraso > ATRASO_TOLERADO:
        fallas.append(f"el último dato es del {est['ultimo_dia']}, "
                      f"{atraso} días de atraso")

    hoy_prod = est.get("productos_ultimo_dia") or 0
    ayer_prod = est.get("productos_dia_anterior")
    if ayer_prod and hoy_prod < ayer_prod * CAIDA_TOLERADA:
        fallas.append(f"el último día trajo {hoy_prod} productos contra "
                      f"{ayer_prod} del anterior "
                      f"({(1 - hoy_prod / ayer_prod) * 100:.0f}% menos)")

    sin_nada = [c for c, v in est.get("por_cadena", {}).items()
                if not v.get("productos")]
    if sin_nada:
        fallas.append("sin productos: " + ", ".join(sorted(sin_nada)))

    return fallas


def avisos(est: dict, hoy: date | None = None) -> list[str]:
    """Lo que hay que ver pero no justifica frenar nada.

    Los días faltantes son esto: una vez que pasaron no se recuperan, así que
    sirven para saber qué le falta al histórico, no para fallar una corrida.
    """
    faltantes = est.get("dias_faltantes") or []
    if not faltantes:
        return []
    hoy = hoy or date.today()
    corte = (hoy - timedelta(days=VENTANA_HUECOS)).isoformat()
    recientes = [d for d in faltantes if d >= corte]
    aviso = f"faltan {len(faltantes)} día(s) en el histórico"
    if recientes:
        aviso += f", {len(recientes)} de la última semana: " + ", ".join(recientes)
    return [aviso]


def badge(est: dict, fallas: list[str], notas: list[str] | None = None) -> dict:
    """El JSON que consume shields.io para dibujar el badge del README.

    El formato lo fija shields: exactamente estas cuatro claves, nada más.
    """
    if not est.get("ultimo_dia"):
        return {"schemaVersion": 1, "label": "datos",
                "message": "sin histórico", "color": "lightgrey"}
    atraso = est.get("atraso_dias") or 0
    cuando = "hoy" if atraso == 0 else ("ayer" if atraso == 1
                                        else f"hace {atraso} días")
    mensaje = f"{cuando} · {est.get('productos_ultimo_dia', 0):,} productos"
    color = "red" if fallas else ("yellow" if notas else "brightgreen")
    return {"schemaVersion": 1, "label": "datos",
            "message": mensaje.replace(",", "."), "color": color}


def publicar(est: dict, fallas: list[str], notas: list[str] | None = None,
             destino: Path = DATA_DIR) -> list[Path]:
    """Escribe el estado y el badge. Devuelve las rutas escritas."""
    destino.mkdir(parents=True, exist_ok=True)
    notas = notas if notas is not None else avisos(est)
    completo = dict(est, problemas=fallas, avisos=notas)
    rutas = []
    for nombre, datos in ((ARCHIVO_ESTADO.name, completo),
                          (ARCHIVO_BADGE.name, badge(est, fallas, notas))):
        ruta = destino / nombre
        # allow_nan=False por lo mismo que en construir_json: Python escribe
        # NaN sin chistar y no es JSON válido.
        ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2,
                                   allow_nan=False) + "\n", encoding="utf-8")
        rutas.append(ruta)
    return rutas


def revisar() -> tuple[dict, list[str], list[str]]:
    """El estado del histórico que hay en disco, leído de cero."""
    from historico_io import cargar_historico
    est = estado(cargar_historico())
    return est, problemas(est), avisos(est)


if __name__ == "__main__":
    import sys

    est, fallas, notas = revisar()
    for ruta in publicar(est, fallas, notas):
        print(f"  escrito {ruta}")
    print(f"\n  último día      {est['ultimo_dia']} "
          f"({est.get('hora_de_la_foto', '—')})")
    print(f"  atraso          {est['atraso_dias']} día(s)")
    print(f"  días con dato   {est['dias_con_dato']} "
          f"desde {est.get('primer_dia')}")
    print(f"  productos       {est.get('productos_ultimo_dia')} el último día, "
          f"{est['productos']} distintos en total")
    if est["dias_faltantes"]:
        print(f"  días faltantes  {len(est['dias_faltantes'])}: "
              + ", ".join(est["dias_faltantes"]))
    print()
    for etiqueta, lista in (("problemas", fallas), ("avisos", notas)):
        if lista:
            print(f"  {'⚠' if etiqueta == 'problemas' else '·'} {etiqueta}:")
            for f in lista:
                print(f"      {f}")
    if not fallas:
        print("  Sin problemas." if not notas else "  Sin problemas (ver avisos).")
    sys.exit(1 if fallas else 0)
