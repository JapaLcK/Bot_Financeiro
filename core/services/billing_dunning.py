"""
core/services/billing_dunning.py — bloqueio do bot por inadimplência de cartão.

Quem está com a cobrança do CARTÃO falhada há `DUNNING_GRACE_DAYS` dias perde o
bot (WhatsApp e Discord). O plano `free` NÃO é tocado: quem nunca escolheu
plano, quem cancelou por vontade, quem escolheu o Grátis e quem teve grant de
admin expirado continuam usando — cada um deles é preservado por uma guarda
explícita de `bloqueado_por_inadimplencia`, na ordem em que elas aparecem.

**A INVARIANTE**: relógio (`auth_accounts.past_due_since`) não nulo só existe
em conta cujo `last_payment_status` está em `PAST_DUE_PAYMENT_STATUSES`. Quem a
mantém é ESTRUTURAL e mora na escrita, não aqui: `db_support.
set_payment_status_impl` zera o relógio no MESMO UPDATE quando o status vai
para fora da lista. Ler a docstring dela antes de mexer em qualquer um dos dois
lados.

A guarda de status deste módulo (passo 2 abaixo) é **defesa em profundidade**,
não o que torna o órfão seguro. Ela cobre a linha PRÉ-EXISTENTE (carimbada
antes de a invariante existir, ou pelo backfill de `db/schema.py`) e o caso de
alguém escrever a coluna por fora. Órfão NÃO é dado morto: enquanto ele
existir, o próximo `invoice.payment_failed` devolve o status para a lista, o
`claim_past_due_since` vê `rowcount 0` e a conta é cortada na PRIMEIRA falha do
ciclo novo, com carência zero e sem o e-mail de aviso. A versão anterior desta
docstring dizia o contrário e foi o raciocínio que deixou o produtor de órfão
(`billing_access.recompute_entitlement`) sem conserto.
Coberta por teste: linhas 16a/16b/16c da tabela de
`tests/test_billing_dunning.py` (a guarda) e `test_T4_*` de
`tests/test_billing_dunning_webhook.py` (a invariante na escrita).

Import leve de propósito (só `os`/`datetime` no topo): este módulo entra no
caminho de TODA mensagem do bot, inclusive no processo do Discord. `db`,
`plan_service` e `billing_access` entram dentro das funções — mesmo padrão de
`trial_downsell.py` e `billing_access.py`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# Status crus de `last_payment_status` (o webhook grava o do Stripe sem
# traduzir, `db_support.set_payment_status`) que descrevem uma cobrança de
# cartão EM ATRASO com a assinatura ainda VIVA lá: 'unpaid' é dunning e
# 'incomplete' é 3DS pendente — nenhum dos dois é terminal (os terminais são
# 'canceled' e 'incomplete_expired').
#
# FONTE ÚNICA da lista (§0.7). Ela nasceu em `core/admin_dashboard.py`, que
# decide com ela o rótulo do painel e o gate de `/trial-reset`; mora aqui
# porque `admin_dashboard` importa fastapi/bcrypt/jwt/slowapi e custa ~357 ms
# de import — caro demais para o caminho de mensagem do bot. O admin passou a
# importar daqui, e `_LIVE_PAYMENT_STATUSES` continua se compondo dela.
# NÃO crie uma quarta lista.
PAST_DUE_PAYMENT_STATUSES = ("past_due", "unpaid", "incomplete")

# Carência entre a primeira falha de cobrança e o corte. Constante de módulo,
# não env: é regra de produto (decisão do dono), não parâmetro de rollout — o
# que liga e desliga é a flag abaixo.
DUNNING_GRACE_DAYS = 7


def dunning_block_enabled() -> bool:
    """Corte do bot por inadimplência. Default DESLIGADO, lido dinamicamente do
    ambiente pra ligar/desligar sem redeploy (mesmo formato de
    `plan_service.paywall_enabled`). Liga com DUNNING_BLOCK_ENABLED=true."""
    return (os.getenv("DUNNING_BLOCK_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def bloqueado_por_inadimplencia(user_id: int, agora: datetime | None = None) -> bool:
    """True se a conta deve perder o bot por inadimplência de cartão.

    A ordem das guardas é do barato ao caro E cada uma preserva uma população
    que NÃO pode ser bloqueada. Não reordene sem reler o teste da tabela:

      1. allowlist de admin/teste;
      2. status fora de `PAST_DUE_PAYMENT_STATUSES` — inclui `free`,
         `canceled`, `active`, `trialing`, `grandfathered` e conta sem status.
         é a DEFESA EM PROFUNDIDADE da invariante do topo do arquivo (quem a
         mantém é `set_payment_status_impl`);
      3. sem `past_due_since` — nunca foi carimbada, não há relógio;
      4. dentro da carência;
      5. direito EFETIVO por outro caminho: grant `pix` ou `admin` vigente.
         `legacy` NÃO resgata — ele é a reconstrução do MESMO acesso de cartão
         feita pelo backfill (ver o `customer.subscription.deleted` do webhook,
         que o revoga junto com o grant do Stripe).

    Não usa `recompute_entitlement` (escreve, e pode chamar a API do Stripe)
    nem `projetar_grants`: um grant `stripe` de conta `past_due` tem `ends_at`
    FUTURO, então a projeção devolve "plano vigente" para o inadimplente. Ela é
    estruturalmente incapaz de separar Pix de cartão em atraso — reusá-la daria
    um gate que nunca bloqueia.
    """
    from core.services import plan_service

    uid = int(user_id)
    if uid in plan_service._ACCESS_ALLOWLIST:
        return False

    from db import get_auth_user

    user = get_auth_user(uid) or {}
    if (user.get("last_payment_status") or "").strip().lower() not in PAST_DUE_PAYMENT_STATUSES:
        return False

    desde = user.get("past_due_since")
    if desde is None:
        return False

    agora = agora or datetime.now(timezone.utc)
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=timezone.utc)
    if agora - desde < timedelta(days=DUNNING_GRACE_DAYS):
        return False

    from core.services.billing_access import grant_vigente
    from db.plan_grants import list_grants

    if grant_vigente(list_grants(uid), agora, sources=("pix", "admin")):
        return False

    return True
