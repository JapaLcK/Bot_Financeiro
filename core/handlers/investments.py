# core/handlers/investments.py
from __future__ import annotations
import re
import db
from utils_text import fmt_brl, fmt_rate
from core.dashboard_links import build_dashboard_link
from core.services.plan_limits import PlanLimitExceeded
import logging

logger = logging.getLogger(__name__)


def _investment_dashboard_link(user_id: int) -> str:
    link = build_dashboard_link(user_id, view="investments")
    if not link:
        return "⚠️ Não consegui gerar o link do dashboard agora. Tente novamente em instantes."
    return (
        "💡 No dashboard você gerencia com mais detalhes (taxas indexadas, lotes, datas):\n"
        f"{link}\n"
        "⏱️ Link mágico de uso único, expira em 5 minutos."
    )


_INV_NAME_UPPERCASE_TOKENS = {
    "CDB", "CRA", "CRI", "LCI", "LCA", "LF", "LCD", "LFT", "LTN",
    "IPCA", "SELIC", "CDI", "ETF", "FII", "FIAGRO",
    "BTG", "XP", "BB", "USA",
}
_INV_NAME_LOWERCASE_TOKENS = {"de", "da", "do", "das", "dos", "e"}


def _format_inv_name(name: str) -> str:
    """Capitaliza nome de investimento preservando siglas (CDB, IPCA, etc).

    "cdb banco luso"        → "CDB Banco Luso"
    "reserva de emergencia" → "Reserva de Emergencia"
    "Tesouro IPCA+ 2032"    → "Tesouro IPCA+ 2032"
    """
    parts = name.split()
    out: list[str] = []
    for i, w in enumerate(parts):
        # Trata sufixo "+" (ex: "IPCA+", "IPCA+2032")
        if "+" in w:
            head, _, tail = w.partition("+")
            if head.upper() in _INV_NAME_UPPERCASE_TOKENS:
                out.append(f"{head.upper()}+{tail}")
                continue
        upper = w.upper()
        lower = w.lower()
        if upper in _INV_NAME_UPPERCASE_TOKENS:
            out.append(upper)
        elif w.isdigit():
            out.append(w)
        elif i > 0 and lower in _INV_NAME_LOWERCASE_TOKENS:
            out.append(lower)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def list_investments(user_id: int, intro: str | None = None, rows: list | None = None) -> str:
    if rows is None:
        rows = db.accrue_all_investments(user_id)
    header = intro or "📈 **Sua carteira**"
    if not rows:
        return (
            f"{header}\n"
            "Você ainda não tem investimentos cadastrados.\n\n"
            f"{_investment_dashboard_link(user_id)}"
        )

    total = 0.0
    lines: list[str] = []
    for r in rows:
        rate_txt = fmt_rate(r.get("rate"), r.get("period"))
        projected_balance = r.get("projected_balance")
        projected_days = r.get("projected_days") or 0
        if projected_days > 0 and projected_balance:
            value = float(projected_balance)
        else:
            value = float(r["balance"] or 0)
        total += value
        name_pretty = _format_inv_name(r["name"])
        rate_part = f" ({rate_txt})" if rate_txt else ""
        lines.append(f"• **{name_pretty}** — {fmt_brl(value)}{rate_part}")

    return (
        f"{header}\n\n"
        f"**{fmt_brl(total)}** no total\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _investment_dashboard_link(user_id)
    )


