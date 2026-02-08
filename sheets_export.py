import os, json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from db import get_balance

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

# Abre a planilha e loga qual service account e qual SHEET_ID estão sendo usados
def _open_sheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Faltou GOOGLE_SHEET_ID")

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Faltou GOOGLE_SERVICE_ACCOUNT_JSON")

    # descobre o client_email do JSON sem expor a chave
    try:
        info = json.loads(raw)
        print("DEBUG client_email =", info.get("client_email"))
    except Exception as e:
        raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON inválido: {e}")

    print("DEBUG GOOGLE_SHEET_ID =", sheet_id)

    return _gs_client().open_by_key(sheet_id)

def month_sheet_name(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"  # 2026-02

# Exporta os lançamentos e atualiza cards e donut na aba do mês no Google Sheets
def export_rows_to_month_sheet(user_id: int, rows, start_dt: datetime, end_dt: datetime):
    sh = _open_sheet()

    # Garante que o período esteja dentro de um único mês
    if start_dt.month != end_dt.month or start_dt.year != end_dt.year:
        raise ValueError("Período deve estar dentro do mesmo mês/ano (ex: 2026-02-01 a 2026-02-28).")

    aba = month_sheet_name(start_dt)
    ws, created = ensure_month_ws(sh, aba)

    # Limpa apenas as áreas de dados se a aba já existia
    if not created:
        ws.batch_clear(["A41:F2000", "B12:C37", "C4:C7"])

    # Monta linhas de lançamentos (A..F)
    values = []
    total_rec = 0.0
    total_des = 0.0

    for r in rows:
        dt = r["criado_em"]
        data_str = dt.strftime("%d/%m/%Y") if hasattr(dt, "strftime") else str(dt)

        tipo = r.get("tipo", "")
        categoria = (r.get("alvo") or "").strip()
        descricao = (r.get("nota") or "").strip()
        valor = float(r["valor"])
        origem = r.get("origem", "") or ""

        values.append([
            data_str,   # A (Data)
            tipo,       # B (Tipo)
            categoria,  # C (Categoria)
            descricao,  # D (Descrição)
            valor,      # E (Valor)
            origem,     # F (Origem)
        ])

        if tipo == "receita":
            total_rec += valor
        elif tipo == "despesa":
            total_des += valor

    # Escreve os lançamentos a partir de A41
    if values:
        ws.update("A41", values, value_input_option="USER_ENTERED")

    # Preenche os cards (C4:C7)
    saldo_periodo = total_rec - total_des
    saldo_atual = get_balance(user_id)  # se precisar do user_id, passe como parâmetro

    ws.update("C4", [[total_rec]], value_input_option="USER_ENTERED")
    ws.update("C5", [[total_des]], value_input_option="USER_ENTERED")
    ws.update("C6", [[saldo_periodo]], value_input_option="USER_ENTERED")
    ws.update("C7", [[saldo_atual]], value_input_option="USER_ENTERED")

    # Preenche a tabela fonte do donut (B12:C37)
    despesas_por_categoria = {}
    for r in rows:
        if r.get("tipo") != "despesa":
            continue
        cat = (r.get("alvo") or "Sem categoria").strip()
        despesas_por_categoria[cat] = despesas_por_categoria.get(cat, 0.0) + float(r["valor"])

    items = sorted(despesas_por_categoria.items(), key=lambda x: x[1], reverse=True)
    if len(items) > 26:
        top = items[:25]
        outros = sum(v for _, v in items[25:])
        items = top + [("Outros", outros)]
    else:
        items = items[:26]

    donut_values = [[cat, float(total)] for cat, total in items]
    if donut_values:
        ws.update("B12", donut_values, value_input_option="USER_ENTERED")



# Garante que exista a aba do mês no Google Sheets, duplicando o TEMPLATE se necessário
def ensure_month_ws(sh, aba, template_name="TEMPLATE"):
    try:
        return sh.worksheet(aba), False
    except gspread.WorksheetNotFound:
        template = sh.worksheet(template_name)
        ws = template.duplicate(new_sheet_name=aba)
        return ws, True

