"""Q41 grupo 5: desconectar mantém a Carteira; reconectar (item e ids novos da
Pluggy, mesmo providerId) não credita de novo; o saque feito DURANTE a
desconexão entra; saque na janela antiga com chave nova pergunta."""
from datetime import datetime
from decimal import Decimal

import db
from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, q, sync, tx  # noqa: F401


def _reconecta(uid):
    """Conta conectada em 01/02, saques em 10/03 e 12/03, desconectada; nova
    conexão hoje, com ids da Pluggy novos para as mesmas transações."""
    c1 = conecta(uid, f"item-a-{uid}")
    sync(c1, uid, [tx("a-1", -200, dia(10), pid="P1"), tx("a-2", -50, dia(12), pid="P2")])
    assert carteira(uid) == Decimal("250")
    assert db.disconnect_open_finance_connection(uid, c1) == 1
    assert carteira(uid) == Decimal("250"), "desconectar desfez o saque em dinheiro"
    return conecta(uid, f"item-b-{uid}", desde=datetime(2026, 4, 1, 12))


def test_reconectar_religa_sem_credito_extra(caixa):
    uid = usuario_pagante()
    c2 = _reconecta(uid)
    sync(c2, uid, [tx("b-1", -200, dia(10), pid="P1"), tx("b-2", -50, dia(12), pid="P2")])
    assert carteira(uid) == Decimal("250")
    assert [r["status"] for r in links(uid)] == ["ativo", "ativo"]
    assert all(r["of_transaction_id"] for r in links(uid)), "vínculo não religou ao espelho novo"


def test_saque_durante_a_desconexao_entra(caixa):
    uid = usuario_pagante()
    c2 = _reconecta(uid)
    sync(c2, uid, [tx("b-1", -200, dia(10), pid="P1"), tx("b-3", -70, dia(20), pid="P3")])
    assert carteira(uid) == Decimal("320")
    assert links(uid)[-1]["status"] == "ativo"


def test_janela_antiga_com_chave_nova_pergunta(caixa):
    """Mesma data que a conexão antiga já cobriu, providerId que ela nunca viu:
    pode ser o mesmo saque com id trocado → pergunta, não credita."""
    uid = usuario_pagante()
    c2 = _reconecta(uid)
    sync(c2, uid, [tx("b-9", -200, dia(11), pid="P9")])
    assert carteira(uid) == Decimal("250")
    assert links(uid)[-1]["status"] == "perguntar_novo"
    cov = q("select covered_from, covered_until from of_cash_coverage where user_id=%s", (uid,), True)
    assert [(r["covered_from"], r["covered_until"]) for r in cov] == [(dia(10), dia(12))]


def test_sem_providerid_pergunta(caixa):
    """Chave não-durável (sem providerId): nunca credita sozinho."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("x-1", -200, dia(10), pid=False)])
    assert carteira(uid) == 0
    assert [(r["status"], r["key_durable"]) for r in links(uid)] == [("perguntar_novo", False)]


def _nubank_ate_abril(uid, instituicao=212):
    """Nubank vivo com saque em 10/03 e extrato até 20/04 (a janela que ele cobre)."""
    c = conecta(uid, f"item-nu-{uid}-{instituicao}", instituicao=instituicao)
    sync(c, uid, [tx("n-1", -200, dia(10), pid="P1"), tx("n-2", -5, dia(20, 4), op="PIX", desc="Pix enviado")])
    return c


def test_mesmo_numero_em_bancos_diferentes_nao_se_misturam(caixa):
    """Inter conectado em 01/04 com o MESMO número de conta do Nubank: o saque do
    Inter anterior à conexão dele é histórico, e o posterior credita — nenhum dos
    dois herda o corte nem a janela da conta do Nubank."""
    uid = usuario_pagante()
    _nubank_ate_abril(uid)
    inter = conecta(uid, f"item-inter-{uid}", desde=datetime(2026, 4, 1, 12), instituicao=77, nome="Inter")
    sync(inter, uid, [tx("i-1", -70, dia(15), pid="I1"), tx("i-2", -30, dia(15, 4), pid="I2")])

    assert [(r["tx_date"], r["status"]) for r in links(uid)] == [
        (dia(10), "ativo"), (dia(15), "historico"), (dia(15, 4), "ativo")]
    assert carteira(uid) == Decimal("230")


def test_mesmo_banco_por_dois_connectors_segue_a_mesma_conta(caixa):
    """Positivo: Nubank direto (212) e Nubank Open Finance (612) vivos, mesmo nome
    e número — é a mesma conta, e o saque que o 612 vê com outro providerId na
    janela do 212 pergunta em vez de creditar em dobro."""
    uid = usuario_pagante()
    _nubank_ate_abril(uid)
    of = conecta(uid, f"item-of-{uid}", instituicao=612)
    sync(of, uid, [tx("o-1", -200, dia(10), pid="Q1")])

    assert [r["status"] for r in links(uid)] == ["ativo", "perguntar_novo"]
    assert carteira(uid) == Decimal("200")
