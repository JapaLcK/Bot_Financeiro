"""
core/services/billing_access.py — a projeção de `plan_grants` em `auth_accounts`.

`auth_accounts.plan`/`plan_expires_at` continuam sendo o modelo de LEITURA de
todo o app (nenhum dos ~30 leitores muda). O que muda é quem os escreve: em vez
de cada ramo do webhook gravar o par na mão, os ramos passam a registrar um
GRANT e esta função projeta o estado a partir da lista de grants.

Plano: docs/plano_pix_anual_asaas.md §4.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.connection import get_conn
from db.plan_grants import list_grants, users_com_grant_na_janela

# §4.1 — dois grants nossos encostados podem ter alguns segundos de folga entre
# `ends_at` de um e `starts_at` do outro. 120 s porque todo encadeamento que NÓS
# geramos é exato; buraco real (revogação antecipada, estorno do meio) é sempre
# de DIAS, e a tolerância não o emenda.
# ponytail: constante fixa, não env. Vira parâmetro no dia em que existir
# produtor de grants com data arredondada em dia.
GAP_TOLERANCIA = timedelta(seconds=120)


def _tier_do_stored(plan_stored: str) -> int:
    """Posição do valor legado da coluna `plan` na escada de tiers.

    Importa a tabela de core/services/plan_service em vez de reescrevê-la: ela é
    a fonte de verdade da escada (CLAUDE.md §0.7), e uma segunda cópia aqui
    seria a que ninguém lembra de atualizar quando um tier novo entrar.
    """
    from core.services.plan_limits import TIER_ORDER
    from core.services.plan_service import _STORED_PLAN_TO_TIER

    return TIER_ORDER.get(_STORED_PLAN_TO_TIER.get((plan_stored or "").lower(), "free"), 0)


def projetar_grants(grants: list[dict], agora: datetime) -> tuple[str, datetime | None]:
    """FUNÇÃO PURA: lista de grants + instante → o par (plan, plan_expires_at).

    Cobertura contígua a partir de AGORA:
      - sem nenhum grant vigente agora → ('free', None). Grant FUTURO não conta:
        acesso que ainda não começou não é acesso.
      - com vigente: a cobertura vai até o maior `ends_at` entre os vigentes, e
        depois estica pelos grants futuros ENQUANTO eles emendarem (dentro da
        GAP_TOLERANCIA). O primeiro buraco encerra a cobertura — é o que faz
        estorno do meio e revogação antecipada aparecerem na data.
      - o plano é o MAIOR tier entre os grants que cobrem agora (o downgrade
        agendado só vira tier quando o grant de tier maior deixa de ser vigente).
    """
    ativos = sorted(
        (g for g in grants if g["status"] == "active" and g["ends_at"] > agora),
        key=lambda g: g["starts_at"],
    )
    vigentes = [g for g in ativos if g["starts_at"] <= agora]
    if not vigentes:
        return "free", None

    cobertura = max(g["ends_at"] for g in vigentes)
    for g in ativos:
        if g["starts_at"] <= agora:
            continue
        if g["starts_at"] > cobertura + GAP_TOLERANCIA:
            break                                   # primeiro buraco
        cobertura = max(cobertura, g["ends_at"])

    plano = max(vigentes, key=lambda g: _tier_do_stored(g["plan_stored"]))["plan_stored"]
    return plano, cobertura


def _ler_conta(user_id: int) -> dict | None:
    """SELECT enxuto de propósito — NÃO passa por `get_auth_user`.

    Aquele traz PII cifrada, chama `decrypt_pii_optional` e registra em
    `pii_access_log`; a projeção roda em todo webhook de cobrança e a cada
    passada do loop de 60 s, então usá-lo duplicaria a auditoria de PII por
    evento de billing. Mesmo motivo do `get_onboarding_state` (db/reports.py).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status"
                "  from auth_accounts where user_id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def recompute_entitlement(user_id: int) -> dict | None:
    """Reprojeta `auth_accounts` a partir dos grants. Devolve o par escrito, ou
    None quando decidiu NÃO escrever.

    Três saídas antecipadas, e cada uma protege um caso que o modelo de grants
    não sabe representar:

    1. `grandfathered` — vitalício por decisão, não tem `ends_at` a inventar.
    2. plano pago com `plan_expires_at is null` — vitalício DE FATO, mesmo sem o
       status. Sem esta saída, o `None` viraria "venceu" e a conta cairia.
    3. **usuário sem NENHUM grant** — ausência de grant é DESCONHECIMENTO, não
       ausência de direito. É a propriedade que torna este PR reversível: o
       código novo nunca destrói os valores de que o código antigo depende.
       Quem TEM grants e todos venceram é rebaixado normalmente — aí há
       informação, e a guarda não pode virar "nunca rebaixa".
    """
    conta = _ler_conta(user_id)
    if not conta:
        return None

    if (conta.get("last_payment_status") or "").lower() == "grandfathered":
        return None

    plano_atual = (conta.get("plan") or "free").lower()
    expira_atual = conta.get("plan_expires_at")
    if plano_atual != "free" and expira_atual is None:
        return None

    agora = datetime.now(timezone.utc)
    grants = list_grants(user_id)
    if not grants:
        pago_e_vigente = plano_atual != "free" and expira_atual is not None and expira_atual > agora
        if pago_e_vigente:
            _alertar_sem_grants(user_id, plano_atual, expira_atual)
        return None

    plano, expira = projetar_grants(grants, agora)

    from db import set_payment_status, update_user_plan

    update_user_plan(user_id, plano, expira)

    # Grant Pix vigente agora manda no status: a assinatura do cartão pode estar
    # 'canceled' e o acesso continuar pago pelo Pix. Sem grant Pix o status do
    # Stripe é preservado — quem manda nele continua sendo o webhook do cartão.
    if any(g["source"] == "pix" and g["status"] == "active"
           and g["starts_at"] <= agora < g["ends_at"] for g in grants):
        set_payment_status(user_id, "active")

    return {"plan": plano, "plan_expires_at": expira}


