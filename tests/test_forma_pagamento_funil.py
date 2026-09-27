"""Q40 — o funil, as tools da IA e o painel.

B. O funil: `add_from_entities` exige a forma declarada e recusa gravar o que
   passou pelo banco (com Open Finance). Um teste `ast` prende quem pode chamar
   `db.bills.mark_bill_paid` e `add_launch_and_update_balance` — chamador novo
   reprova, no mesmo desenho de `tests/test_pending_registry.py`.
D. As tools `add_launch` e `mark_bill_paid` só DECLARAM a forma.
E. A rota `POST /launches` e o campo `exige_forma_pagamento` do painel.
"""
from __future__ import annotations

import ast
import asyncio
import collections
import pathlib

import pytest

import db
from conftest import usuario_pagante
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank, _dashboard_client


def _q(sql, params):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
    return row


def manuais(uid) -> int:
    return _q("select count(*) n from launches where user_id=%s "
              "and coalesce(source,'manual') <> 'open_finance'", (uid,))["n"]


@pytest.fixture
def com_of():
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    return uid


@pytest.fixture
def sem_of():
    return usuario_pagante()


# ── B. funil ─────────────────────────────────────────────────────────────────

def test_add_from_entities_sem_forma_e_type_error(sem_of):
    from core.handlers.launches import add_from_entities
    with pytest.raises(TypeError):
        add_from_entities(sem_of, tipo="despesa", valor=50, alvo="mercado")  # noqa
    assert manuais(sem_of) == 0


@pytest.mark.parametrize("forma", ["banco", "desconhecida", "misto"])
def test_add_from_entities_com_of_so_grava_dinheiro(com_of, forma):
    from core.handlers.launches import add_from_entities
    with pytest.raises(ValueError, match="FORMA_PAGAMENTO_NAO_GRAVA"):
        add_from_entities(com_of, tipo="despesa", valor=50, alvo="mercado",
                          forma_pagamento=forma)
    assert manuais(com_of) == 0


def test_add_from_entities_com_of_dinheiro_grava(com_of):
    from core.handlers.launches import add_from_entities
    add_from_entities(com_of, tipo="despesa", valor=50, alvo="mercado",
                      forma_pagamento="dinheiro")
    assert manuais(com_of) == 1


def test_add_from_entities_sem_of_banco_grava_como_antes(sem_of):
    from core.handlers.launches import add_from_entities
    add_from_entities(sem_of, tipo="despesa", valor=50, alvo="mercado",
                      forma_pagamento="banco")
    assert manuais(sem_of) == 1


def test_forma_fora_do_conjunto_e_recusada(sem_of):
    from core.handlers.launches import add_from_entities
    with pytest.raises(ValueError, match="FORMA_PAGAMENTO_INVALIDA"):
        add_from_entities(sem_of, tipo="despesa", valor=50, alvo="mercado",
                          forma_pagamento="pix")
    assert manuais(sem_of) == 0


_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_IGNORADOS = {".venv", ".claude", ".git", "tests", "harness_tests", "node_modules"}

# Referência (chamada ou passada ao `asyncio.to_thread`) por arquivo de
# PRODUÇÃO. É um ratchet: referência nova em qualquer arquivo reprova. Os
# chamadores de `add_launch_and_update_balance` fora da Q40 (fatura,
# saldo inicial e ajuste, o seed do harness) ficam de fora por decisão
# do dono (plano, seção 1).
_PERMITIDOS = {
    "mark_bill_paid": {"core/handlers/forma_pagamento.py": 1},
    "add_launch_and_update_balance": {
        "core/handlers/launches.py": 1,          # add_from_entities (com a guarda)
        "core/services/quick_entry.py": 1,       # entrada rápida (com a regra)
        "frontend/finance_bot_websocket_custom.py": 3,  # POST /launches + saldo inicial/ajuste
        "db/bills.py": 1,                        # mark_bill_paid(metodo="carteira")
        "db/cards.py": 3,                        # fatura, antecipação, estorno
        "scripts/whatsapp_qa_vault_harness.py": 1,
    },
}


def _referencias() -> dict[str, collections.Counter]:
    achados = {nome: collections.Counter() for nome in _PERMITIDOS}
    for caminho in _RAIZ.rglob("*.py"):
        rel = caminho.relative_to(_RAIZ)
        if set(rel.parts) & _IGNORADOS:
            continue
        for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
            nome = (no.id if isinstance(no, ast.Name)
                    else no.attr if isinstance(no, ast.Attribute) else None)
            if nome in achados:
                achados[nome][rel.as_posix()] += 1
    return achados


def test_so_os_chamadores_conhecidos_escrevem_na_carteira():
    achados = _referencias()
    for nome, permitido in _PERMITIDOS.items():
        assert dict(achados[nome]) == permitido, (nome, dict(achados[nome]))


