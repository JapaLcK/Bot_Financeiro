"""O PREÇO da correção por leitura: dois números fixados, nenhum consertado.

Estes casos NÃO são o comportamento desejado — são o custo conhecido de corrigir
na leitura, fixados para que mudá-los seja deliberado e não acidente. A
abordagem anterior (por escrita) errava os MESMOS dois casos pelo outro lado;
não existe terceira opção barata aqui, e o dono precisa do número, não do
adjetivo.

Nenhum destes dois mede o conserto: os dois são vermelhos com a correção e
verdes sem ela, de propósito. Quem discrimina o conserto está em
`tests/test_fusao_of_nao_conta_duas_vezes.py` (caminho_1) e no caso 8 de
`tests/test_fusao_of_evapora_sozinha.py` (snapshot do dashboard).
"""
from __future__ import annotations

import pytest

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, of_tx_pendente, sincroniza, tx,
    uid_pro,
)


def test_preco_fusao_falsa_positiva_superconta_450(uid_pro, ia_fora):
    """FIXA 450,00 (a `main` dá 400,00, certa por acidente).

    "gastei 50 no almoço" em ESPÉCIE e um `UBER *TRIP SAO PAULO` de R$ 50 no
    banco, no mesmo dia: gastos DIFERENTES, e a heurística os funde. Fundidos, a
    correção devolve o débito do manual e o espelho conta só o Uber — sobra um
    gasto contado onde houve dois.

    O TAMANHO DA PORTA: `almoço` normaliza para `almoco`, que está em
    `_GENERIC_MERCHANTS` (`db/open_finance.py:1563`) junto de `comida`,
    `gasto`, `compra`, `lanche`, `jantar`, `cafe`, `diversos`, `outros`,
    `conta`, `boleto`, `pix` e `""`. Um `alvo` genérico é "similar" a QUALQUER
    descrição (`_is_generic_merchant`, `:1591`), então basta valor igual
    (±0,05) com um único candidato elegível para o casamento ser proposto —
    e, com a confirmação do usuário, a fusão acontece.

    O erro TROCA DE DIREÇÃO entre as abordagens: a `main` subconta no
    verdadeiro-positivo (o bug deste PR), o branch superconta no
    falso-positivo. Consertar é consertar a HEURÍSTICA, não a leitura.
    """
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "500.00")
    manda(uid_pro, "gastei 50 no almoço")                  # em espécie
    sincroniza(conexao, uid_pro, "450.00",
               [tx(uid_pro, "-50.00", hoje, "UBER *TRIP SAO PAULO")])  # outro gasto
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, \
        "a porta genérica propôs o casamento (fora do escopo)"
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))

    assert consolidado(uid_pro) == (450.0, 0.0), \
        "NÚMERO FIXADO, não desejado: a main dá 400,0 aqui"


def test_preco_espelho_atrasado_superconta_ate_o_proximo_sync(uid_pro, ia_fora):
    """FIXA 114,88 enquanto o espelho não alcança (a `main` dá 113,88).

    ALCANÇABILIDADE MEDIDA, não deduzida: `core/services/pluggy_sync.py:263-269`
    chama `list_pluggy_accounts` (que traz o `balance`) e SÓ ENTÃO, por conta,
    `list_pluggy_transactions` — duas chamadas HTTP, a segunda com paginação por
    cursor (até 60 páginas). O `balance` do payload é então estritamente MAIS
    VELHO que a lista de transações, e toda transação que cai entre as duas
    chegou com o saldo de antes dela. A janela existe a cada sync.

    E SE CURA SOZINHA: `save_open_finance_sync` sobrescreve o `balance` no
    `on conflict ... do update set balance = excluded.balance`
    (db/open_finance.py:1258), então o sync seguinte já traz o saldo com a
    transação dentro e o número volta a fechar — o que a segunda metade deste
    teste prova. A superconta dura no máximo um intervalo de sync, e vale o
    tamanho da transação.
    """
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara")

    # espelho AINDA cheio (114,88), transação já entregue: a janela do relato.
    # o casamento é rebaixado a pendência (candidato manual) e o dono confirma:
    # só a fusão confirmada devolve o débito na leitura.
    sincroniza(conexao, uid_pro, "114.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))

    assert consolidado(uid_pro) == (114.88, 0.0), \
        "NÚMERO FIXADO, não desejado: a main dá 113,88 aqui"

    # o sync seguinte traz o saldo já com o PIX dentro: cura
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, conexao)
    assert consolidado(uid_pro) == (113.88, 0.0), "o sync seguinte tem de curar"


def test_preco_extrato_reconciliado_fica_50_acima_do_ledgerbal(uid_pro, ia_fora, monkeypatch):
    """FIXA 827,00 com um extrato que declara 777,00 (a `main` dá 777,00).

    `set_balance` grava no cru EXATAMENTE o saldo final que o extrato informa —
    e a correção soma por cima os 50,00 do gasto que o Open Finance fundiu. O
    relatório e o dashboard ficam COERENTES entre si (é o que este PR promete),
    mas os dois 50,00 acima do número que o próprio extrato declarou.

    Mesma família do falso-positivo: quando a fusão é boa, os 50,00 saíram do
    banco e o espelho já os conta, então o total está certo e é o LEDGERBAL que
    está velho. Quando a fusão é falsa, o total sobe indevidamente. Não dá para
    separar os dois casos aqui — quem separa é a heurística de fusão.
    """
    from datetime import date as _date
    from decimal import Decimal
    import statement_import as si

    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "1000.00")
    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    manda(uid_pro, "gastei 50 no mercado")
    sincroniza(conexao, uid_pro, "950.00", [tx(uid_pro, "-50.00", hoje, "MERCADO")])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))

    monkeypatch.setattr(si, "_extract_pdf_text", lambda _d: "extrato falso")
    monkeypatch.setattr(si, "parse_pdf_statement_text", lambda _t: [
        {"posted_at": _date.today(), "amount": Decimal("-20"), "memo": "SAQUE"}])
    monkeypatch.setattr(si, "extract_pdf_final_balance", lambda _t: Decimal("777.00"))

    rep = si.import_statement_bytes(uid_pro, b"%PDF-falso", "extrato.pdf", "pdf")

    assert rep["reconciled"] is True, rep
    assert float(rep["new_balance"]) == pytest.approx(827.0), \
        "NÚMERO FIXADO, não desejado: a main dá 777,00, o que o extrato declarou"
