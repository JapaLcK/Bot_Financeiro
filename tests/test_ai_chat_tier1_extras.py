"""
Cobre as 2 tools finais do Tier 1 da E2:
  - delete_launch: apaga lançamento normal (`#N` = user_seq ou `l<id>`) pelo `pode` do Extrato;
    compra no cartão, parcelamento manual, guardado e lançamento antigo são RECUSADOS antes de
    armar a confirmação (P5-A: a IA segue o mesmo `pode` do Extrato; a tabela completa está em
    `tests/test_ai_chat_extrato_ia.py`)
  - get_card_limit_usage: limite/usado/disponível; tratamento pra limite null
"""
from datetime import date

import db
from core.services.ai_chat.tools.cards import _get_card_limit_usage
from core.services.ai_chat.tools.extrato_ia import (
    ao_confirmar,
    apagar,
    resumo_apagar,
    validar_apagar,
)


def _canonico(uid, bruto):
    return ao_confirmar(uid, {"launch_id": bruto})


# ─── delete_launch ──────────────────────────────────────────────────────────

def test_delete_launch_summary():
    item = {"descricao": "Mercado", "valor": 50.0, "data": "2026-10-17", "parcelas": 1}
    assert resumo_apagar({"_item": item}) == 'apagar "Mercado", R$ 50,00, 17/10'
    assert resumo_apagar({}) == "apagar o lançamento"


def test_delete_launch_lancamento_normal_por_user_seq(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    _, user_seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, None, "compra")
    assert db.get_balance(user_id) == 950

    msg = apagar(user_id, _canonico(user_id, str(user_seq)))
    assert "apagado" in msg.lower() and '"compra", R$ 50,00' in msg
    assert db.get_balance(user_id) == 1000  # saldo revertido


def test_delete_launch_compra_manual_no_credito_e_recusada_sem_apagar(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    tx_id, _, _ = db.add_credit_purchase(user_id, card_id, 50, "outros", "uber", date.today())

    for bruto in (f"c{tx_id}", f"CC{tx_id}"):
        err = validar_apagar(user_id, {"launch_id": bruto})
        assert err and "só de leitura" in err, err
    assert "mudou" in apagar(user_id, {"launch_id": f"c{tx_id}"})  # nem forjando a pendência
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from credit_transactions where id = %s", (tx_id,))
        assert cur.fetchone() is not None


def _parcelamento(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    r = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=300, categoria="outros",
        nota="celular", purchased_at=date.today(), installments=3,
    )
    return r[0] if isinstance(r, tuple) else r


def test_delete_launch_parcelamento_manual_e_recusado_pelo_codigo_pc_e_pela_parcela(user_id):
    info = _parcelamento(user_id)
    pc_code = f"PC{info['group_id'].replace('-', '').upper()[:8]}"

    for bruto in (pc_code, pc_code.lower(), f"c{info['tx_ids'][1]}"):
        err = validar_apagar(user_id, {"launch_id": bruto})
        assert err and "só de leitura" in err, (bruto, err)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from credit_transactions where user_id = %s", (user_id,))
        assert cur.fetchone()["n"] == 3


def test_delete_launch_id_vazio(user_id):
    assert "Faltou" in validar_apagar(user_id, {"launch_id": ""})


def test_delete_launch_id_nao_resolve(user_id):
    assert "Não achei" in validar_apagar(user_id, {"launch_id": "abc"})
    assert "Não achei" in validar_apagar(user_id, {"launch_id": "99999999"})
    assert "Não achei" in validar_apagar(user_id, {"launch_id": 81524273})  # o que o LLM inventou


def test_delete_launch_validate_user_seq_existe(user_id):
    """user_seq válido de launches passa pela validação."""
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _, user_seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, None, "x")

    assert validar_apagar(user_id, {"launch_id": user_seq}) is None
    assert validar_apagar(user_id, {"launch_id": f"#{user_seq}"}) is None


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


def test_delete_launch_sem_efeitos_nao_promete_retry_no_ai_chat(user_id):
    """Porta `/ai/chat`, mesma ação do WhatsApp: lançamento sem `efeitos` é
    condição PERMANENTE (`LaunchNoEffects` de `db/accounts.py`), não erro técnico.

    O `_ERRO_APAGAR` genérico manda "tenta de novo em alguns minutos" — que
    aqui nunca vai funcionar. É o mesmo defeito do `resolve_delete`, e a mesma
    distinção que o `delete_all_launches` já faz (`kept_no_effects` × `errors`).

    Controle negativo (medido): tirando o `LaunchNoEffects` da tabela `_RECUSAS` de
    `extrato_ia.apagar` este teste falha; o `test_delete_launch_lancamento_normal_por_user_seq`
    (caminho legítimo) continua verde.
    """
    _, user_seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, None, "compra")
    internal = db.resolve_user_seq_to_id(user_id, user_seq)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update launches set efeitos=null where id=%s", (internal,))
        conn.commit()

    msg = apagar(user_id, _canonico(user_id, str(user_seq)))

    assert "de novo" not in msg.lower(), f"condição permanente prometendo retry: {msg!r}"
    assert "antigo" in msg.lower() and '"compra"' in msg, msg
    assert db.resolve_user_seq_to_id(user_id, user_seq) == internal, "não podia apagar"


def test_delete_launch_aporte_e_recusado_e_o_aporte_fica(user_id):
    """Aporte/depósito de caixinha é guardado: `pode = []` (P1 do dono). Antes a IA o apagava e
    explicava o resgate que travava; agora recusa antes de armar e o aporte fica."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment_db(user_id, "CDB Lote", rate=0.01, period="monthly", nota="t")
    aporte_id, *_ = db.investment_deposit_from_account(user_id, "CDB Lote", 200, "aporte")

    msg = validar_apagar(user_id, {"launch_id": f"l{aporte_id}"})

    assert msg and "só de leitura" in msg, msg
    assert db.resolve_user_seq_to_id(user_id, db.get_launch_user_seq(user_id, int(aporte_id))) == int(aporte_id)
