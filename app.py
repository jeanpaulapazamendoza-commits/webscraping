"""
Dashboard de precios de supermercados peruanos.

Lee el histórico de scrapeos desde data/historico_YYYY-MM.csv (particionado por
mes, vía historico_io.py) y muestra:
- KPIs generales
- Distribución de precios
- Comparación entre supermercados
- Evolución temporal de precio por producto

Para correr localmente:
    streamlit run app.py

Para deploy:
    1. Push a GitHub
    2. share.streamlit.io → conectar repo → seleccionar app.py
"""

import pandas as pd
import streamlit as st
import altair as alt

from historico_io import cargar_historico

st.set_page_config(
    page_title="Precios Supermercados Perú",
    page_icon="🛒",
    layout="wide",
)

# ── Carga de datos ──────────────────────────────────────────────────────────

COLS_NUMERICAS = [
    "precio_tarjeta", "tarjeta_descuento_pct", "precio_internet",
    "precio_normal", "precio_descuento", "precio_regular", "descuento_pct",
]


@st.cache_data(ttl=3600)
def cargar_datos() -> pd.DataFrame:
    df = cargar_historico()
    if df.empty:
        return df
    df["fecha_extraccion"] = pd.to_datetime(df["fecha_extraccion"])
    for c in COLS_NUMERICAS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["tiene_descuento"] = df["tiene_descuento"] == "True"
    return df


df = cargar_datos()

if df.empty:
    st.error("No se encontró histórico en `data/historico_*.csv`. Ejecutá el scraper primero.")
    st.stop()


# ── Sidebar: filtros ────────────────────────────────────────────────────────

st.sidebar.title("🔎 Filtros")

supers = sorted(df["supermercado"].unique())
super_sel = st.sidebar.multiselect("Supermercado", supers, default=supers)

# Familia (agrupador macro): res, pollo, cerdo, frutas, verduras, etc.
# Solo aparece si la columna existe (compatibilidad con datos viejos sin familia)
familia_sel = None
if "familia" in df.columns:
    familias = sorted(df["familia"].dropna().unique())
    familia_sel = st.sidebar.multiselect("Familia", familias, default=familias)

# Categoría detalle: la subcategoría específica de cada sitio
cats = sorted(df["categoria"].unique())
with st.sidebar.expander("Categoría detalle", expanded=False):
    cat_sel = st.multiselect("", cats, default=cats, label_visibility="collapsed")

solo_descuento = st.sidebar.checkbox("Solo con descuento", value=False)

# Aplicar filtros
dff = df[df["supermercado"].isin(super_sel) & df["categoria"].isin(cat_sel)]
if familia_sel is not None:
    dff = dff[dff["familia"].isin(familia_sel)]
if solo_descuento:
    dff = dff[dff["tiene_descuento"]]

# El "snapshot actual" = última fecha de cada producto
ultima_fecha_por_prod = (
    dff.sort_values("fecha_extraccion")
       .groupby(["supermercado", "producto_id"])
       .tail(1)
)
n_snapshots = dff["fecha_extraccion"].nunique()


# ── Header ──────────────────────────────────────────────────────────────────

st.title("🛒 Precios Supermercados Perú")
st.caption(f"Histórico de {n_snapshots} snapshots · "
           f"última actualización: {dff['fecha_extraccion'].max():%Y-%m-%d %H:%M}")


# ── KPIs ────────────────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)
col1.metric("Productos únicos", f"{ultima_fecha_por_prod['producto_id'].nunique():,}")
col2.metric("Con descuento", f"{ultima_fecha_por_prod['tiene_descuento'].sum():,}")
col3.metric("Precio mín (S/)", f"{ultima_fecha_por_prod['precio_descuento'].min():.2f}")
col4.metric("Precio máx (S/)", f"{ultima_fecha_por_prod['precio_descuento'].max():.2f}")


# ── Tab 1: Snapshot actual ─────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📋 Snapshot actual",
    "📊 Distribución",
    "🏷️ Top descuentos",
    "📈 Evolución temporal",
    "📅 Histórico y exportar",
])

