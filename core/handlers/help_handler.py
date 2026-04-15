# core/handlers/help_handler.py
from __future__ import annotations
from core.help_text import render_full, render_help, TUTORIAL_TEXT, resolve_section


def help_general(platform: str) -> str:
    return render_full(platform)


def help_section(section: str, platform: str) -> str:
    key = resolve_section(section)
    return render_help(key, platform)


def tutorial(platform: str) -> str:
    return render_help("tutorial", platform)


def _infer_precise_help(norm: str) -> str | None:
    if any(expr in norm for expr in ("lancamento", "lançamento", "lancamentos", "lançamentos")):
        if any(expr in norm for expr in ("fazer", "criar", "registrar")):
            return (
                "🧾 Para fazer um lançamento, você pode usar:\n"
                "• `gastei 50 mercado`\n"
                "• `recebi 1000 salario`\n\n"
                "Se quiser ver depois, use `listar lançamentos`."
            )
        if any(expr in norm for expr in ("apagar", "excluir", "remover")):
            return (
                "🗑️ Para apagar um lançamento comum, use o número dele.\n"
                "Exemplo: `apagar 17`."
            )

    if any(expr in norm for expr in ("compra", "compras")) and any(expr in norm for expr in ("cartao", "cartão", "credito", "crédito")):
        if any(expr in norm for expr in ("fazer", "registrar", "lancar", "lançar")):
            return (
                "💳 Para registrar uma compra no crédito, use:\n"
                "• `credito 150 mercado`\n"
                "• `credito Nubank 150 mercado`\n"
                "• `gastei 150 no cartao Nubank`\n\n"
                "Depois eu mostro um código como `CC17` para você apagar com `apagar CC17`."
            )
        if any(expr in norm for expr in ("apagar", "excluir", "remover", "desfazer")):
            return (
                "🗑️ Para apagar uma compra no crédito, use o código dela.\n"
                "Exemplo: `apagar CC17`."
            )

    if any(expr in norm for expr in ("parcela", "parcelas", "parcelamento")):
        if any(expr in norm for expr in ("apagar", "excluir", "remover", "desfazer")):
            return (
                "🗑️ Para apagar um parcelamento, use o código dele.\n"
                "Exemplo: `apagar PCAB12CD34`.\n\n"
                "Se quiser descobrir o código, mande `parcelamentos`."
            )
        if any(expr in norm for expr in ("fazer", "criar", "registrar", "parcelar")):
            return (
                "💳 Para parcelar uma compra, use:\n"
                "• `parcelar 600 em 3x no cartao Nubank`\n"
                "• `parcelei 300 em 6x no cartao Nubank`"
            )

    if "caixinha" in norm or "caixinhas" in norm:
        if any(expr in norm for expr in ("fazer", "criar", "abrir")):
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