def _norm(txt: str) -> str:
    """Comparação de nomes tolerante a acento e caixa."""
    import unicodedata

    base = unicodedata.normalize("NFD", (txt or "").strip().lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


# Comparados já normalizados por _norm (minúsculo, sem acento) — por isso
# "nao" e não "não".
_NEGATIVAS = {"nao", "n", "cancelar", "cancela", "deixa", "esquece", "depois"}


def _casa_investimento(alvo: str, rows: list) -> list:
    """Investimentos cujo nome bate com `alvo` — exato primeiro, depois parcial.

    Devolve lista: 1 item = escolha clara; vários = ambíguo (o usuário decide,
    porque aportar no investimento errado só se desfaz com resgate); 0 = não achou.
    """
    alvo_n = _norm(alvo)
    if not alvo_n:
        return []
    exatos = [r for r in rows if _norm(r["name"]) == alvo_n]
    if exatos:
        return exatos
    return [r for r in rows if alvo_n in _norm(r["name"]) or _norm(r["name"]) in alvo_n]


def _pergunta_qual(user_id: int, amount: float, rows: list, texto: str, alvo: str) -> str:
    """Nome não resolveu sozinho: mostra a carteira e pergunta qual."""
    db.set_pending_action(
        user_id,
        "investment_pick",
        {"amount": float(amount), "text": texto, "alvo": alvo},
        minutes=10,
    )
    linhas = [f"• **{_format_inv_name(r['name'])}**" for r in rows]
    return (
        f"Em qual investimento você quer aportar {fmt_brl(float(amount))}?\n\n"
        + "\n".join(linhas)
        + "\n\nResponda com o nome. Se preferir cadastrar um novo, diga *criar <nome>*."
    )


def _oferece_criar(user_id: int, amount: float, nome_sugerido: str, texto: str) -> str:
    """Não há investimento que sirva: oferece criar, já com o nome que o usuário disse."""
    pretty = _format_inv_name(nome_sugerido) if nome_sugerido else ""
    db.set_pending_action(
        user_id,
        "investment_create",
        {"amount": float(amount), "text": texto, "etapa": "nome", "nome": pretty},
        minutes=10,
    )
    if pretty:
        return (
            f"Você ainda não tem **{pretty}** cadastrado.\n"
            f"Quer que eu crie agora e já lance os {fmt_brl(float(amount))} nele?\n\n"
            f"Responda *sim* para criar com esse nome, ou mande outro nome. "
            "Para desistir, *não*."
        )
    return (
        "Você ainda não tem investimentos cadastrados.\n"
        f"Quer criar um agora e já lançar os {fmt_brl(float(amount))} nele?\n\n"
        "Me diga o nome (ex: *CDB Nubank*, *Tesouro Selic 2029*). Para desistir, *não*."
    )


def _pergunta_taxa(user_id: int, payload: dict) -> str:
    payload["etapa"] = "taxa"
    db.set_pending_action(user_id, "investment_create", payload, minutes=10)
    return (
        f"Boa. E quanto **{payload['nome']}** rende?\n\n"
        "Ex: *100% do CDI* • *12% ao ano* • *1% ao mês* • *IPCA + 6%*\n"
        "Preciso disso para projetar o saldo certo. Para desistir, *não*."
    )


def _cria_e_aporta(user_id: int, payload: dict, spec: dict) -> str:
    """Cria o investimento e lança o aporte na mesma transação."""
    nome = payload["nome"]
    amount = float(payload["amount"])
    try:
        launch_id, _inv_id, canon = db.create_investment_db(
            user_id,
            nome,
            spec["rate"],
            spec["period"],
            f"Criado pelo WhatsApp: {payload.get('text') or nome}",
            indexer=spec.get("indexer"),
            asset_type=spec.get("asset_type"),
            tax_profile=spec.get("tax_profile"),
            initial_amount=amount,
            initial_note=f"Aporte inicial em {nome}",
        )
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_ACCOUNT":
            # Criação e aporte são a MESMA transação em create_investment_db
            # (o commit só vem no fim): sem saldo, nada foi gravado. Medido
            # contra o Postgres — a tabela fica vazia depois do erro. Dizer
            # "criei o cadastro" aqui seria mentira.
            db.clear_pending_action(user_id)
            return (
                f"Não criei **{_format_inv_name(nome)}**: seu saldo em conta não cobre "
                f"{fmt_brl(amount)}.\n"
                "Manda de novo com um valor que caiba, ou deposite antes.\n\n"
                + _investment_dashboard_link(user_id)
            )
        logger.warning("criar investimento: código inesperado user=%s code=%s", user_id, code)
        db.clear_pending_action(user_id)
        return "Não consegui criar o investimento. Confira os dados e tente de novo."
    except PlanLimitExceeded:
        raise
    except Exception:
        logger.exception("criar investimento falhou user=%s nome=%s", user_id, nome)
        db.clear_pending_action(user_id)
        return "Não consegui criar o investimento agora. Tente de novo em instantes."

    db.clear_pending_action(user_id)

    # `launch_id is None` = o investimento JÁ existia (o insert bateu no
    # `on conflict do nothing`), e nesse caminho o create_investment_db retorna
    # ANTES de lançar o aporte inicial — medido: saldo da conta não muda e o
    # investimento fica com o saldo antigo. Anunciar "criado com aporte de X"
    # aqui seria confirmar dinheiro que não saiu do lugar. Acontece por corrida
    # (o user cadastrou pelo dashboard no meio da conversa), então em vez de só
    # avisar, faz o aporte que ele pediu.
    if launch_id is None:
        return deposit(
            user_id,
            payload.get("text") or canon,
            {"investment_name": canon, "amount": amount},
        )

    taxa_txt = fmt_rate(spec["rate"], spec["period"])
    return (
        f"✅ **{_format_inv_name(canon)}** criado ({taxa_txt}) "
        f"com aporte de **{fmt_brl(amount)}**. ID #{db.display_id_for(user_id, launch_id)}.\n\n"
        "Ele já aparece no seu dashboard.\n\n" + _investment_dashboard_link(user_id)
    )


def resolve_pending(user_id: int, text: str, pending: dict) -> str | None:
    """Responde às perguntas armadas pelo fluxo de aporte.

    Devolve None quando a mensagem não é resposta a esta pergunta — aí o
    roteador segue o caminho normal, sem prender o usuário no fluxo.
    """
    tipo = pending.get("action_type")
    if tipo not in ("investment_pick", "investment_create"):
        return None  # pendente de outro fluxo: não é nosso para cancelar

    payload = dict(pending.get("payload") or {})
    resposta = (text or "").strip()
    amount = float(payload.get("amount") or 0)

    if _norm(resposta) in _NEGATIVAS:
        db.clear_pending_action(user_id)
        return "❌ Beleza, cancelei o aporte."

    if tipo == "investment_pick":
        rows = db.accrue_all_investments(user_id)

        # O nome COMPLETO ganha do prefixo "criar/novo". Sem isto, quem tem um
        # investimento chamado "Novo CDB" e responde exatamente isso cai no
        # fluxo de criação de um "CDB" — o prefixo comeria o nome real.
        exato = [r for r in rows if _norm(r["name"]) == _norm(resposta)]
        if len(exato) == 1:
            db.clear_pending_action(user_id)
            return deposit(
                user_id,
                payload.get("text") or resposta,
                {"investment_name": exato[0]["name"], "amount": amount},
            )

        # "criar <nome>" durante a escolha muda para o fluxo de criação
        m = re.match(r"^\s*(?:criar|cria|novo|nova)\s+(.+)$", resposta, re.I)
        if m:
            return _oferece_criar(user_id, amount, m.group(1), payload.get("text") or resposta)

        achados = _casa_investimento(resposta, rows)
        if len(achados) == 1:
            db.clear_pending_action(user_id)
            return deposit(
                user_id,
                payload.get("text") or resposta,
                {"investment_name": achados[0]["name"], "amount": amount},
            )
        if len(achados) > 1:
            return _pergunta_qual(user_id, amount, achados, payload.get("text") or resposta, resposta)
        # não bateu com nada: oferece criar com o que ele digitou
        return _oferece_criar(user_id, amount, resposta, payload.get("text") or resposta)

    if tipo == "investment_create":
        etapa = payload.get("etapa")

        if etapa == "nome":
            # "sim" confirma o nome sugerido; qualquer outra coisa vira o nome
            if _norm(resposta) in {"sim", "s", "isso", "pode", "claro", "ok"}:
                if not payload.get("nome"):
                    return "Qual nome você quer dar? (ex: *CDB Nubank*)"
            else:
                payload["nome"] = _format_inv_name(resposta)
            if not payload.get("nome"):
                return "Qual nome você quer dar? (ex: *CDB Nubank*)"
            return _pergunta_taxa(user_id, payload)

        if etapa == "taxa":
            from investment_parse import parse_investment_spec

            spec = parse_investment_spec(resposta)
            if not spec or not spec.get("rate") or not spec.get("period"):
                return (
                    "Não entendi a taxa. Escreva assim:\n"
                    "*100% do CDI* • *12% ao ano* • *1% ao mês* • *IPCA + 6%*"
                )
            return _cria_e_aporta(user_id, payload, spec)

    return None


def create(user_id: int, raw_name: str, original_text: str) -> str:
    return list_investments(
        user_id,
        "📈 A criação de investimentos agora é feita pelo dashboard.",
    )


def propose_delete(user_id: int, investment_name: str) -> str:
    db.set_pending_action(user_id, "delete_investment", {"investment_name": investment_name})
    return (
        f"⚠️ Isso vai deletar o investimento **{investment_name}** permanentemente.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def deposit(user_id: int, text: str, entities: dict) -> str:
    investment_name = entities.get("investment_name")
    amount = entities.get("amount")

    if not amount or float(amount) <= 0:
        return list_investments(user_id, "Qual valor você quer aportar?")

    # Sem nome, ou com nome que não resolve sozinho, o bot PERGUNTA em vez de
    # devolver um beco sem saída: se há carteira, pede para escolher; se não há,
    # oferece criar. A criação continua exigindo taxa (db exige rate > 0), então
    # ela é perguntada antes de gravar.
    rows = db.accrue_all_investments(user_id)
    achados = _casa_investimento(investment_name, rows) if investment_name else []
    if len(achados) == 1:
        investment_name = achados[0]["name"]
    elif rows and len(achados) > 1:
        return _pergunta_qual(user_id, float(amount), achados, text, investment_name)
    elif rows and not achados:
        return _pergunta_qual(user_id, float(amount), rows, text, investment_name or "")
    elif not rows:
        return _oferece_criar(user_id, float(amount), investment_name or "", text)

    # Códigos vêm de db/investments.py como str(exc) — trate por TIPO e código
    # exato. Casar substring é frágil: "not found" nunca casou com
    # "INV_NOT_FOUND" (espaço × underscore) e o código cru vazava pro WhatsApp.
    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_deposit_from_account(
            user_id, investment_name, float(amount), text
        )
    except LookupError:
        # Corrida rara: sumiu entre a checagem acima e o aporte.
        return _oferece_criar(user_id, float(amount), investment_name, text)
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_ACCOUNT":
            return "Saldo insuficiente na conta para esse aporte.\n\n" + _investment_dashboard_link(user_id)
        if code == "AMOUNT_INVALID":
            return "Valor inválido para aporte. Tente: *aportar 500 no CDB Nubank*."
        logger.warning("deposit: código inesperado user=%s inv=%s code=%s", user_id, investment_name, code)
        return "Não consegui registrar esse aporte. Confira o valor e tente de novo."
    except PlanLimitExceeded:
        raise  # handle_incoming responde com a mensagem amigável de upgrade
    except Exception:
        logger.exception("deposit falhou user=%s inv=%s", user_id, investment_name)
        return "Não consegui registrar esse aporte agora. Tente de novo em instantes."

    display_id = db.display_id_for(user_id, launch_id)
    return f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**. ID #{display_id}."


def check_cdi() -> str:
    """
    Retorna a taxa CDI anual (a.a.) mais recente do Banco Central.
    Usa a função get_latest_cdi_aa do db, que consulta o SGS/BCB.
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                res = db.get_latest_cdi_aa(cur)

        if not res:
            return (
                "⚠️ Não consegui obter a taxa CDI agora.\n"
                "O Banco Central pode estar fora do ar. Tente novamente em alguns minutos."
            )

        ref_date, cdi_aa = res
        # Calcula estimativa mensal e diária para contexto
        cdi_mensal = ((1 + cdi_aa / 100) ** (1 / 12) - 1) * 100
        cdi_diaria = ((1 + cdi_aa / 100) ** (1 / 252) - 1) * 100

        return (
            f"📊 *Taxa CDI — Banco Central*\n\n"
            f"📅 Referência: {ref_date.strftime('%d/%m/%Y')}\n"
            f"📈 *CDI a.a.:* {cdi_aa:.2f}%\n"
            f"📆 CDI mensal (aprox.): {cdi_mensal:.4f}%\n"
            f"📆 CDI diário (aprox.): {cdi_diaria:.5f}%\n\n"
            f"💡 Para criar um investimento atrelado ao CDI, digite *investimentos* e abra o dashboard."
        )

    except Exception as e:
        logger.exception("check_cdi error: %s", e)
        return (
            "⚠️ Erro ao consultar a taxa CDI.\n"
            "Verifique sua conexão ou tente novamente em instantes."
        )


_WITHDRAW_ALL_RX = re.compile(r"\b(tudo|esvaziar|esvazia|zerar|zera)\b", re.I)


def withdraw(user_id: int, text: str, entities: dict) -> str:
    investment_name = entities.get("investment_name")
    amount = entities.get("amount")
    want_all = bool(_WITHDRAW_ALL_RX.search(text or ""))

    if not investment_name:
        return list_investments(user_id, "De qual investimento você quer resgatar?")
    if not want_all and (not amount or float(amount) <= 0):
        return list_investments(user_id, "Qual valor você quer resgatar?")

    try:
        launch_id, _new_acc, _new_inv, canon, taxes = db.investment_withdraw_to_account(
            user_id,
            investment_name,
            None if want_all else float(amount),
            text,
            withdraw_all=want_all,
        )
    except LookupError:
        # Carteira vazia precisa de texto próprio: "Estes são seus
        # investimentos:" seguido de "você ainda não tem nenhum" é contraditório.
        rows = db.accrue_all_investments(user_id)
        pretty = _format_inv_name(investment_name)
        if not rows:
            return (
                f"Você ainda não tem investimentos, então não dá para resgatar "
                f"de **{pretty}**.\n\n" + _investment_dashboard_link(user_id)
            )
        return list_investments(
            user_id,
            f"Não encontrei **{pretty}**. Estes são seus investimentos:",
            rows=rows,
        )
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_INVEST":
            return f"Saldo insuficiente no investimento **{investment_name}**.\n\n" + list_investments(user_id)
        if code == "AMOUNT_INVALID":
            return "Valor inválido para resgate. Tente: *resgatar 500 do CDB Nubank*."
        logger.warning("withdraw: código inesperado user=%s inv=%s code=%s", user_id, investment_name, code)
        return "Não consegui registrar esse resgate. Confira o valor e tente de novo."
    except PlanLimitExceeded:
        raise  # handle_incoming responde com a mensagem amigável de upgrade
    except Exception:
        logger.exception("withdraw falhou user=%s inv=%s", user_id, investment_name)
        return "Não consegui registrar esse resgate agora. Tente de novo em instantes."

    gross = float(taxes.get("gross", amount or 0)) if taxes else float(amount or 0)
    tax_note = ""
    if taxes and float(taxes.get("iof", 0) or 0) + float(taxes.get("ir", 0) or 0) > 0:
        tax_note = f" Líquido: **{fmt_brl(float(taxes.get('net', 0)))}**."
    verb = "Resgate total" if want_all else "Resgate"
    return f"✅ {verb} de **{fmt_brl(gross)}** de **{canon}**.{tax_note} ID #{db.display_id_for(user_id, launch_id)}.\n\n" + list_investments(user_id)
