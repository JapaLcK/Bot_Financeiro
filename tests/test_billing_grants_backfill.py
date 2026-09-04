"""
tests/test_billing_grants_backfill.py — resync do §5.1 e o webhook do Stripe
escrevendo grants (§4.2, §5.1, §6 do docs/plano_pix_anual_asaas.md).

Casos 1 a 7, 11 e 24 do §16.

A infra do webhook (`_FakeStripe`, `_setup`, `_post`) é reusada de
`test_billing_webhook_lifecycle.py` em vez de reescrita — é a mesma máquina de
cobrança, e uma segunda cópia divergiria na primeira mudança do webhook.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.connection import get_conn
from db.plan_grants import list_grants
from db.schema import RESYNC_LEGACY_GRANTS_SQL
from test_billing_webhook_lifecycle import _post, _setup

from core.services.billing_access import recompute_entitlement


def _rodar_resync() -> None:
    """Executa EXATAMENTE o SQL que o init_db roda no boot — sem segunda cópia."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(RESYNC_LEGACY_GRANTS_SQL)
        conn.commit()


def _conta(uid: int, plan: str, expires, status: str = "active") -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan=%s, plan_expires_at=%s,"
                "       last_payment_status=%s where user_id=%s",
                (plan, expires, status, uid),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "insert into auth_accounts (user_id, email, password_hash, plan,"
                    "                           plan_expires_at, last_payment_status)"
                    " values (%s, %s, 'x', %s, %s, %s)",
                    (uid, f"bf-{uid}@t.local", plan, expires, status),
                )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _ler(uid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status"
                "  from auth_accounts where user_id=%s", (uid,))
            return dict(cur.fetchone())


def _legacy(uid: int) -> dict | None:
    linhas = [g for g in list_grants(uid) if g["source"] == "legacy"]
    return linhas[0] if linhas else None


def _stripe(uid: int) -> dict | None:
    linhas = [g for g in list_grants(uid) if g["source"] == "stripe"]
    return linhas[0] if linhas else None


# ──────────────────────────────────────────────────────────────────────────────
# Resync (§5.1) — casos 1 a 4
# ──────────────────────────────────────────────────────────────────────────────

def test_01_assinante_preexistente_sem_grants_e_o_resync(user_id, monkeypatch):
    """No dia do deploy a base pagante tem plano e validade, e zero grants.

    Primeira metade: a projeção NÃO escreve nada (é a guarda que torna o PR
    reversível). Segunda metade: depois do resync ela reproduz exatamente o que
    já estava em auth_accounts. É a segunda metade que discrimina — sem o
    resync ela fica vermelha.
    """
    expira = datetime.now(timezone.utc) + timedelta(days=180)
    _conta(user_id, "pro", expira)

    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda msg: True)

    assert recompute_entitlement(user_id) is None
    assert list_grants(user_id) == []
    assert _ler(user_id)["plan"] == "pro"

    _rodar_resync()
    g = _legacy(user_id)
    assert g is not None and g["status"] == "active"
    assert g["plan_stored"] == "pro" and g["event_version"] == 0

    assert recompute_entitlement(user_id) == {"plan": "pro", "plan_expires_at": expira}
    assert _ler(user_id)["plan_expires_at"] == expira


def test_02_resync_duas_vezes_da_um_grant_so(user_id):
    _conta(user_id, "pro_max", datetime.now(timezone.utc) + timedelta(days=90))
    _rodar_resync()
    _rodar_resync()
    assert len([g for g in list_grants(user_id) if g["source"] == "legacy"]) == 1


def test_03_grandfathered_fica_de_fora_do_resync(user_id):
    """Vitalício não é grant: `plan_expires_at is null` não tem ends_at a inventar."""
    _conta(user_id, "pro", None, "grandfathered")
    _rodar_resync()
    assert _legacy(user_id) is None
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro"


