# core/handlers/investments.py
from __future__ import annotations
import re
import db
from utils_text import fmt_brl, fmt_rate
from core.dashboard_links import build_dashboard_link
from core.services.plan_limits import PlanLimitExceeded
import logging

logger = logging.getLogger(__name__)


def _norm_nome(txt: str) -> str:
    """Comparação de nomes tolerante a acento e caixa."""
    import unicodedata

    base = unicodedata.normalize("NFD", (txt or "").strip().lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def _casa_investimento(alvo: str, rows: list) -> list:
    """Investimentos cujo nome bate com `alvo` — exato primeiro, depois parcial.

    1 item = escolha clara; vários = ambíguo (quem decide é o usuário, porque aportar
    no investimento errado só se desfaz com resgate, que recolhe IR/IOF); 0 = não achou.
    """
    alvo_n = _norm_nome(alvo)
    if not alvo_n:
        return []
    exatos = [r for r in rows if _norm_nome(r["name"]) == alvo_n]
    if exatos:
        return exatos
    return [r for r in rows if alvo_n in _norm_nome(r["name"]) or _norm_nome(r["name"]) in alvo_n]


def _pergunta_qual_investimento(user_id: int, amount: float, rows: list, texto: str) -> str:
    """Pergunta em qual investimento aportar — e ARMA a pendência para ouvir a resposta.

    Antes o bot perguntava "Em qual investimento você quer aportar?" e não guardava
    nada: a resposta do usuário virava mensagem nova, era reclassificada do zero e — se
    ele tivesse uma caixinha de mesmo nome — caía na caixinha. Pergunta sem pendência é
    beco sem saída.
    """
    db.set_pending_action(
        user_id,
        "investment_pick",
        {"amount": float(amount), "text": texto,
         "nomes": [r["name"] for r in rows]},
        minutes=10,
    )
    linhas = [f"{i}. **{_format_inv_name(r['name'])}**" for i, r in enumerate(rows, start=1)]
    return (
        f"Em qual investimento você quer aportar {fmt_brl(float(amount))}?\n\n"
        + "\n".join(linhas)
        + "\n\nResponda com o número (ex: *1*) ou o nome. Para desistir, *cancelar*."
    )


def resolve_investment_pick(user_id: int, text: str, pending: dict) -> str | None:
    """Resposta à pergunta "em qual investimento?".

    Devolve None quando a mensagem não é resposta a esta pergunta — aí o roteador
    segue o caminho normal em vez de prender o usuário.
    """
    from utils_text import normalize_text

    if pending.get("action_type") != "investment_pick":
        return None

    payload = dict(pending.get("payload") or {})
    resposta = (text or "").strip()
    norm = normalize_text(resposta)

    if norm in ("nao", "n", "cancelar", "cancela"):
        # Abandono: condicional para não apagar pendência de outra tarefa.
        db.consume_pending_action(user_id, pending)
        return "❌ Beleza, cancelei o aporte."

    nomes = payload.get("nomes") or []
    escolhido = None
    if norm.isdigit() and 1 <= int(norm) <= len(nomes):
        escolhido = nomes[int(norm) - 1]
    else:
        achados = _casa_investimento(resposta, [{"name": n} for n in nomes])
        if len(achados) == 1:
            escolhido = achados[0]["name"]

    if escolhido is None:
        linhas = [f"{i}. **{_format_inv_name(n)}**" for i, n in enumerate(nomes, start=1)]
        return ("Não entendi qual. Responda com o número:\n\n"
                + "\n".join(linhas) + "\n\nOu *cancelar*.")

    # Porteiro: o `deposit` movimenta dinheiro. Se a linha já não é a que
    # lemos, outra tarefa consumiu a pergunta — não aporta duas vezes.
    if not db.consume_pending_action(user_id, pending):
        return None
    # O que sobe até aqui é só o que estoura ANTES de o dinheiro andar: a leitura
    # da carteira (`db.accrue_all_investments`), a resolução da origem
    # (`funding.resolve`) e a transação do aporte, que faz commit no fim. O que
    # falha DEPOIS do commit o `_aporta` engole de propósito — devolver a pergunta
    # ali faria a próxima resposta aportar duas vezes.
    with db.restore_pending_on_error(user_id, pending):
        return deposit(
            user_id,
            payload.get("text") or resposta,
            {"investment_name": escolhido, "amount": float(payload.get("amount") or 0)},
        )


def _pergunta_origem(user_id: int, fontes: list, amount: float, retomar: dict) -> str:
    """Mais de um saldo cobre o valor: o usuário escolhe de onde sai.

    Mesmo formato do "qual fatura você quer pagar?" (core/handlers/credit.py::
    _ask_which_bill): opções numeradas e resposta pelo número. `retomar` guarda o que
    reexecutar depois da escolha, para a resposta não perder o contexto.
    """
    db.set_pending_action(
        user_id,
        "funding_source_choice",
        {
            "amount": float(amount),
            "retomar": retomar,
            "fontes": [
                {"kind": f["kind"], "of_account_id": f["of_account_id"],
                 "label": f["label"], "balance": float(f["balance"])}
                for f in fontes
            ],
        },
        minutes=10,
    )
    linhas = [f"De onde sai {fmt_brl(float(amount))}?", ""]
    for i, f in enumerate(fontes, start=1):
        linhas.append(f"{i}. **{f['label']}** — {fmt_brl(float(f['balance']))}")
    linhas += ["", "Responda com o número (ex: *1*) ou *cancelar*."]
    return "\n".join(linhas)


def resolve_funding_choice(user_id: int, text: str, pending: dict) -> str | None:
    """Resposta à pergunta "de onde sai o dinheiro?".

    Devolve None quando a mensagem não é resposta a esta pergunta — aí o roteador
    segue o caminho normal em vez de prender o usuário no fluxo.
    """
    from core.services import funding
    from utils_text import normalize_text

    if pending.get("action_type") != "funding_source_choice":
        return None

    payload = dict(pending.get("payload") or {})
    resposta = (text or "").strip()
    norm = normalize_text(resposta)

    if norm in ("nao", "n", "cancelar", "cancela"):
        db.consume_pending_action(user_id, pending)
        return "❌ Beleza, cancelei."

    fontes = payload.get("fontes") or []
    escolhida = None
    if norm.isdigit() and 1 <= int(norm) <= len(fontes):
        escolhida = fontes[int(norm) - 1]
    else:
        # aceita o nome também ("carteira", "nubank") — o número é só o atalho
        alvo = _norm_txt(resposta)
        if alvo:
            casam = [f for f in fontes if alvo in _norm_txt(f["label"])]
            if len(casam) == 1:
                escolhida = casam[0]

    if escolhida is None:
        linhas = [f"{i}. **{f['label']}**" for i, f in enumerate(fontes, start=1)]
        return ("Não entendi de onde sai. Responda com o número:\n\n"
                + "\n".join(linhas) + "\n\nOu *cancelar*.")

    # Porteiro: retoma um fluxo que debita da fonte escolhida.
    if not db.consume_pending_action(user_id, pending):
        return None
    retomar = payload.get("retomar") or {}
    amount = float(payload.get("amount") or 0)
    source = {
        "kind": escolhida["kind"],
        "of_account_id": escolhida.get("of_account_id"),
        "label": escolhida.get("label"),
    }

    # Mesma regra do site acima: sobe o que estourou antes do commit (e só isso —
    # `_aporta` e `deposita_com_origem` engolem o que falha depois). Recusa
    # legítima (saldo insuficiente, caixinha inexistente, valor inválido) volta
    # como TEXTO e não re-arma: repetir a escolha daria o mesmo resultado.
    with db.restore_pending_on_error(user_id, pending):
        if retomar.get("fluxo") == "investment_deposit":
            return _aporta(user_id, retomar.get("name"), amount,
                           retomar.get("text") or resposta, source)
        if retomar.get("fluxo") == "pocket_deposit":
            from core.handlers import pockets as _pockets
            return _pockets.deposita_com_origem(
                user_id, retomar.get("name"), amount, retomar.get("text") or resposta, source)
    return None


def _norm_txt(txt: str) -> str:
    import unicodedata
    base = unicodedata.normalize("NFD", (txt or "").strip().lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


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


def _investment_not_found(user_id: int, investment_name: str, *, action: str) -> str:
    """Resposta para INV_NOT_FOUND (nome não bate com nada cadastrado).

    O cadastro de investimento é feito só pelo dashboard (ver `create`), então
    a resposta precisa levar o usuário até lá. Só dizer "não encontrei" — ou
    pior, devolver o código cru — deixa ele sem saída nenhuma.
    """
    rows = db.accrue_all_investments(user_id)
    pretty = _format_inv_name(investment_name)
    if not rows:
        return (
            f"Você ainda não tem nenhum investimento cadastrado, "
            f"por isso não dá para {action} **{pretty}**.\n"
            "Cadastre ele primeiro no dashboard — depois é só mandar o comando aqui pelo WhatsApp.\n\n"
            + _investment_dashboard_link(user_id)
        )
    return list_investments(
        user_id,
        f"Não encontrei **{pretty}**. Estes são seus investimentos:",
        rows=rows,
    )


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

    # Resolve o NOME antes de qualquer coisa. Sem nome, ou com nome que não bate,
    # o bot pergunta E FICA ESPERANDO (pendência armada) — antes ele perguntava e
    # não ouvia, e a resposta solta era reclassificada do zero.
    rows = db.accrue_all_investments(user_id)
    achados = _casa_investimento(investment_name, rows) if investment_name else []
    if len(achados) == 1:
        investment_name = achados[0]["name"]
    elif rows and len(achados) > 1:
        return _pergunta_qual_investimento(user_id, float(amount), achados, text)
    elif rows:
        # nome não bateu com nada: mostra a carteira inteira e espera a escolha
        intro = (f"Não encontrei **{_format_inv_name(investment_name)}**."
                 if investment_name else "")
        msg = _pergunta_qual_investimento(user_id, float(amount), rows, text)
        return f"{intro}\n\n{msg}" if intro else msg
    elif not investment_name:
        return list_investments(user_id, "Em qual investimento você quer aportar?")

    from core.services import funding

    # De onde sai o dinheiro. Com banco conectado a Carteira fica zerada de
    # propósito, então exigir cobertura nela recusava aporte de quem tem saldo.
    escolha = funding.resolve(user_id, float(amount))
    if "ask" in escolha:
        return _pergunta_origem(
            user_id, escolha["ask"], float(amount),
            {"fluxo": "investment_deposit", "name": investment_name, "text": text},
        )
    if "insufficient" in escolha:
        return (
            funding.msg_insuficiente(
                user_id, float(amount), sources=escolha["insufficient"]["sources"])
            + "\n\n" + _investment_dashboard_link(user_id)
        )
    return _aporta(user_id, investment_name, float(amount), text, escolha["source"])


def _aporta(user_id: int, investment_name: str, amount: float, text: str, source: dict) -> str:
    """Executa o aporte com a origem já decidida.

    Separado de `deposit` porque a resposta da pergunta "de onde sai?" retoma por aqui
    — sem isso o contexto (investimento, valor, texto original) se perderia.
    """
    from core.services import funding

    # Códigos vêm de db/investments.py como str(exc) — trate por TIPO e código
    # exato. Casar substring é frágil: "not found" nunca casou com
    # "INV_NOT_FOUND" (espaço × underscore) e o código cru vazava pro WhatsApp.
    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_deposit_from_account(
            user_id, investment_name, float(amount), text,
            funding_source=funding.to_db_arg(source),
        )
    except LookupError:
        return _investment_not_found(user_id, investment_name, action="aportar em")
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_ACCOUNT":
            return (funding.msg_insuficiente(user_id, float(amount))
                    + "\n\n" + _investment_dashboard_link(user_id))
        if code == "AMOUNT_INVALID":
            return "Valor inválido para aporte. Tente: *aportar 500 no CDB Nubank*."
        logger.warning("deposit: código inesperado user=%s inv=%s code=%s", user_id, investment_name, code)
        return "Não consegui registrar esse aporte. Confira o valor e tente de novo."
    except PlanLimitExceeded:
        raise  # handle_incoming responde com a mensagem amigável de upgrade
    except Exception:
        # SOBE, não vira texto. `investment_deposit_from_account` é uma transação
        # só, com o commit no fim: o que estoura nela não moveu dinheiro. Enquanto
        # isto devolvia "Não consegui registrar esse aporte agora", o
        # `restore_pending_on_error` de `resolve_funding_choice` nunca via a falha
        # — a pendência ficava consumida e responder a origem de novo não repetia
        # o aporte. Mesmo defeito da caixinha, apontado pelo Codex no PR #144.
        # O usuário continua recebendo uma mensagem de "tente em instantes": a do
        # `handle_incoming`, que ainda registra o erro no dashboard.
        logger.exception("deposit falhou user=%s inv=%s", user_id, investment_name)
        raise

    # DEPOIS do commit: o dinheiro JÁ ANDOU e a exceção NÃO pode subir daqui —
    # a devolução re-armaria a pergunta e a próxima resposta aportaria de novo.
    # `db.display_id_for` lê o banco, então falha pela mesma causa transitória.
    try:
        display_id = db.display_id_for(user_id, launch_id)
        msg = (f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**"
               f"{funding.origem_txt(source)}. ID #{display_id}.")
        if source["kind"] == funding.BANK:
            msg += "\n\n" + funding.nota_sync()
        return msg
    except Exception:
        logger.exception(
            "aporte %s registrado, mas a mensagem falhou (user=%s)", launch_id, user_id)
        return (f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}** "
                f"registrado (ID interno #{launch_id}).")


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

    from core.services import funding

    # Destino do resgate: com banco conectado o dinheiro volta pro banco, não pra
    # Carteira — creditar a Carteira inflaria o consolidado com o mesmo dinheiro
    # que o sync devolve. Não pergunta (ver funding.resolve_destination).
    destino = funding.resolve_destination(user_id)["source"]

    try:
        launch_id, _new_acc, _new_inv, canon, taxes = db.investment_withdraw_to_account(
            user_id,
            investment_name,
            None if want_all else float(amount),
            text,
            withdraw_all=want_all,
            funding_source=funding.to_db_arg(destino),
        )
    except LookupError:
        return _investment_not_found(user_id, investment_name, action="resgatar de")
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
    destino_txt = (f", para o {destino['label']}" if destino["kind"] == funding.BANK else "")
    nota = ("\n\n" + funding.nota_sync(saida=False)) if destino["kind"] == funding.BANK else ""
    return (
        f"✅ {verb} de **{fmt_brl(gross)}** de **{canon}**{destino_txt}.{tax_note} "
        f"ID #{db.display_id_for(user_id, launch_id)}.{nota}\n\n"
        + list_investments(user_id)
    )
