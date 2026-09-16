# core/handlers/help_handler.py
from __future__ import annotations
import re
from difflib import get_close_matches
from core.help_text import render_full, render_help, resolve_section
from utils_text import normalize_text


def _has_any(norm: str, *terms: str) -> bool:
    return any(term in norm for term in terms)


def _has_token_close_to(norm: str, *terms: str) -> bool:
    tokens = [tok for tok in norm.split() if tok]
    for token in tokens:
        if get_close_matches(token, terms, n=1, cutoff=0.84):
            return True
    return False


def _has_hint(norm: str, *terms: str) -> bool:
    return _has_any(norm, *terms) or _has_token_close_to(norm, *terms)


def _is_statement_file_help(norm: str) -> bool:
    """Aceita domínio explícito ou uma pergunta que termina no formato."""
    norm = normalize_text(norm)
    if not re.search(r"\b(csv|pdf)\b", norm):
        return False
    if re.search(r"\b(extrato|fatura|pigbank)\b", norm):
        return True
    return bool(
        re.fullmatch(
            r"(?:como(?: faco)?(?: para)? )?"
            r"(?:importar|enviar|anexar)(?: um| o)?(?: arquivo)? (?:csv|pdf)",
            norm,
        )
    )


def _prepend_not_understood(topic: str, body: str) -> str:
    if body.strip().lower().startswith("não entendi"):
        return body
    return f"Não entendi exatamente o que você quis fazer com {topic}.\n{body}"


def _credit_contextual_fallback(text: str, platform: str) -> str:
    from core.handlers import credit as h_credit

    return h_credit.contextual_help(text) or render_help("credit", platform)


def _launches_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "apagar", "excluir", "remover", "deletar", "desfazer"):
        return (
            "🧾 Posso te ajudar com lançamentos assim:\n"
            "• `listar lancamentos`\n"
            "• `apagar 17`\n"
            "• `desfazer`"
        )

    if _has_any(norm, "saldo"):
        return (
            "💰 Se você quer consultar saldo ou movimentações, tente:\n"
            "• `saldo`\n"
            "• `gastos hoje`\n"
            "• `listar lancamentos`"
        )

    if _has_any(norm, "recebi", "ganhei", "entrada", "receita"):
        return (
            "💰 Para registrar uma receita, use por exemplo:\n"
            "• `recebi 1000 salario`\n"
            "• `recebi 250 freela`\n\n"
            "Depois você pode consultar com `listar lancamentos`."
        )

    return (
        "🧾 Posso te ajudar com lançamentos de algumas formas:\n"
        "• `gastei 50 mercado`\n"
        "• `recebi 1000 salario`\n"
        "• `gastos hoje`\n"
        "• `listar lancamentos`\n"
        "• `apagar 17`"
    )


def _pockets_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "criar", "abrir", "nova", "novo"):
        return (
            "📦 Para criar uma caixinha, use:\n"
            "• `criar caixinha viagem`\n"
            "• `criar caixinha emergencia`"
        )

    if _has_any(norm, "colocar", "depositar", "guardar", "adicionar"):
        return (
            "📦 Para colocar dinheiro em uma caixinha, use:\n"
            "• `coloquei 300 na caixinha viagem`"
        )

    if _has_any(norm, "retirar", "sacar", "tirar"):
        return (
            "📦 Para retirar dinheiro de uma caixinha, use:\n"
            "• `retirei 100 da caixinha viagem`"
        )

    if _has_any(norm, "apagar", "excluir", "remover", "deletar"):
        return (
            "📦 Para excluir uma caixinha, use:\n"
            "• `excluir caixinha viagem`\n\n"
            "Eu vou pedir confirmação antes de apagar."
        )

    return (
        "📦 Posso te ajudar com caixinhas assim:\n"
        "• `criar caixinha viagem`\n"
        "• `coloquei 300 na caixinha viagem`\n"
        "• `retirei 100 da caixinha viagem`\n"
        "• `listar caixinhas`"
    )


