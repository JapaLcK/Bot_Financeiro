"""A fusão com o Open Finance para de contar o mesmo real duas vezes.

O DEFEITO: o dono lança "gastei 1 real" à mão (Carteira -1) e o Pix chega
depois pelo sync (o espelho autoritativo do banco cai 1 real também). A fusão
vincula a transação ao lançamento manual e o `delta_conta` continua vivo —
o MESMO real saía duas vezes do consolidado, para sempre.

O conserto é na LEITURA: nada é escrito, `accounts.balance` continua -1,00 e
`delta_conta` continua -1. `MERGED_WALLET_DELTA_SQL` soma o delta de volta na
Carteira enquanto a conta do banco estiver no recorte de `BANK_ACCOUNTS_SQL` —
por isso os asserts sobre `delta_conta` aqui afirmam `-1`, não `0`: é a
afirmação central da abordagem ("não escrevemos nada").

A MEDIÇÃO É O CONSOLIDADO: é o número que o dono vê no dashboard e no /saldo.
Sem o conserto o caso 1 fecha em 112,88 em vez de 113,88.

Controle negativo (CLAUDE.md §3): `MERGED_WALLET_DELTA_SQL` devolvendo 0 tem de
deixar vermelhos os casos dos três caminhos de fusão. Controle positivo: o
lançamento que NÃO funde continua debitando a Carteira e devolvendo o dinheiro
no delete — sem ele, um conserto que zerasse TUDO passaria.
"""
from __future__ import annotations

from decimal import Decimal

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, delta_conta, ia_fora, manda, saldo_bruto,
    sincroniza, soma_delta_conta, tx, uid_pro, ultimo_launch,
)


# ── caminho 1: import_open_finance_launches (verdict "auto") ────────────────

def test_caminho_1_import_devolve_o_debito(uid_pro, ia_fora):
    """Ponta a ponta, com a frase do relato: maiúscula, valor por extenso,
    nome sem acento."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    assert consolidado(uid_pro) == (114.88, 0.0)

    resp = manda(uid_pro, "Gastei 1 real com a barbara")
    assert "registrada" in resp.lower(), resp
    assert consolidado(uid_pro) == (113.88, -1.0), "o gasto ainda não chegou no banco"

    # o Pix cai no extrato: o espelho do banco desce 1 real e a transação aparece
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid_pro, conexao)

    assert rep["auto_merged"] == 1, rep
    # sem o conserto: (112.88, -1.0) — o mesmo real contado duas vezes
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_caminho_1_depois_de_outro_assunto_na_mesma_conversa(uid_pro, ia_fora):
    """Estado que OUTRO fluxo deixou no banco (CLAUDE.md §3): um gasto sem banco
    envolvido antes, e só então o que funde."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")

    manda(uid_pro, "gastei 50 no mercado")
    assert consolidado(uid_pro) == (64.88, -50.0)

    manda(uid_pro, "Gastei 1 real com a barbara")
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid_pro, conexao)

    assert rep["auto_merged"] == 1, rep
    # os 50 do mercado continuam debitados (não fundiram); só o 1 real voltou
    assert consolidado(uid_pro) == (63.88, -50.0)


# ── caminho 2: confirm_reconciliation (o usuário confirma o "ask") ──────────

def test_caminho_2_confirmacao_devolve_o_debito(uid_pro, ia_fora):
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara")
    manual_id = ultimo_launch(uid_pro)

    # descrição que NÃO parece a do manual → verdict "ask", não "auto"
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "COMPRA CARTAO 4412 XPTO")])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["pending"] == 1, rep
    assert consolidado(uid_pro) == (112.88, -1.0), "pendente: ainda conta duas vezes"

    of_tx_id = _tx_pendente(uid_pro)
    assert db.confirm_reconciliation(uid_pro, of_tx_id)["ok"] is True

    assert consolidado(uid_pro) == (113.88, 0.0)
    assert delta_conta(uid_pro, manual_id) == Decimal("-1"), \
        "a correção é de LEITURA: o banco não pode ter mudado"


