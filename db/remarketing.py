"""db/remarketing.py — a régua de remarketing do checkout abandonado
(docs/plano-remarketing.md). PR 1: só o T0 e o "já comprou"; nada envia.
"""
from __future__ import annotations

from .connection import get_conn

# `ja_comprou(user_id)` é a ÚNICA porta: o lote do PR 6 a chama por candidato. Este fragmento
# é só a parte SQL (parcial: a allowlist e a carência de inadimplência ficam em Python), e é
# parametrizado por `_params(uid)`. Compra = QUALQUER uma das pernas (plano §1), todas por
# `user_id`. Grant de qualquer fonte e status: trial, revogado, vencido, admin, pix e legacy
# contam (quem já foi cliente fica fora, P2). As demais pegam o que não tem grant: ex-assinante
# de antes de `plan_grants`, vigente que o backfill de boot não alcança (`plan_expires_at`
# nulo), Pix pago cujo dreno falhou antes do grant. Plano pago = a lista do `plan_service`
# (vigente ou vencido); status e Pix = só os que existem depois de haver pagamento (`_params`).
_JA_COMPROU_SQL = """(
  exists (select 1 from plan_grants where user_id = %(uid)s and source <> 'family')
  or exists (select 1 from checkout_funnel_events where user_id = %(uid)s and kind = 'completed')
  or exists (select 1 from pix_charges where user_id = %(uid)s and status = any(%(pix_pagos)s))
  or exists (select 1 from auth_accounts where user_id = %(uid)s
             and lower(coalesce(plan, '')) = any(%(pagos)s))
  or exists (select 1 from auth_accounts where user_id = %(uid)s
             and lower(coalesce(last_payment_status, '')) = any(%(assinou)s))
)"""

# Status de `pix_charges` (alfabeto: check `pix_charges_status_valido`, db/schema.py) em que o
# dinheiro chegou alguma vez: paid, paid_orphan (pago, sem dono ainda) e os três de estorno,
# que só se alcançam a partir de um pagamento. FORA: draft, creating, pending, canceling,
# canceled e expired (nunca pagou). Não há constante pronta (as outras são tuplas locais).
_PIX_PAGOS = ("paid", "paid_orphan", "refunded", "refunded_partial", "chargeback")


def _params(uid: int) -> dict:
    from core.services import plan_service  # noqa: PLC0415 — db/ não importa core/ no topo
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES

    return {
        "uid": uid,
        "pix_pagos": list(_PIX_PAGOS),
        # a mesma derivação de `scripts/aviso_fim_do_gratis.PLANOS_PAGOS`
        "pagos": sorted(p for p, t in plan_service._STORED_PLAN_TO_TIER.items() if t != "free"),
        # Status de `last_payment_status` que só existem depois de haver assinatura:
        # grandfathered (vitalício), active, trialing, canceled e os de atraso (past_due,
        # unpaid). FORA: `incomplete` e `incomplete_expired` (1ª cobrança que nunca fechou:
        # abandono, o público da régua) e `inactive` (o padrão da conta nova).
        "assinou": ["grandfathered", "active", "trialing", "canceled",
                    *(x for x in PAST_DUE_PAYMENT_STATUSES if x != "incomplete")],
    }