def _investments_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "criar", "novo", "nova", "cadastrar", "registrar"):
        return (
            "📈 Eu consigo te ajudar a criar investimentos pelo dashboard.\n"
            "Digite `investimentos`: o bot lista sua carteira e envia um link mágico direto para a aba completa de criação."
        )

    if _has_any(norm, "aplicar", "aporte", "aportar", "investir"):
        return (
            "📈 Para fazer um aporte, use:\n"
            "• `apliquei 200 no investimento CDB`\n"
            "• `apliquei 500 no investimento Tesouro`"
        )

    if _has_any(norm, "resgatar", "resgate", "retirar", "sacar"):
        return (
            "📈 Para resgatar de um investimento, use:\n"
            "• `resgatei 100 do investimento CDB`"
        )

    if _has_any(norm, "apagar", "excluir", "remover", "deletar"):
        return (
            "📈 Para excluir um investimento, use:\n"
            "• `excluir investimento CDB Nubank`\n\n"
            "Eu vou pedir confirmação antes de apagar."
        )

    if _has_any(norm, "cdi"):
        return (
            "📊 Se você quer consultar ou usar CDI, tente:\n"
            "• `ver cdi`\n"
            "• `investimentos` para receber o link mágico do dashboard e criar um ativo atrelado ao CDI"
        )

    return (
        "📈 Posso te ajudar com investimentos assim:\n"
        "• `investimentos` para listar a carteira e receber o link mágico do dashboard\n"
        "• `apliquei 200 no investimento CDB`\n"
        "• `resgatei 100 do investimento CDB`\n"
        "• `listar investimentos`"
    )


def _categories_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "criar", "adicionar", "nova", "novo", "aprender"):
        return (
            "🏷️ Para ensinar uma categoria ao bot, use:\n"
            "• `aprender ifood como alimentacao`\n"
            "• `aprender rifa como aposta`"
        )

    if _has_any(norm, "remover", "apagar", "excluir", "deletar"):
        return (
            "🏷️ Para remover uma regra de categoria, use:\n"
            "• `remover regra ifood`\n"
            "• `remove regra ifood`"
        )

    return (
        "🏷️ Posso te ajudar com categorias assim:\n"
        "• `regras de categoria`, `regras de categorias` ou `listar regras`\n"
        "• `aprender ifood como alimentacao`\n"
        "• `aprender rifa como aposta`\n"
        "• `remover regra ifood`\n\n"
        "O bot também tenta aprender categorias sozinho conforme você usa."
    )


def _report_contextual_fallback(norm: str) -> str:
    if re.search(r"\b(semanal|semana)\b", norm):
        return (
            "🗓️ Para consultar ou configurar o resumo semanal, use:\n"
            "• `resumo semanal`\n"
            "• `ligar resumo semanal`\n"
            "• `desligar resumo semanal`"
        )

    if re.search(r"\b(mensal|mes)\b", norm):
        return (
            "🗓️ Para consultar ou configurar o resumo mensal, use:\n"
            "• `resumo mensal`\n"
            "• `ligar resumo mensal`\n"
            "• `desligar resumo mensal`"
        )

    if _has_any(norm, "desligar", "parar", "desativar"):
        return (
            "🗓️ Para desligar o report diário, use:\n"
            "• `desligar report diario`"
        )

    if _has_any(norm, "hora", "horario", "horário", "20h", "8h", "9h"):
        return (
            "🗓️ Para configurar o horário do report diário, use:\n"
            "• `ligar report diario 20h`\n"
            "• `ligar report diario 8h30`"
        )

    return (
        "🗓️ Posso te ajudar com relatórios diários, semanais e mensais:\n"
        "• `relatorio`\n"
        "• `resumo semanal`\n"
        "• `resumo mensal`\n"
        "• `ligar report diario`\n"
        "• `ligar report diario 20h`\n"
        "• `desligar report diario`"
    )


