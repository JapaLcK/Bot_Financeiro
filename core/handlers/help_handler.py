# core/handlers/help_handler.py
from __future__ import annotations
from core.help_text import render_full, render_help, resolve_section
from utils_text import normalize_text


def _has_any(norm: str, *terms: str) -> bool:
    return any(term in norm for term in terms)


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
            "📈 Para criar um investimento, use:\n"
            "• `criar investimento CDB 110% CDI`\n"
            "• `criar investimento Tesouro 0,03% ao dia`"
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
            "• `criar investimento CDB 110% CDI`"
        )

    return (
        "📈 Posso te ajudar com investimentos assim:\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `apliquei 200 no investimento CDB`\n"
        "• `resgatei 100 do investimento CDB`\n"
        "• `listar investimentos`"
    )


def _categories_contextual_fallback(norm: str) -> str:
    if _has_any(norm, "criar", "adicionar", "nova", "novo"):
        return (
            "🏷️ Para criar uma categoria com regra, use:\n"
            "• `criar categoria alimentacao linkar destinatario ifood`\n"
            "• `criar categoria mercado linkar Carrefour`"
        )

    if _has_any(norm, "remover", "apagar", "excluir", "deletar"):
        return (
            "🏷️ Para remover uma regra de categoria, use:\n"
            "• `remover destinatario ifood`"
        )

    return (
        "🏷️ Posso te ajudar com categorias assim:\n"
        "• `categorias`\n"
        "• `criar categoria mercado linkar destinatario Carrefour`\n"
        "• `remover destinatario ifood`"
    )


def _report_contextual_fallback(norm: str) -> str:
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
        "🗓️ Posso te ajudar com o report diário assim:\n"
        "• `relatorio`\n"
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
        "🧾 Para importar um extrato OFX, envie a mensagem:\n"
        "• `importar ofx`\n\n"
        "Junto com o arquivo `.ofx` em anexo."
    )


def help_general(platform: str) -> str:
    return render_full(platform)


def help_section(section: str, platform: str) -> str:
    key = resolve_section(section)
    return render_help(key, platform)


def tutorial(platform: str) -> str:
    return render_help("tutorial", platform)


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

    if any(expr in norm for expr in ("ofx", "extrato")) and any(expr in norm for expr in ("importar", "enviar")):
        return (
            "🧾 Para importar um OFX, envie o arquivo `.ofx` junto com a mensagem:\n"
            "• `importar ofx`"
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


def infer_help_from_text(text: str, platform: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    norm = raw.casefold()
    help_markers = (
        "como faco",
        "como faço",
        "como usar",
        "como registro",
        "como registrar",
        "como crio",
        "como criar",
        "como vejo",
        "como consultar",
        "como apago",
        "com apago",
        "como apagar",
        "como removo",
        "como excluir",
        "me ensina",
        "me explica",
        "me explique",
        "qual comando",
        "quero ajuda",
        "tenho duvida",
        "tenho dúvida",
        "nao sei como",
        "não sei como",
    )
    if not any(marker in norm for marker in help_markers):
        return None

    precise = _infer_precise_help(norm)
    if precise is not None:
        return precise

    topic_hints = {
        "credit": ("cartao", "cartão", "cartoes", "cartões", "credito", "crédito", "fatura", "parcela", "parcelamento", "limite"),
        "pockets": ("caixinha", "caixinhas"),
        "invest": ("investimento", "investimentos", "aporte", "resgate", "cdb", "tesouro"),
        "ofx": ("ofx", "extrato", "importar"),
        "dashboard": ("dashboard", "painel"),
        "categories": ("categoria", "categorias", "regra", "regras", "linkar"),
        "launches": ("lancamento", "lançamentos", "lancamentos", "gasto", "gastos", "despesa", "despesas", "receita", "receitas", "saldo"),
    }

    for section, hints in topic_hints.items():
        if any(hint in norm for hint in hints):
            return render_help(section, platform)

    return render_help("start", platform)


def infer_contextual_fallback(text: str, platform: str) -> str:
    norm = normalize_text(text)

    if _has_any(norm, "cartao", "cartoes", "fatura", "credito", "parcela", "parcelamento", "vence", "fecha", "limite"):
        return _credit_contextual_fallback(text, platform)

    if _has_any(norm, "caixinha", "caixinhas"):
        return _pockets_contextual_fallback(norm)

    if _has_any(norm, "investimento", "investimentos", "aporte", "aplicar", "apliquei", "resgate", "resgatar", "cdb", "tesouro", "cdi"):
        return _investments_contextual_fallback(norm)

    if _has_any(norm, "categoria", "categorias", "regra", "regras", "linkar", "destinatario", "destinatário"):
        return _categories_contextual_fallback(norm)

    if _has_any(norm, "dashboard", "painel"):
        return _dashboard_contextual_fallback()

    if _has_any(norm, "report", "relatorio", "relatório"):
        return _report_contextual_fallback(norm)

    if _has_any(norm, "link", "vincular", "codigo", "código", "whatsapp", "discord"):
        return _account_contextual_fallback(norm)

    if _has_any(norm, "ofx", "extrato", "importar"):
        return _ofx_contextual_fallback()

    if _has_any(norm, "saldo", "lancamento", "lancamentos", "gastei", "gasto", "gastos", "despesa", "despesas", "recebi", "receita", "receitas", "historico", "histórico", "extrato"):
        return _launches_contextual_fallback(norm)

    return (
        "Não entendi exatamente o que você quer fazer.\n"
        "Tente uma destas opções:\n"
        "• `saldo`\n"
        "• `gastei 50 mercado`\n"
        "• `criar cartao Nubank`\n"
        "• `criar caixinha viagem`\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `ajuda`"
    )
