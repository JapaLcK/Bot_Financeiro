"""
tests/test_billing_grants_backfill.py — backfill inicial do §5 e as guardas de
escrita da projeção (§4.1) do docs/plano_pix_anual_asaas.md.

Casos 1–11 e 9f do §16. Materialização e regra da redução ficam em
`test_billing_grants_materializacao.py`; ordem de eventos e projeção pura em
`test_billing_grants_projecao.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, evt_deleted as _evt_deleted, evt_paid as _evt_paid,
    grant as _grant, ler as _ler, rodar_resync as _rodar_resync,
    sub_stripe as _sub,
)
from core.services.billing_access import recompute_entitlement
from db.connection import get_conn
from db.plan_grants import list_grants, upsert_grant
from test_billing_webhook_lifecycle import _post, _setup


# ── Backfill inicial (§5.1) ───────────────────────────────────────────────────

def test_01_assinante_preexistente_sem_grants_e_o_backfill(user_id, monkeypatch):
    """No dia do deploy a base pagante tem plano e validade, e zero grants.

    Primeira metade: a projeção NÃO escreve nada (é a guarda que torna o PR
    reversível). Segunda metade: depois do backfill ela reproduz exatamente o
    que já estava em auth_accounts — e é ela que discrimina.
    """
    expira = datetime.now(timezone.utc) + timedelta(days=180)
    _conta(user_id, "pro", expira)

    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda msg: True)

    assert recompute_entitlement(user_id) is None
    assert list_grants(user_id) == []
    assert _ler(user_id)["plan"] == "pro"

    _rodar_resync()
    g = _grant(user_id, "legacy")
    assert g is not None and g["status"] == "active"
    assert g["plan_stored"] == "pro" and g["event_version"] == 0

    assert recompute_entitlement(user_id) == {"plan": "pro", "plan_expires_at": expira}


def test_02_backfill_duas_vezes_da_um_grant_so(user_id):
    _conta(user_id, "pro_max", datetime.now(timezone.utc) + timedelta(days=90))
    _rodar_resync()
    _rodar_resync()
    assert len([g for g in list_grants(user_id) if g["source"] == "legacy"]) == 1


def test_03_grandfathered_fica_de_fora(user_id):
    """Vitalício não é grant: `plan_expires_at is null` não tem ends_at a inventar."""
    _conta(user_id, "pro", None, "grandfathered")
    _rodar_resync()
    assert _grant(user_id, "legacy") is None
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro"


def test_04_o_boot_NAO_repara_grant_existente(user_id):
    """**v6 (§5.1): o backfill deixou de reparar.** Linha de grant existente
    nunca é tocada pelo boot, nem para "estender".

    A versão anterior usava `do update` e copiava `auth_accounts` de volta para
    dentro do grant — transformava a PROJEÇÃO em fonte de verdade. Quem repara
    grant defasado é a regra da redução (§4.1.1 B), que lê o Stripe, fora do
    boot; o pagante não é rebaixado na janela porque a varredura confirma antes
    de reduzir (ver `test_billing_grants_materializacao.py`).

    NOTA: o §16 do plano ainda descreve o caso 4 com o contrato ANTIGO
    ("roda o resync → ends_at atualizado"). §5.1 e §17 v6 dizem o contrário e
    riscam o `do update` explicitamente; este teste segue o §5.1/§17.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=200))
    _rodar_resync()

    congelado = agora - timedelta(days=5)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update plan_grants set ends_at=%s where user_id=%s and source='legacy'",
                        (congelado, user_id))
        conn.commit()

    _rodar_resync()
    g = _grant(user_id, "legacy")
    assert abs((g["ends_at"] - congelado).total_seconds()) < 1, "o boot mexeu num grant existente"
    assert len(list_grants(user_id)) == 1, "o boot criou grant para quem já tinha"


