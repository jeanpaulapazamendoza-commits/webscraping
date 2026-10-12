"""
Carga y escritura del histórico de precios, particionado por mes.

El histórico vive en Parquet, una partición por mes. Medido sobre los 446.220
registros que había al migrar: 138 MB de CSV son 5,5 MB de Parquet (4%), porque
las columnas que más pesan —supermercado, categoría, nombre, marca— son las
mismas pocas cadenas repetidas cientos de miles de veces, y Parquet guarda cada
una una vez y un entero por fila.

Y como el histórico se versiona en git, lo que importa no es solo el tamaño del
árbol sino cuánto crece el repo. También medido, 20 días de commits: el CSV que
crece por append deja 216 KB por día en .git, el Parquet reescrito entero 170
KB. Parquet gana en los dos lados.

El precio de Parquet es que no se puede ampliar: agregar una fila es reescribir
el archivo. Como append_historico() se llama una vez por target —74 veces por
corrida— reescribir la partición del mes costaba 56 segundos, y subiendo, porque
la partición crece todo el mes. Por eso la corrida no escribe Parquet directo:
agrega a un pendiente en CSV, que es el formato en el que agregar al final sale
gratis, y al terminar consolidar() lo pliega en las particiones de una sola vez.

El pendiente también es la red de seguridad: si la corrida se cae a mitad de
camino, queda en disco y la próxima lo recupera. Ninguna fila se pierde por
quedar sin consolidar, y cargar_historico() lo lee junto con las particiones,
así que el dato de hoy se ve igual antes y después de consolidarlo.

Todo se guarda como texto, igual que cuando era CSV. Tipar las columnas no
ahorra nada —medido: 5,5 MB en los dos casos, porque el diccionario de Parquet
comprime "6.50" tan bien como un float— y en cambio obligaría a revisar cada
consumidor, que hoy parsea desde texto y distingue el vacío ("no se midió") del
cero. Sin beneficio, no vale el riesgo.

Uso:
    from historico_io import cargar_historico, append_historico, consolidar
"""

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")

# El pendiente de la corrida en curso. No se versiona: es un archivo de trabajo
# que vive lo que dura la corrida.
PENDIENTE = DATA_DIR / "_pendiente.csv"

# zstd y no snappy: 5,5 MB contra 7,5 MB sobre el mismo histórico, y lo que
# cuesta descomprimir no se nota al lado de leer el archivo.
COMPRESION = "zstd"

CAMPOS_HISTORICO = [
    "supermercado", "categoria", "familia", "nombre",
    "producto_id", "sku_id", "marca", "url", "imagen", "vendedor",
    "disponible", "stock",
    "precio_tarjeta", "nombre_tarjeta", "tarjeta_descuento_pct", "tarjeta_origen",
    "precio_internet", "precio_unidad", "precio_normal",
    "precio_descuento", "precio_regular",
    "tiene_descuento", "descuento_pct", "descuento_pct_sitio", "fecha_extraccion",
]


def particion(mes: str) -> Path:
    """La partición de un mes, en formato YYYY-MM."""
    return DATA_DIR / f"historico_{mes}.parquet"


def listar_particiones() -> list[Path]:
    """Las particiones mensuales, de la más vieja a la más nueva."""
    return sorted(DATA_DIR.glob("historico_*.parquet"))


def listar_fuentes() -> list[Path]:
    """Todo lo que hay que leer para ver el histórico completo.

    Las particiones más el pendiente, si quedó uno. Quien recorra el histórico
    archivo por archivo debería usar esto y no listar_particiones(), o se
    perdería lo que la corrida de hoy todavía no consolidó.
    """
    fuentes = listar_particiones()
    if PENDIENTE.exists() and PENDIENTE.stat().st_size > 0:
        fuentes.append(PENDIENTE)
    return fuentes


def leer(ruta: Path, columnas: list[str] | None = None) -> pd.DataFrame:
    """Una fuente del histórico, como texto.

    `columnas` se intersecta con lo que el archivo realmente tiene en vez de
    pasarse tal cual: las particiones viejas no tienen las columnas que se
    agregaron después, y pedirle a Parquet una columna que no existe es un
    error, no un vacío. Lo que falta se devuelve como columna nula, que es la
    verdad: ese dato no se capturaba todavía.
    """
    if ruta.suffix == ".parquet":
        disponibles = pd.read_parquet(ruta, columns=[]).columns.tolist()
        if not disponibles:  # pyarrow < 13 no soporta columns=[]
            disponibles = pd.read_parquet(ruta).columns.tolist()
        pedidas = [c for c in columnas if c in disponibles] if columnas else None
        df = pd.read_parquet(ruta, columns=pedidas)
    else:
        df = pd.read_csv(
            ruta, dtype=str, encoding="utf-8-sig",
            usecols=(lambda c: c in columnas) if columnas else None,
        )
    return df.reindex(columns=columnas) if columnas else df


