"""
core/services/billing_commands.py — handlers para comandos de assinatura.

Comandos suportados (case-insensitive, sem acentos):
- "assinar plano", "assinar pigbank+", "fazer upgrade", "quero pro"
- "cancelar plano", "cancelar assinatura", "encerrar plano", "encerrar assinatura"
- "plano", "meu plano", "minha assinatura", "ver plano"

IMPORTANTE: NUNCA aceitar "assinar" nem "cancelar" puros — esses verbos
sao usados em outros fluxos do bot (confirmacao de delete, parcelamento,
etc). Sempre exigir um substantivo junto ("plano", "assinatura", etc).

Compartilhado entre Discord (cog) e WhatsApp (handle_incoming),
pra resposta consistente entre canais.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

from core.dashboard_links import build_dashboard_link
from core.services import billing_copy


# "assinar"/"cancelar" puros são ambíguos (colisão com confirmação de outros
# fluxos) — antes de processar, checamos pending action no DB e devolvemos
# None se houver. Assim a confirmação tem prioridade.
_ASSINAR_TRIGGERS = {
    "assinar", "assinar plano", "assinar assinatura", "assinar pro",
    "assinar pigbank+", "assinar pigbank plus", "fazer upgrade", "upgrade",
    "quero pro", "quero o pro", "virar pro", "pigbank+", "pigbank plus",
    "renovar", "renovar plano", "renovar assinatura",
}
_CANCELAR_TRIGGERS = {
    "cancelar", "cancelar plano", "cancelar assinatura", "cancelar pro",
    "cancelar pigbank+", "cancelar pigbank plus", "encerrar assinatura",
    "encerrar plano",
}
_PLANO_TRIGGERS = {
    "plano", "meu plano", "minha assinatura", "ver plano", "qual meu plano",
    "qual eh meu plano", "qual e meu plano",
}


def _norm_cmd(text: str) -> str:
    """Normaliza e tira o "/" do Discord (ex: "/assinar" → "assinar")."""
    norm = _normalize(text)
    return norm[1:].strip() if norm.startswith("/") else norm


def is_billing_command(text: str) -> bool:
    """True se o texto é assinar/cancelar/plano. Sem DB, sem efeito colateral.

    Existe pro gate de plano (core.handle_incoming._paywall_gate) poder isentar
    estes comandos SEM reimplementar os triggers: é o mesmo papel do
    `_GATE_EXEMPT_PREFIXES = ("/billing", ...)` da web (frontend/routes/shared.py)
    — quem está barrado precisa conseguir assinar."""
    norm = _norm_cmd(text)
    return bool(norm) and (
        norm in _ASSINAR_TRIGGERS
        or norm in _CANCELAR_TRIGGERS
        or norm in _PLANO_TRIGGERS
    )


def _normalize(text: str) -> str:
    """Lowercase + remove acentos + colapsa espacos."""
    if not text:
        return ""
    t = text.strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


def _bold(s: str, platform: str) -> str:
    return f"*{s}*" if platform == "whatsapp" else f"**{s}**"


def _format_plan_expires(expires_at) -> str:
    if not expires_at:
        return "sem data definida"
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except Exception:
            return str(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    # Converte pra horario de Brasilia (UTC-3) sem precisar de pytz
    local = expires_at.astimezone(timezone.utc)
    # Usa offset fixo BRT (UTC-3); suficiente pra exibir no chat.
    # DÍVIDA (#179): offset cravado, fora da fonte única `utils_date._tz()`. Só
    # exibição (data de validade da assinatura), sem efeito em dinheiro nem em
    # janela de consulta — por isso ficou fora do PR que unificou as leituras.
    from datetime import timedelta
    brt = local.astimezone(timezone(timedelta(hours=-3)))
    return brt.strftime("%d/%m/%Y às %H:%M")


def _handle_assinar(user_id: int, platform: str) -> str:
    from core.services.email_service import plan_display_name
    from core.services.plan_service import is_pro
    from db import get_auth_user

    if is_pro(user_id):
        user = get_auth_user(user_id)
        expires = _format_plan_expires((user or {}).get("plan_expires_at"))
        # #351 no bot: `pro_max` (PigBank Pro) lia "PigBank+" aqui, sem gate de v2.
        nome = plan_display_name((user or {}).get("plan"))
        link = build_dashboard_link(user_id, hours=1.0, next_path="/conta") or "https://pigbankai.com/conta"
        b = lambda s: _bold(s, platform)
        return (
            f"🐷 Você já tá no {b(nome)}!\n\n"
            f"Próxima renovação: {b(expires)}\n\n"
            f"Pra ver detalhes ou cancelar:\n{link}"
        )

    link = build_dashboard_link(user_id, hours=1.0, next_path="/precos")
    if not link:
        return (
            "🐷 Ih, não consegui gerar o link agora. Tenta de novo em uns segundos "
            "ou abre direto pigbankai.com/precos no navegador."
        )
    b = lambda s: _bold(s, platform)
    try:
        from core.services.plan_service import plans_v2_enabled
        v2_enabled = plans_v2_enabled()
    except ImportError:
        v2_enabled = False
    # Import local: `trial_offer` importa `_bold` DAQUI; no topo fecharia o ciclo.
    from .trial_offer import texto_da_oferta
    offer_text = texto_da_oferta(user_id, platform) if v2_enabled else (
        f"Aqui ó, link pra assinar com {b('15 dias grátis')} "
        "(cancela quando quiser, sem cobrança no trial):"
    )
    return (
        f"🐷✨ Bora pro {b('PigBank+')}?\n\n"
        f"{offer_text}\n"
        f"{link}\n\n"
        f"Esse link é só seu e expira em 1h."
    )


def _handle_cancelar(user_id: int, platform: str) -> str:
    """Entrega o portal da Stripe a quem TEM o que cancelar.

    **O predicado é `plano pago vigente OR carência`, não `is_pro`** — aquele é
    `tier >= plus` e mandava o assinante ESSENCIAL (que paga) e a conta em
    CARÊNCIA (assinatura viva em retentativa) para a copy de quem não tem nada.
    """
    # Import defensivo, mesmo motivo do `_handle_plano`: testes (e deploys sem a
    # escada v2) mockam plan_service só com `is_pro`. Sem os símbolos v2 cai no
    # binário legado, onde Essencial não existe e `is_pro` É o predicado certo.
    try:
        from core.services.plan_service import get_plan_tier
        tier = get_plan_tier(user_id)
    except ImportError:
        from core.services.plan_service import is_pro
        tier = "plus" if is_pro(user_id) else "free"

    if tier == "free":
        estado = billing_copy.estado_sem_plano_pago(user_id)
        if estado == "sem_plano":
            return billing_copy.SEM_PLANO.format(assinar=_bold("assinar plano", platform))
        if estado == "sem_acesso":
            return billing_copy.SEM_ACESSO.format(assinar=_bold("assinar plano", platform))
        # carência: a assinatura EXISTE e está em retentativa — segue para o
        # portal, que é onde se atualiza o cartão ou se encerra de vez.

    link = build_dashboard_link(user_id, hours=1.0, next_path="/conta")
    if not link:
        return (
            "🐷 Não consegui gerar o link agora. Tenta de novo em uns segundos "
            "ou abre direto pigbankai.com/conta no navegador."
        )
    b = lambda s: _bold(s, platform)
    return (
        f"🐷 Quer cancelar? Sem hard feelings.\n\n"
        f"Abre esse link pra gerenciar sua assinatura no portal da Stripe:\n{link}\n\n"
        f"Você continua com acesso {b('até o fim do período já pago')} — "
        f"depois disso a conta fica sem plano ativo e eu paro de anotar por aqui."
    )


def _handle_plano(user_id: int, platform: str) -> str:
    from core.services.plan_service import is_pro
    from db import get_auth_user

    user = get_auth_user(user_id) or {}
    plan = (user.get("plan") or "free").lower()
    status = user.get("last_payment_status") or "—"
    expires = user.get("plan_expires_at")
    b = lambda s: _bold(s, platform)

    # ── Escada v2 (Grátis/Essencial/Plus/Pro) ───────────────────────────────
    # Import defensivo: testes (e deploys sem a escada) mockam plan_service só
    # com is_pro — sem os símbolos v2, cai no comportamento legado.
    try:
        from core.services.plan_service import plans_v2_enabled, get_plan_tier
        _v2 = plans_v2_enabled()
    except ImportError:
        _v2 = False
    if _v2:
        tier = get_plan_tier(user_id)

        if tier == "free":
            # Aqui vinha a ficha do plano Grátis ("30 lançamentos por mês · 1
            # caixinha · 20 mensagens de IA"). Ela descrevia um plano em que
            # dava pra ficar, e o corte tirou esse lugar. Tier `free` hoje é um
            # de TRÊS estados, e a carência não recebe a frase dos outros dois:
            # nela a assinatura está VIVA na Stripe, em retentativa.
            # `user` já veio do get_auth_user acima — sem SELECT novo.
            estado = billing_copy.estado_sem_plano_pago(user_id, user)
            if estado == "sem_plano":
                return billing_copy.SEM_PLANO.format(assinar=b("assinar plano"))
            if estado == "carencia":
                return billing_copy.COBRANCA_EM_ATRASO.format(cancelar=b("cancelar plano"))
            return billing_copy.SEM_ACESSO.format(assinar=b("assinar plano"))

        # Nota: o trial de 15 dias hoje é uma assinatura Stripe do plano escolhido
        # (status trialing) — cai no ramo pago abaixo com status_label "Período
        # grátis em andamento" e "Primeira cobrança" na data. Não há mais o
        # estado "Plus sem assinatura" (trial sem cartão).
        tier_name = {"essencial": "Essencial", "plus": "Plus", "pro": "Pro"}.get(tier, tier.title())
        if status == "grandfathered":
            return (
                f"🐷 Plano: {b(tier_name)}\n\n"
                f"Status: {b('Ativo · acesso vitalício')}\n"
                f"Você tem o {tier_name} de brinde, pra sempre — sem cobrança. 🐷✨"
            )
        status_label = {
            "trialing": "Período grátis em andamento",
            "active": "Ativo",
            "past_due": "Pagamento em atraso",
            "canceled": "Cancelado",
            "unpaid": "Não pago",
        }.get(status, "Ativo")
        next_label = "Próxima renovação" if status == "active" else (
            "Primeira cobrança" if status == "trialing" else "Expira em"
        )
        return (
            f"🐷 Plano: {b(tier_name)}\n\n"
            f"Status: {status_label}\n"
            f"{next_label}: {b(_format_plan_expires(expires))}\n\n"
            f"Pra cancelar: manda {b('cancelar plano')}"
        )

    if not is_pro(user_id):
        return (
            f"🐷 Plano: {b('Free')}\n\n"
            f"O que vem aqui:\n"
            f"• 1 caixinha e 1 cartão de crédito\n"
            f"• Histórico dos últimos 30 dias\n"
            f"• Lançamentos manuais por chat e dashboard\n\n"
            f"Quer ver o que rola no PigBank+? Manda {b('assinar plano')} 🐷✨"
        )

    expires_fmt = _format_plan_expires(expires)

    # Grandfathered = Pro vitalício de brinde (backfill pré-paywall). Não tem
    # assinatura no Stripe, então não há expiração nem "cancelar plano".
    if status == "grandfathered":
        return (
            f"🐷 Plano: {b('PigBank+')}\n\n"
            f"Status: {b('Ativo · acesso vitalício')}\n"
            f"Você tem o PigBank+ de brinde, pra sempre — sem cobrança. 🐷✨"
        )

    # is_pro=True mas last_payment_status vazio/None/"inactive" significa
    # que o user é Pro mas o webhook do Stripe nunca rodou (acesso manual,
    # ambiente de teste, etc). Não faz sentido dizer "Status: Inativo"
    # quando o acesso tá liberado — mostra como "Ativo" pra não confundir.
    is_uninitialized = (
        not status or status in ("—", "inactive")
    )
    if is_uninitialized:
        status_label = "Ativo"
        next_label = "Renovação"
    else:
        status_label = {
            "trialing": "Trial em andamento (15 dias grátis)",
            "active": "Ativo",
            "past_due": "Pagamento em atraso",
            "canceled": "Cancelado",
            "unpaid": "Não pago",
        }.get(status, status)
        next_label = "Próxima renovação" if status == "active" else (
            "Fim do trial / primeira cobrança" if status == "trialing" else "Expira em"
        )

    return (
        f"🐷 Plano: {b('PigBank+')}\n\n"
        f"Status: {status_label}\n"
        f"{next_label}: {b(expires_fmt)}\n\n"
        f"Pra cancelar: manda {b('cancelar plano')}"
    )


def handle_billing_command(user_id: int, text: str, platform: str = "whatsapp") -> str | None:
    """
    Retorna a resposta pro comando de billing, ou None se nao for um comando reconhecido.
    `platform` controla a formatacao (negrito etc): "whatsapp" ou "discord".

    Importante: se o user tem pending action no DB (confirmação de delete,
    parcelamento etc), retorna None pra deixar o ai_chat_command resolver
    a confirmação. Senão "cancelar" puro durante uma confirmação de delete
    viraria comando de billing.
    """
    # Match rápido: se nem encosta nos triggers, retorna sem mexer no DB (e sem
    # normalizar duas vezes — a maioria das mensagens sai por aqui).
    if not is_billing_command(text):
        return None
    norm = _norm_cmd(text)

    # Pending action → cede o turno pro ai_chat_command. Cobre "cancelar"
    # puro durante confirmação de delete, etc.
    try:
        import db
        if db.ai_get_pending_action(user_id):
            return None
    except Exception:
        # Falha de DB no check não pode bloquear o billing — segue pra
        # resposta normal.
        pass

    if norm in _ASSINAR_TRIGGERS:
        return _handle_assinar(user_id, platform)
    if norm in _CANCELAR_TRIGGERS:
        return _handle_cancelar(user_id, platform)
    if norm in _PLANO_TRIGGERS:
        return _handle_plano(user_id, platform)
    return None
