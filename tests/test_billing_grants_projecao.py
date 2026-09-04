"""
tests/test_billing_grants_projecao.py — projeção de `plan_grants` (§4 e §6 do
docs/plano_pix_anual_asaas.md), ordem de eventos e reparo manual do admin.

Casos 8, 9, 10, 12, 12b, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 49 e 50 do §16.

Os casos 20 e 20b (antecipação no cancelamento manual) NÃO estão aqui: eles
dependem de `pix_charges.stripe_subscription_id`, e `pix_charges` é do PR 1b.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.services import billing_access
from core.services.billing_access import (
    projetar_grants,
    recompute_entitlement,
    reprojetar_grants_recentes,
)
from db.connection import get_conn
from db.plan_grants import list_grants, revoke_grant, upsert_grant

AGORA = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _grant(plan_stored, starts, ends, *, source="stripe", status="active"):
    """Linha de grant como o SELECT devolve — a projeção é pura, não vê banco."""
    return {
        "source": source,
        "status": status,
        "plan_stored": plan_stored,
        "starts_at": AGORA + starts,
        "ends_at": AGORA + ends,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Projeção pura (§4.1) — casos 16 a 22
# ──────────────────────────────────────────────────────────────────────────────

def test_16_grant_futuro_sem_vigente_da_free():
    """Acesso que ainda não começou não é acesso: só grant FUTURO → free."""
    grants = [_grant("pro", timedelta(days=10), timedelta(days=375))]
    assert projetar_grants(grants, AGORA) == ("free", None)


def test_17_buraco_no_meio_corta_a_cobertura():
    """O primeiro buraco encerra a cobertura — o grant depois dele não conta."""
    grants = [
        _grant("pro", timedelta(days=-10), timedelta(days=20)),
        _grant("pro", timedelta(days=90), timedelta(days=200), source="admin"),
    ]
    plano, expira = projetar_grants(grants, AGORA)
    assert plano == "pro"
    assert expira == AGORA + timedelta(days=20)


def test_18_positivo_renovacao_contigua_e_ininterrupta():
    """Renovação encostada: a cobertura atravessa os dois grants."""
    grants = [
        _grant("pro", timedelta(days=-30), timedelta(days=1)),
        _grant("pro", timedelta(days=1), timedelta(days=31), source="admin"),
    ]
    assert projetar_grants(grants, AGORA) == ("pro", AGORA + timedelta(days=31))


def test_19_positivo_downgrade_agendado_vira_tier_na_data():
    """Data do futuro, tier do vigente — e o tier vira quando o maior expira."""
    grants = [
        _grant("pro_max", timedelta(days=-30), timedelta(days=10)),
        _grant("essencial", timedelta(days=10), timedelta(days=375), source="admin"),
    ]
    plano, expira = projetar_grants(grants, AGORA)
    assert plano == "pro_max"                      # tier do vigente AGORA
    assert expira == AGORA + timedelta(days=375)   # data do encadeamento

    depois = AGORA + timedelta(days=20)
    assert projetar_grants(grants, depois) == ("essencial", AGORA + timedelta(days=375))


def test_21_positivo_tolerancia_de_120s_emenda_1s_e_nao_emenda_10min(monkeypatch):
    """1 s de folga emenda; 10 min é buraco. Com a tolerância ZERADA, o de 1 s
    fica vermelho — é o controle NEGATIVO do grupo, rodando de verdade."""
    def _par(folga):
        return [
            _grant("pro", timedelta(days=-30), timedelta(days=1)),
            _grant("pro", timedelta(days=1) + folga, timedelta(days=31), source="admin"),
        ]

    assert projetar_grants(_par(timedelta(seconds=1)), AGORA)[1] == AGORA + timedelta(days=31)
    assert projetar_grants(_par(timedelta(minutes=10)), AGORA)[1] == AGORA + timedelta(days=1)

    # NEGATIVO: sem a tolerância, o encadeamento de 1 s deixa de emendar.
    monkeypatch.setattr(billing_access, "GAP_TOLERANCIA", timedelta(0))
    assert projetar_grants(_par(timedelta(seconds=1)), AGORA)[1] == AGORA + timedelta(days=1)


def test_22_positivo_sobreposicao_pega_maior_data_e_maior_tier():
    grants = [
        _grant("essencial", timedelta(days=-10), timedelta(days=100)),
        _grant("pro_max", timedelta(days=-5), timedelta(days=40), source="admin"),
    ]
    assert projetar_grants(grants, AGORA) == ("pro_max", AGORA + timedelta(days=100))


def test_projecao_ignora_grant_revogado_e_vencido():
    """Os dois filtros do §4.1 numa asserção só: revogado e vencido não cobrem."""
    grants = [
        _grant("pro", timedelta(days=-10), timedelta(days=100), status="revoked"),
        _grant("pro", timedelta(days=-100), timedelta(days=-1)),
    ]
    assert projetar_grants(grants, AGORA) == ("free", None)


# ──────────────────────────────────────────────────────────────────────────────
# recompute_entitlement contra o banco — casos 8, 9, 10, 23
# ──────────────────────────────────────────────────────────────────────────────

def _conta(uid: int, plan: str, expires, status: str = "inactive") -> None:
    """Cria/atualiza a auth_accounts do usuário da fixture.

    UPDATE e só então INSERT: `auth_accounts` não tem unique em `user_id`
    (só em email/phone), então `on conflict (user_id)` não existe.
    """
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
                    (uid, f"grants-{uid}@t.local", plan, expires, status),
                )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _ler(uid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status"
                "  from auth_accounts where user_id = %s", (uid,))
            return dict(cur.fetchone())


def test_08_conta_paga_sem_nenhum_grant_nao_escreve_e_alerta(user_id, monkeypatch):
    """A guarda que torna o PR reversível: ausência de grant é DESCONHECIMENTO.
    Se a projeção escrevesse 'free' aqui, o revert do PR encontraria a base
    pagante já rebaixada."""
    expira = datetime.now(timezone.utc) + timedelta(days=200)
    _conta(user_id, "pro", expira, "active")

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda msg: alertas.append(msg) or True)

    assert recompute_entitlement(user_id) is None
    row = _ler(user_id)
    assert row["plan"] == "pro"
    assert row["plan_expires_at"] == expira
    assert len(alertas) == 1, "conta paga sem grant tem de alertar"


def test_09_grants_todos_vencidos_rebaixam_para_free(user_id):
    """A guarda do 8 não pode virar 'nunca rebaixa': com grants, há informação."""
    _conta(user_id, "pro", datetime.now(timezone.utc) + timedelta(days=200), "active")
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "stripe", f"sub_venc_{user_id}", "pro",
                 agora - timedelta(days=400), agora - timedelta(days=1), 1000)

    assert recompute_entitlement(user_id) == {"plan": "free", "plan_expires_at": None}
    row = _ler(user_id)
    assert row["plan"] == "free" and row["plan_expires_at"] is None


def test_10_vitalicio_sai_antes_de_qualquer_escrita(user_id):
    """Duas saídas antecipadas: `grandfathered` e pago com validade NULL."""
    _conta(user_id, "pro", None, "grandfathered")
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro"

    # vitalício DE FATO (sem o status): validade NULL não é "venceu"
    _conta(user_id, "pro_max", None, "active")
    assert recompute_entitlement(user_id) is None
    assert _ler(user_id)["plan"] == "pro_max"


def test_23_grant_que_comecou_ha_90s_entra_na_passada_de_60s(user_id):
    """§4.3 — grant futuro que vira vigente é transição sem evento externo
    nenhum; sem o loop, o acesso só apareceria no próximo webhook."""
    _conta(user_id, "free", None, "inactive")
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "admin", f"admin:{user_id}", "pro",
                 agora - timedelta(seconds=90), agora + timedelta(days=365), 1)

    assert _ler(user_id)["plan"] == "free"          # ainda não projetado
    n = reprojetar_grants_recentes(agora - timedelta(seconds=120))
    assert n >= 1
    assert _ler(user_id)["plan"] == "pro"

    # Janela que não alcança o grant não reprojeta ninguém deste usuário.
    _conta(user_id, "free", None, "inactive")
    reprojetar_grants_recentes(agora - timedelta(seconds=30))
    assert _ler(user_id)["plan"] == "free"


# ──────────────────────────────────────────────────────────────────────────────
# Ordem de eventos (§6) — casos 12, 12b, 13, 14, 15
# ──────────────────────────────────────────────────────────────────────────────

def _grant_row(uid: int, source: str = "stripe") -> dict:
    return [g for g in list_grants(uid) if g["source"] == source][0]


def test_12_evento_antigo_nao_desfaz_revogacao(user_id):
    """`deleted` em T e `invoice.paid` em T-100 → o grant continua revogado."""
    _conta(user_id, "pro", datetime.now(timezone.utc) + timedelta(days=30), "active")
    agora = datetime.now(timezone.utc)
    sub = f"sub_ord_{user_id}"
    upsert_grant(user_id, "stripe", sub, "pro", agora - timedelta(days=1),
                 agora + timedelta(days=30), 1_000_000)
    assert revoke_grant(user_id, "stripe", sub, "deleted", 1_000_100) is True

    # invoice.paid ATRASADO, 100 s mais velho que a revogação
    assert upsert_grant(user_id, "stripe", sub, "pro", agora,
                        agora + timedelta(days=30), 1_000_000) is None
    assert _grant_row(user_id)["status"] == "revoked"

    recompute_entitlement(user_id)
    assert _ler(user_id)["plan"] == "free"


@pytest.mark.parametrize("ordem", ["paid_primeiro", "deleted_primeiro"])
def test_12b_empate_de_segundo_termina_igual_nas_duas_ordens(user_id, ordem):
    """§6.1 — `event["created"]` tem precisão de SEGUNDOS, então `invoice.paid` e
    `subscription.deleted` empatam. No empate a REVOGAÇÃO ganha, e o estado
    final é o mesmo nas duas ordens de chegada.

    O grant já existe (renovação): é aí que o empate morde. Se o `deleted`
    chegasse antes de QUALQUER grant existir, não haveria linha para revogar —
    e nesse caso as duas ordens já terminavam iguais antes deste PR, porque o
    `update_user_plan` do ramo pago era o último a escrever.
    """
    _conta(user_id, "pro", datetime.now(timezone.utc) + timedelta(days=30), "active")
    agora = datetime.now(timezone.utc)
    sub = f"sub_tie_{user_id}"
    T = 1_700_000_000

    upsert_grant(user_id, "stripe", sub, "pro", agora - timedelta(days=30),
                 agora + timedelta(days=1), T - 10, "evt_anterior")

    def paid():
        upsert_grant(user_id, "stripe", sub, "pro", agora,
                     agora + timedelta(days=30), T, "evt_paid")

    def deleted():
        revoke_grant(user_id, "stripe", sub, "stripe_subscription_deleted", T, "evt_deleted")

    if ordem == "paid_primeiro":
        paid(); deleted()
    else:
        deleted(); paid()

    g = _grant_row(user_id)
    assert g["status"] == "revoked", f"ordem {ordem} não convergiu para revogado"
    assert g["last_event_id"] == "evt_deleted", "o desempate tem de ser observável"
    recompute_entitlement(user_id)
    assert _ler(user_id)["plan"] == "free"


def test_13_paid_antigo_nao_estica_ends_at_nem_restaura_tier(user_id):
    _conta(user_id, "pro_max", datetime.now(timezone.utc) + timedelta(days=30), "active")
    agora = datetime.now(timezone.utc)
    sub = f"sub_velho_{user_id}"
    fim = agora + timedelta(days=30)
    upsert_grant(user_id, "stripe", sub, "pro_max", agora, fim, 2_000_000)

    # evento mais velho, com plano MENOR e data MAIOR: não pode aplicar nada
    assert upsert_grant(user_id, "stripe", sub, "essencial", agora,
                        agora + timedelta(days=900), 1_999_000) is None
    g = _grant_row(user_id)
    assert g["plan_stored"] == "pro_max"
    assert abs((g["ends_at"] - fim).total_seconds()) < 1


def test_14_paid_antigo_bloqueado_nao_supersede_o_legacy(user_id):
    """A supersessão do `legacy` é consequência de o upsert TER APLICADO."""
    _conta(user_id, "pro", datetime.now(timezone.utc) + timedelta(days=300), "active")
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro",
                 agora, agora + timedelta(days=300), 0)
    sub = f"sub_sup_{user_id}"
    upsert_grant(user_id, "stripe", sub, "pro", agora, agora + timedelta(days=30), 3_000_000)
    assert _grant_row(user_id, "legacy")["status"] == "revoked"

    # Ressuscita o legacy e manda um evento VELHO: ele não pode supersedê-lo.
    upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro",
                 agora, agora + timedelta(days=300), 3_000_001)
    assert _grant_row(user_id, "legacy")["status"] == "active"
    assert upsert_grant(user_id, "stripe", sub, "pro", agora,
                        agora + timedelta(days=30), 2_999_000) is None
    assert _grant_row(user_id, "legacy")["status"] == "active"


def test_15_evento_mais_novo_legitimo_aplica(user_id):
    """Reativação: versão maior aplica, inclusive ressuscitando grant revogado."""
    _conta(user_id, "free", None, "canceled")
    agora = datetime.now(timezone.utc)
    sub = f"sub_react_{user_id}"
    upsert_grant(user_id, "stripe", sub, "pro", agora, agora + timedelta(days=30), 4_000_000)
    revoke_grant(user_id, "stripe", sub, "deleted", 4_000_100)

    assert upsert_grant(user_id, "stripe", sub, "pro_max", agora,
                        agora + timedelta(days=60), 4_000_200) is not None
    g = _grant_row(user_id)
    assert g["status"] == "active" and g["plan_stored"] == "pro_max"
    assert recompute_entitlement(user_id)["plan"] == "pro_max"


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