def _alertar_sem_grants(user_id: int, plano: str, expira) -> None:
    """Conta paga e vigente sem um grant sequer: ou o resync não rodou, ou algo
    apagou os grants. Loga sempre e alerta no máximo 1x/dia por usuário.

    Falha silenciosa: observabilidade nunca derruba o fluxo de cobrança.
    """
    try:
        from core.observability import log_system_event_sync, recent_event_exists

        log_system_event_sync(
            "warning",
            "projecao_sem_grants",
            "Conta paga e vigente sem nenhum grant; projecao nao escreveu nada.",
            source="billing",
            user_id=int(user_id),
            details={"plan": plano, "plan_expires_at": expira.isoformat() if expira else None},
        )
        if not recent_event_exists("projecao_sem_grants_alertado", int(user_id), within_days=1.0):
            from core.services.admin_notify import _send

            _send(f"Conta {user_id} paga ({plano}) e vigente sem nenhum plan_grant.")
            log_system_event_sync(
                "info", "projecao_sem_grants_alertado", "Alerta enviado.",
                source="billing", user_id=int(user_id),
            )
    except Exception as exc:                                  # pragma: no cover
        print(f"[billing_access] alerta projecao_sem_grants falhou user={user_id}: {exc}")


def reprojetar_grants_recentes(desde: datetime) -> int:
    """Reprojeta quem teve grant COMEÇANDO ou TERMINANDO desde `desde` (§4.3).

    São as duas únicas transições de acesso que acontecem sem nenhum evento
    externo — grant futuro que vira vigente e grant vigente que vence. Sem isto,
    um downgrade agendado só apareceria no próximo webhook do usuário.

    Devolve quantos usuários foram reprojetados.
    """
    uids = users_com_grant_na_janela(desde)
    for uid in uids:
        try:
            recompute_entitlement(uid)
        except Exception as exc:                              # pragma: no cover
            print(f"[billing_access] reprojecao falhou user={uid}: {exc}")
    return len(uids)
