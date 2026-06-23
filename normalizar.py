"""
Funciones de normalización para el dataset de precios.

Diseñado como módulo reusable: cada función hace una cosa específica
y se puede testear/usar por separado. La función `normalizar_df()` 
las orquesta todas para un DataFrame completo.

Uso:
    from normalizar import normalizar_df
    df_limpio = normalizar_df(df_crudo)
"""

import re
import pandas as pd


# ─── DICCIONARIOS DE MAPEO ───────────────────────────────────────────────────

SUPERMERCADO_DISPLAY = {
    "tottus":   "Tottus",
    "wong":     "Wong",
    "metro":    "Metro",
    "plazavea": "Plaza Vea",
}


# ─── HELPERS DE LIMPIEZA DE TEXTO ────────────────────────────────────────────

def _limpiar_invisibles(s: str) -> str:
    """Reemplaza espacios invisibles (NBSP, espacios estrechos, BOM, etc.)
    por espacios normales. Estos caracteres son el problema más común al
    scrapear: rompen comparaciones e .strip() los ignora."""
    if not isinstance(s, str):
        return s
    # NBSP, espacios estrechos, espacios tipográficos
    s = re.sub(r"[\u00A0\u2000-\u200B\u202F\u205F\u3000]", " ", s)
    # BOM, ZWNJ, ZWJ
    s = s.replace("\uFEFF", "").replace("\u200C", "").replace("\u200D", "")
    return s


def _limpiar_comillas(s: str) -> str:
    """Convierte comillas tipográficas (smart quotes) a ASCII."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"[\u201C\u201D\u201E\u201F\u2033]", '"', s)  # comillas dobles
    s = re.sub(r"[\u2018\u2019\u201A\u201B\u2032]", "'", s)  # comillas simples
    s = re.sub(r"[\u2013\u2014\u2015]", "-", s)              # en/em-dash → guión
    return s


def _colapsar_espacios(s: str) -> str:
    """Reemplaza secuencias de espacios por uno solo. Strip al final."""
    if not isinstance(s, str):
        return s
    return re.sub(r"\s+", " ", s).strip()


def _fix_apostrofe_casing(s: str) -> str:
    """Arregla casing roto por `.str.title()` con apóstrofes.

        "Bell'S Frescos"   →  "Bell's Frescos"
        "O'Higgins"        →  "O'Higgins"  (sin cambios — ya está bien)
    """
    if not isinstance(s, str):
        return s
    # Buscar: letra + apóstrofe + S mayúscula seguida de espacio o fin
    return re.sub(r"(\w)'S\b", lambda m: m.group(1) + "'s", s)


def limpiar_texto(s):
    """Pipeline completo de limpieza para campos de texto libre."""
    if pd.isna(s) or s is None:
        return s
    s = str(s)
    s = _limpiar_invisibles(s)
    s = _limpiar_comillas(s)
    s = _colapsar_espacios(s)
    return s


# ─── CONVERSORES DE TIPO ─────────────────────────────────────────────────────

def slug_a_titulo(s):
    """'carne-de-res' → 'Carne De Res'"""
    if pd.isna(s) or s is None:
        return s
    return re.sub(r"-", " ", str(s)).title()


def bool_a_si_no(v):
    """True/False/'true'/'false' → 'Sí'/'No'"""
    if pd.isna(v):
        return ""
    v = str(v).strip().lower()
    if v in ("true", "1", "sí", "si", "yes"):
        return "Sí"
    if v in ("false", "0", "no"):
        return "No"
    return v  # devolver tal cual si no matchea


def calcular_precio_efectivo(row):
    """El mejor precio disponible para el cliente.
    Prioridad: tarjeta > descuento > internet > normal > regular
    (precio_tarjeta es el más bajo cuando existe — es el incentivo del super)"""
    for col in ["precio_tarjeta", "precio_descuento", "precio_internet",
                "precio_normal", "precio_regular"]:
        v = row.get(col)
        if pd.notna(v) and v > 0:
            return v
    return None


# ─── PIPELINE PRINCIPAL ──────────────────────────────────────────────────────

PRECIO_COLS = ["precio_tarjeta", "precio_internet", "precio_normal",
               "precio_descuento", "precio_regular"]

# Schema final con columnas en español y ordenadas para Power BI
COL_RENAME = {
    "supermercado":          "Supermercado",
    "categoria":             "Categoría",
    "familia":               "Familia",
    "nombre":                "Nombre Producto",
    "producto_id":           "ID Producto",
    "sku_id":                "SKU",
    "marca":                 "Marca",
    "url":                   "URL",
    "imagen":                "URL Imagen",
    "vendedor":              "Vendedor",
    "precio_tarjeta":        "Precio Tarjeta (S/)",
    "nombre_tarjeta":        "Nombre Tarjeta",
    "tarjeta_descuento_pct": "Descuento Tarjeta %",
    "precio_internet":       "Precio Internet (S/)",
    "precio_normal":         "Precio Normal (S/)",
    "precio_descuento":      "Precio Descuento (S/)",
    "precio_regular":        "Precio Regular (S/)",
    "precio_efectivo":       "Precio Efectivo (S/)",
    "tiene_descuento":       "Tiene Descuento",
    "descuento_pct":         "Descuento %",
    "fecha_extraccion":      "Fecha Extracción",
}

ORDEN_COLUMNAS = [
    "Supermercado", "Categoría", "Familia", "Nombre Producto",
    "ID Producto", "SKU", "Marca",
    "Precio Efectivo (S/)",
    "Precio Tarjeta (S/)", "Nombre Tarjeta", "Descuento Tarjeta %",
    "Precio Internet (S/)", "Precio Normal (S/)",
    "Precio Descuento (S/)", "Precio Regular (S/)",
    "Tiene Descuento", "Descuento %", "Fecha Extracción",
    "URL", "URL Imagen", "Vendedor",
]


def normalizar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica todas las normalizaciones y devuelve un DataFrame listo para Power BI.

    No modifica el DataFrame original (trabaja sobre una copia).
    """
    df = df.copy()

    # 1. Supermercado: codename → nombre display
    df["supermercado"] = (
        df["supermercado"].astype(str).str.strip().str.lower()
        .map(SUPERMERCADO_DISPLAY)
        .fillna(df["supermercado"])
    )

    # 2. Categoría y Familia: slug → Title Case legible
    df["categoria"] = df["categoria"].apply(slug_a_titulo)
    df["familia"]   = df["familia"].apply(slug_a_titulo)

    # 3. Campos de texto libre — limpiar invisibles, comillas, espacios
    for col in ["nombre", "marca", "vendedor"]:
        if col in df.columns:
            df[col] = df[col].apply(limpiar_texto)

    # 4. Marca: Title Case + arreglar apóstrofes destruidos por .title()
    df["marca"] = (
        df["marca"].astype(str).str.strip().str.title()
        .apply(_fix_apostrofe_casing)
    )

    # 5. Precios: a numérico (strings vacíos / "None" → NaN)
    for col in PRECIO_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 5b. Tarjeta descuento %: numérico, opcional
    if "tarjeta_descuento_pct" in df.columns:
        df["tarjeta_descuento_pct"] = pd.to_numeric(
            df["tarjeta_descuento_pct"], errors="coerce"
        )

    # 5c. Limpiar nombre_tarjeta de NBSPs/espacios extras
    if "nombre_tarjeta" in df.columns:
        df["nombre_tarjeta"] = df["nombre_tarjeta"].apply(limpiar_texto)

    # 6. Precio efectivo: el mejor disponible para el cliente
    # (eliminar cualquier columna pre-existente para evitar duplicados)
    df = df.drop(columns=["precio_efectivo", "Precio Efectivo (S/)"], errors="ignore")
    df["precio_efectivo"] = df.apply(calcular_precio_efectivo, axis=1)
    df["precio_efectivo"] = pd.to_numeric(df["precio_efectivo"], errors="coerce")

    # 7. Tiene descuento: bool → Sí/No
    df["tiene_descuento"] = df["tiene_descuento"].apply(bool_a_si_no)

    # 8. Descuento %: numérico, vacíos → 0
    df["descuento_pct"] = pd.to_numeric(
        df["descuento_pct"], errors="coerce"
    ).fillna(0).astype(float)

    # 9. Fecha: formato YYYY-MM-DD
    df["fecha_extraccion"] = (
        pd.to_datetime(df["fecha_extraccion"], errors="coerce")
        .dt.strftime("%Y-%m-%d")
    )

    # 10. Renombrar columnas al esquema español
    df = df.rename(columns=COL_RENAME)

    # 11. Reordenar (sólo columnas que existen — robusto a datasets parciales)
    cols_finales = [c for c in ORDEN_COLUMNAS if c in df.columns]
    df = df[cols_finales]

    return df


