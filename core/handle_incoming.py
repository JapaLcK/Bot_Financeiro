# core/handle_incoming.py
from __future__ import annotations
from ai_router import handle_ai_message
import re
from typing import List
from datetime import datetime, date, time
from utils_date import _tz
from db import get_launches_by_period, add_launch_and_update_balance
from sheets_export import export_rows_to_dados, get_sheet_links
from core.types import IncomingMessage, OutgoingMessage
from core.help_text import (
    HELP_TEXT_SHORT,
    TUTORIAL_TEXT,
    render_full,
    render_help,
    resolve_section,
)
from core.services.ofx_service import handle_ofx_import
from db import ensure_user, get_balance, list_launches, create_link_code, consume_link_code, bind_identity
from utils_text import fmt_brl
import re
from parsers import parse_receita_despesa_natural
from db import (
    get_or_create_canonical_user,
    create_link_code,
    consume_link_code,
    link_platform_identity,
    list_categories,
    list_category_rules,
    add_category_rule,
    delete_category_rule
)

LINK_RE = re.compile(r"^\s*link(?:\s+(\d{6}))?\s*$", re.IGNORECASE)

def _cmd_link(msg):
    m = LINK_RE.match(msg.text or "")
    if not m:
        return None

    code = m.group(1)

    # precisa ter external_id pra link funcionar (wa_id no whatsapp, discord_user_id no discord)
    if not getattr(msg, "external_id", None):
        return [OutgoingMessage(text="⚠️ Não consegui identificar seu id externo nesta plataforma (external_id).")]

    # link (sem código) -> gera código
    if not code:
        uid = get_or_create_canonical_user(msg.platform, msg.external_id)
        link_code = create_link_code(uid, minutes_valid=10)
        return [OutgoingMessage(
            text=(
                f"🔗 Código de link: *{link_code}*\n"
                "Agora digite *link 123456* na outra plataforma (o código expira em 10 min)."
            )
        )]

    # link 123456 -> consome e mergeia
    target_user_id = consume_link_code(code)
    if not target_user_id:
        return [OutgoingMessage(text="❌ Código inválido ou expirado. Envie *link* para gerar um novo.")]

    final_uid = link_platform_identity(msg.platform, msg.external_id, target_user_id)

    return [OutgoingMessage(
        text="✅ Contas vinculadas com sucesso! Agora Discord e WhatsApp usam os mesmos dados."
    )]

def _fmt_bold(platform: str, s: str) -> str:
    # Discord: **bold** | WhatsApp: *bold*
    return f"*{s}*" if platform == "whatsapp" else f"**{s}**"

def _month_range_current(tz):
    now = datetime.now(tz).date()
    start = now.replace(day=1)
    end = now
    return start, end

def _is_ofx_attachment(a) -> bool:
    fn = (getattr(a, "filename", "") or "").lower()
    ct = (getattr(a, "content_type", "") or "").lower()
    return fn.endswith(".ofx") or "ofx" in ct

