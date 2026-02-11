import os, json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from db import get_balance
from decimal import Decimal

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _to_sheet_value(x):
    if isinstance(x, Decimal):
        return float(x)
    return x


def _gs_client():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Faltou GOOGLE_SERVICE_ACCOUNT_JSON")
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)

# Abre a planilha e loga qual service account e qual SHEET_ID estão sendo usados
# Abre a planilha e loga o SHEET_ID com repr/len para achar espaços/aspas escondidos
def _open_sheet():
    sheet_id_raw = os.getenv("GOOGLE_SHEET_ID")

    if not sheet_id_raw:
        raise RuntimeError("Faltou GOOGLE_SHEET_ID")

    sheet_id = sheet_id_raw.strip().strip('"').strip("'")
    return _gs_client().open_by_key(sheet_id)

def get_sheet_links(worksheet=None):
    """
    Retorna (url_planilha, url_aba).
    url_aba aponta direto pra aba (gid) se worksheet for informado.
    """
    sheet_id_raw = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id_raw:
        raise RuntimeError("Faltou GOOGLE_SHEET_ID")

    sheet_id = sheet_id_raw.strip().strip('"').strip("'")
    base_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    if worksheet is None:
        return base_url, base_url

    try:
        gid = worksheet.id  # gspread Worksheet id
        return base_url, f"{base_url}#gid={gid}"
    except Exception:
        return base_url, base_url



def month_sheet_name(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"  # 2026-02

# Ignora ações (criar/apagar), mas mantém qualquer movimentação de dinheiro
def _is_monetary_row(r) -> bool:
    tipo = (r.get("tipo") or "").lower()
    valor = r.get("valor")

    # 1) Se for ação administrativa, ignora
    if any(k in tipo for k in ("criar", "criou", "apagar", "apagou", "delete", "remover", "removeu")):
        return False

    # 2) Se não tem valor, não é movimentação monetária
    if valor is None:
        return False

    # 3) Valor = 0 (ou vazio) normalmente é ação; ignora
    try:
        if float(valor) == 0.0:
            return False
    except Exception:
        return False

    # 4) Caso contrário: é movimentação
    return True


# Exporta lançamentos monetários para a aba do mês e atualiza cards + donut no template do Sheets.
def export_rows_to_month_sheet(user_id: int, rows, start_dt: datetime, end_dt: datetime, worksheet_name: str | None = None):

    sh = _open_sheet()

    # Garante que o período esteja dentro de um único mês
    if start_dt.month != end_dt.month or start_dt.year != end_dt.year:
        raise ValueError("Período deve estar dentro do mesmo mês/ano (ex: 2026-02-01 a 2026-02-28).")

    aba = worksheet_name or month_sheet_name(start_dt)
    ws, created = ensure_month_ws(sh, aba)


    # Limpa apenas as áreas de dados se a aba já existia
    if not created:
        ws.batch_clear(["A44:Q2000", "B12:C37", "C4:C7"])

    # Registra somente movimentações monetárias (ignora ações administrativas tipo criar/apagar caixinha/investimento)
    def _is_money_movement(r: dict) -> bool:
        tipo = (r.get("tipo") or "").strip().lower()
        # Tipos que SEMPRE são movimento de dinheiro
        if tipo in {"receita", "despesa", "transferencia", "transferência", "saque", "deposito", "depósito",
                    "investimento", "aplicacao", "aplicação", "resgate", "resgate_investimento", "aporte_investimento"}:
            return True

        # Se tiver valor numérico != 0, consideramos movimento
        try:
            v = float(r.get("valor") or 0)
        except Exception:
            v = 0.0
        if abs(v) > 0:
            # Mas bloqueia explicitamente ações administrativas mais comuns
            bloqueados = {
                "criar_caixinha", "apagar_caixinha", "deletar_caixinha",
                "criar_investimento", "apagar_investimento", "deletar_investimento",
                "criar_categoria", "apagar_categoria", "deletar_categoria",
            }
            return tipo not in bloqueados

        return False

    # Monta linhas de lançamentos (A..F)
    values = []
    total_rec = 0.0
    total_des = 0.0
    despesas_por_categoria: dict[str, float] = {}

    for r in rows:
        if not _is_money_movement(r):
            continue

        dt = r.get("criado_em")
        data_str = dt.strftime("%d/%m/%Y") if hasattr(dt, "strftime") else (str(dt) if dt else "")

        tipo = (r.get("tipo") or "").strip().lower()

        # Categoria: qualquer uma (não limita). Se vazio, cai em "Outros"
        categoria = (r.get("alvo") or "").strip() or "Outros"

        # Descrição
        descricao = (r.get("nota") or "").strip()

        # Valor sempre numérico pro Sheets
        valor = float(r.get("valor") or 0)

        # Origem (se tiver)
        origem = (r.get("origem") or "").strip()

        values.append([
            tipo,       # B (Tipo)
            categoria,  # C (Categoria)
            descricao,  # D (Descrição)
            _to_sheet_value(valor),  # E (Valor)
            data_str,   # A (Data)
            origem,     # F (Origem)
        ])

        if tipo == "receita":
            total_rec += valor

        elif tipo == "despesa":
            total_des += valor
            despesas_por_categoria[categoria] = despesas_por_categoria.get(categoria, 0.0) + float(valor)

        elif tipo == "aporte_investimento":
            # Aporte é saída de dinheiro da conta -> entra como "categoria" no dashboard
            total_des += valor
            despesas_por_categoria["investimentos"] = despesas_por_categoria.get("investimentos", 0.0) + float(valor)

        elif tipo in {"resgate_investimento", "resgate"}:
            # Resgate é entrada na conta. Se você quiser que apareça em "Categorias" também,
            # dá pra somar em receitas_por_categoria (mas seu dashboard hoje só plota despesas).
            total_rec += valor


    # Envia lançamentos + cards + donut em batch_update (mais estável)
    saldo_periodo = total_rec - total_des
    saldo_atual = get_balance(user_id)

    updates = []

    if values:
        updates.append({"range": "A44", "values": values})

    # Cards (C4:C7) — labels estão em B4:B7 no seu template
    updates.append({"range": "C4", "values": [[_to_sheet_value(total_rec)]]})
    updates.append({"range": "C5", "values": [[_to_sheet_value(total_des)]]})
    updates.append({"range": "C6", "values": [[_to_sheet_value(saldo_periodo)]]})
    updates.append({"range": "C7", "values": [[_to_sheet_value(saldo_atual)]]})

    # Donut source (B12:C37)
    items = sorted(despesas_por_categoria.items(), key=lambda x: x[1], reverse=True)

    # Limita a 26 linhas no donut: top 25 + "Outros"
    if len(items) > 26:
        top = items[:25]
        outros = sum(v for _, v in items[25:])
        items = top + [("Outros", outros)]
    else:
        items = items[:26]

    donut_values = [[cat, _to_sheet_value(total)] for cat, total in items]
    if donut_values:
        updates.append({"range": "B12", "values": donut_values})

    # Executa tudo de uma vez
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    # Retorna links pra mostrar no Discord
    _base, _tab = get_sheet_links(ws)
    return _tab



# Garante que exista a aba do mês no Google Sheets, duplicando o TEMPLATE se necessário
def ensure_month_ws(sh, aba, template_name="TEMPLATE"):
    try:
        return sh.worksheet(aba), False
    except gspread.WorksheetNotFound:
        template = sh.worksheet(template_name)
        ws = template.duplicate(new_sheet_name=aba)
        return ws, True

