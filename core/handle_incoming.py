# core/handle_incoming.py
from __future__ import annotations
from ai_router import handle_ai_message
import re
from typing import List
from datetime import datetime, date, time
from utils_date import _tz
from db import get_launches_by_period, add_launch_and_update_balance, set_daily_report_enabled
import os
from core.types import IncomingMessage, OutgoingMessage
from core.services.quick_entry import handle_quick_entry
from core.help_text import (
    HELP_TEXT_SHORT,
    TUTORIAL_TEXT,
    render_full,
    render_help,
    resolve_section,
)
from core.dashboard_links import build_dashboard_link
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
from core.reports.reports_daily import build_daily_report_text
from utils_text import normalize_text


# "link 123456" — vincula Discord ↔ WhatsApp (merge de duas plataformas)
LINK_RE = re.compile(r"^\s*link(?:\s+(\d{6}))?\s*$", re.IGNORECASE)

# "vincular 123456" — vincula conta web (email/senha) ao bot nesta plataforma
VINCULAR_RE = re.compile(r"^\s*vincular\s+(\d{6})\s*$", re.IGNORECASE)

def _cmd_link(msg):
    """Link entre plataformas: Discord ↔ WhatsApp."""
    m = LINK_RE.match(msg.text or "")
    if not m:
        return None

    code = m.group(1)
    bold = lambda s: f"*{s}*" if msg.platform == "whatsapp" else f"**{s}**"

    if not getattr(msg, "external_id", None):
        return [OutgoingMessage(text="⚠️ Não consegui identificar seu ID nesta plataforma.")]

    # link (sem código) -> gera código para digitar na outra plataforma
    if not code:
        uid = get_or_create_canonical_user(msg.platform, msg.external_id)
        link_code = create_link_code(uid, minutes_valid=10)
        return [OutgoingMessage(
            text=(
                f"🔗 Código de link: {bold(link_code)}\n"
                "Agora digite *link 123456* na outra plataforma (expira em 10 min)."
            )
        )]

    # link XXXXXX -> consome e mergeia as duas plataformas
    target_user_id = consume_link_code(code)
    if not target_user_id:
        return [OutgoingMessage(text="❌ Código inválido ou expirado. Envie *link* para gerar um novo.")]

    link_platform_identity(msg.platform, msg.external_id, target_user_id)

    return [OutgoingMessage(text="✅ Contas vinculadas com sucesso! Agora Discord e WhatsApp usam os mesmos dados.")]


def _cmd_vincular(msg):
    """Vincula conta web (cadastro por email/senha) a esta plataforma."""
    m = VINCULAR_RE.match(msg.text or "")
    if not m:
        return None

    code = m.group(1)
    bold = lambda s: f"*{s}*" if msg.platform == "whatsapp" else f"**{s}**"
    platform_label = "WhatsApp" if msg.platform == "whatsapp" else "Discord"

    if not getattr(msg, "external_id", None):
        return [OutgoingMessage(text="⚠️ Não consegui identificar seu ID nesta plataforma.")]

    target_user_id = consume_link_code(code)
    if not target_user_id:
        return [OutgoingMessage(
            text="❌ Código inválido ou expirado.\nGere um novo no site e tente novamente."
        )]

    link_platform_identity(msg.platform, msg.external_id, target_user_id)

    return [OutgoingMessage(
        text=(
            f"✅ {platform_label} vinculado à sua conta!\n"
            f"Seus dados já estão disponíveis aqui. Digite {bold('ajuda')} para ver os comandos."
        )
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

    t_norm = normalize_text(t)  # remove acentos + baixa + limpa

    # comandos "fixos" (NUNCA caem no AI)
    if t_norm in ("relatorio diario", "report diario", "resumo diario"):
        return [OutgoingMessage(text=build_daily_report_text(int(msg.user_id)))]

        # --- REPORT DIÁRIO (manual + liga/desliga) ---
    if t_low in ("report", "resumo", "report diario", "relatorio diario", "resumo diario"):
        return [OutgoingMessage(text=build_daily_report_text(int(msg.user_id)))]

    if t_low in ("desligar report diario", "desativar report diario", "parar report diario"):
        set_daily_report_enabled(int(msg.user_id), False)
        return [OutgoingMessage(text="✅ Report diário desligado. Para ligar de novo: *ligar report diario*")]

    if t_low in ("ligar report diario", "ativar report diario", "voltar report diario"):
        set_daily_report_enabled(int(msg.user_id), True)
        return [OutgoingMessage(text="✅ Report diário ligado. Vou te mandar todo dia no horário configurado.")]


    # --- OFX ---
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

    # 1) LINK Discord ↔ WhatsApp
    out = _cmd_link(msg)
    if out is not None:
        return out

    # 1b) VINCULAR conta web → plataforma
    out = _cmd_vincular(msg)
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

    # dashboard financeiro
    if t_low in {"dashboard", "ver dashboard", "abrir dashboard", "painel", "ver painel",
                 "exportar sheets", "exportar planilha", "exportar sheet"}:
        link = build_dashboard_link(msg.user_id, hours=0.25)
        if not link:
            return [OutgoingMessage(text=(
                "⚠️ Nao consegui gerar seu link do dashboard agora.\n"
                "Tente novamente em instantes."
            ))]
        return [OutgoingMessage(text=f"📊 Dashboard financeiro:\n{link}")]

    msg_out = handle_quick_entry(msg.user_id, t0)
    if msg_out:
        return [msg_out]

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
    
    #comandos de ligar e desligar report diario 
    t_low = (msg.text or "").strip().casefold()

    if t_low in {"desligar report diario", "desativar report diario", "parar report diario"}:
        set_daily_report_enabled(int(msg.user_id), False)
        return [OutgoingMessage(text="✅ Report diário desligado.\nPara ligar de novo: *ligar report diario*")]

    if t_low in {"ligar report diario", "ativar report diario", "voltar report diario"}:
        set_daily_report_enabled(int(msg.user_id), True)
        return [OutgoingMessage(text="✅ Report diário ligado.\nVocê vai receber todo dia às 09:00.")]


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