def registrar_t0(user_id: int, origem: str) -> None:
    """Grava o T0 uma vez e nunca o zera: a 2ª chamada não muda nada.
    Levanta em falha; quem chama decide (as duas rotas engolem e logam)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into remarketing_regua (user_id, origem) values (%s, %s)"
            " on conflict (user_id) do nothing",
            (int(user_id), origem),
        )
        conn.commit()


def ja_comprou(user_id: int) -> bool:
    """O usuário já comprou, em qualquer sentido (P2: reconquista é outra campanha).

    O Pix recebido alguma vez é uma perna SQL (`pix_charges`), não `pix_cobre_agora`: P2 não
    pede janela em curso, e ela importa o monólito.
    "Tem acesso pago agora" vem de `plan_service.tem_direito_hoje` (plano vigente OU
    carência de inadimplência), a regra do gate, sem cópia. Não uso `has_app_access`: ela
    devolve True para todo mundo com `ACCESS_GATE_ENABLED=0` e muda de regra com o v2
    desligado. `tem_direito_hoje` é pura sobre um dict: leio as 4 colunas que ela usa, sem
    `get_auth_user` (que decifra PII, grava auditoria e tem cache de 10 s).
    Exclusão agendada NÃO entra aqui: é outra pergunta (o `/retomar` manda quem comprou
    para o login e quem está saindo para a /precos), e o PR 6 a lê ao lado.
    """
    from core.services import plan_service  # noqa: PLC0415

    uid = int(user_id)
    if uid in plan_service._ACCESS_ALLOWLIST:
        return True
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select {_JA_COMPROU_SQL} as ok", _params(uid))
        if cur.fetchone()["ok"]:
            return True
        # `auth_accounts.user_id` não é unique: basta uma linha com direito.
        cur.execute("select plan, plan_expires_at, past_due_since, last_payment_status"
                    " from auth_accounts where user_id = %s", (uid,))
        contas = cur.fetchall()
        conn.commit()
    return any(plan_service.tem_direito_hoje(c) for c in contas)


def marcar_clique(user_id: int, etapa: int) -> None:
    """Carimba o 1º clique no link da etapa (`clicado_em`, só se ainda nulo). Sem a linha
    (a etapa não foi reivindicada: link forjado ou de teste) não faz nada. Prévias de link
    inflam o número (plano §6)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update remarketing_envios set clicado_em = now()"
            " where user_id = %s and etapa = %s and clicado_em is null",
            (int(user_id), int(etapa)),
        )
        conn.commit()


# ── PR 6: o lote de envio ────────────────────────────────────────────────────

# (etapa, início, fim) a partir do T0: `[início, fim)`. Fonte única das janelas (plano §1).
_JANELAS = ((1, "30 minutes", "12 hours"), (2, "24 hours", "36 hours"), (3, "48 hours", "60 hours"))


def candidatos(desde, agora, limite: int = 200) -> list[tuple[int, int]]:
    """`(user_id, etapa)` devidos em `agora`. É a ÚNICA query sem `user_id` do módulo: é a
    varredura, e só devolve chaves (nenhum dado). Já com linha da etapa (qualquer status) não volta; E2/E3 só após a anterior `enviado`."""
    janelas = " union all ".join(
        f"select {e} as etapa, interval '{i}' as ini, interval '{f}' as fim" for e, i, f in _JANELAS)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select r.user_id, j.etapa from remarketing_regua r"
            f" join ({janelas}) j on %(agora)s >= r.t0 + j.ini and %(agora)s < r.t0 + j.fim"
            f" where r.t0 >= %(desde)s"
            f" and not exists (select 1 from remarketing_envios v where v.user_id = r.user_id and v.etapa = j.etapa)"
            f" and (j.etapa = 1 or exists (select 1 from remarketing_envios p where p.user_id = r.user_id"
            f"      and p.etapa = j.etapa - 1 and p.status = 'enviado'))"
            f" order by r.t0, j.etapa limit %(limite)s",
            {"agora": agora, "desde": desde, "limite": limite},
        )
        linhas = cur.fetchall()
        conn.commit()
    # ponytail: teto de 200 por tick (300 s); quem comprou/saiu é fechado (`sem_canal`) pelo `_um` e sai da fila.
    # Só `em_andamento` (< 60 min) segue na fila sem linha. Acima disso, paginar por keyset.
    return [(int(r["user_id"]), int(r["etapa"])) for r in linhas]