with tab1:
    st.subheader("Productos al último snapshot")
    cols_show = ["supermercado", "familia", "categoria", "nombre", "marca",
                 "precio_descuento", "precio_regular", "descuento_pct",
                 "tiene_descuento", "url"]
    cols_show = [c for c in cols_show if c in ultima_fecha_por_prod.columns]
    st.dataframe(
        ultima_fecha_por_prod[cols_show]
            .sort_values(["supermercado", "categoria", "precio_descuento"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Link"),
            "precio_descuento": st.column_config.NumberColumn(format="S/ %.2f"),
            "precio_regular":   st.column_config.NumberColumn(format="S/ %.2f"),
            "descuento_pct":    st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

with tab2:
    eje_x_col = "familia" if "familia" in ultima_fecha_por_prod.columns else "categoria"
    st.subheader(f"Distribución de precios por supermercado y {eje_x_col}")
    chart = (
        alt.Chart(ultima_fecha_por_prod)
        .mark_boxplot(extent="min-max")
        .encode(
            x=alt.X(f"{eje_x_col}:N", title=eje_x_col.capitalize()),
            y=alt.Y("precio_descuento:Q", title="Precio (S/)", scale=alt.Scale(zero=False)),
            color=alt.Color("supermercado:N", title="Supermercado"),
            column=alt.Column("supermercado:N", title=None),
        )
        .properties(height=400)
    )
    st.altair_chart(chart, use_container_width=False)

with tab3:
    st.subheader("Top 20 descuentos del snapshot actual")
    top = (
        ultima_fecha_por_prod[ultima_fecha_por_prod["tiene_descuento"]]
            .sort_values("descuento_pct", ascending=False)
            .head(20)
    )
    if top.empty:
        st.info("No hay productos con descuento en los filtros actuales.")
    else:
        bars = (
            alt.Chart(top)
            .mark_bar()
            .encode(
                y=alt.Y("nombre:N", sort="-x", title=None),
                x=alt.X("descuento_pct:Q", title="Descuento %"),
                color=alt.Color("supermercado:N"),
                tooltip=["nombre", "supermercado", "precio_descuento",
                         "precio_regular", "descuento_pct"],
            )
            .properties(height=500)
        )
        st.altair_chart(bars, use_container_width=True)

with tab4:
    st.subheader("Evolución de precio por producto")

    if n_snapshots < 2:
        st.info(
            "Necesitás al menos 2 snapshots para ver evolución. "
            f"Actualmente hay {n_snapshots}. "
            "Después de la segunda corrida del scraper, este gráfico se llenará."
        )
        st.divider()
        st.caption("Vista previa de cómo se verá:")

    productos_opciones = (
        ultima_fecha_por_prod.assign(
            label=lambda d: d["supermercado"] + " · " + d["nombre"]
        )
        .set_index(["supermercado", "producto_id"])["label"]
        .to_dict()
    )

    seleccionados = st.multiselect(
        "Elegí productos a comparar",
        options=list(productos_opciones.keys()),
        format_func=lambda k: productos_opciones[k],
        default=list(productos_opciones.keys())[:3] if productos_opciones else [],
    )

    if seleccionados:
        mask = dff.set_index(["supermercado", "producto_id"]).index.isin(seleccionados)
        evol = dff[mask]
        line = (
            alt.Chart(evol)
            .mark_line(point=True)
            .encode(
                x=alt.X("fecha_extraccion:T", title="Fecha"),
                y=alt.Y("precio_descuento:Q", title="Precio (S/)",
                        scale=alt.Scale(zero=False)),
                color=alt.Color("nombre:N", title="Producto"),
                tooltip=["nombre", "fecha_extraccion:T",
                         "precio_descuento", "precio_regular"],
            )
            .properties(height=400)
        )
        st.altair_chart(line, use_container_width=True)

with tab5:
    st.subheader("Fechas disponibles en el histórico")
    resumen_fechas = (
        dff.assign(fecha=dff["fecha_extraccion"].dt.date)
           .groupby("fecha")
           .agg(productos=("producto_id", "nunique"), filas=("producto_id", "size"))
           .reset_index()
           .sort_values("fecha", ascending=False)
           .rename(columns={"fecha": "Fecha", "productos": "Productos únicos", "filas": "Filas"})
    )
    st.dataframe(resumen_fechas, use_container_width=True, hide_index=True)
    st.caption(f"{len(resumen_fechas)} fecha(s) de extracción con los filtros actuales del sidebar.")

    st.divider()

    st.subheader("Exportar a CSV por rango de fechas")
    fechas_extraccion = dff["fecha_extraccion"].dt.date
    fecha_min, fecha_max = fechas_extraccion.min(), fechas_extraccion.max()

    col_a, col_b = st.columns(2)
    fecha_ini = col_a.date_input("Desde", value=fecha_min, min_value=fecha_min, max_value=fecha_max)
    fecha_fin = col_b.date_input("Hasta", value=fecha_max, min_value=fecha_min, max_value=fecha_max)

    export_df = dff[(fechas_extraccion >= fecha_ini) & (fechas_extraccion <= fecha_fin)]
    st.caption(f"{len(export_df):,} filas seleccionadas (del {fecha_ini} al {fecha_fin}).")

    nombre_archivo = (
        f"historico_{fecha_ini}.csv" if fecha_ini == fecha_fin
        else f"historico_{fecha_ini}_a_{fecha_fin}.csv"
    )
    st.download_button(
        "⬇️ Descargar CSV",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name=nombre_archivo,
        mime="text/csv",
        disabled=export_df.empty,
    )


# ── Footer ──────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    f"Total filas en histórico: {len(df):,} · "
    f"Cobertura: {df['supermercado'].nunique()} super × "
    f"{df['categoria'].nunique()} categorías"
)