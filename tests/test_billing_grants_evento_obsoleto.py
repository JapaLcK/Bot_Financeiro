"""
tests/test_billing_grants_evento_obsoleto.py — QUEM É "evento obsoleto", e o
contrato de retorno de `upsert_grant` (§4.1.2 do docs/plano_pix_anual_asaas.md).

A corrida entre o REPARO da varredura e o evento em voo (invariante 3 do
§4.1.1 D) é assunto vizinho, e mora em `test_billing_grants_reparo_nao_e_evento.py`.

Arquivo próprio porque a pergunta é própria, e porque errá-la custa nos DOIS
sentidos. `upsert_grant` devolver `None` mandava o webhook pular
`set_payment_status`/`recompute_entitlement`:

  • largo demais → conta paga fica `trialing`/`past_due` (empate de segundo,
    reentrega do 5xx, corrida com o reparo da varredura);
  • estreito demais → evento velho devolve `active` a uma conta cancelada, e
    grant revogado ressuscita.

`..._materializacao.py` cobre o grant ENTRAR e `..._reducao.py` o grant SAIR;
aqui é só a CLASSIFICAÇÃO do evento.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, evt_checkout as _evt_checkout,
    evt_deleted as _evt_deleted, evt_paid as _evt_paid, ler as _ler,
    sub_stripe as _sub,
)
from db.connection import get_conn
from db.plan_grants import list_grants, revoke_grant, upsert_grant
from test_billing_webhook_lifecycle import _post, _setup


def test_9p_invoice_paid_VELHO_nao_devolve_status_active_a_conta_cancelada(user_id, monkeypatch):
    """Ordem fora de sequência: paid, deleted, e o paid ANTIGO reentregue.

    A guarda de versão protegia o GRANT e o `set_payment_status` passava por
    fora dela: o evento velho voltava a gravar `active` numa conta que o
    `deleted` acabou de cancelar.

    **Das três asserções, só a do `last_payment_status` DISCRIMINA.** As outras
    duas (`plan == "free"` e o grant `revoked`) passam com e sem o conserto,
    porque o grant já estava protegido pela guarda de `event_version` — elas
    são contexto, não medição.
    """
    uid, client, fake = _setup(monkeypatch, f"g9p-{user_id}")
    _conta(uid, "free", None, "inactive")
    subs = {"sub_9p": _sub("active", "price_x", 30)}

    r = _post(client, fake, _evt_paid(uid, "sub_9p", 1_800_000_000), subs=subs)
    assert r.status_code == 200, r.text
    assert _ler(uid)["last_payment_status"] == "active"

    r = _post(client, fake, _evt_deleted(uid, "sub_9p", 1_800_000_200))
    assert r.status_code == 200, r.text

    r = _post(client, fake, _evt_paid(uid, "sub_9p", 1_800_000_100), subs=subs)
    assert r.status_code == 200, r.text

    row = _ler(uid)
    assert row["plan"] == "free"
    assert row["last_payment_status"] == "canceled", (
        "evento velho devolveu 'active' a uma conta cancelada — a guarda de "
        "versao protegia o grant e o status passava por fora dela")
    assert [g["status"] for g in list_grants(uid) if g["source"] == "stripe"] == ["revoked"]


def test_9q_reentrega_apos_5xx_conclui_e_NAO_e_tratada_como_evento_velho(user_id, monkeypatch):
    """POSITIVO: retry do 5xx != evento obsoleto.

    A falha entra DEPOIS do grant (que já commitou), então a reentrega encontra
    a MESMA `event_version` — `V > V` é falso e o `returning` do upsert vem
    vazio, exatamente como num evento velho. Sem a reclassificação, a 2a
    entrega seria obsoleta e o acesso NUNCA seria escrito: 200 na Stripe e
    conta em `free` para sempre.

    O `9b` não cobre isto: lá a 1a entrega conclui e já escreveu tudo.
    """
    uid, client, fake = _setup(monkeypatch, f"g9q-{user_id}")
    _conta(uid, "free", None, "inactive")

    import db
    real = db.set_payment_status
    tentativas: list[int] = []

    def falha_na_primeira(*a, **k):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise RuntimeError("banco caiu DEPOIS do grant")
        return real(*a, **k)

    monkeypatch.setattr(db, "set_payment_status", falha_na_primeira)

    evento = _evt_paid(uid, "sub_9q", 1_800_000_000)
    subs = {"sub_9q": _sub("active", "price_x", 30)}

    r = _post(client, fake, evento, subs=subs)
    assert r.status_code >= 500, f"falha pos-grant devolveu {r.status_code}"
    assert _ler(uid)["plan"] == "free", "auth_accounts escrito apesar do 5xx"
    assert len(list_grants(uid)) == 1, "o grant tem de estar commitado antes da falha"

    r = _post(client, fake, evento, subs=subs)          # a MESMA entrega, de novo
    assert r.status_code == 200, r.text

    row = _ler(uid)
    assert row["plan"] == "pro", (
        "reentrega do MESMO evento foi tratada como evento velho — o 5xx "
        "retryable deixa de reparar coisa nenhuma")
    assert row["plan_expires_at"] > datetime.now(timezone.utc)
    assert row["last_payment_status"] == "active"
    assert len(list_grants(uid)) == 1, "reentrega duplicou o grant"


# ── 9u / 9w: empate de segundo, e o que o obsoleto NÃO bloqueia ──────────────

def test_9u_checkout_e_paid_no_MESMO_segundo_sao_eventos_irmaos_nao_velhos(user_id, monkeypatch):
    """Compra imediata: a Stripe emite `checkout.session.completed` e
    `invoice.paid` praticamente juntos, e `created` tem precisão de SEGUNDOS —
    o §6.1 chama esse empate de caso real, não teórico.

    São eventos DIFERENTES com a MESMA versão. Classificar por `last_event_id`
    igual fazia o segundo virar "obsoleto": a conta ficava `trialing` com a
    fatura paga, e saía um `billing_evento_obsoleto` FALSO.
    """
    uid, client, fake = _setup(monkeypatch, f"g9u-{user_id}")
    _conta(uid, "free", None, "inactive")
    T = 1_800_000_000

    r = _post(client, fake, _evt_checkout(uid, "sub_9u", T, "cs_9u"),
              subs={"sub_9u": _sub("trialing", "price_x", 15)})
    assert r.status_code == 200, r.text
    assert _ler(uid)["last_payment_status"] == "trialing"

    r = _post(client, fake, _evt_paid(uid, "sub_9u", T),
              subs={"sub_9u": _sub("active", "price_x", 30)})
    assert r.status_code == 200, r.text
    assert _ler(uid)["last_payment_status"] == "active", (
        "invoice.paid empatado em segundos com o checkout virou EVENTO VELHO; "
        "a conta ficou 'trialing' com a fatura paga")


def test_9w_evento_obsoleto_de_VERDADE_ainda_queima_o_trial_e_registra_o_funil(user_id, monkeypatch):
    """Decisão do dono, opção (B): obsoleto bloqueia só ACESSO.

    `claim_trial_for_user` (uma trava de trial por telefone NA VIDA), o funil e
    o `mark_plan_selected` continuam rodando — evento obsoleto quer dizer que
    outro evento já decidiu o acesso, não que a fatura não foi paga. Também é o
    positivo do 9u/9v: aqui a classificação PRECISA dar obsoleto.
    """
    uid, client, fake = _setup(monkeypatch, f"g9w-{user_id}")
    _conta(uid, "free", None, "inactive")
    agora = datetime.now(timezone.utc)
    upsert_grant(uid, "stripe", "sub_9w", "pro", agora,
                 agora + timedelta(days=30), 1_900_000_000, "evt_novo")

    r = _post(client, fake, _evt_checkout(uid, "sub_9w", 1_800_000_000, "cs_9w"),
              subs={"sub_9w": _sub("trialing", "price_x", 15)})
    assert r.status_code == 200, r.text
    assert _ler(uid)["last_payment_status"] != "trialing", (
        "evento VELHO de verdade rebaixou o status de uma conta ja paga")

    from db.plans import is_trial_eligible_for_user
    assert is_trial_eligible_for_user(uid) is False, (
        "evento obsoleto pulou o claim_trial_for_user — trava antifraude furada")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from checkout_funnel_events"
                        " where user_id=%s and kind='completed'", (uid,))
            assert int(cur.fetchone()["n"]) == 1, "funil não registrou"
            cur.execute("select plan_selected_at from auth_accounts where user_id=%s", (uid,))
            assert cur.fetchone()["plan_selected_at"] is not None, "mark_plan_selected pulado"
            cur.execute("delete from plan_trials where phone_hash=%s", (f"ph_wh_{uid}",))
        conn.commit()


# ── as três cláusulas do `_NAO_E_EVENTO_VELHO`, uma a uma ────────────────────

def test_reentrega_nao_supersede_o_legacy_com_versao_MENOR(user_id):
    """Onde a supersessão do `legacy` mora: amarrada ao `returning` do upsert, e
    NÃO à reclassificação.

    O caso 14 do §16 usa evento VELHO, cuja reclassificação também devolve None
    — ele fica verde com a supersessão em QUALQUER das duas posições. Quem
    discrimina é a REENTREGA, que devolve `id`: se a supersessão rodasse ali,
    revogaria o `legacy` com `event_version` MENOR que a dele
    (`_SUPERSEDE_LEGACY` não tem guarda de versão).
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=300), "active")
    sub = f"sub_sup_{user_id}"
    upsert_grant(user_id, "stripe", sub, "pro", agora,
                 agora + timedelta(days=30), 3_000_000, "evt_paid_X")
    upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro", agora,
                 agora + timedelta(days=300), 5_000_000)   # resync posterior
    assert next(g for g in list_grants(user_id)
                if g["source"] == "legacy")["status"] == "active"

    assert upsert_grant(user_id, "stripe", sub, "pro", agora,
                        agora + timedelta(days=30), 3_000_000, "evt_paid_X") is not None

    assert next(g for g in list_grants(user_id)
                if g["source"] == "legacy")["status"] == "active", (
        "a reentrega revogou o legacy com event_version 3_000_000 < 5_000_000")


