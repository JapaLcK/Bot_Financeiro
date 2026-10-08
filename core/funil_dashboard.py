"""Painel de funil do admin (`/admin/funil`): só consultas e links, zero escrita.

A resposta é uma LISTA FECHADA de agregados: contagens e taxas. Nenhum `user_id`,
e-mail, nome, `signup_quiz`, `dashboard_profile` ou hash sai daqui — o quiz entra só
como "veio do quiz sim/não" (canal), nunca por perfil/respostas.
`tests/test_funil_dashboard.py` trava as chaves.

A regra de status de conta NÃO é copiada: vem de `admin_dashboard._ACCOUNT_STATUS_SQL`
(§0.7), assim como o funil de checkout por sessão (`_fetch_checkout_funnel`).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from core import admin_dashboard
from core.admin_dashboard import (
    _ACCOUNT_STATUS_SQL,
    _USER_STATUSES,
    _fetch_checkout_funnel,
    db_connect,
)
from core.services.billing_dunning import DUNNING_GRACE_DAYS, PAST_DUE_PAYMENT_STATUSES

JANELAS = (7, 30)
# Precedência do canal: quem veio de afiliado é afiliado mesmo que tenha feito o quiz.
CANAIS = ("afiliado", "prospeccao", "quiz", "direto")
# Valores que `signup_source_from_request` (frontend/routes/shared.py) grava. Lista FECHADA:
# a coluna é texto livre, então o que não estiver aqui (inclusive NULL) sai como "outro" e
# nunca vai ao JSON. `tests/test_funil_dashboard.py` trava a igualdade com a função.
ORIGENS = ("web", "app", "google", "google_app", "apple", "apple_app")


def _taxa(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None  # divisão por zero: sem taxa, nunca 0%


def _etapas(cad: int, v: int, s: int, c: int, *, viram_medido: bool) -> list[dict]:
    """Etapas CUMULATIVAS ("alcançou pelo menos"), já recebidas como contagens.

    `viram_medido` falso = o coorte começa antes de `viewed_pricing` existir: o n de
    "Viram /precos" só enxerga quem abriu checkout, então toda taxa que passa por ele
    (entrando ou saindo) vira None em vez de um número enganoso.
    """
    ns = [("cadastros", cad), ("viram_precos", v), ("abriram_checkout", s), ("concluiram", c)]
    out = []
    for i, (nome, n) in enumerate(ns):
        passa_por_viram = nome in ("viram_precos", "abriram_checkout")  # entra ou sai dele
        out.append({
            "id": nome,
            "n": n,
            "taxa_etapa": None if i == 0 or (passa_por_viram and not viram_medido)
            else _taxa(n, ns[i - 1][1]),
            "taxa_acum": None if i == 0 or (nome == "viram_precos" and not viram_medido)
            else _taxa(n, cad),
        })
    return out


def _linha(cad: int, v: int, s: int, c: int) -> dict:
    return {"cadastros": cad, "viram_precos": v, "abriram_checkout": s,
            "concluiram": c, "taxa_conclusao": _taxa(c, cad)}


def _canonica(onde: str = "") -> str:
    """Uma conta por PESSOA: `auth_accounts.user_id` não é único (só email/hashes são).

    A canônica é a mais antiga (o 1º cadastro; `id` desempata). `onde` só poda o que entra
    no DISTINCT ON, sempre por `user_id` inteiro (nunca corta linhas de uma mesma pessoa).
    """
    return (f"(SELECT DISTINCT ON (a.user_id) a.* FROM auth_accounts a {onde} "
            "ORDER BY a.user_id, a.created_at, a.id)")


# Coorte = pessoas cujo 1º cadastro (conta canônica) caiu na janela; a 2ª conta de um
# usuário antigo NÃO é cadastro novo. Plano/origem/quiz/status vêm da canônica.
# v/s/c/e = o usuário teve esse kind DEPOIS do início do coorte.
# Pix só grava `completed` (sem `started`), por isso as etapas são cumulativas.
_COORTE_SQL = f"""
WITH flags AS (
    SELECT a.user_id,
           {_ACCOUNT_STATUS_SQL} AS status,
           CASE WHEN a.signup_source = ANY(%(origens)s) THEN a.signup_source ELSE 'outro' END AS origem,
           CASE
             WHEN EXISTS (SELECT 1 FROM affiliate_referrals r WHERE r.referred_user_id = a.user_id) THEN 'afiliado'
             WHEN EXISTS (SELECT 1 FROM prospect_referrals p WHERE p.referred_user_id = a.user_id) THEN 'prospeccao'
             WHEN a.signup_quiz IS NOT NULL THEN 'quiz'
             ELSE 'direto'
           END AS canal,
           coalesce(bool_or(e.kind = 'viewed_pricing'), false) AS v,
           coalesce(bool_or(e.kind = 'started'), false)        AS s,
           coalesce(bool_or(e.kind = 'completed'), false)      AS c,
           coalesce(bool_or(e.kind = 'expired'), false)        AS e
    FROM {_canonica("WHERE a.user_id IN (SELECT user_id FROM auth_accounts WHERE created_at >= %(ini)s)")} a
    LEFT JOIN checkout_funnel_events e
           ON e.user_id = a.user_id AND e.created_at >= %(ini)s
    WHERE a.created_at >= %(ini)s
    GROUP BY a.user_id, a.plan, a.last_payment_status, a.plan_expires_at,
             a.signup_source, a.signup_quiz
)
SELECT GROUPING(canal, origem, status) AS g, canal, origem, status,
       count(*)                                  AS cad,
       count(*) FILTER (WHERE v OR s OR c)       AS viram,
       count(*) FILTER (WHERE s OR c)            AS abriram,
       count(*) FILTER (WHERE c)                 AS conc,
       count(*) FILTER (WHERE e AND NOT c)       AS exp_sem_conc