def _dashboard_contextual_fallback() -> str:
    return (
        "📊 Para abrir o dashboard, use:\n"
        "• `dashboard`\n"
        "• `abrir dashboard`\n\n"
        "Eu vou te enviar um link temporário para o painel."
    )


def _account_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "codigo", "código", "gerar"):
        return (
            "🔗 Para gerar um código de vinculação entre plataformas, use:\n"
            "• `link`\n\n"
            "Depois, cole o código na outra plataforma com `link 123456`."
        )

    return (
        "🔗 Para vincular suas contas entre plataformas, use:\n"
        "• `link` para gerar um código\n"
        "• `link 123456` na outra plataforma para concluir"
    )


def _ofx_contextual_fallback() -> str:
    return (
        "🧾 Para importar um OFX, envie ou anexe o arquivo no chat.\n"
        "• `importar ofx`\n"
        "Extratos também aceitam `.csv` ou `.pdf`; para faturas, use `.ofx`."
    )


def help_general(platform: str) -> str:
    return render_full(platform)


def help_section(section: str, platform: str) -> str:
    key = resolve_section(section)
    return render_help(key, platform)


def tutorial(platform: str) -> str:
    return render_help("tutorial", platform)


def answer_help(intent: str, text: str, platform: str) -> str:
    """A rota de ajuda inteira (`help` + `help.tutorial`), num lugar só.

    Mora aqui, e não no intent_router, porque quem tem DOIS chamadores é a
    resposta, não o roteamento: o ramo de ajuda do `route()` e o
    `_paywall_gate`, que responde à ajuda ele mesmo em vez de deixar a mensagem
    seguir o fluxo (o `route()` resolve pendências ANTES do ramo de ajuda, e uma
    delas registra parcelamento). Duas cópias do `split(maxsplit=1)` seriam duas
    ajudas que divergem — §0.7 do CLAUDE.md.
    """
    if intent == "help.tutorial":
        return tutorial(platform)
    # O split só DECIDE entre menu completo e seção; quem resolve a seção é o
    # `resolve_section`, e ele casa `^(ajuda|help)\s+(.+)$` — então recebe o
    # texto inteiro, igual aos outros dois chamadores dele. Passar o fragmento
    # ("ofx") caía no fallback "start" e quebrava TODA seção pedida por texto.
    if len(text.split(maxsplit=1)) > 1:
        return help_section(text, platform)
    return help_general(platform)