def test_reclassificacao_nao_ressuscita_grant_REVOGADO(user_id):
    """A cláusula `status = 'active'`, e a razão de ela existir.

    `revoke_grant` preserva o `last_event_id` (`coalesce`) e, no empate do §6.1,
    carimba a MESMA versão da concessão — então um grant revogado casa ref,
    versão e evento. Só o `status` o separa. Sem ele, o `paid` reentregue depois
    do `deleted` empatado devolveria `id`, o webhook reprojetaria acesso, e a
    invariante 1 do §4.1.1 D cairia pela porta dos fundos.
    """
    agora = datetime.now(timezone.utc)
    sub = f"sub_res_{user_id}"
    T = 1_700_000_000
    upsert_grant(user_id, "stripe", sub, "pro", agora,
                 agora + timedelta(days=30), T, "evt_paid_1")
    assert revoke_grant(user_id, "stripe", sub, "deleted", T, None) is True
    g = next(x for x in list_grants(user_id) if x["source"] == "stripe")
    assert g["status"] == "revoked" and g["last_event_id"] == "evt_paid_1"
    assert int(g["event_version"]) == T

    assert upsert_grant(user_id, "stripe", sub, "pro", agora,
                        agora + timedelta(days=30), T, "evt_paid_1") is None, (
        "reclassificação ressuscitou grant revogado")


def test_reclassificacao_nao_vaza_grant_entre_donos(user_id):
    """CLAUDE.md §0: a unique é `(source, external_ref)`, GLOBAL.

    O upsert do dono B colide com a linha de A e a guarda
    `plan_grants.user_id = excluded.user_id` o recusa. A reclassificação é a
    única query nova que LÊ `plan_grants`, e sem `user_id = %s` ela devolveria o
    **id do grant de A** para B — que então teria acesso projetado a partir de
    assinatura alheia.
    """
    import uuid as _uuid

    from db import ensure_user
    outro = int(_uuid.uuid4().int % 10_000_000_000)   # o `second_user_id` dos
    ensure_user(outro)                                # irmãos, sem 3a cópia da fixture

    agora = datetime.now(timezone.utc)
    ref = f"sub_vaza_{user_id}"
    assert upsert_grant(user_id, "stripe", ref, "pro", agora,
                        agora + timedelta(days=30), 2_000_000, "evt_do_A") is not None

    assert upsert_grant(outro, "stripe", ref, "pro_max", agora,
                        agora + timedelta(days=30), 2_000_000, "evt_do_A") is None, (
        "o grant do usuario A foi devolvido para o usuario B")
    assert [g["external_ref"] for g in list_grants(outro)] == []