FROM flags
GROUP BY GROUPING SETS ((), (canal), (origem), (status))
"""
# GROUPING(canal, origem, status): bit 1 = coluna fora do agrupamento.
_G_TOTAL, _G_CANAL, _G_ORIGEM, _G_STATUS = 7, 3, 5, 6


async def _janela(cur, dias: int, agora: datetime, desde, ck: dict) -> dict:
    ini = agora - timedelta(days=dias)  # relógio do app (Python), não do banco; deriva de segundos é imaterial em 7-30 dias
    viram_medido = desde is not None and ini > desde
    p = {"ini": ini}

    await cur.execute(_COORTE_SQL, {**p, "origens": list(ORIGENS)})
    tot = {"cad": 0, "viram": 0, "abriram": 0, "conc": 0, "exp_sem_conc": 0}
    canais = {c: _linha(0, 0, 0, 0) for c in CANAIS}
    origens: dict[str, dict] = {}
    estado = {s: 0 for s in _USER_STATUSES}
    for r in await cur.fetchall():
        linha = _linha(r["cad"], r["viram"], r["abriram"], r["conc"])
        if r["g"] == _G_TOTAL:
            tot = r
        elif r["g"] == _G_CANAL:
            canais[r["canal"]] = linha
        elif r["g"] == _G_ORIGEM:
            origens[r["origem"]] = linha
        elif r["g"] == _G_STATUS:
            estado[r["status"]] = r["cad"]

    # Etapa 0 (informativa, janela de eventos e não coorte): e-mails distintos que
    # pediram código. Só a contagem; o hash nunca sai da query.
    await cur.execute(
        "SELECT count(DISTINCT email_hash) AS n FROM email_verification_codes "
        "WHERE created_at >= %(ini)s AND email_hash IS NOT NULL", p)
    emails = (await cur.fetchone())["n"]

    # Bloco 3: ativação de quem CONCLUIU checkout na janela (1 conta canônica por pessoa).
    # Sem filtro de data nos lançamentos: "já lançou alguma vez".
    concluiu = ("WHERE a.user_id IN (SELECT user_id FROM checkout_funnel_events "
                "WHERE kind = 'completed' AND created_at >= %(ini)s)")
    await cur.execute(
        f"""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE a.onboarding_completed_at IS NOT NULL) AS onboarding,
               count(*) FILTER (WHERE a.phone_status = 'confirmed' OR EXISTS (
                   SELECT 1 FROM user_identities ui
                   WHERE ui.user_id = a.user_id AND ui.provider = 'whatsapp')) AS whatsapp,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM launches l
                   WHERE l.user_id = a.user_id AND l.is_internal_movement = false)) AS lancamento
        FROM {_canonica(concluiu)} a
        """, p)
    ativ = await cur.fetchone()

    # Bloco 4: quem iniciou trial na janela e onde está hoje (pessoa = conta canônica;
    # trial iniciado só na 2ª conta, com a canônica sem trial, não conta).
    await cur.execute(
        f"SELECT {_ACCOUNT_STATUS_SQL} AS status, count(*) AS n FROM "
        + _canonica("WHERE a.user_id IN (SELECT user_id FROM auth_accounts "
                    "WHERE trial_started_at >= %(ini)s)")
        + " a WHERE a.trial_started_at >= %(ini)s GROUP BY 1", p)
    tr = {r["status"]: r["n"] for r in await cur.fetchall()}
    trial = {"iniciaram": sum(tr.values()), "em_trial": tr.get("trial", 0),
             "pagando": tr.get("paying", 0), "cancelaram": tr.get("canceled", 0)}
    trial["outros"] = trial["iniciaram"] - trial["em_trial"] - trial["pagando"] - trial["cancelaram"]

    # Bloco 2 (por sessão): pessoas / sessões abertas / concluídas vêm do helper do admin.
    await cur.execute(
        "SELECT count(DISTINCT session_id) AS n FROM checkout_funnel_events "
        "WHERE kind = 'expired' AND created_at >= %(ini)s", p)
    checkout = {"pessoas": ck[f"people_{dias}d"],
                "sessoes_abertas": ck[f"sessions_started_{dias}d"],
                "sessoes_concluidas": ck[f"sessions_completed_{dias}d"],
                "sessoes_expiradas": (await cur.fetchone())["n"]}
    checkout["conversao"] = _taxa(checkout["sessoes_concluidas"], checkout["sessoes_abertas"])

    # Bloco 5: Pix anual. draft/creating ainda não têm QR, não contam como "gerado".
    await cur.execute(
        """
        SELECT count(*) FILTER (WHERE status NOT IN ('draft', 'creating')) AS gerados,
               count(*) FILTER (WHERE paid_at IS NOT NULL)                 AS pagos,
               count(*) FILTER (WHERE status = 'expired')                  AS expirados,
               count(*) FILTER (WHERE status IN ('canceled', 'canceling')) AS cancelados,
               count(*) FILTER (WHERE status = 'pending')                  AS abertos
        FROM pix_charges WHERE created_at >= %(ini)s
        """, p)
    pix = dict(await cur.fetchone())
    pix["taxa_pago"] = _taxa(pix["pagos"], pix["gerados"])

    # Bloco 6: e-book da /assinar. pendente = ainda sem fechamento.
    await cur.execute(
        """
        SELECT count(*)                                            AS entregas,
               count(*) FILTER (WHERE resultado = 'enviado')       AS enviados,
               count(*) FILTER (WHERE resultado = 'nao_comprou')   AS nao_comprou,
               count(*) FILTER (WHERE resultado = 'estornado')     AS estornados,
               count(*) FILTER (WHERE fechada_em IS NULL)          AS pendentes
        FROM ebook_entregas WHERE criada_em >= %(ini)s
        """, p)
    ebook = dict(await cur.fetchone())

    return {
        "dias": dias,
        "inicio": ini,
        "viram_precos_medido": viram_medido,
        "emails_verificacao": emails,
        "etapas": _etapas(tot["cad"], tot["viram"], tot["abriram"], tot["conc"],
                          viram_medido=viram_medido),
        "abandono": tot["abriram"] - tot["conc"],
        "expiraram_sem_concluir": tot["exp_sem_conc"],
        "estado_atual": estado,
        "canais": [{"canal": c, **canais[c]} for c in CANAIS],
        "origens": [{"origem": o, **origens[o]} for o in sorted(origens)],
        "checkout": checkout,
        "ativacao": {"concluiram": ativ["n"], "onboarding": ativ["onboarding"],
                     "whatsapp": ativ["whatsapp"], "lancamento": ativ["lancamento"]},
        "trial": trial,
        "pix": pix,
        "ebook": ebook,
    }


async def fetch_funil() -> dict[str, Any]:
    """UMA conexão, 7d e 30d na mesma resposta."""
    agora = datetime.now(timezone.utc)
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT min(created_at) AS desde FROM checkout_funnel_events "
                "WHERE kind = 'viewed_pricing'")
            desde = (await cur.fetchone())["desde"]
            ck = await _fetch_checkout_funnel(cur)
            janelas = {f"{d}d": await _janela(cur, d, agora, desde, ck) for d in JANELAS}
            # Bloco 7: cobrança em atraso AGORA (não depende de janela); pessoa, não linha.
            await cur.execute(
                """
                SELECT count(DISTINCT user_id) AS total,
                       count(DISTINCT user_id) FILTER (WHERE past_due_since < now() - make_interval(days => %s)) AS alem_carencia
                FROM auth_accounts
                WHERE past_due_since IS NOT NULL
                  AND lower(coalesce(last_payment_status, '')) = ANY(%s)
                """, (DUNNING_GRACE_DAYS, list(PAST_DUE_PAYMENT_STATUSES)))
            atraso = dict(await cur.fetchone())
    atraso["carencia_dias"] = DUNNING_GRACE_DAYS
    return {
        "gerado_em": agora,
        "viewed_pricing_desde": desde,
        "janelas": janelas,
        "atraso": atraso,
        "links": links_externos(),
    }


def links_externos() -> list[dict]:
    """Só os painéis cujo ID existe. NUNCA inventa ID: sem ID, o link não aparece."""
    from frontend.routes import shared  # lazy: o módulo é caro de importar

    links = []
    if shared.CLARITY_PROJECT_ID:
        links.append({
            "painel": "Microsoft Clarity",
            "url": f"https://clarity.microsoft.com/projects/view/{shared.CLARITY_PROJECT_ID}/dashboard",
            "olhar": "Gravações e mapas de calor da /precos: onde a pessoa para antes de abrir o checkout.",
        })
    if shared.META_PIXEL_ID:
        links.append({
            "painel": "Meta Events Manager",
            "url": f"https://business.facebook.com/events_manager2/list/pixel/{shared.META_PIXEL_ID}/overview",
            "olhar": "Eventos do pixel (PageView, InitiateCheckout, Purchase) e a qualidade da correspondência.",
        })
    ga4 = (os.getenv("GA4_PROPERTY_ID") or "").strip()
    if ga4:
        links.append({
            "painel": "Google Analytics 4",
            "url": f"https://analytics.google.com/analytics/web/#/p{ga4}/reports/intelligenthome",
            "olhar": "Crie uma vez Explorar, Exploração de funil, com view_item_list, begin_checkout e purchase.",
        })
    stripe_key = admin_dashboard.STRIPE_SECRET_KEY  # no call time: o teste troca a chave
    if stripe_key:
        base = "https://dashboard.stripe.com" + ("/test" if stripe_key.startswith("sk_test_") else "")
        links.append({
            "painel": "Stripe, assinaturas",
            "url": f"{base}/subscriptions",
            "olhar": "Assinaturas ativas, em trial, em atraso e canceladas, com o motivo.",
        })
        links.append({
            "painel": "Stripe, pagamentos",
            "url": f"{base}/payments",
            "olhar": "Cobranças aprovadas e recusadas; recusa em série costuma ser cartão, não oferta.",
        })
    links.append({
        "painel": "Afiliados",
        "url": "/admin",
        "olhar": "Comissões, repasses e quem trouxe cadastro pelo programa de afiliados.",
    })
    return links