def construir_resumen(df_normalizado: pd.DataFrame) -> pd.DataFrame:
    """Genera la tabla resumen de la hoja 'Resumen Competitivo'.
    Espera que df_normalizado ya pasó por normalizar_df()."""
    g = df_normalizado.groupby(["Supermercado", "Categoría"])
    summary = g.agg(
        total_productos = ("Nombre Producto",       "count"),
        con_precio      = ("Precio Efectivo (S/)",  "count"),
        precio_min      = ("Precio Efectivo (S/)",  "min"),
        precio_max      = ("Precio Efectivo (S/)",  "max"),
        precio_prom     = ("Precio Efectivo (S/)",  "mean"),
        con_descuento   = ("Tiene Descuento",       lambda x: (x == "Sí").sum()),
        desc_prom       = ("Descuento %",           "mean"),
    ).reset_index()

    summary.columns = [
        "Supermercado", "Categoría", "Total Productos", "Con Precio",
        "Precio Mín (S/)", "Precio Máx (S/)", "Precio Promedio (S/)",
        "Con Descuento", "Descuento Promedio %",
    ]
    summary["Precio Promedio (S/)"] = summary["Precio Promedio (S/)"].round(2)
    summary["Descuento Promedio %"] = summary["Descuento Promedio %"].round(2)
    return summary


# ─── SELF-TEST RÁPIDO ────────────────────────────────────────────────────────
# Permite ejecutar `python normalizar.py` para verificar que las funciones andan

if __name__ == "__main__":
    casos = [
        ("Bondiola Duroc\u00A0Redondos x Kg",  "Bondiola Duroc Redondos x Kg"),
        ("Pulpa De  Carambola Doypack",         "Pulpa De Carambola Doypack"),
        ('Macetitas \u201CCoquette\u201D 450g', 'Macetitas "Coquette" 450g'),
        ("  Lomo Fino  Tottus  ",               "Lomo Fino Tottus"),
    ]
    print("Test de limpiar_texto():")
    for entrada, esperado in casos:
        got = limpiar_texto(entrada)
        ok = "✓" if got == esperado else "✗"
        print(f"  {ok} {repr(entrada)} → {repr(got)}")

    print("\nTest de _fix_apostrofe_casing():")
    for entrada, esperado in [("Bell'S", "Bell's"), ("Bell'S Frescos", "Bell's Frescos"),
                               ("O'Higgins", "O'Higgins"), ("Tottus", "Tottus")]:
        got = _fix_apostrofe_casing(entrada)
        ok = "✓" if got == esperado else "✗"
        print(f"  {ok} {repr(entrada)} → {repr(got)}")