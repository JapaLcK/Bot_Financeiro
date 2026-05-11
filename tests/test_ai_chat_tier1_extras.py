"""
Cobre as 2 tools finais do Tier 1 da E2:
  - delete_launch: apaga lançamento normal (por user_seq) e compra no cartão
    (por id de credit_transaction); parcelamento derruba o grupo inteiro
  - get_card_limit_usage: limite/usado/disponível; tratamento pra limite null
"""
from datetime import date

import db
from core.services.ai_chat.tools.cards import _get_card_limit_usage
from core.services.ai_chat.tools.launches import (
    _delete_launch_execute,
    _delete_launch_summary,
    _delete_launch_validate,
)


# ─── delete_launch ──────────────────────────────────────────────────────────

def test_delete_launch_summary():
    assert _delete_launch_summary({"launch_id": 5}) == "apagar o lançamento #5"
    assert _delete_launch_summary({}) == "apagar lançamento"


def test_delete_launch_lancamento_normal_por_user_seq(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    _, user_seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, None, "compra")
    assert db.get_balance(user_id) == 950

    msg = _delete_launch_execute(user_id, {"launch_id": str(user_seq)})
    assert "apagado" in msg.lower()
    assert db.get_balance(user_id) == 1000  # saldo revertido


def test_delete_launch_compra_credito_por_id(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    tx_id, _, _ = db.add_credit_purchase(user_id, card_id, 50, "outros", "uber", date.today())

    msg = _delete_launch_execute(user_id, {"launch_id": str(tx_id)})
    assert "apagada" in msg.lower() or "apagado" in msg.lower()

    # Confere que tx foi removida
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from credit_transactions where id = %s", (tx_id,))
            assert cur.fetchone() is None


def test_delete_launch_parcelamento_derruba_grupo(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    r = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=300, categoria="outros",
        nota="celular", purchased_at=date.today(), installments=3,
    )
    info = r[0] if isinstance(r, tuple) else r
    tx_ids = info["tx_ids"]

    msg = _delete_launch_execute(user_id, {"launch_id": str(tx_ids[1])})  # apaga a 2ª parcela
    assert "Parcelamento apagado" in msg
    assert "3 parcelas" in msg


def test_delete_launch_por_codigo_pc(user_id):
    """User digita o código PCxxxxxxxx do parcelamento — resolve e apaga
    o grupo inteiro. Era o caso do feedback do Lucas."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    r = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=200, categoria="outros",
        nota="x", purchased_at=date.today(), installments=2,
    )
    info = r[0] if isinstance(r, tuple) else r
    group_id = info["group_id"]
    # Code formato: PC + primeiros 8 chars do uuid hex em UPPERCASE
    group_hex = group_id.replace("-", "").upper()
    pc_code = f"PC{group_hex[:8]}"

    msg = _delete_launch_execute(user_id, {"launch_id": pc_code})
    assert "Parcelamento" in msg and pc_code in msg
    assert "2 parcelas" in msg


def test_delete_launch_validate_aceita_pc_code(user_id):
    """validate retorna None pra PC code que existe — não bloqueia confirmação."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    r = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=200, categoria="outros",
        nota="x", purchased_at=date.today(), installments=2,
    )
    info = r[0] if isinstance(r, tuple) else r
    group_hex = info["group_id"].replace("-", "").upper()
    pc_code = f"PC{group_hex[:8]}"

    assert _delete_launch_validate(user_id, {"launch_id": pc_code}) is None
    # E o número sem PC também (sem o prefixo, só hex)
    assert _delete_launch_validate(user_id, {"launch_id": group_hex[:8]}) is None


def test_delete_launch_id_vazio(user_id):
    msg = _delete_launch_execute(user_id, {"launch_id": ""})
    assert "Faltou" in msg


def test_delete_launch_id_nao_resolve(user_id):
    msg = _delete_launch_execute(user_id, {"launch_id": "abc"})
    assert "Não achei" in msg


def test_delete_launch_nao_existe(user_id):
    msg = _delete_launch_execute(user_id, {"launch_id": "99999999"})
    assert "Não achei" in msg


# ─── delete_launch validate (anti-hallucination de IDs) ────────────────────

def test_delete_launch_validate_id_inexistente_aborta(user_id):
    """ID que LLM inventou (não existe nem como user_seq nem como CT) deve
    falhar a validação ANTES de pedir confirmação enganosa."""
    err = _delete_launch_validate(user_id, {"launch_id": 81524273})
    assert err is not None
    assert "Não achei" in err


def test_delete_launch_validate_id_invalido(user_id):
    err = _delete_launch_validate(user_id, {"launch_id": "abc"})
    assert "Não achei" in err  # abc não é número nem PC code

    err = _delete_launch_validate(user_id, {"launch_id": ""})
    assert "Faltou" in err


def test_delete_launch_validate_user_seq_existe(user_id):
    """user_seq válido de launches passa pela validação."""
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _, user_seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, None, "x")

    assert _delete_launch_validate(user_id, {"launch_id": user_seq}) is None


def test_delete_launch_validate_credit_transaction_existe(user_id):
    """ID de credit_transaction passa pela validação."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    tx_id, _, _ = db.add_credit_purchase(user_id, card_id, 50, "outros", "uber", date.today())

    assert _delete_launch_validate(user_id, {"launch_id": tx_id}) is None


# ─── get_card_limit_usage ───────────────────────────────────────────────────

def test_get_card_limit_usage_com_limite_cadastrado(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.set_card_limit(user_id, card_id, 1000.0)
    db.add_credit_purchase(user_id, card_id, 150, "outros", "compra", date.today())

    result = _get_card_limit_usage(user_id, {})  # usa cartão padrão
    assert result["card_name"] == "Nubank"
    assert result["credit_limit"] == 1000.0
    assert result["used"] == 150.0
    assert result["available"] == 850.0
    assert result["used_pct"] == 15.0


def test_get_card_limit_usage_sem_limite_cadastrado(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 80, "outros", "x", date.today())

    result = _get_card_limit_usage(user_id, {})
    assert result["credit_limit"] is None
    assert result["used"] == 80.0
    assert result["available"] is None
    assert "Limite não cadastrado" in result["note"]


def test_get_card_limit_usage_por_nome(pro_user_id):
    """Pro pra criar 2 cartões e checar que respeita o card_name."""
    a_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    b_id = db.create_card(pro_user_id, "Inter", closing_day=15, due_day=22)
    db.set_default_card(pro_user_id, a_id)
    db.set_card_limit(pro_user_id, b_id, 500.0)
    db.add_credit_purchase(pro_user_id, b_id, 100, "outros", "x", date.today())

    result = _get_card_limit_usage(pro_user_id, {"card_name": "Inter"})
    assert result["card_name"] == "Inter"
    assert result["used"] == 100.0
    assert result["available"] == 400.0


def test_get_card_limit_usage_sem_cartao(user_id):
    result = _get_card_limit_usage(user_id, {})
    assert "error" in result
