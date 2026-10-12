"""
Genera los archivos JSON que consume el frontend, a partir del histórico.

La corrida diaria hace de paso de build: además de escribir el CSV, deja en
`web/datos/` todo lo que la web necesita, ya calculado. Así el frontend no
necesita base de datos ni función de servidor — solo archivos estáticos sobre
un CDN.

Cuatro archivos, y los cuatro son chicos a propósito:

    indice.json    Un producto por entrada, con su estado de hoy. Es lo que
                   alimenta la búsqueda, que corre entera en el navegador.
    series.json    La historia de precios como LOG DE CAMBIOS: solo los días en
                   que el precio se movió. El 91% de las lecturas repiten el
                   precio del día anterior, así que guardarlas todas pesa 7,5
                   veces más sin agregar información. El cliente reconstruye la
                   escalera.
    resumen.json   Agregados por día × cadena × familia, para las vistas macro.
    salud.json     De cuándo es el dato y qué tan completa salió la corrida.

Uso:
    python construir_json.py              # escribe en web/datos/
    python construir_json.py --salida X   # a otro directorio
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from historico_io import cargar_historico

SALIDA = Path("web") / "datos"

NUMERICAS = [
    "precio_tarjeta", "precio_internet", "precio_unidad", "precio_normal",
    "precio_descuento", "precio_regular", "descuento_pct", "descuento_pct_sitio",
]


def _preparar() -> pd.DataFrame:
    df = cargar_historico()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["fecha_extraccion"])
    df["dia"] = df["ts"].dt.strftime("%Y-%m-%d")
    for c in NUMERICAS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Un precio de cero es "sin precio publicado", no una ganga.
    for c in ("precio_tarjeta", "precio_internet", "precio_descuento",
              "precio_regular", "precio_normal"):
        if c in df.columns:
            df.loc[df[c] <= 0, c] = None
    df["clave"] = df["supermercado"].astype(str) + "|" + df["producto_id"].astype(str)
    # Una lectura por producto y día: dentro de una corrida un producto puede
    # venir dos veces, porque la búsqueda por path de VTEX incluye las
    # subcategorías y targets.yaml pide la madre y la hija por separado.
    return df.sort_values("ts").groupby(["dia", "clave"], as_index=False).tail(1)


def _booleano(valor):
    """El CSV guarda texto; devuelve True, False o None (dato no capturado)."""
    if valor in ("True", "true", True):
        return True
    if valor in ("False", "false", False):
        return False
    return None


def _redondear(valor):
    return None if pd.isna(valor) else round(float(valor), 2)


def construir_indice(df: pd.DataFrame) -> dict:
    """Un producto por entrada, con su estado en el último día que se lo vio."""
    ultimo = df["dia"].max()
    hoy = df[df["dia"] == ultimo]
    productos = []
    for _, r in hoy.iterrows():
        productos.append({
            "id": r["clave"],
            "cadena": r["supermercado"],
            "nombre": r["nombre"],
            "categoria": r["categoria"],
            "familia": r["familia"],
            "marca": r["marca"] or None,
            "precio": _redondear(r["precio_descuento"]),
            "regular": _redondear(r["precio_regular"]),
            "unidad": _redondear(r.get("precio_unidad")),
            "descuento": _redondear(r["descuento_pct"]),
            "tarjeta": _redondear(r.get("precio_tarjeta")),
            "tarjeta_nombre": r.get("nombre_tarjeta") or None,
            "disponible": _booleano(r.get("disponible")),
            "url": r["url"] or None,
        })
    productos.sort(key=lambda p: (p["cadena"], p["nombre"]))
    return {"dia": ultimo, "productos": productos}


def construir_series(df: pd.DataFrame) -> dict:
    """La historia como log de cambios: solo los días en que el precio se movió.

    Reconstruirla del lado del cliente es arrastrar el último valor hacia
    adelante. El precio es una función escalonada, así que esta representación
    no pierde nada y pesa 7,5 veces menos que guardar todos los puntos.

    Un punto con precio nulo marca el día en que el producto dejó de tener
    precio publicado. Se registra en vez de omitirse: si no, el arrastre hacia
    adelante seguiría mostrando el último precio conocido y afirmaría un precio
    que ya no existe.
    """
    series = {}
    for clave, g in df.sort_values("dia").groupby("clave", sort=False):
        puntos = []
        anterior = object()
        for dia, precio in zip(g["dia"], g["precio_descuento"]):
            actual = _redondear(precio)
            if actual != anterior:
                puntos.append([dia, actual])
                anterior = actual
        if puntos:
            series[clave] = puntos
    return series


def construir_resumen(df: pd.DataFrame) -> list:
    """Agregados por día × cadena × familia."""
    g = (df.groupby(["dia", "supermercado", "familia"], observed=True)
           .agg(n=("precio_descuento", "size"),
                mediana=("precio_descuento", "median"),
                minimo=("precio_descuento", "min"),
                con_descuento=("tiene_descuento", lambda s: int((s == "True").sum())))
           .reset_index())
    g["mediana"] = g["mediana"].round(2)
    g["minimo"] = g["minimo"].round(2)
    return g.rename(columns={"supermercado": "cadena"}).to_dict("records")


def construir_salud(df: pd.DataFrame, generado: str) -> dict:
    """De cuándo es el dato y qué trajo la última corrida.

    Existe porque hoy no hay forma de saber que falta un día sin mirarlo a ojo:
    un día ausente no se distingue de uno normal.
    """
    ultimo = df["dia"].max()
    hoy = df[df["dia"] == ultimo]
    por_cadena = (hoy.groupby("supermercado", observed=True)
                    .agg(productos=("clave", "nunique"),
                         categorias=("categoria", "nunique"))
                    .to_dict("index"))
    dias = sorted(df["dia"].unique())
    return {
        "generado": generado,
        "ultimo_dia": ultimo,
        "hora_de_la_foto": hoy["ts"].max().strftime("%H:%M"),
        "dias_con_dato": len(dias),
        "primer_dia": dias[0],
        "filas": int(len(df)),
        "productos": int(df["clave"].nunique()),
        "por_cadena": {k: {kk: int(vv) for kk, vv in v.items()}
                       for k, v in por_cadena.items()},
    }


def escribir(destino: Path, nombre: str, datos) -> int:
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / nombre
    # separators sin espacios: son archivos para la red, no para leer a mano.
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    ruta.write_text(texto, encoding="utf-8")
    return len(texto.encode("utf-8"))


def construir(destino: Path = SALIDA) -> dict:
    """Escribe los cuatro archivos y devuelve el peso de cada uno.

    Separada de main() a propósito: scrape_all.py la llama directamente. Si
    llamara a main(), argparse leería los argumentos de scrape_all (--super,
    --familia) y abortaría el proceso sin decir por qué.
    """
    df = _preparar()
    if df.empty:
        print("  Sin histórico todavía, no hay nada que construir.")
        return {}

    generado = datetime.now().isoformat(timespec="seconds")
    piezas = [
        ("indice.json", construir_indice(df)),
        ("series.json", construir_series(df)),
        ("resumen.json", construir_resumen(df)),
        ("salud.json", construir_salud(df, generado)),
    ]
    pesos = {}
    for nombre, datos in piezas:
        pesos[nombre] = escribir(destino, nombre, datos)
        print(f"  {nombre:16s} {pesos[nombre]/1024:>8,.0f} KB")
    print(f"  {'total':16s} {sum(pesos.values())/1024:>8,.0f} KB en {destino}")
    return pesos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(SALIDA),
                    help=f"Directorio de salida (default: {SALIDA})")
    construir(Path(ap.parse_args().salida))


if __name__ == "__main__":
    main()
