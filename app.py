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
from normalizar import normalizar_df

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

    # (G) Aviso preventivo de saturación: más de ~8 líneas se vuelve ilegible.
    if len(seleccionados) > 8:
        st.warning(
            f"Seleccionaste {len(seleccionados)} productos. Con más de 8 líneas "
            "el gráfico se satura y cuesta distinguirlas. Te recomiendo comparar "
            "de a pocos, o usar la leyenda para aislar una serie."
        )

    if seleccionados:
        mask = dff.set_index(["supermercado", "producto_id"]).index.isin(seleccionados)
        # Copia para no mutar dff; etiqueta "super · producto" como identidad de serie.
        evol = dff[mask].copy()
        evol["serie"] = evol["supermercado"] + " · " + evol["nombre"]

        # (F) Selección interactiva: clic en la leyenda para aislar/resaltar una serie.
        sel_serie = alt.selection_point(fields=["serie"], bind="legend")

        # (E) Fechas del eje X en español y formato día-mes legible.
        eje_x = alt.X(
            "fecha_extraccion:T",
            title="Fecha",
            axis=alt.Axis(format="%d %b", labelAngle=0),
        )
        eje_y = alt.Y(
            "precio_descuento:Q",
            title="Precio (S/)",
            scale=alt.Scale(zero=False),
        )

        # (A) Paleta categórica de alto contraste (tableau10, colorblind-friendly).
        # (B) El color mapea la etiqueta completa "super · producto", no solo el nombre;
        #     leyenda horizontal abajo, con más ancho de etiqueta para no truncar.
        color = alt.Color(
            "serie:N",
            title="Producto (supermercado · nombre)",
            scale=alt.Scale(scheme="tableau10"),
            legend=alt.Legend(
                orient="bottom",
                columns=2,
                labelLimit=420,
                symbolType="stroke",
                titleLimit=400,
            ),
        )

        # (D) Segunda señal visual: forma del punto según supermercado, para
        #     distinguir series aunque dos colores se parezcan.
        shape = alt.Shape("supermercado:N", title="Supermercado")

        # (C) Tooltip enriquecido y en español, con formato de moneda y fecha.
        tooltip = [
            alt.Tooltip("serie:N", title="Producto"),
            alt.Tooltip("supermercado:N", title="Supermercado"),
            alt.Tooltip("fecha_extraccion:T", title="Fecha", format="%d %b %Y"),
            alt.Tooltip("precio_descuento:Q", title="Precio (S/)", format=".2f"),
            alt.Tooltip("precio_regular:Q", title="Precio regular (S/)", format=".2f"),
        ]
        if "descuento_pct" in evol.columns:
            tooltip.append(
                alt.Tooltip("descuento_pct:Q", title="Descuento", format=".1f")
            )

        # Opacidad ligada a la selección: la serie elegida resalta, el resto se atenúa.
        opacidad = alt.condition(sel_serie, alt.value(1.0), alt.value(0.15))

        base = alt.Chart(evol).encode(
            x=eje_x,
            y=eje_y,
            color=color,
            opacity=opacidad,
            tooltip=tooltip,
        )

        lineas = base.mark_line(strokeWidth=2.5).encode(
            detail="serie:N",
        )
        puntos = base.mark_point(size=70, filled=True).encode(
            shape=shape,
        )

        chart = (
            (lineas + puntos)
            .add_params(sel_serie)
            .properties(height=460)
            .interactive()  # zoom/pan con rueda y arrastre
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "Tip: clic en un producto de la leyenda para aislarlo · "
            "rueda o arrastre para hacer zoom · doble clic para resetear."
        )

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

    # Normaliza solo lo que se descarga (no afecta filtros ni graficos del dashboard):
    # nombres de columna en espanol, slugs legibles, Si/No, limpieza de NBSP/comillas.
    # utf-8-sig anade el BOM para que Excel en Windows muestre la N y los acentos
    # correctamente, sin simbolos raros.
    if export_df.empty:
        datos_csv = b""
    else:
        export_norm = normalizar_df(export_df)
        datos_csv = export_norm.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        "⬇️ Descargar CSV",
        data=datos_csv,
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