def handle_incoming(msg: IncomingMessage) -> List[OutgoingMessage]:
    t = (msg.text or "").strip()
    platform = msg.platform
    t_low = t.casefold().strip()


    # --- OFX: prioridade máxima ---
    if msg.attachments:
        ofx_atts = [a for a in msg.attachments if _is_ofx_attachment(a)]
        if ofx_atts:
            a = ofx_atts[0]

            if getattr(a, "data", None):
                report_txt = handle_ofx_import(
                str(msg.user_id),
                a.data,
                getattr(a, "filename", "arquivo.ofx"),
            )
                return [OutgoingMessage(text=report_txt)]

            return [OutgoingMessage(
                text="📎 Recebi o OFX, mas não consegui baixar o arquivo. Reenvie o .ofx por favor."
            )]
        
    # CATEGORIAS / REGRAS
    t0 = (msg.text or "").strip()

    # "categoria" -> lista categorias
    if t_low == "categoria" or t_low == "categorias" or t_low == "listar categorias" or t_low == "listar categoria":
        cats = list_categories(msg.user_id)
        rules = list_category_rules(msg.user_id)  # retorna (keyword, category)

        # agrupa keywords por categoria
        by_cat = {}
        for kw, c in rules:
            by_cat.setdefault(c, []).append(kw)

        lines = ["📚 **Categorias**"]
        if not cats and not rules:
            return [OutgoingMessage(text="Você ainda não tem categorias criadas. Use: criar categoria <X> linkar destinatario <Y>")]

        # se cats vier vazio, ainda mostramos categorias que existem via rules
        cats_all = sorted(set(list(cats or []) + list(by_cat.keys())))

        for c in cats_all:
            kws = by_cat.get(c, [])
            lines.append(f"• **{c}** ({len(kws)} regras)")
            if kws:
                lines.append("  └ " + ", ".join(kws))

        lines.append("")
        lines.append("Para criar: `criar categoria <X> linkar destinatario <Y>`")
        lines.append("Para remover: `remover destinatario <Y>`")
        return [OutgoingMessage(text="\n".join(lines))]


    # "criar categoria X linkar destinatario Y"
    # aceitando variações simples: "criar categoria X linkar Y"
    if t_low.startswith("criar categoria "):
        rest = t0[len("criar categoria "):].strip()

        # tenta dividir por "linkar destinatario"
        sep1 = " linkar destinatario "
        sep2 = " linkar "
        if sep1 in rest:
            cat, kw = rest.split(sep1, 1)
        elif sep2 in rest:
            cat, kw = rest.split(sep2, 1)
        else:
            return [OutgoingMessage(text="Formato: `criar categoria <X> linkar destinatario <Y>`")]

        cat = cat.strip()
        kw = kw.strip().strip('"').strip("'")

        if not cat or not kw:
            return [OutgoingMessage(text="Formato: `criar categoria <X> linkar destinatario <Y>`")]

        add_category_rule(msg.user_id, kw, cat)
        return [OutgoingMessage(text=f"✅ Regra criada: **{kw}** → **{cat}**")]


    # opcional: remover regra
    # "remover destinatario Y" (remove keyword)
    if t_low.startswith("remover destinatario "):
        kw = t0[len("remover destinatario "):].strip().strip('"').strip("'")
        n = delete_category_rule(msg.user_id, kw)
        if n:
            return [OutgoingMessage(text=f"✅ Removido: **{kw}**")]
        return [OutgoingMessage(text=f"⚠️ Não encontrei regra para: **{kw}**")]

    # 1) LINK
    out = _cmd_link(msg)
    if out is not None:
        return out

    # WhatsApp (ou qualquer plataforma) consome: "link 123456"
    if t_low.startswith("link "):
        parts = t_low.split()
        if len(parts) >= 2:
            code = parts[1].strip()
            target_user_id = consume_link_code(code)
            if not target_user_id:
                return [OutgoingMessage(text="❌ Código inválido ou expirado. Gere um novo no Discord com **link whatsapp**.")]

            # Vincula a identidade atual (platform + id externo) ao user canônico do Discord
            # Precisamos do external_id aqui. Então o IncomingMessage precisa carregar isso.
            # Se você já tem msg.message_id e platform, vamos adicionar msg.external_id (ver Passo 5).
            bind_identity(msg.platform, msg.external_id, target_user_id)

            return [OutgoingMessage(text="✅ Vinculado com sucesso! Agora WhatsApp e Discord usam os mesmos dados.")]

    # exportar sheets
    if t_low in {"exportar sheets", "exportar planilha", "exportar sheet"}:
        tz = _tz()
        start_d, end_d = _month_range_current(tz)
        worksheet = start_d.strftime("%Y-%m")

        rows = get_launches_by_period(msg.user_id, start_d, end_d)
        if not rows:
            return [OutgoingMessage(text="Não encontrei lançamentos neste mês para exportar.")]

        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.max)

        try:
            export_rows_to_dados(
                msg.user_id,
                rows,
            )
        except Exception as e:
            print("EXPORT_SHEETS_ERROR:", repr(e))
            return [OutgoingMessage(text="❌ Falhou ao exportar para o Google Sheets. Veja o log do servidor.")]

        url_sheet, url_tab = get_sheet_links("DADOS")
        return [OutgoingMessage(
            text=(
                "✅ Exportação concluída!\n"
                f"Planilha: {url_sheet}\n"
            )
        )]

    # ----------------
    # REGISTRO RÁPIDO (sem IA)
    # ----------------
    parsed = parse_receita_despesa_natural(msg.user_id, t0)
    if parsed:
        add_launch_and_update_balance(
            user_id=msg.user_id,
            tipo=parsed["tipo"],
            valor=parsed["valor"],
            alvo=parsed.get("alvo"),
            nota=parsed.get("nota"),
            categoria=parsed.get("categoria"),
            criado_em=parsed.get("criado_em"),
        )

        tipo_txt = "Despesa" if parsed["tipo"] == "despesa" else "Receita"
        cat_txt = parsed.get("categoria") or "outros"
        return [OutgoingMessage(
            text=f"✅ {tipo_txt} registrada: {fmt_brl(parsed['valor'])} • {cat_txt}"
        )]

    # ----------------
    # HELP / TUTORIAL (serve pros dois; Discord pode ignorar se usar dropdown no adapter)
    # ----------------
    if t_low == "tutorial":
        txt = TUTORIAL_TEXT
        if platform == "whatsapp":
            # converte **bold** -> *bold*
            txt = re.sub(r"\*\*(.+?)\*\*", r"*\1*", txt)
        return [OutgoingMessage(text=txt)]

    if t_low.startswith("ajuda") or t_low == "help":
        # suporta: "ajuda", "ajuda ofx", "ajuda lançamentos"
        parts = t.split(maxsplit=1)
        arg = parts[1] if len(parts) > 1 else None

        if arg:
            sec = resolve_section(arg)
            return [OutgoingMessage(text=render_help(sec, platform))]
        return [OutgoingMessage(text=render_full(platform))]

    # ----------------
    # SALDO (conta corrente)
    # ----------------
    if t_low in {"saldo", "saldo conta", "saldo da conta", "conta", "saldo geral"}:
        bal = get_balance(msg.user_id)
        title = _fmt_bold(platform, "Conta Corrente")
        return [OutgoingMessage(text=f"🏦 {title}: {fmt_brl(float(bal))}")]

    # ----------------
    # LISTAR LANÇAMENTOS (últimos 10)
    # ----------------
    if t_low in {
        "listar lancamentos", "listar lançamentos",
        "ultimos lancamentos", "últimos lançamentos",
        "lancamentos", "lançamentos"
    }:
        rows = list_launches(msg.user_id, limit=10)
        if not rows:
            return [OutgoingMessage(text="Você ainda não tem lançamentos.")]

        header = _fmt_bold(platform, "Últimos lançamentos")
        lines = []
        for r in rows:
            tipo = r.get("tipo")
            valor = r.get("valor")
            alvo = r.get("alvo") or "-"
            criado = r.get("criado_em")
            nota = r.get("nota")

            # mesma limpeza que você já usa em outros pontos:
            if tipo == "create_investment" and nota and "taxa=" in nota:
                try:
                    m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                    m_per = re.search(r"periodo=(\w+)", nota)
                    taxa = float(m_taxa.group(1)) * 100 if m_taxa else None
                    per = m_per.group(1) if m_per else ""
                    per = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                    nota = f"{taxa:.4g}% {per}" if taxa is not None else nota
                except:
                    pass

            valor_str = fmt_brl(float(valor)) if valor is not None else "-"
            nota_part = f" • {nota}" if nota else ""
            created_str = str(criado) if criado is not None else "-"
            lines.append(f"#{r['id']} • {tipo} • {valor_str} • {alvo}{nota_part} • {created_str}")

        return [OutgoingMessage(text=f"🧾 {header}:\n" + "\n".join(lines))]

    # ----------------
    # OFX (anexo)
    # ----------------
    if "importar ofx" in t_low:
        if not msg.attachments:
            return [OutgoingMessage(text="Envie `importar ofx` junto com o arquivo `.ofx` anexado.")]

        ofx_att = None
        for a in msg.attachments:
            ct = (getattr(a, "content_type", None) or getattr(a, "mime_type", None) or "").lower()
            if a.filename.lower().endswith(".ofx") or "ofx" in ct:
                ofx_att = a
                break

        if not ofx_att:
            return [OutgoingMessage(text="Não achei um `.ofx` no anexo. Envie um arquivo OFX, por favor.")]

        ensure_user(msg.user_id)  # garante user antes de importar

        report = handle_ofx_import(msg.user_id, ofx_att.data, ofx_att.filename)

        periodo = f"{report.get('dt_start')} → {report.get('dt_end')}"
        total = report.get("total_in_file")
        ins = report.get("inserted")
        dup = report.get("duplicates")
        saldo = report.get("new_balance") or report.get("balance")

        title = _fmt_bold(platform, "OFX importado")
        saldo_txt = fmt_brl(float(saldo)) if saldo is not None else "(indisponível)"

        return [OutgoingMessage(text=(
            f"✅ {title}\n"
            f"📅 Período: {periodo}\n"
            f"🧾 Transações no arquivo: {total}\n"
            f"➕ Inseridas: {ins} | ♻️ Duplicadas: {dup}\n"
            f"🏦 Saldo atual: {saldo_txt}\n"
        ))]

    # ----------------
    # FALLBACK (GPT)
    # ----------------
    if msg.platform == "whatsapp":
        try:
            ai_txt = handle_ai_message(msg.user_id, t)
        except Exception as e:
            print("AI fallback error:", e)
            ai_txt = None

        if ai_txt:
            return [OutgoingMessage(text=ai_txt)]

        return [OutgoingMessage(text=HELP_TEXT_SHORT)]

    return []