def cargar_historico() -> pd.DataFrame:
    """El histórico completo, con el esquema actual y como texto."""
    fuentes = listar_fuentes()
    if not fuentes:
        return pd.DataFrame(columns=CAMPOS_HISTORICO)
    return pd.concat(
        (leer(f).reindex(columns=CAMPOS_HISTORICO) for f in fuentes),
        ignore_index=True,
    )


def _cabecera(destino: Path) -> list[str] | None:
    """Columnas de un CSV existente, o None si no existe o está vacío."""
    if not destino.exists() or destino.stat().st_size == 0:
        return None
    with open(destino, encoding="utf-8", newline="") as f:
        return next(csv.reader(f), None)


def migrar_esquema(destino: Path) -> None:
    """Reescribe un CSV para que use las columnas actuales.

    Agregar un campo a CAMPOS_HISTORICO sin esto desalinea el CSV en silencio:
    DictWriter escribiría N+1 valores bajo una cabecera de N, y todo lo que
    viniera después de la columna nueva quedaría corrido un lugar. Las filas
    viejas quedan con la columna nueva vacía, que es la verdad: ese dato no se
    capturaba todavía.

    Las particiones en Parquet no necesitan esto: se reindexan al leerlas y al
    escribirlas. Queda para el pendiente, que sigue siendo CSV.
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
    """Agrega filas al pendiente de la corrida (append-only).

    No escribe la partición: ver la nota de arriba sobre por qué agregar a
    Parquet de a 74 veces por corrida costaba casi un minuto.
    """
    if not rows:
        return PENDIENTE
    DATA_DIR.mkdir(exist_ok=True)
    cabecera = _cabecera(PENDIENTE)
    if cabecera is not None and cabecera != CAMPOS_HISTORICO:
        migrar_esquema(PENDIENTE)
    with open(PENDIENTE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO, extrasaction="ignore")
        if cabecera is None:
            w.writeheader()
        w.writerows(rows)
    return PENDIENTE


def escribir_particion(df: pd.DataFrame, destino: Path) -> Path:
    """Escribe una partición de forma atómica.

    Primero a un .tmp y después un replace, que en el mismo sistema de archivos
    es atómico: si el proceso muere a mitad de la escritura, la partición que
    queda es la anterior completa y no un Parquet truncado, que no se puede
    leer ni en parte.
    """
    DATA_DIR.mkdir(exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    df.reindex(columns=CAMPOS_HISTORICO).to_parquet(
        tmp, compression=COMPRESION, index=False)
    tmp.replace(destino)
    return destino


def consolidar() -> list[Path]:
    """Pliega el pendiente en las particiones mensuales y lo borra.

    Idempotente: sin pendiente no hace nada. Se llama al final de la corrida y
    también al principio, para recoger lo que haya dejado una corrida anterior
    que se cayó.
    """
    if not (PENDIENTE.exists() and PENDIENTE.stat().st_size > 0):
        return []
    nuevas = leer(PENDIENTE).reindex(columns=CAMPOS_HISTORICO)
    if nuevas.empty:
        PENDIENTE.unlink()
        return []

    # El mes sale de la fecha de extracción de cada fila, no de la de hoy: una
    # corrida que arranca el 31 a las 23:50 y termina el 1 escribe en los dos
    # meses, y una consolidación atrasada tiene que ir al mes al que pertenece.
    mes = nuevas["fecha_extraccion"].astype(str).str.slice(0, 7)
    # Una fila sin fecha usable va al mes corriente. Dejarla afuera la perdería
    # para siempre, porque abajo se borra el pendiente.
    mes = mes.where(mes.str.fullmatch(r"\d{4}-\d{2}"),
                    datetime.now().strftime("%Y-%m"))

    tocadas = []
    for m, grupo in nuevas.groupby(mes, sort=True):
        destino = particion(m)
        if destino.exists():
            viejo = leer(destino).reindex(columns=CAMPOS_HISTORICO)
            grupo = pd.concat([viejo, grupo], ignore_index=True)
        tocadas.append(escribir_particion(grupo, destino))
    PENDIENTE.unlink()
    return tocadas
