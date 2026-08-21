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

# Altair rechaza por defecto cualquier gráfico de más de 5.000 filas. El snapshot
# diario ronda las 3.500 y crece con el catálogo, así que fijamos un techo
# explícito en vez de descubrir el MaxRowsError en producción. Aun así, a cada
# gráfico le pasamos solo las columnas que dibuja (ver tab2/tab3/tab4): Altair
# serializa a JSON todo lo que reciba, y mandarle las 20 columnas del histórico
# infla la página sin ningún beneficio.
alt.data_transformers.enable("default", max_rows=50_000)

# ── Carga de datos ──────────────────────────────────────────────────────────

COLS_PRECIO = [
    "precio_tarjeta", "precio_internet", "precio_normal",
    "precio_descuento", "precio_regular",
]
COLS_NUMERICAS = COLS_PRECIO + ["tarjeta_descuento_pct", "descuento_pct"]


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

    # VTEX publica Price = 0 cuando el producto está en catálogo pero sin precio
    # (casi siempre por falta de stock): 0,9% del histórico, y 2.161 de esas
    # 2.175 filas son de Plaza Vea. Cero no es un precio, así que lo pasamos a
    # nulo: si no, el KPI de precio mínimo muestra "S/ 0.00" y los promedios y
    # boxplots quedan arrastrados hacia abajo por productos que no se venden.
    for c in COLS_PRECIO:
        if c in df.columns:
            df.loc[df[c] <= 0, c] = float("nan")
    return df


def soles(v) -> str:
    """Precio para un KPI. Devuelve un guión si no hay dato, en vez de 'nan'."""
    return f"{v:.2f}" if pd.notna(v) else "—"


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
    # El label va oculto (el expander ya dice "Categoría detalle") pero no puede
    # ir vacío: Streamlit lo desaconseja por accesibilidad y avisa que en el
    # futuro va a ser un error.
    cat_sel = st.multiselect("Categoría detalle", cats, default=cats,
                             label_visibility="collapsed")

solo_descuento = st.sidebar.checkbox("Solo con descuento", value=False)

# Aplicar filtros
dff = df[df["supermercado"].isin(super_sel) & df["categoria"].isin(cat_sel)]
if familia_sel is not None:
    dff = dff[dff["familia"].isin(familia_sel)]
if solo_descuento:
    dff = dff[dff["tiene_descuento"]]

# Si los filtros no dejan ninguna fila, cortamos acá con un mensaje. Sin esto,
# el .max() sobre una columna de fechas vacía devuelve NaT y el formateo del
# subtítulo revienta con "NaTType does not support strftime" — un stack trace
# en pantalla apenas alguien deselecciona todos los supermercados.
if dff.empty:
    st.title("🛒 Precios Supermercados Perú")
    st.warning(
        "Ningún producto coincide con los filtros actuales. Volvé a activar al "
        "menos un supermercado, una familia y una categoría en el panel de la "
        "izquierda."
    )
    st.stop()

# El "snapshot actual" es la foto del último día CON datos dentro de los filtros,
# no la última fila de cada producto visto alguna vez: un producto que salió del
# catálogo en junio no debe seguir aportando su precio de junio a los KPIs de hoy.
fecha_snapshot = dff["fecha_extraccion"].dt.date.max()
filas_del_dia  = dff[dff["fecha_extraccion"].dt.date == fecha_snapshot]

# Dentro de una misma corrida un producto puede venir dos veces, porque la
# búsqueda por path de VTEX incluye las subcategorías: los de 'cortes-premium'
# reaparecen dentro de 'res-y-otras-carnes'. Nos quedamos con su última lectura.
snapshot_actual = (
    filas_del_dia.sort_values("fecha_extraccion")
                 .groupby(["supermercado", "producto_id"], as_index=False)
                 .tail(1)
)

# Días distintos, no timestamps: cada categoría se scrapea en su propio segundo,
# así que contar 'fecha_extraccion' a secas daba miles de "snapshots" en vez de
# los días de histórico que el usuario espera leer.
n_dias = dff["fecha_extraccion"].dt.date.nunique()


# ── Header ──────────────────────────────────────────────────────────────────

st.title("🛒 Precios Supermercados Perú")
st.caption(
    f"{n_dias} día{'s' if n_dias != 1 else ''} de histórico · "
    f"snapshot del {fecha_snapshot:%d/%m/%Y} con {len(snapshot_actual):,} productos"
)


