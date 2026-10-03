"""Tabela de estados × `pode`/`origem`/`motivos` da `GET /api/v2/lancamentos` (`db/lancamentos.PODE_SQL`).

Um caso por linha da tabela do dono (P2, P3, P5): a linha antiga (sem a marca
`launches.origem`) é só leitura; a marcada é a que o escritor da carteira grava (PR 2a), e a
antiga é a mesma com a marca apagada aqui. Controle POSITIVO: a carteira pura marcada pode
tudo. NEGATIVO: a mesma linha sem a marca não pode nada.
"""
from __future__ import annotations

from datetime import date

from psycopg.types.json import Jsonb

import db
from conftest import usuario_pagante
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_lancamentos import ok
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)

TUDO = ["categoria", "descricao", "data", "valor", "apagar"]


def _carteira(uid, marcada=True, valor=30, **extra) -> int:
    lid = db.add_launch_and_update_balance(uid, "despesa", valor, "mercado", "msg", extra_efeitos=extra or None)[0]
    if not marcada:  # o escritor marca; a linha anterior ao PR 2a não tem a marca
        q("update launches set origem = null where id = %s returning id", (lid,))
    return lid


def _sombra(uid, valor=30) -> int:
    return q("""insert into launches (user_id, tipo, valor, source, external_id, efeitos)
                values (%s, 'despesa', %s, 'open_finance', %s, %s) returning id""",
             (uid, valor, f"ext-{valor}-{uid}", Jsonb({"delta_conta": 0})))["id"]


def _itens(uid) -> dict:
    return {i["id"]: i for i in ok(uid)["itens"]}


def test_tabela_de_estados(libera):
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-pode-{a}"), "acc-pode", "100")
    pura, antiga = _carteira(a), _carteira(a, marcada=False)
    pendente, sombra_p = _carteira(a, valor=31), _sombra(a, 31)
    tx_banco(acc, "tx-pend", "-31", imported_launch_id=sombra_p, match_launch_id=pendente,
             reconciliation_status="pending")
    fundida = _carteira(a, valor=32)
    tx_banco(acc, "tx-fund", "-32", imported_launch_id=fundida, reconciliation_status="auto_merged")
    especie, especie_antiga = _carteira(a, valor=33), _carteira(a, marcada=False, valor=34)
    for i, lid in enumerate((especie, especie_antiga)):
        q("""insert into of_cash_links (user_id, tx_key, key_durable, account_key, kind, status,
                 launch_id, amount, tx_date) values (%s, %s, true, 'k', 'saque', 'ativo', %s, 1, %s)
             returning id""", (a, f"k{i}", lid, date.today()))
    paga = _carteira(a, valor=35, bill_id=1, paid_amount_added=35)
    delta_zero = db.add_launch_and_update_balance(a, "despesa", 36, "x", None, apply_delta=False)[0]  # marcada
    ofx = q("""insert into launches (user_id, tipo, valor, source, external_id, efeitos)
               values (%s, 'despesa', 37, 'ofx', 'ofx-1', %s) returning id""", (a, Jsonb({"delta_conta": -37})))["id"]
    sombra = _sombra(a, 38)
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    manual_ct = db.add_credit_purchase(a, cartao, 39, "mercado", "manual", date.today())[0]
    of_ct = db.add_imported_credit_purchase(a, cartao, -40, "mercado", date.today(), "ct-ext-1")[0]
    libera(a)

    it = _itens(a)
    casos = {  # id: (origem, pode, motivos, fundido)
        f"l{pura}": ("carteira", TUDO, [], False),  # positivo: a pura marcada pode tudo
        f"l{antiga}": ("registro_antigo", [], [], False),  # negativo: sem a marca, nada (P2)
        f"l{pendente}": ("carteira", ["categoria", "descricao", "apagar"], ["conciliacao_pendente"], False),
        f"l{sombra_p}": ("banco", ["categoria", "descricao"], ["conciliacao_pendente"], False),
        f"l{fundida}": ("carteira", ["categoria", "descricao", "apagar"], [], True),  # P3
        f"l{especie}": ("carteira", ["descricao", "apagar"], [], False),
        f"l{especie_antiga}": ("registro_antigo", [], [], False),
        f"l{paga}": ("carteira", ["categoria", "data"], [], False),  # sem apagar (dono, 2026-10-03)
        f"l{delta_zero}": ("registro_antigo", [], [], False),  # manual com delta 0 não é carteira
        f"l{ofx}": ("registro_antigo", [], [], False),
        f"l{sombra}": ("banco", ["categoria", "descricao"], [], False),  # P5
        f"c{manual_ct}": ("registro_antigo", [], [], False),
        f"c{of_ct}": ("cartao", ["categoria", "descricao"], [], False),  # P5
    }
    for ident, (origem, pode, motivos, fundido) in casos.items():
        i = it[ident]
        assert (i["origem"], i["pode"], i["motivos"], i["fundido"]) == (origem, pode, motivos, fundido), (ident, i)
    assert it[f"l{fundida}"]["instituicao"] == "Banco" and it[f"l{fundida}"]["conta_id"] == acc


def test_transacao_pendente_e_moeda_pela_conta(libera):
    a = usuario_pagante()
    usd = conta(conexao(a, f"item-usd-{a}"), "acc-usd", "10", moeda="USD", code=None)
    lid = _sombra(a, 50)
    tx_banco(usd, "tx-usd", "-50", imported_launch_id=lid, raw=Jsonb({"status": "PENDING"}))
    libera(a)
    i = _itens(a)[f"l{lid}"]
    assert (i["moeda"], i["motivos"]) == ("USD", ["transacao_pendente", "outra_moeda", "moeda_presumida"])


def test_pode_da_linha_isolado_por_usuario(libera):
    """`pode_da_linha` é API de `db/`: filtra pelo dono sozinha, sem contar com o `for update`
    do chamador. Negativo: a linha de A lida por B é None. Positivo: lida por A, é o `pode`."""
    from db.lancamentos import pode_da_linha
    a, b = usuario_pagante(), usuario_pagante()
    lid = _carteira(a)
    with db.get_conn() as conn, conn.cursor() as cur:
        assert pode_da_linha(cur, b, lid) is None
        assert pode_da_linha(cur, a, lid) == TUDO