def _infer_precise_help(norm: str) -> str | None:
    if "cartao" in norm or "cartão" in norm or "cartoes" in norm or "cartões" in norm:
        if any(expr in norm for expr in ("apagar", "apago", "excluir", "remover", "deletar")) and "compra" not in norm:
            return (
                "🗑️ Para apagar um cartão, use o nome dele.\n"
                "Exemplo: `excluir cartao Nubank`.\n\n"
                "Eu vou pedir confirmação antes de remover."
            )

    if any(expr in norm for expr in ("lancamento", "lançamento", "lancamentos", "lançamentos")):
        if any(expr in norm for expr in ("fazer", "faco", "faço", "criar", "registrar")):
            return (
                "🧾 Para fazer um lançamento, você pode usar:\n"
                "• `gastei 50 mercado`\n"
                "• `recebi 1000 salario`\n\n"
                "Se quiser ver depois, use `listar lançamentos`."
            )
        if any(expr in norm for expr in ("apagar", "apago", "excluir", "remover")):
            return (
                "🗑️ Para apagar um lançamento comum, use o número dele.\n"
                "Exemplo: `apagar 17`."
            )

    if any(expr in norm for expr in ("compra", "compras")) and any(expr in norm for expr in ("cartao", "cartão", "credito", "crédito")):
        if any(expr in norm for expr in ("apagar", "apago", "excluir", "remover", "desfazer")):
            return (
                "🗑️ Para apagar uma compra no crédito, use o código dela.\n"
                "Exemplo: `apagar CC17`."
            )
        if any(expr in norm for expr in ("fazer", "faco", "faço", "registrar", "lancar", "lançar")):
            return (
                "💳 Para registrar uma compra no crédito, use:\n"
                "• `credito 150 mercado`\n"
                "• `credito Nubank 150 mercado`\n"
                "• `gastei 150 no cartao Nubank`\n\n"
                "Depois eu mostro um código como `CC17` para você apagar com `apagar CC17`."
            )

    if any(expr in norm for expr in ("parcela", "parcelas", "parcelamento")):
        if any(expr in norm for expr in ("apagar", "apago", "excluir", "remover", "desfazer")):
            return (
                "🗑️ Para apagar um parcelamento, use o código dele.\n"
                "Exemplo: `apagar PCAB12CD34`.\n\n"
                "Se quiser descobrir o código, mande `parcelamentos`."
            )
        if any(expr in norm for expr in ("fazer", "faco", "faço", "criar", "registrar", "parcelar")):
            return (
                "💳 Para parcelar uma compra, use:\n"
                "• `parcelar 600 em 3x no cartao Nubank`\n"
                "• `parcelei 300 em 6x no cartao Nubank`"
            )

    if "caixinha" in norm or "caixinhas" in norm:
        if any(expr in norm for expr in ("fazer", "faco", "faço", "criar", "abrir")):
            return (
                "📦 Para criar uma caixinha, use:\n"
                "• `criar caixinha viagem`"
            )
        if any(expr in norm for expr in ("colocar", "depositar", "guardar")):
            return (
                "📦 Para colocar dinheiro numa caixinha, use:\n"
                "• `coloquei 300 na caixinha viagem`"
            )

    import_terms = ("importar", "importacao", "enviar", "anexar")
    if (
        any(expr in norm for expr in ("ofx", "extrato"))
        and any(expr in norm for expr in import_terms)
    ) or _is_statement_file_help(norm):
        return (
            "🧾 Para importar um OFX, envie ou anexe o arquivo no chat.\n"
            "• `importar ofx`\n"
            "Extratos também aceitam `.csv` ou `.pdf`; para faturas, use `.ofx`."
        )

    if "fatura" in norm and any(expr in norm for expr in ("ver", "consultar", "pagar", "registrar")):
        return (
            "🧾 Para consultar ou pagar uma fatura, use:\n"
            "• `fatura Nubank`\n"
            "• `listar faturas`\n"
            "• `pagar fatura Nubank 1200`\n"
            "• `pagar fatura Nubank com saldo`"
        )

    return None


_HELP_MARKERS = (
    "como faco", "como faço", "como usar", "como registro", "como registrar",
    "como crio", "como criar", "como vejo", "como consultar", "como apago",
    "com apago", "como apagar", "como removo", "como excluir", "me ensina",
    "me explica", "me explique", "qual comando", "quero ajuda", "tenho duvida",
    "tenho dúvida", "nao sei como", "não sei como",
)