# ── caminho 3: reconcile_manual_launch (manual criado DEPOIS do import) ─────

def test_caminho_3_fusao_reversa_devolve_o_debito(uid_pro, ia_fora):
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "113.88",
                            [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["inserted"] == 1, rep
    assert consolidado(uid_pro) == (113.88, 0.0)

    # o dono lança o MESMO gasto à mão depois; add_from_entities chama a fusão reversa
    manda(uid_pro, "Gastei 1 real com a barbara")

    # sem o conserto: (112.88, -1.0)
    assert consolidado(uid_pro) == (113.88, 0.0)


# ── receita: o delta positivo volta pelo mesmo caminho, sem sinal cravado ───

def test_receita_fundida_nao_derruba_o_consolidado(uid_pro, ia_fora):
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")

    manda(uid_pro, "recebi 100 do fulano")
    assert consolidado(uid_pro) == (214.88, 100.0)

    sincroniza(conexao, uid_pro, "214.88",
               [tx(uid_pro, "100.00", hoje, "PIX RECEBIDO FULANO")])
    rep = db.import_open_finance_launches(uid_pro, conexao)

    assert rep["auto_merged"] == 1, rep
    # sem o conserto: 314,88 — 100 contados duas vezes, para CIMA
    assert consolidado(uid_pro) == (214.88, 0.0)


# ── delete depois da fusão: não devolve dinheiro que já voltou ──────────────

def test_apagar_o_lancamento_fundido_nao_cria_dinheiro(uid_pro, ia_fora):
    """`imported_launch_id` é `on delete set null` (db/schema.py): apagar o
    lançamento desfaz o vínculo sem uma linha de código, a correção deixa de se
    aplicar e o rollback devolve o débito ao cru. O consolidado não se mexe —
    o espelho do banco continua contando o real uma vez."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara")
    manual_id = ultimo_launch(uid_pro)
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, conexao)
    assert consolidado(uid_pro) == (113.88, 0.0)

    db.delete_launch_and_rollback(uid_pro, manual_id)

    assert consolidado(uid_pro) == (113.88, 0.0), "apagar mexeu no consolidado"
    assert saldo_bruto(uid_pro) == Decimal("0"), "o delete tem de reverter o débito"
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro), \
        f"saldo {saldo_bruto(uid_pro)} != soma dos delta_conta {soma_delta_conta(uid_pro)}"


# ── POSITIVO: o que não funde continua debitando e devolvendo ──────────────

def test_lancamento_que_nao_funde_continua_debitando_a_carteira(uid_pro, ia_fora):
    """Sem banco conectado. Sem este caso, um conserto que zerasse TODO
    `delta_conta` passaria no grupo — e seria pior que o bug."""
    manda(uid_pro, "gastei 50 no mercado")
    launch_id = ultimo_launch(uid_pro)

    assert delta_conta(uid_pro, launch_id) == Decimal("-50")
    assert saldo_bruto(uid_pro) == Decimal("-50")

    db.delete_launch_and_rollback(uid_pro, launch_id)
    assert saldo_bruto(uid_pro) == Decimal("0"), "apagar tem de devolver o dinheiro"


def test_com_banco_mas_fora_da_tolerancia_continua_debitando(uid_pro, ia_fora):
    """Banco conectado e transação do MESMO dia, mas valor fora da tolerância:
    não funde, o débito fica de pé e o consolidado soma os dois."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara")
    launch_id = ultimo_launch(uid_pro)

    sincroniza(conexao, uid_pro, "37.88",
               [tx(uid_pro, "-77.00", hoje, "MERCADO LIVRE")])
    rep = db.import_open_finance_launches(uid_pro, conexao)

    assert rep["auto_merged"] == 0, rep
    assert delta_conta(uid_pro, launch_id) == Decimal("-1")
    assert consolidado(uid_pro) == (36.88, -1.0)


def _tx_pendente(uid: int) -> int:
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select o.id from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s and o.reconciliation_status = 'pending'
                """,
                (uid,),
            )
            row = cur.fetchone()
        conn.commit()
    assert row, "nenhuma OF tx pendente"
    return row["id"]
