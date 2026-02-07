import os, json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

def _gs_client():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Faltou GOOGLE_SERVICE_ACCOUNT_JSON")
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)

def _open_sheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Faltou GOOGLE_SHEET_ID")
    return _gs_client().open_by_key(sheet_id)

def month_sheet_name(dt: datetime) -> str:
    return MESES_PT[dt.month]

def export_rows_to_month_sheet(rows, start_dt: datetime, end_dt: datetime):
    """
    Escreve os lançamentos do período na aba do MÊS correspondente (colunas O..U, a partir da linha 4).
    Observação: este export pressupõe que o período está dentro de UM único mês (default: mês atual).
    """
    sh = _open_sheet()

    # garante que período é de 1 mês só (pra não bagunçar abas)
    if start_dt.month != end_dt.month or start_dt.year != end_dt.year:
        raise ValueError("Período deve estar dentro do mesmo mês/ano (ex: 2026-02-01 a 2026-02-28).")

    aba = month_sheet_name(start_dt)
    ws = sh.worksheet(aba)

    # limpa a área O4:U (até um limite alto)
    ws.batch_clear(["O4:U2000"])

    values = []
    for r in rows:
        dt = r["criado_em"]
        if hasattr(dt, "strftime"):
            data_str = dt.strftime("%d/%m/%Y")
        else:
            data_str = str(dt)

        categoria = (r.get("alvo") or "").strip()
        descricao = (r.get("nota") or "").strip()
        valor = float(r["valor"])

        # Se despesa, mantém positivo (o template normalmente já entende pelo contexto),
        # ou se você preferir, pode pôr negativo. Por enquanto: positivo.
        values.append([
            categoria,            # O
            descricao,            # P
            valor,                # Q
            "",                   # R (Cartão)
            data_str,             # S (Data)
            "",                   # T (Parcela)
            "",                   # U (Classificação)
        ])

    if values:
        ws.update("O4", values, value_input_option="USER_ENTERED")
