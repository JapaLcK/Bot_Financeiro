"""
tests/test_billing_grants_admin.py — reparo manual do admin (§15 do
docs/plano_pix_anual_asaas.md) e o isolamento por usuário das escritas de grant.

Casos 49 e 50 do §16. Arquivo próprio porque é assunto próprio: `set_account_plan`
é a ferramenta de conciliação, e o §15 diz que ela não pode ser desligada pelo PR
que cria os casos que ela repara.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta as _conta, ler as _ler
from core.services.billing_access import recompute_entitlement
from db.plan_grants import list_grants, revoke_grant, upsert_grant


def test_grants_nao_vazam_entre_usuarios(user_id, second_user_id):
    """`(source, external_ref)` é unique GLOBAL: sem o filtro por user_id, o
    revoke de um usuário alcançaria a linha do outro."""
    agora = datetime.now(timezone.utc)
    ref = f"admin:{user_id}"
    upsert_grant(user_id, "admin", ref, "pro", agora, agora + timedelta(days=30), 1)

    assert revoke_grant(second_user_id, "admin", ref, "tentativa", 9_999_999) is False
    assert _grant_row(user_id, "admin")["status"] == "active"
    assert list_grants(second_user_id) == []


@pytest.fixture
def second_user_id():
    import uuid as _uuid
    from db import ensure_user
    uid = int(_uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    return uid


def _grant_row(uid: int, source: str = "stripe") -> dict:
    return [g for g in list_grants(uid) if g["source"] == source][0]


@pytest.fixture
def second_user_id():
    import uuid as _uuid
    from db import ensure_user
    uid = int(_uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    return uid


# ──────────────────────────────────────────────────────────────────────────────
# Reparo manual do admin (§15) — casos 49 e 50
# ──────────────────────────────────────────────────────────────────────────────

def test_49_set_account_plan_grava_grant_e_a_projecao_preserva(user_id):
    """Sem o grant, o ajuste do admin sumia na primeira reprojeção."""
    from core.admin_dashboard import set_account_plan

    _conta(user_id, "free", None, "inactive")
    row = set_account_plan("pro_max", months=3, user_id=user_id)
    assert row and row["plan"] == "pro_max"

    g = _grant_row(user_id, "admin")
    assert g["status"] == "active" and g["plan_stored"] == "pro_max"
    assert g["external_ref"] == f"admin:{user_id}"

    assert recompute_entitlement(user_id)["plan"] == "pro_max"
    assert _ler(user_id)["plan"] == "pro_max"


def test_50_set_account_plan_free_revoga_todos_os_grants_ativos(user_id):
    from core.admin_dashboard import set_account_plan

    _conta(user_id, "free", None, "inactive")
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "stripe", f"sub_adm_{user_id}", "pro",
                 agora, agora + timedelta(days=300), 5_000_000)
    set_account_plan("pro", months=6, user_id=user_id)

    set_account_plan("free", user_id=user_id)
    assert all(g["status"] == "revoked" for g in list_grants(user_id))
    assert all(g["revoked_reason"] == "admin_override" for g in list_grants(user_id))
    assert recompute_entitlement(user_id) == {"plan": "free", "plan_expires_at": None}


def test_D3_rebaixamento_do_admin_sobrevive_a_projecao(user_id):
    """A escrita do admin é AUTORITATIVA, e não só ao descer para `free`.

    Revogar apenas no `free` deixava todo REBAIXAMENTO sem efeito: a projeção
    pega o MAIOR tier vigente e a MAIOR cobertura, então um grant `stripe` de
    `pro_max` com 300 dias ressuscitava o plano na primeira reprojeção e o
    ajuste manual sumia — o defeito que este grant existe para consertar.
    """
    from core.admin_dashboard import set_account_plan

    _conta(user_id, "pro_max", datetime.now(timezone.utc) + timedelta(days=300), "active")
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "stripe", f"sub_d3_{user_id}", "pro_max",
                 agora - timedelta(days=1), agora + timedelta(days=300), 6_000_000)

    set_account_plan("essencial", months=1, user_id=user_id)
    assert _grant_row(user_id, "stripe")["status"] == "revoked"

    projetado = recompute_entitlement(user_id)
    assert projetado["plan"] == "essencial", "a projeção desfez o rebaixamento manual"
    assert projetado["plan_expires_at"] < agora + timedelta(days=40)


def test_D5_upsert_de_outro_dono_nao_reescreve_o_grant_alheio(user_id, second_user_id):
    """A unique é `(source, external_ref)` — GLOBAL. Sem o filtro por dono no
    `on conflict … do update`, um upsert do usuário B reescreve plano e validade
    da linha do usuário A (CLAUDE.md §0, bloco permanente)."""
    agora = datetime.now(timezone.utc)
    ref = f"sub_compartilhada_{user_id}"
    upsert_grant(user_id, "stripe", ref, "essencial", agora,
                 agora + timedelta(days=30), 100)

    assert upsert_grant(second_user_id, "stripe", ref, "pro_max", agora,
                        agora + timedelta(days=900), 999_999) is None

    g = _grant_row(user_id)
    assert g["plan_stored"] == "essencial", "grant de A foi reescrito por B"
    assert g["ends_at"] < agora + timedelta(days=40)
    assert list_grants(second_user_id) == []


def test_D7_falha_ao_gravar_o_grant_nao_derruba_o_reparo_do_admin(user_id, monkeypatch):
    """A ferramenta de REPARO manual não pode ser a mais frágil do sistema:
    falha na escrita do grant não pode abortar o UPDATE de `auth_accounts`."""
    import core.admin_dashboard as admin

    _conta(user_id, "free", None, "inactive")

    def _explode(cur, uid, plan, months):
        cur.execute("select 1 from tabela_que_nao_existe")

    monkeypatch.setattr(admin, "_gravar_grant_do_admin", _explode)
    row = admin.set_account_plan("pro", months=2, user_id=user_id)

    assert row is not None and row["plan"] == "pro"
    assert _ler(user_id)["plan"] == "pro", "o reparo do admin foi abortado pelo grant"