def infer_help_from_text(text: str, platform: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    norm = raw.casefold()
    if not any(marker in norm for marker in _HELP_MARKERS):
        return None

    precise = _infer_precise_help(norm)
    if precise is not None:
        return precise

    normalized = normalize_text(raw)
    topic = _financial_topic(normalized)
    section = {
        "credit": "credit",
        "pockets": "pockets",
        "investments": "invest",
        "ofx": "ofx",
        "dashboard": "dashboard",
        "categories": "categories",
        "launches": "launches",
    }.get(topic)
    if section is not None:
        return render_help(section, platform)
    if topic == "report":
        return _report_contextual_fallback(normalized)
    if topic == "account":
        return _account_contextual_fallback(normalized)

    return render_help("start", platform)


def _financial_topic(norm: str) -> str | None:
    """Classifica somente sinais inequívocos do domínio do PigBank."""
    if re.search(r"\b(cartao|cartoes|fatura|credito|parcela|parcelamento)\b", norm):
        return "credit"
    if re.search(r"\b(caixinha|caixinhas)\b", norm):
        return "pockets"
    if re.search(r"\bcaxinhas?\b", norm):
        return "pockets"
    if re.search(r"\b(investimento|investimentos|aporte|resgate|cdb|tesouro|cdi)\b", norm):
        return "investments"
    if re.search(r"\b(ofx|extrato)\b", norm):
        return "ofx"
    if _is_statement_file_help(norm):
        return "ofx"
    if re.search(r"\bcategorias?\b", norm) or re.search(
        r"\bcategoriz(?:ar|e)\b.*\b(gastos?|despesas?|receitas?|lancamentos?)\b", norm
    ):
        return "categories"
    if re.search(
        r"\b(?:report|relatorio)(?:\s+(?:diario|semanal|mensal|financeiro))?\b",
        norm,
    ):
        return "report"
    if re.search(r"\bvincul(?:ar|acao)\b.*\b(contas?|whatsapp|discord)\b", norm) or re.search(
        r"\bcodigo\s+de\s+vinculacao\b", norm
    ):
        return "account"
    if any(marker in norm for marker in _HELP_MARKERS) and re.search(
        r"\bcomando\s+link\b", norm
    ):
        return "account"
    if re.search(r"\b(saldo|lancamento|lancamentos|gasto|gastos|despesa|despesas|receita|receitas)\b", norm):
        return "launches"
    if re.search(r"\bdashboard\b", norm) or re.search(
        r"\bpainel\s+(?:financeiro|do\s+pigbank)\b", norm
    ):
        return "dashboard"
    if any(marker in norm for marker in _HELP_MARKERS) and re.search(
        r"\bpainel\s*[?.!]*$", norm
    ):
        return "dashboard"
    return None


def has_financial_context(text: str) -> bool:
    return _financial_topic(normalize_text(text)) is not None


def infer_financial_contextual_fallback(text: str, platform: str) -> str | None:
    """Retorna ajuda apenas quando o texto contém um tópico financeiro conhecido."""
    norm = normalize_text(text)
    topic = _financial_topic(norm)

    if topic == "credit":
        return _prepend_not_understood("cartões", _credit_contextual_fallback(text, platform))

    if topic == "pockets":
        return _prepend_not_understood("caixinhas", _pockets_contextual_fallback(norm))

    if topic == "investments":
        return _prepend_not_understood("investimentos", _investments_contextual_fallback(norm))

    if topic == "categories":
        return _prepend_not_understood("categorias", _categories_contextual_fallback(norm))

    if topic == "dashboard":
        return _prepend_not_understood("dashboard", _dashboard_contextual_fallback())

    if topic == "report":
        return _prepend_not_understood("report diário", _report_contextual_fallback(norm))

    if topic == "account":
        return _prepend_not_understood("vinculação de conta", _account_contextual_fallback(norm))

    if topic == "ofx":
        return _prepend_not_understood("importação de extrato", _ofx_contextual_fallback())

    if topic == "launches":
        return _prepend_not_understood("lançamentos", _launches_contextual_fallback(norm))

    return None


def infer_contextual_fallback(text: str, platform: str) -> str:
    financial_help = infer_financial_contextual_fallback(text, platform)
    if financial_help is not None:
        return financial_help

    return (
        "Não entendi exatamente o que você quer fazer.\n"
        "Tente uma destas opções:\n"
        "• `saldo` — ver saldo atual\n"
        "• `gastei 50 mercado` — registrar despesa\n"
        "• `pagar fatura Nubank` — pagar uma fatura\n"
        "• `faturas` — listar faturas em aberto\n"
        "• `cartoes` — listar seus cartões\n"
        "• `criar caixinha viagem` — criar caixinha\n"
        "• `investimentos` — ver carteira\n"
        "• `ajuda` — ver todos os comandos"
    )