# ── D. tools ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("forma,lancamentos,trecho", [
    (None, 0, "Pergunte ao usuário"),
    ("banco", 0, "Open Finance"),
    ("dinheiro", 1, "registrada"),
])
def test_tool_add_launch_com_of(com_of, forma, lancamentos, trecho):
    from core.services.ai_chat.tools.launches import _add_launch_execute
    args = {"tipo": "despesa", "valor": 50, "alvo": "mercado"}
    if forma:
        args["forma_pagamento"] = forma
    r = _add_launch_execute(com_of, args)
    assert trecho in r, r
    assert manuais(com_of) == lancamentos


def test_tool_add_launch_forma_inventada_nao_grava(com_of):
    from core.services.ai_chat.tools.launches import _add_launch_execute
    r = _add_launch_execute(com_of, {"tipo": "despesa", "valor": 50, "forma_pagamento": "cash"})
    assert "Pergunte ao usuário" in r, r
    assert manuais(com_of) == 0


def test_tool_add_launch_sem_of_grava_como_antes(sem_of):
    from core.services.ai_chat.tools.launches import _add_launch_execute
    r = _add_launch_execute(sem_of, {"tipo": "despesa", "valor": 50, "alvo": "mercado"})
    assert "registrada" in r, r
    assert manuais(sem_of) == 1


# ── E. painel ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("conectado,funding,status,lancamentos", [
    (True, None, 400, 0),
    (True, "carteira", 200, 1),
    (False, None, 200, 1),
    (False, "carteira", 200, 1),
])
def test_post_launches(conectado, funding, status, lancamentos):
    uid = usuario_pagante()
    if conectado:
        _connect_fake_bank(uid)
    client, headers = _dashboard_client(uid, f"q40-{uid}@t.com")
    corpo = {"tipo": "despesa", "valor": 50, "alvo": "mercado"}
    if funding:
        corpo["funding_source"] = funding
    r = client.post(f"/launches/{uid}", json=corpo, headers=headers)
    assert r.status_code == status, r.text
    if status == 400:
        assert "dinheiro vivo" in r.text and "Open Finance" in r.text
    assert manuais(uid) == lancamentos


def test_payload_do_painel_diz_se_exige_forma(com_of, sem_of):
    import frontend.finance_bot_websocket_custom as mono
    assert asyncio.run(mono.get_financial_data(com_of))["exige_forma_pagamento"] is True
    assert asyncio.run(mono.get_financial_data(sem_of))["exige_forma_pagamento"] is False


# ── POST /recurring-bills/.../pay: corpo fora do contrato nunca paga ─────────

@pytest.mark.parametrize("conectado", [True, False])
@pytest.mark.parametrize("metodo", ["pix", "carteira", "misto", "desconhecida", ""])
def test_rota_pay_metodo_fora_do_enum_e_400_e_nao_paga(conectado, metodo):
    from tests.test_conta_paga_forma import B, _cliente, conta_fixa
    uid = usuario_pagante()
    if conectado:
        _connect_fake_bank(uid)
    conta = conta_fixa(uid)
    client, headers = _cliente(uid)
    r = client.post(f"/recurring-bills/{uid}/{conta['id']}/pay",
                    json={"metodo": metodo, "amount": 120}, headers=headers)
    assert r.status_code == 400, r.text
    assert "Diga como pagou" in r.text
    assert B.get_bill(uid, conta["id"])["status"] == "pending"
    assert manuais(uid) == 0


@pytest.mark.parametrize("metodo", [123, 1.5, True, ["dinheiro"], {"a": 1},
                                    "Dinheiro", "DINHEIRO", " banco", "banco "])
def test_rota_pay_metodo_de_outro_tipo_ou_grafia_nunca_paga(com_of, metodo):
    """Tipo errado sai 422 do pydantic; grafia diferente, 400. Nenhum paga."""
    from tests.test_conta_paga_forma import B, _cliente, conta_fixa
    conta = conta_fixa(com_of)
    client, headers = _cliente(com_of)
    r = client.post(f"/recurring-bills/{com_of}/{conta['id']}/pay",
                    json={"metodo": metodo, "amount": 120}, headers=headers)
    assert r.status_code in (400, 422), r.text
    assert B.get_bill(com_of, conta["id"])["status"] == "pending"
    assert manuais(com_of) == 0


def test_rota_pay_amount_infinito_nao_paga(com_of):
    """JSON não tem Infinity, mas o parser do Python aceita o literal."""
    from tests.test_conta_paga_forma import B, _cliente, conta_fixa
    conta = conta_fixa(com_of)
    client, headers = _cliente(com_of)
    r = client.post(f"/recurring-bills/{com_of}/{conta['id']}/pay",
                    content=b'{"amount": Infinity, "metodo": "dinheiro"}',
                    headers={**headers, "content-type": "application/json"})
    assert r.status_code >= 400, r.text
    assert B.get_bill(com_of, conta["id"])["status"] == "pending"
    assert manuais(com_of) == 0