def estado_fresco(user_id: int) -> dict | None:
    """Lido direto do banco (sem o cache de 10 s do `get_auth_user`): e-mail, telefone, os
    dois opt-outs, exclusão agendada e origem do T0. None = sem `auth_accounts` ou sem T0."""
    from core.crypto import PiiAccessContext, decrypt_pii_optional  # noqa: PLC0415
    from .privacy import email_da_linha  # noqa: PLC0415

    uid = int(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select a.email, a.email_enc, a.phone_e164, a.phone_enc, a.engagement_opt_out,"
            " a.whatsapp_updates_opt_out,"
            " (a.deletion_status in ('scheduled', 'processing') and a.deletion_scheduled_for is not null) as saindo,"
            " (select origem from remarketing_regua where user_id = %(uid)s) as origem"
            " from auth_accounts a where a.user_id = %(uid)s order by a.id limit 1",
            {"uid": uid},
        )
        row = cur.fetchone()
        conn.commit()
    if not row or not row["origem"]:
        return None
    fone = row["phone_e164"]
    if row["phone_enc"]:
        fone = decrypt_pii_optional(row["phone_enc"], ctx=PiiAccessContext(
            purpose="remarketing", actor="system", subject_user_id=uid, field="phone"))
    return {
        "email": email_da_linha(row, uid), "telefone": fone, "origem": row["origem"],
        "opt_out_email": bool(row["engagement_opt_out"]),
        "opt_out_whatsapp": bool(row["whatsapp_updates_opt_out"]),   # NULL = não desligou
        "saindo": bool(row["saindo"]),
    }


def em_andamento(user_id: int, agora) -> bool:
    """Checkout aberto agora (adia sem reivindicar): `started` de menos de 60 min sem
    `completed`/`expired` na mesma sessão, ou Pix draft/creating/pending de menos de 60 min."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select exists (select 1 from checkout_funnel_events s where s.user_id = %(uid)s"
            "   and s.kind = 'started' and s.created_at > %(agora)s - interval '60 minutes'"
            "   and not exists (select 1 from checkout_funnel_events f where f.user_id = %(uid)s"
            "                   and f.session_id = s.session_id and f.kind in ('completed', 'expired')))"
            " or exists (select 1 from pix_charges where user_id = %(uid)s"
            "   and status in ('draft', 'creating', 'pending')"
            "   and created_at > %(agora)s - interval '60 minutes') as sim",
            {"uid": int(user_id), "agora": agora},
        )
        sim = cur.fetchone()["sim"]
        conn.commit()
    return bool(sim)


def numero_de_outra_conta(user_id: int, telefone: str) -> bool:
    """O número é de OUTRA conta (#721)? Os mesmos candidatos do auto-vínculo
    (`candidatos_simetricos`, 9º dígito) contra `auth_accounts.phone_hash` e as identidades
    de WhatsApp, sempre `user_id <> uid`. Devolve só o booleano."""
    from core.crypto import hash_pii_optional  # noqa: PLC0415
    from utils_phone import candidatos_simetricos  # noqa: PLC0415

    cands = candidatos_simetricos(telefone)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select exists (select 1 from auth_accounts where phone_hash = any(%(fones)s) and user_id <> %(uid)s)"
            " or exists (select 1 from user_identities where provider = 'whatsapp'"
            "            and external_id_hash = any(%(ext)s) and user_id <> %(uid)s) as sim",
            {"uid": int(user_id), "fones": [hash_pii_optional(c, kind="phone") for c in cands],
             "ext": [hash_pii_optional(c, kind="external_id") for c in cands]},
        )
        sim = cur.fetchone()["sim"]
        conn.commit()
    return bool(sim)


def reivindicar(user_id: int, etapa: int) -> bool:
    """Claim atômico da etapa: só quem insere a linha envia (duas réplicas, um envio)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into remarketing_envios (user_id, etapa, status) values (%s, %s, 'enviando')"
            " on conflict (user_id, etapa) do nothing returning id",
            (int(user_id), int(etapa)),
        )
        ganhou = cur.fetchone() is not None
        conn.commit()
    return ganhou


def fechar(user_id: int, etapa: int, email_ok: bool | None, whatsapp_ok: bool | None, motivo: str | None) -> None:
    """Grava o resultado: `enviado` se algum canal saiu, senão `sem_canal`. `None` = não tentado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update remarketing_envios set status = case when coalesce(%(e)s::boolean, false) or coalesce(%(w)s::boolean, false) then 'enviado' else 'sem_canal' end,"
            " email_ok = %(e)s, whatsapp_ok = %(w)s, motivo = left(%(m)s, 120), fechado_em = now()"
            " where user_id = %(uid)s and etapa = %(etapa)s",
            {"e": email_ok, "w": whatsapp_ok, "m": motivo, "uid": int(user_id), "etapa": int(etapa)},
        )
        conn.commit()