def test_9f_boot_nao_ressuscita_grant_revogado(user_id, monkeypatch):
    """`legacy` revogado por `superseded_by_stripe` + `auth_accounts` pago e
    vigente (sustentado pelo grant `stripe`): DOIS boots e ele continua morto.

    `auth_accounts` é PROJEÇÃO dos grants; deixá-la recriar grant revogado é
    transformar projeção de volta em direito.
    """
    uid, client, fake = _setup(monkeypatch, f"g9f-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=30))
    _rodar_resync()
    _post(client, fake, _evt_paid(uid, "sub_9f", 1_800_000_000),
          subs={"sub_9f": _sub("active", "price_x", 300)})
    assert _grant(uid, "legacy")["status"] == "revoked"

    antes = list_grants(uid)
    _rodar_resync()
    _rodar_resync()
    depois = list_grants(uid)

    assert _grant(uid, "legacy")["status"] == "revoked", "o boot ressuscitou grant revogado"
    assert _grant(uid, "legacy")["revoked_reason"] == "superseded_by_stripe"
    assert len(depois) == len(antes)
    assert [g["ends_at"] for g in depois] == [g["ends_at"] for g in antes]


def test_backfill_com_DUAS_auth_accounts_do_mesmo_user_nao_derruba_o_boot(user_id):
    """`auth_accounts` não tem unique em `user_id`. Duas linhas pagas e vigentes
    gerariam dois `legacy:<uid>` no MESMO `on conflict`, e o Postgres recusa o
    comando (`CardinalityViolation`). Isto roda dentro do `init_db`: não
    degradaria a migração, derrubaria a SUBIDA da aplicação.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "essencial", agora + timedelta(days=10))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts (user_id, email, password_hash, plan,"
                "                           plan_expires_at, last_payment_status)"
                " values (%s, %s, 'x', 'pro_max', %s, 'active')",
                (user_id, f"bf-dup-{user_id}@t.local", agora + timedelta(days=300)))
        conn.commit()

    _rodar_resync()                      # sem o distinct on, estoura aqui

    legados = [g for g in list_grants(user_id) if g["source"] == "legacy"]
    assert len(legados) == 1
    assert legados[0]["plan_stored"] == "pro_max", "desempate tem de ser a maior validade"


# ── Guardas de escrita da projeção (§4.1) ─────────────────────────────────────

def test_08_conta_paga_sem_nenhum_grant_nao_escreve_e_alerta(user_id, monkeypatch):
    """Ausência de grant é DESCONHECIMENTO. Se a projeção escrevesse `free`
    aqui, o revert do PR encontraria a base pagante já rebaixada."""
    expira = datetime.now(timezone.utc) + timedelta(days=200)
    _conta(user_id, "pro", expira)

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda msg: alertas.append(msg) or True)

    assert recompute_entitlement(user_id) is None
    row = _ler(user_id)
    assert row["plan"] == "pro" and row["plan_expires_at"] == expira
    assert len(alertas) == 1


def test_09_grants_todos_vencidos_rebaixam_por_EVENTO(user_id):
    """A guarda do 8 não pode virar "nunca rebaixa": com grants, há informação.
    Por evento não há consulta ao Stripe — o evento é a autoridade."""
    _conta(user_id, "pro", datetime.now(timezone.utc) + timedelta(days=200))
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "stripe", f"sub_venc_{user_id}", "pro",
                 agora - timedelta(days=400), agora - timedelta(days=1), 1000)

    assert recompute_entitlement(user_id) == {"plan": "free", "plan_expires_at": None}
    assert _ler(user_id)["plan"] == "free"


def test_10_vitalicio_sai_antes_de_qualquer_escrita(user_id):
    """Duas saídas antecipadas: `grandfathered` e pago com validade NULL."""
    _conta(user_id, "pro", None, "grandfathered")
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro"

    _conta(user_id, "pro_max", None, "active")
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro_max"


# ── Supersessão e cancelamento (§5.1, §4.2) ───────────────────────────────────

def test_05_primeiro_invoice_paid_cria_grant_e_revoga_o_legacy(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"g05-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    assert _grant(uid, "legacy")["status"] == "active"

    r = _post(client, fake, _evt_paid(uid, "sub_g05", 1_800_000_000),
              subs={"sub_g05": _sub("active", "price_x", 30)})
    assert r.status_code == 200, r.text

    assert _grant(uid, "stripe")["status"] == "active"
    assert _grant(uid, "legacy")["status"] == "revoked"
    assert _grant(uid, "legacy")["revoked_reason"] == "superseded_by_stripe"
    assert _ler(uid)["plan"] == "pro"


def test_06_grant_do_stripe_mais_curto_ENCURTA_o_acesso(user_id, monkeypatch):
    """Sem a supersessão, o legado sustentaria acesso além do que o Stripe diz."""
    uid, client, fake = _setup(monkeypatch, f"g06-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()

    r = _post(client, fake, _evt_paid(uid, "sub_g06", 1_800_000_000),
              subs={"sub_g06": _sub("active", "price_x", 7)})
    assert r.status_code == 200, r.text
    assert _ler(uid)["plan_expires_at"] < datetime.now(timezone.utc) + timedelta(days=10)


def test_07_assinatura_que_lapsa_perde_o_acesso_no_vencimento(user_id, monkeypatch):
    """`payment_failed` → `past_due` e nenhum `deleted`: quando o grant vence, o
    acesso ACABA — o legado já foi superseded e não segura."""
    uid, client, fake = _setup(monkeypatch, f"g07-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    _post(client, fake, _evt_paid(uid, "sub_g07", 1_800_000_000),
          subs={"sub_g07": _sub("active", "price_x", 5)})

    r = _post(client, fake,
              {"type": "invoice.payment_failed", "id": "evt_pf", "created": 1_800_000_100,
               "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                   "attempt_count": 1}}})
    assert r.status_code == 200
    assert _ler(uid)["last_payment_status"] == "past_due"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update plan_grants set ends_at=now() - interval '1 day'"
                        " where user_id=%s", (uid,))
        conn.commit()
    assert recompute_entitlement(uid) == {"plan": "free", "plan_expires_at": None}


def test_11_subscription_deleted_revoga_stripe_e_legacy(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"g11-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    _post(client, fake, _evt_paid(uid, "sub_g11", 1_800_000_000),
          subs={"sub_g11": _sub("active", "price_x", 30)})

    r = _post(client, fake, _evt_deleted(uid, "sub_g11", 1_800_000_500))
    assert r.status_code == 200, r.text
    assert _grant(uid, "stripe")["status"] == "revoked"
    assert _grant(uid, "legacy")["status"] == "revoked"
    assert _ler(uid)["plan"] == "free"


def test_11b_deleted_sem_grant_stripe_ainda_derruba_o_legacy(user_id, monkeypatch):
    """Assinante que já existia no deploy e cancela SEM passar por um
    `invoice.paid`: só existe o `legacy`, e ele é a reconstrução do mesmo acesso
    de cartão — tem de cair junto, senão o cancelamento deixa de rebaixar."""
    uid, client, fake = _setup(monkeypatch, f"g11b-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    assert _grant(uid, "legacy")["status"] == "active"

    r = _post(client, fake, _evt_deleted(uid, "sub_g11b", 1_800_000_500))
    assert r.status_code == 200, r.text
    assert _grant(uid, "legacy")["status"] == "revoked"
    assert _ler(uid)["plan"] == "free"
