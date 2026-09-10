"""
core/services/trial_offer.py — a frase de oferta que é VERDADE para uma conta.

UM assunto: o trial é 15 dias por telefone NA VIDA (`plan_trials`), então "com
15 dias grátis" não é uma frase que se possa escrever para todo mundo. Aqui mora
a decisão dos TRÊS estados — elegível / não elegível / não sei — e nada mais.

**Saiu de `core/services/billing_commands.py` quando ganhou o SEGUNDO chamador**,
e é separação por assunto, não por tamanho: `billing_commands` é o roteamento e
as respostas dos comandos `assinar`/`cancelar`/`plano`; isto é a verdade sobre a
elegibilidade, e ela passou a ser lida também pela mensagem de bloqueio do bot
(`core.handle_incoming._paywall_gate`) no corte do Grátis. Mesmo movimento e
mesma razão do `db/dunning.py`, que saiu de `db/plans.py` no teto de 350 linhas
(`tests/test_max_lines_python.py`) quando o assunto se separou.

Importe daqui — não há re-export em `billing_commands`, de propósito (§0.7).
"""

from __future__ import annotations

from .billing_commands import _bold


def texto_da_oferta(user_id: int, platform: str) -> str:
    """A frase sobre o trial que é VERDADE para ESTA conta — os TRÊS estados
    (elegível / não elegível / não sei) num lugar só.

    EXTRAÍDA de `_handle_assinar` (§0.1), não criada, quando a mensagem de
    bloqueio do bot (`core.handle_incoming._paywall_gate`) passou a precisar da
    mesma verdade. A copy de lá prometia "15 dias grátis (um teste por número)"
    a todo mundo, e ela é FALSA justamente para quem o corte do Grátis manda
    para cá: o trial é 15 dias por telefone NA VIDA (`plan_trials`), então o
    ex-assinante que já o queimou leria uma promessa que o checkout não cumpre.

    `eligible is None` = a consulta LEVANTOU. "Não sei" nunca vira "não tem":
    a frase do meio não afirma nem nega, deixa o checkout dizer.

    TETO CONHECIDO, herdado: `is_trial_eligible_for_user` devolve False também
    quando a conta não tem `phone_hash` (nenhum WhatsApp vinculado), então lá a
    frase diz "esse telefone já usou o período grátis" sem que telefone nenhum
    tenha usado. É o comportamento de hoje do `assinar plano` e o checkout
    concorda com ele (mesmo valor alimenta `trial_period_days`), então a frase
    acerta o RESULTADO e erra a RAZÃO. Não foi tocado aqui: é anterior a este
    PR (§0.3).

    Não é chamada com o v2 desligado: `_handle_assinar` a gateia por
    `plans_v2_enabled()` e o gate do bot só chega aqui com a linha em mão.
    """
    b = lambda s: _bold(s, platform)
    try:
        from db.plans import is_trial_eligible_for_user
        eligible = is_trial_eligible_for_user(user_id)
    except Exception:
        eligible = None
    if eligible is False:
        return (
            "Aqui ó, link pra assinar. Como esse telefone já usou o período grátis, "
            "o checkout mostra o valor da cobrança imediata antes da confirmação:"
        )
    if eligible is None:
        return (
            "Aqui ó, link pra assinar. O checkout confirma seu período grátis ou o "
            "valor da primeira cobrança antes da confirmação:"
        )
    return (
        f"Aqui ó, link pra assinar com {b('15 dias grátis')} "
        "(cancela quando quiser, sem cobrança no trial):"
    )
