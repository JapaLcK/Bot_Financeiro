import os, json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from decimal import Decimal
from db import get_balance, list_user_category_rules, update_launch_category
from utils_text import normalize_text, contains_word, LOCAL_RULES

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

# DEPRECATED (2026-02-20)
# Não usar mais. Substituído por export_rows_to_dados().
# Mantido temporariamente por segurança durante a migração.
# Após migração, conferir e remover.
# Exporta lançamentos monetários para a aba do mês e atualiza cards + donut no template do Sheets.

def export_rows_to_month_sheet(user_id: int, rows, start_dt: datetime, end_dt: datetime, worksheet_name: str | None = None):
    raise RuntimeError("DEPRECATED: use export_rows_to_dados() (não cria aba mensal).")
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
                    "investimento", "aplicacao", "aplicação", "resgate"}:
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

        # ✅ ISO (YYYY-MM-DD) pra Sheets reconhecer como data real
        if hasattr(dt, "date"):
            data_str = dt.date().isoformat()
        else:
            data_str = ""


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

# -----------------------------
# NOVO: export único para aba DADOS (sem criar aba por mês)
# -----------------------------
def ensure_ws(sh, title: str, rows: int = 2000, cols: int = 12):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def export_rows_to_dados(user_id: int, rows):
    """
    Exporta movimentações para aba DADOS (A:H).
    Otimizações:
      - NÃO limpa 99k linhas
      - Escreve em chunks
      - Apaga só o excedente da exportação anterior
      - Reclassifica OFX "outros" usando regras
      - Atualiza DB em lote (1 commit)
    """
    from gspread.utils import rowcol_to_a1

    sh = _open_sheet()
    ws = ensure_ws(sh, "DADOS", rows=5000, cols=12)

    # Guardamos o "tamanho anterior" em LISTAS!Z1 (pode trocar se quiser)
    ws_meta = ensure_ws(sh, "LISTAS", rows=200, cols=30)
    META_CELL = "Z1"  # guarda quantas linhas de dados (sem header) foram exportadas na última vez

    header = ["Data", "Tipo", "Categoria", "Descrição", "Valor", "Fonte", "Nome", "Mês"]

    # Lê quantas linhas tinham sido exportadas da última vez (sem header)
    prev_n = 0
    try:
        v = ws_meta.acell(META_CELL).value
        prev_n = int(v) if v and str(v).strip().isdigit() else 0
    except Exception:
        prev_n = 0

    # Escreve header (barato)
    ws.update("A1", [header], value_input_option="USER_ENTERED")

    # --- regras 1x ---
    rules = list_user_category_rules(user_id) or []
    rules_norm: list[tuple[str, str]] = []
    for kw, cat in rules:
        kw_n = normalize_text(kw or "")
        cat_n = normalize_text(cat or "")
        if kw_n and cat_n:
            rules_norm.append((kw_n, cat_n))

    RECLASSIFY_SOURCES = {"ofx"}

    def _infer_category_fast(text_base: str) -> str:
        t = normalize_text(text_base or "")
        if not t:
            return "outros"

        for kw_n, cat_n in rules_norm:
            try:
                if contains_word(t, kw_n) or (kw_n in t):
                    return cat_n
            except Exception:
                if kw_n in t:
                    return cat_n

        for keywords, cat2 in (LOCAL_RULES or []):
            cat2_n = normalize_text(cat2 or "")
            for kw in keywords:
                kw_n = normalize_text(kw or "")
                if not kw_n:
                    continue
                try:
                    if contains_word(t, kw_n) or (kw_n in t):
                        return cat2_n or "outros"
                except Exception:
                    if kw_n in t:
                        return cat2_n or "outros"

        return "outros"

    values: list[list] = []
    to_fix: list[tuple[int, str]] = []

    for r in rows:
        if not _is_monetary_row(r):
            continue

        dt = r.get("criado_em")
        if not hasattr(dt, "date"):
            continue

        d = dt.date()
        data_str = d.isoformat()
        mes_str = f"{d.year:04d}-{d.month:02d}"

        tipo = (r.get("tipo") or "").strip().lower()
        fonte0 = (r.get("origem") or r.get("source") or "").strip().lower()

        descricao = (r.get("nota") or "").strip()
        nome = (r.get("alvo") or "").strip()

        cat0 = normalize_text((r.get("categoria") or "").strip()) or "outros"

        if fonte0 in RECLASSIFY_SOURCES and cat0 == "outros":
            base = descricao or nome or ""
            new_cat = _infer_category_fast(base)
            if new_cat and new_cat != "outros" and new_cat != cat0:
                launch_id = r.get("id")
                if launch_id is not None:
                    try:
                        to_fix.append((int(launch_id), new_cat))
                        cat0 = new_cat
                    except Exception:
                        pass

        categoria_sheet = "Outros" if cat0 == "outros" else cat0
        valor = float(r.get("valor") or 0)
        fonte = (r.get("origem") or r.get("source") or "").strip()

        values.append([data_str, tipo, categoria_sheet, descricao, _to_sheet_value(valor), fonte, nome, mes_str])

    # --- DB bulk update (1 commit) ---
    if to_fix:
        try:
            from db import update_launch_categories_bulk
            update_launch_categories_bulk(user_id, to_fix)
        except Exception:
            try:
                from db import get_conn, ensure_user
                ensure_user(user_id)
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            update launches
                               set categoria=%s
                             where user_id=%s and id=%s
                            """,
                            [(cat, user_id, lid) for (lid, cat) in to_fix],
                        )
                    conn.commit()
            except Exception:
                pass

    # --- escreve em chunks ---
    n = len(values)
    if n:
        CHUNK = 800
        start_row = 2
        for i in range(0, n, CHUNK):
            chunk = values[i:i + CHUNK]
            r0 = start_row + i
            cell = rowcol_to_a1(r0, 1)  # A{r0}
            ws.update(cell, chunk, value_input_option="USER_ENTERED")

    # --- apaga SOMENTE o excedente antigo ---
    # Se antes exportou 3000 linhas e agora exportou 2500,
    # apaga só A(2502):H(3001)
    if prev_n > n:
        try:
            first_clear = n + 2            # +1 header, +1 base 1-indexed
            last_clear = prev_n + 1        # header ocupa linha 1
            ws.batch_clear([f"A{first_clear}:H{last_clear}"])
        except Exception:
            pass

        # --- salva novo tamanho ---
    try:
        ws_meta.update(META_CELL, [[str(n)]], value_input_option="RAW")
    except Exception:
        pass

    # --- atualiza saldo atual no DASHBOARD ---
    try:
        from db import get_balance
        saldo_atual = float(get_balance(user_id))

        ws_dashboard = sh.worksheet("DASHBOARD")
        ws_dashboard.update("C7", [[saldo_atual]], value_input_option="USER_ENTERED")
    except Exception:
        pass

        # --- DEBUG: marca que passou aqui ---
    try:
        ws_meta.update("Z2", [["EXPORT_DADOS_RODOU"]], value_input_option="RAW")
    except Exception:
        pass
    _base, _tab = get_sheet_links(ws)
    return _tab