# ── KPIs ────────────────────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)
# Cada fila del snapshot ya es un producto de un supermercado. Contar
# producto_id a secas se comía 352: Wong y Metro corren sobre el mismo catálogo
# VTEX y reciclan IDs — el 10476 es "Pecana Pelada x kg" en los dos.
col1.metric("Productos únicos", f"{len(snapshot_actual):,}")
col2.metric("Con descuento", f"{snapshot_actual['tiene_descuento'].sum():,}")
col3.metric("Precio mín (S/)", soles(snapshot_actual["precio_descuento"].min()))
col4.metric("Precio máx (S/)", soles(snapshot_actual["precio_descuento"].max()))


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
    cols_show = [c for c in cols_show if c in snapshot_actual.columns]
    st.dataframe(
        snapshot_actual[cols_show]
            .sort_values(["supermercado", "categoria", "precio_descuento"]),
        width="stretch",
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Link"),
            "precio_descuento": st.column_config.NumberColumn(format="S/ %.2f"),
            "precio_regular":   st.column_config.NumberColumn(format="S/ %.2f"),
            "descuento_pct":    st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

with tab2:
    eje_x_col = "familia" if "familia" in snapshot_actual.columns else "categoria"
    st.subheader(f"Distribución de precios por supermercado y {eje_x_col}")
    datos_dist = snapshot_actual[["supermercado", eje_x_col, "precio_descuento"]]
    chart = (
        alt.Chart(datos_dist)
        .mark_boxplot(extent="min-max")
        .encode(
            x=alt.X(f"{eje_x_col}:N", title=eje_x_col.capitalize()),
            y=alt.Y("precio_descuento:Q", title="Precio (S/)", scale=alt.Scale(zero=False)),
            color=alt.Color("supermercado:N", title="Supermercado"),
            column=alt.Column("supermercado:N", title=None),
        )
        .properties(height=400)
    )
    st.altair_chart(chart, width="content")

with tab3:
    st.subheader("Top 20 descuentos del snapshot actual")
    cols_top = ["nombre", "supermercado", "precio_descuento",
                "precio_regular", "descuento_pct"]
    top = (
        snapshot_actual[snapshot_actual["tiene_descuento"]]
            .sort_values("descuento_pct", ascending=False)
            .head(20)[cols_top]
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
        st.altair_chart(bars, width="stretch")

with tab4:
    st.subheader("Evolución de precio por producto")

    if n_dias < 2:
        st.info(
            "Necesitás al menos 2 días de histórico para ver evolución. "
            f"Actualmente hay {n_dias}. "
            "Después de la segunda corrida del scraper, este gráfico se llenará."
        )
        st.divider()
        st.caption("Vista previa de cómo se verá:")

    productos_opciones = (
        snapshot_actual.assign(
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

    # (G) Selector de vista: "Superpuesto" (todas las series en un panel, compacto)
    # vs "Separado" (small multiples: un mini-panel por producto, sin solapamiento).
    # Util cuando dos productos tienen el mismo precio y sus lineas se pisan.
    modo_vista = st.radio(
        "Ver",
        options=["Superpuesto", "Separado"],
        horizontal=True,
        help="Superpuesto: comparacion compacta en un solo grafico. "
             "Separado: un panel por producto, ideal cuando los precios coinciden "
             "y las lineas se superponen.",
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
        # Copia para no mutar dff.
        evol = dff[mask].copy()

        # Identidad ESTABLE de la serie: (supermercado, producto_id). No usamos el
        # nombre para agrupar, porque un supermercado puede RENOMBRAR el mismo
        # articulo con el tiempo (p.ej. Tottus paso "Palta Fuerte x Kg" a
        # "Palta Fuerte Sin Madurar x Kg"): si agrupamos por nombre, un solo
        # producto se parte en dos lineas de distinto color. La clave por id
        # garantiza una linea continua por producto.
        evol["serie_id"] = (
            evol["supermercado"] + "|" + evol["producto_id"].astype(str)
        )
        # Etiqueta visible = "super · nombre ACTUAL" (el nombre de la fecha mas
        # reciente de cada producto), para que la leyenda muestre el nombre vigente.
        nombre_actual = (
            evol.sort_values("fecha_extraccion")
            .groupby("serie_id")["nombre"]
            .last()
        )
        evol["serie"] = (
            evol["supermercado"] + " · " + evol["serie_id"].map(nombre_actual)
        )

        # Igual que en Distribución: al gráfico solo le pasamos lo que dibuja o
        # muestra en el tooltip, no las 20 columnas del histórico.
        cols_evol = ["fecha_extraccion", "precio_descuento", "serie", "supermercado"]
        cols_evol += [c for c in ("precio_tarjeta", "precio_regular",
                                  "precio_normal", "descuento_pct")
                      if c in evol.columns]
        evol = evol[cols_evol]

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

        # (C) Estilo de linea distinto por serie (solida, guiones, puntos...). Cuando
        #     dos lineas se solapan por tener el mismo precio, el patron de guiones
        #     deja "ver" la de abajo a traves de los huecos, haciendo perceptible que
        #     hay dos series en el mismo trazo.
        dash = alt.StrokeDash("serie:N", title="Producto (supermercado · nombre)",
                              legend=None)

        # (C) Tooltip enriquecido y en español, con formato de moneda y fecha.
        #     Muestra los TRES niveles de precio de la ficha cuando existen:
        #       - precio_tarjeta  = precio con tarjeta del super (p.ej. CMR)
        #       - precio_regular  = precio internet/online sin tarjeta
        #       - precio_normal   = precio de lista original (el tachado, p.ej. 13.45)
        #     Antes solo se veian descuento y regular, y el "normal" (13.45) se perdia.
        #     Cada campo se agrega solo si la columna existe, para no romper con VTEX
        #     (Wong/Metro/Plaza Vea) que a veces no traen los tres niveles.
        tooltip = [
            alt.Tooltip("serie:N", title="Producto"),
            alt.Tooltip("supermercado:N", title="Supermercado"),
            alt.Tooltip("fecha_extraccion:T", title="Fecha", format="%d %b %Y"),
            alt.Tooltip("precio_descuento:Q", title="Precio efectivo (S/)", format=".2f"),
        ]
        if "precio_tarjeta" in evol.columns:
            tooltip.append(
                alt.Tooltip("precio_tarjeta:Q", title="Precio con tarjeta (S/)", format=".2f")
            )
        if "precio_regular" in evol.columns:
            tooltip.append(
                alt.Tooltip("precio_regular:Q", title="Precio regular (S/)", format=".2f")
            )
        if "precio_normal" in evol.columns:
            tooltip.append(
                alt.Tooltip("precio_normal:Q", title="Precio normal / lista (S/)", format=".2f")
            )
        if "descuento_pct" in evol.columns:
            tooltip.append(
                alt.Tooltip("descuento_pct:Q", title="Descuento %", format=".1f")
            )

        if modo_vista == "Superpuesto":
            # Vista compacta: todas las series en un panel. Opacidad ligada a la
            # seleccion (clic en leyenda) para resaltar una serie; estilo de linea
            # (C) para percibir solapamientos.
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
                strokeDash=dash,
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
            st.altair_chart(chart, width="stretch")
            st.caption(
                "Tip: clic en un producto de la leyenda para aislarlo · "
                "rueda o arrastre para hacer zoom · doble clic para resetear · "
                "cambia a \"Separado\" si dos lineas se superponen."
            )
        else:
            # (E) Small multiples: un mini-panel por producto. Nunca hay
            # solapamiento porque cada serie tiene su propio espacio. Todos los
            # paneles comparten la misma escala Y para que la comparacion sea justa.
            n_series = evol["serie"].nunique()
            columnas = 1 if n_series <= 3 else 2
            base_sm = alt.Chart(evol).encode(
                x=eje_x,
                y=eje_y,
                color=alt.Color("serie:N", scale=alt.Scale(scheme="tableau10"),
                                legend=None),
                tooltip=tooltip,
            )
            lineas_sm = base_sm.mark_line(strokeWidth=2.5)
            puntos_sm = base_sm.mark_point(size=55, filled=True)
            # Ancho por panel: un panel ancho si hay 1 columna, mas angosto si hay 2.
            # Numerico (no "container") para que el facet con columnas sea predecible.
            ancho_panel = 640 if columnas == 1 else 320
            chart = (
                (lineas_sm + puntos_sm)
                .properties(height=220, width=ancho_panel)
                .facet(
                    facet=alt.Facet("serie:N", title=None,
                                    header=alt.Header(labelFontSize=13,
                                                      labelFontWeight="bold",
                                                      labelLimit=500)),
                    columns=columnas,
                )
                .resolve_scale(y="shared")
            )
            st.altair_chart(chart, width="stretch")
            st.caption(
                "Cada panel es un producto, todos con la misma escala de precios "
                "para comparar de un vistazo · vuelve a \"Superpuesto\" para la "
                "vista compacta."
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
    st.dataframe(resumen_fechas, width="stretch", hide_index=True)
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
