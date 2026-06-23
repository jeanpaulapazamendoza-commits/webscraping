"""
Exporta el histórico de precios a Google Sheets. Reemplaza el rol que tenía
build_excel.py en el pipeline automático: en vez de un .xlsx local, el
entregable es un Google Sheet con 3 pestañas:

    Histórico            — append-only, día a día, SIN rotación. Si se llega
                            al límite de 10M celdas de Sheets, esta función
                            lo loguea y sigue (hay que archivar/rotar a mano).
    Snapshot Actual       — último valor por producto. Se sobreescribe.
    Resumen Competitivo   — agregación por supermercado x categoría. Se sobreescribe.

Credenciales — dos modos posibles:

  1. Service account (si tu proyecto de Google Cloud permite crear claves):
       GOOGLE_CREDS_JSON   contenido JSON completo de la service account (CI)
       GOOGLE_CREDS_FILE   ruta a un archivo de credenciales local
                           (default: service_account.json, gitignored)

  2. OAuth de usuario (si una política de la organización bloquea la creación
     de claves de service account — caso común en cuentas personales nuevas
     de Google Cloud):
       GOOGLE_OAUTH_CLIENT_FILE   ruta al "OAuth client ID" tipo Desktop app
                                  descargado de Google Cloud Console
                                  (default: oauth_client.json, gitignored)
       GOOGLE_OAUTH_TOKEN_FILE    ruta donde gspread guarda/lee el token ya
                                  autorizado (default: authorized_user.json,
                                  gitignored). La primera corrida abre el
                                  navegador para loguearte y aprobar el
                                  acceso; las siguientes lo reusan sin pedir
                                  nada.

Se intenta primero el modo service account; si no hay credenciales de ese
tipo, se cae al modo OAuth de usuario.

Spreadsheet destino:
    GOOGLE_SHEET_ID     ID del Google Sheet (de su URL)

Uso:
    python export_sheets.py     # solo recalcula Snapshot Actual + Resumen
                                  (sin filas nuevas que appendear al Histórico)

scrape_all.py importa exportar() y le pasa las filas de la corrida actual
para que se agreguen al Histórico.
"""

import json
import os
from pathlib import Path

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from historico_io import cargar_historico
from normalizar import normalizar_df, construir_resumen

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HOJA_HISTORICO = "Histórico"
HOJA_SNAPSHOT = "Snapshot Actual"
HOJA_RESUMEN = "Resumen Competitivo"


def _autenticar():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    creds_file = os.environ.get("GOOGLE_CREDS_FILE", "service_account.json")
    if Path(creds_file).exists():
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        return gspread.authorize(creds)

    oauth_client_file = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "oauth_client.json")
    if Path(oauth_client_file).exists():
        token_file = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "authorized_user.json")
        return gspread.oauth(
            scopes=SCOPES,
            credentials_filename=oauth_client_file,
            authorized_user_filename=token_file,
        )

    raise RuntimeError(
        "No hay credenciales de Google: definí GOOGLE_CREDS_JSON/GOOGLE_CREDS_FILE "
        f"(service account, buscado en '{creds_file}') o poné un "
        f"GOOGLE_OAUTH_CLIENT_FILE (OAuth de usuario, buscado en '{oauth_client_file}')"
    )


def _abrir_spreadsheet(cliente):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Falta la variable de entorno GOOGLE_SHEET_ID")
    return cliente.open_by_key(sheet_id)


def _hoja(spreadsheet, nombre):
    try:
        return spreadsheet.worksheet(nombre)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=nombre, rows=1000, cols=25)


def _df_a_valores(df: pd.DataFrame) -> list:
    """DataFrame -> lista de listas para gspread, con NaN -> ''."""
    df = df.where(pd.notna(df), "")
    return [df.columns.tolist()] + df.astype(str).values.tolist()


def _sobrescribir(ws, df: pd.DataFrame):
    ws.clear()
    if df.empty:
        return
    ws.update(_df_a_valores(df), value_input_option="USER_ENTERED")


def ultimo_snapshot_por_producto(df_norm: pd.DataFrame) -> pd.DataFrame:
    """Última fila por (Supermercado, ID Producto) según Fecha Extracción.
    Misma idea que `ultima_fecha_por_prod` en app.py."""
    return (
        df_norm.sort_values("Fecha Extracción")
        .groupby(["Supermercado", "ID Producto"], as_index=False)
        .tail(1)
    )


def actualizar_snapshot_y_resumen(spreadsheet):
    df_raw = cargar_historico()
    if df_raw.empty:
        print("  Sin histórico todavía, se omite Snapshot/Resumen.")
        return
    df_norm = normalizar_df(df_raw)

    snapshot = ultimo_snapshot_por_producto(df_norm)
    _sobrescribir(_hoja(spreadsheet, HOJA_SNAPSHOT), snapshot)
    print(f"  {HOJA_SNAPSHOT}: {len(snapshot)} filas")

    resumen = construir_resumen(df_norm)
    _sobrescribir(_hoja(spreadsheet, HOJA_RESUMEN), resumen)
    print(f"  {HOJA_RESUMEN}: {len(resumen)} filas")


def append_historico_sheet(spreadsheet, rows: list[dict]):
    """Agrega SOLO las filas nuevas de la corrida actual a 'Histórico'.
    Nunca reescribe lo existente. Sin rotación automática: si la API rechaza
    el append por límite de celdas, se loguea y se sigue (acción manual)."""
    if not rows:
        return
    df_norm = normalizar_df(pd.DataFrame(rows))
    valores = _df_a_valores(df_norm)

    ws = _hoja(spreadsheet, HOJA_HISTORICO)
    primera_celda = ws.acell("A1").value
    filas_a_subir = valores if not primera_celda else valores[1:]

    try:
        ws.append_rows(filas_a_subir, value_input_option="USER_ENTERED")
        print(f"  {HOJA_HISTORICO}: +{len(filas_a_subir)} filas agregadas")
    except gspread.exceptions.APIError as e:
        print(f"  ⚠ No se pudo agregar a '{HOJA_HISTORICO}' (¿límite de celdas?): {e}")
        print("    -> Acción manual: archivar este Sheet y crear uno nuevo.")


def exportar(rows_nuevas: list[dict] | None = None):
    print("📤 Exportando a Google Sheets...")
    cliente = _autenticar()
    spreadsheet = _abrir_spreadsheet(cliente)

    if rows_nuevas:
        append_historico_sheet(spreadsheet, rows_nuevas)
    actualizar_snapshot_y_resumen(spreadsheet)

    print(f"✅ Sheet actualizado: {spreadsheet.url}")


if __name__ == "__main__":
    exportar()