def test_04_resync_e_do_update_entao_o_revert_reconcilia_sozinho(user_id):
    """Simula revert + re-aplicação do PR: durante a janela do revert os grants
    ficam congelados enquanto auth_accounts anda. O `do update` do §5.1 põe os
    dois de acordo no boot seguinte; com `do nothing` o assinante em dia viraria
    `free` na primeira reprojeção.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=200))
    _rodar_resync()

    # janela do revert: o legacy fica para trás, auth_accounts é renovado
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update plan_grants set ends_at=%s where user_id=%s and source='legacy'",
                (agora - timedelta(days=5), user_id))
        conn.commit()
    assert recompute_entitlement(user_id) == {"plan": "free", "plan_expires_at": None}

    _conta(user_id, "pro", agora + timedelta(days=200))     # re-aplicação: boot
    _rodar_resync()
    g = _legacy(user_id)
    assert g["ends_at"] > agora, "o resync tem de ESTENDER o legacy congelado"
    assert recompute_entitlement(user_id)["plan"] == "pro"


# ──────────────────────────────────────────────────────────────────────────────
# Webhook do Stripe escrevendo grants — casos 5, 6, 7, 11 e 24
# ──────────────────────────────────────────────────────────────────────────────

def _sub(status: str, price_id: str, dias: int) -> dict:
    ts = int((datetime.now(timezone.utc) + timedelta(days=dias)).timestamp())
    return {"status": status,
            "current_period_end": ts,
            "items": {"data": [{"price": {"id": price_id}, "current_period_end": ts}]}}


def _evt_paid(uid: int, sub_id: str, created: int) -> dict:
    return {"type": "invoice.paid", "id": f"evt_paid_{created}", "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": sub_id, "amount_paid": 0,
                                "id": f"in_{created}"}}}


def _evt_deleted(uid: int, sub_id: str, created: int) -> dict:
    return {"type": "customer.subscription.deleted", "id": f"evt_del_{created}",
            "created": created,
            "data": {"object": {"id": sub_id, "metadata": {"finbot_user_id": str(uid)}}}}


def test_05_primeiro_invoice_paid_cria_grant_stripe_e_revoga_o_legacy(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"g05-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    assert _legacy(uid)["status"] == "active"

    r = _post(client, fake, _evt_paid(uid, "sub_g05", 1_800_000_000),
              subs={"sub_g05": _sub("active", "price_x", 30)})
    assert r.status_code == 200, r.text

    assert _stripe(uid)["status"] == "active"
    assert _legacy(uid)["status"] == "revoked"
    assert _legacy(uid)["revoked_reason"] == "superseded_by_stripe"
    # cobertura contínua: o grant do Stripe cobre a partir de agora
    assert _ler(uid)["plan"] == "pro"


def test_06_grant_do_stripe_mais_curto_ENCURTA_o_acesso(user_id, monkeypatch):
    """Sem a supersessão do §5.1, o legado sustentaria acesso além do que o
    Stripe diz — o assinante que trocou para um período menor continuaria pago
    por 100 dias."""
    uid, client, fake = _setup(monkeypatch, f"g06-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()

    r = _post(client, fake, _evt_paid(uid, "sub_g06", 1_800_000_000),
              subs={"sub_g06": _sub("active", "price_x", 7)})
    assert r.status_code == 200, r.text

    novo = _ler(uid)["plan_expires_at"]
    assert novo < datetime.now(timezone.utc) + timedelta(days=10), (
        "o legacy de 100 dias não pode sobreviver ao grant do Stripe de 7")


def test_07_assinatura_que_lapsa_sem_deleted_perde_o_acesso_no_vencimento(user_id, monkeypatch):
    """`payment_failed` → `past_due` e nenhum `deleted`: quando o grant do
    Stripe vence, o acesso ACABA — o legado já foi superseded e não segura."""
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

    # o grant vence (relógio do teste = mexer na linha, não no relógio do banco)
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
    assert _stripe(uid)["status"] == "revoked"
    assert _legacy(uid)["status"] == "revoked"
    assert _ler(uid)["plan"] == "free"


def test_11b_deleted_sem_grant_stripe_ainda_derruba_o_legacy(user_id, monkeypatch):
    """Assinante que já existia no deploy e cancela SEM passar por um
    `invoice.paid` no meio: só existe o grant `legacy`. Ele é a reconstrução do
    mesmo acesso de cartão, então tem de cair junto — senão o cancelamento
    deixaria de rebaixar, que é o comportamento de HOJE."""
    uid, client, fake = _setup(monkeypatch, f"g11b-{user_id}")
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=100))
    _rodar_resync()
    assert _legacy(uid)["status"] == "active"

    r = _post(client, fake, _evt_deleted(uid, "sub_g11b", 1_800_000_500))
    assert r.status_code == 200, r.text
    assert _legacy(uid)["status"] == "revoked"
    assert _ler(uid)["plan"] == "free"


def test_11c_deleted_SEM_id_da_assinatura_ainda_rebaixa(user_id, monkeypatch):
    """O objeto do `customer.subscription.deleted` nem sempre traz o `id`.

    Amarrar a revogação a ele deixava o grant `stripe` vivo, e a projeção que
    roda em seguida RESSUSCITAVA o plano por cima do `free` que o webhook
    acabara de escrever — regressão contra o comportamento de hoje, e não teoria:
    foi o que quebrou
    `test_billing_webhook_lifecycle.py::test_checkout_completed_fecha_o_gate_de_escolha`.
    """
    uid, client, fake = _setup(monkeypatch, f"g11c-{user_id}")
    _post(client, fake, _evt_paid(uid, "sub_g11c", 1_800_000_000),
          subs={"sub_g11c": _sub("active", "price_x", 30)})
    assert _stripe(uid)["status"] == "active"

    r = _post(client, fake,
              {"type": "customer.subscription.deleted", "id": "evt_sem_sub",
               "created": 1_800_000_500,
               "data": {"object": {"metadata": {"finbot_user_id": str(uid)}}}})
    assert r.status_code == 200, r.text
    assert _stripe(uid)["status"] == "revoked"
    assert _ler(uid)["plan"] == "free"


def test_24_positivo_assinante_stripe_puro_se_comporta_como_hoje(user_id, monkeypatch):
    """O positivo mais importante do PR: sem nenhum grant Pix, o assinante do
    cartão termina em `plan_expires_at == current_period_end` a cada renovação,
    e o `subscription.deleted` manda para `free` — igualzinho a antes."""
    uid, client, fake = _setup(monkeypatch, f"g24-{user_id}")

    for i, dias in enumerate((30, 60, 90)):
        sub = _sub("active", "price_x", dias)
        r = _post(client, fake, _evt_paid(uid, "sub_g24", 1_800_000_000 + i),
                  subs={"sub_g24": sub})
        assert r.status_code == 200, r.text
        esperado = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
        row = _ler(uid)
        assert row["plan"] == "pro"
        assert row["plan_expires_at"] == esperado, (
            f"renovação {i}: projeção divergiu do current_period_end")
        assert row["last_payment_status"] == "active"

    r = _post(client, fake, _evt_deleted(uid, "sub_g24", 1_800_000_500))
    assert r.status_code == 200, r.text
    row = _ler(uid)
    assert row["plan"] == "free" and row["plan_expires_at"] is None
    assert row["last_payment_status"] == "canceled"
