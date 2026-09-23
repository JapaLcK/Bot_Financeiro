"""A LÁPIDE do vínculo OF: o que a ausência desfez, religado na volta.

Irmão direto de `tests/test_of_investimento_reconciliacao.py` (a REMOÇÃO da
posição que sumiu), de onde vêm os helpers. A divisão é o teto de 350 linhas por
arquivo (`tests/test_max_lines_python.py`), e o corte é por assunto: lá o que SAI
quando o banco para de mandar a posição, aqui o que VOLTA quando ela reaparece.

O bloqueante que originou o arquivo, provado pelo Tester no `sync_pluggy_item`
real: a meta MANUAL perdia o vínculo na ausência — correto, senão sobra saldo
bancário fantasma — e na volta a posição vinha com id NOVO, porque a linha foi
apagada. O auto-import criava uma caixinha AUTOMÁTICA duplicada, e o usuário não
conseguia religar a meta dele: `bind_pocket_to_caixinha` levantava
`OF_POCKET_READONLY`, já que a posição agora era de um pocket `source='open_finance'`.

A remoção continua IMEDIATA e atômica — a lápide não é carência, e B11 mede isso.
Ela só guarda a chave natural (conexão + `provider_investment_id`) para reconhecer
a mesma posição quando ela voltar.

CONTROLE NEGATIVO (medido, ver relato): tirar o UPDATE de religação deixa B9,
B13, B14, B16 e o E2E vermelhos; tirar `grava_lapide=True` deixa B9, B11, B13 e
B16 vermelhos; tirar `p.user_id` do UPDATE deixa B13 vermelho; tirar
`p.of_investment_id is null` deixa B14 vermelho; tirar o `not exists` deixa B12
vermelho; tirar a inicialização de `of_last_seen_*` deixa B9 vermelho; tirar a
inicialização de `of_last_seen_*` deixa B9 vermelho.

CONTROLE POSITIVO: B14 (o vínculo que o usuário fez na mão sobrevive à volta da posição
antiga) — sem eles o grupo passaria num código que religasse tudo por cima de
qualquer coisa.
"""
import pytest

import db
from db import get_conn
from test_of_caixinha_autoimport import _pockets, _save, _seed_connection
from test_of_caixinha_dinheiro import _carteira, _lotes_abertos
from test_of_caixinha_vinculo import CDB, _meta_manual, _of_id, _total
from test_of_investimento_reconciliacao import CX_AUTO, _posicoes, _reconcilia


# ── A LÁPIDE: o vínculo que a ausência desfez, religado na volta ─────────────
# O bloqueante que originou este bloco (provado pelo Tester no `sync_pluggy_item`
# real): a meta MANUAL perdia o vínculo na ausência — correto, senão sobra saldo
# bancário fantasma — e na volta a posição vinha com id NOVO, porque a linha foi
# apagada. O auto-import criava uma caixinha AUTOMÁTICA duplicada, e o usuário não
# conseguia religar a meta dele: `bind_pocket_to_caixinha` levantava
# `OF_POCKET_READONLY`, já que a posição agora era de um pocket `source='open_finance'`.
#
# A remoção continua IMEDIATA e atômica — a lápide não é carência (B11 mede isso).
# Ela só guarda a chave natural (conexão + provider_investment_id) para reconhecer
# a mesma posição quando ela voltar.

def _lapide(user_id: int, pocket_id: int) -> tuple:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select of_tombstone_connection_id as c, of_tombstone_provider_id as p "
                "from pockets where id=%s and user_id=%s",
                (pocket_id, user_id),
            )
            r = cur.fetchone()
            return (r["c"], r["p"])


def _meta_vinculada_que_some(user_id: int) -> tuple[int, int, int]:
    """O cenário do bloqueante, até o fim da ausência: meta manual com R$300 de
    aporte próprio, vinculada a uma posição de 800 que o banco deixa de mandar.
    Devolve (conn_id, pocket_id, id_da_posicao_antes)."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)       # carteira 2000 → 1700
    antes = _of_id(conn_id, "cx-auto")
    db.bind_pocket_to_caixinha(user_id, pocket_id, antes)
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 800.0   # espelho do banco
    _reconcilia(conn_id, [])                                        # o banco omite a posição
    return conn_id, pocket_id, antes


# B9 ─────────── a regressão exata do bloqueante ─────────────────────────────

def test_meta_manual_que_perdeu_o_vinculo_por_ausencia_religa_na_volta(user_id):
    conn_id, pocket_id, antes = _meta_vinculada_que_some(user_id)
    # a IDA continua sendo o B2, sem mudança: vínculo solto, saldo próprio de volta
    assert _pockets(user_id)["Viagem"]["of_investment_id"] is None
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 300.0

    res = _reconcilia(conn_id, [CX_AUTO])                           # a posição volta
    db.sync_open_finance_caixinhas(conn_id, user_id)

    depois = _of_id(conn_id, "cx-auto")
    assert res["caixinhas_religadas"] == 1
    pk = _pockets(user_id)
    assert pk["Viagem"]["of_investment_id"] == depois, "a meta reconhece a posição que voltou"
    assert float(pk["Viagem"]["balance"]) == 800.0, "espelho do banco de novo"
    assert [n for n in pk if n.startswith("Caixinha")] == [], "nenhuma duplicada nasce"
    assert _lotes_abertos(user_id, pocket_id) == 1, "os R$300 de aporte próprio ficam"
    assert _carteira(user_id) == 1700.0
    assert _total(user_id) == 800.0, "o dinheiro do banco conta UMA vez (não 1100)"
    # O baseline do Banqueiro nasce NO SALDO ATUAL, como no insert do auto-import:
    # sem isso o delta da primeira rodada seria a carteira inteira e ele anunciaria
    # "você guardou R$800" numa religação em que o usuário não guardou nada.
    b = {x["pocket_id"]: x for x in db.list_banqueiro_pockets(user_id)}[pocket_id]
    assert float(b["of_last_seen_balance"]) == float(b["of_balance"]) == 800.0

    # e o usuário não fica preso: religar na mão continua possível
    assert db.bind_pocket_to_caixinha(user_id, pocket_id, depois) is True
    assert _lapide(user_id, pocket_id) == (None, None)


# B10 ─────────── anti-tautologia: o id muda MESMO ───────────────────────────

def test_a_posicao_volta_com_id_novo_de_verdade(user_id):
    """Se o id fosse o mesmo, a religação não precisaria existir e B9 estaria
    medindo o nada. Também guarda que a remoção continua acontecendo."""
    conn_id, _pocket_id, antes = _meta_vinculada_que_some(user_id)
    assert _posicoes(conn_id) == set(), "a posição foi mesmo removida"

    _reconcilia(conn_id, [CX_AUTO])

    assert _of_id(conn_id, "cx-auto") != antes


# B11 ─────────── a lápide NÃO é carência ────────────────────────────────────

def test_durante_a_ausencia_nao_ha_saldo_fantasma(user_id):
    """Entre o sumiço e a volta, o dinheiro do banco não pode aparecer em lugar
    nenhum: a posição não existe, a meta vale o PRÓPRIO dela (300, não 800) e a
    renda fixa não conta nada."""
    conn_id, pocket_id, _antes = _meta_vinculada_que_some(user_id)

    assert _posicoes(conn_id) == set()
    p = _pockets(user_id)["Viagem"]
    assert float(p["balance"]) == 300.0 and p["of_investment_id"] is None
    assert db.list_of_fixed_income(user_id) == []
    assert _total(user_id) == 300.0, "só o aporte próprio"
    assert _carteira(user_id) == 1700.0
    # a lápide existe, mas é só a chave natural — não segura saldo nenhum
    assert _lapide(user_id, pocket_id) == (conn_id, "cx-auto")


# B12 ─────────── nunca dois pockets na mesma posição ────────────────────────

def test_lapide_nao_poe_dois_pockets_na_mesma_posicao(user_id):
    """A terceira guarda do UPDATE (`not exists ... q.of_investment_id = i.id`).

    Estado FORJADO, como em B13/B14: uma lápide viva apontando para uma posição
    que OUTRO pocket já ocupa. No caminho normal a lápide é consumida ou apagada
    antes disso, e medido: tirar esta guarda sozinha não deixa nada vermelho sem
    este teste. Ela é a rede contra o defeito mais caro deste PR — duas caixinhas
    espelhando o mesmo dinheiro do banco, que é o patrimônio contado em dobro."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    dona = _pockets(user_id)["Caixinha Nubank"]
    assert dona["of_investment_id"] == _of_id(conn_id, "cx-auto")

    outra = _meta_manual(user_id)                       # manual, solta, com lápide viva
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pockets set of_tombstone_connection_id=%s, "
                "of_tombstone_provider_id='cx-auto' where id=%s and user_id=%s",
                (conn_id, outra, user_id),
            )
        conn.commit()

    assert _reconcilia(conn_id, [CX_AUTO])["caixinhas_religadas"] == 0

    pk = _pockets(user_id)
    assert pk["Caixinha Nubank"]["of_investment_id"] == dona["of_investment_id"]
    assert pk["Viagem"]["of_investment_id"] is None, "a posição já tem dono"
    assert _total(user_id) == 800.0, "os 800 do banco contam UMA vez"


# B13 ─────────── isolamento da lápide ───────────────────────────────────────

def test_lapide_de_um_usuario_nao_religa_pocket_de_outro(user_id):
    """Mesmo `provider_investment_id` em conexões de donos diferentes. E, na mesma
    conexão, um provider diferente também não religa.

    A terceira parte FORJA o estado que as guardas existem para recusar. Medido:
    com a lápide escrita pelo caminho normal, tirar `p.user_id`/`i.connection_id`
    do UPDATE não deixa nada vermelho — a lápide guarda o `connection_id`, que já
    pertence a um dono só, então o cruzamento entre usuários é estruturalmente
    impossível por ali. A guarda continua (CLAUDE.md §0 é regra dura, e a rede não
    pode depender de um argumento de estrutura), e esta parte é o que a tranca: com
    uma lápide APONTANDO para a conexão do vizinho — estado que nenhum caminho
    produz —, o pocket do vizinho segue intocado."""
    outro = user_id + 1
    db.ensure_user(outro)
    conn_b = _seed_connection(outro, item="test-cx-item-b")
    _save(conn_b, [CX_AUTO])
    meta_b = _meta_manual(outro)
    db.bind_pocket_to_caixinha(outro, meta_b, _of_id(conn_b, "cx-auto"))
    db.sync_open_finance_caixinhas(conn_b, outro)
    vinculo_b = _pockets(outro)["Viagem"]["of_investment_id"]

    conn_a, meta_a, _antes = _meta_vinculada_que_some(user_id)
    assert _lapide(user_id, meta_a) == (conn_a, "cx-auto")

    # 1. um provider DIFERENTE na conexão de A não religa a lápide de A
    _reconcilia(conn_a, [CDB])
    assert _pockets(user_id)["Viagem"]["of_investment_id"] is None
    assert _lapide(user_id, meta_a) == (conn_a, "cx-auto"), "a lápide segue esperando"

    # 2. lápide FORJADA: a do vizinho aponta para a conexão de A (ver docstring)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pockets set of_investment_id=null, of_tombstone_connection_id=%s, "
                "of_tombstone_provider_id='cx-auto' where id=%s and user_id=%s",
                (conn_a, meta_b, outro),
            )
        conn.commit()

    # 3. a posição de A volta: religa A e não encosta em B
    _reconcilia(conn_a, [CX_AUTO])
    posicao_a = _of_id(conn_a, "cx-auto")
    assert _pockets(user_id)["Viagem"]["of_investment_id"] == posicao_a
    assert _pockets(outro)["Viagem"]["of_investment_id"] is None, (
        "a lápide forjada do vizinho não pode pescar a posição de A")
    assert _lapide(user_id, meta_a) == (None, None), "a de A foi consumida"


# B14 ─────────── a decisão do usuário ganha da lápide ───────────────────────

def test_vinculo_manual_durante_a_ausencia_ganha_da_lapide(user_id):
    """Durante a ausência o usuário vincula a meta a OUTRA posição. A antiga volta
    e NÃO pode roubar o pocket de volta.

    São DUAS proteções, e a segunda parte forja o estado que separa as duas.
    No caminho normal o bind já apaga a lápide, então `p.of_investment_id is null`
    fica redundante — medido: tirá-lo sozinho não deixa nada vermelho. Ele é a rede
    para o caso de uma lápide sobreviver a um bind (caminho novo, bug futuro), e a
    segunda parte é o que o tranca: lápide viva + pocket JÁ vinculado, e a posição
    que volta não rouba nada."""
    conn_id, pocket_id, _antes = _meta_vinculada_que_some(user_id)
    _save(conn_id, [CDB])                                  # outra posição, 1000
    outra = _of_id(conn_id, "cdb-vinc")
    assert db.bind_pocket_to_caixinha(user_id, pocket_id, outra) is True
    assert _lapide(user_id, pocket_id) == (None, None), "o bind manual mata a lápide"

    res = _reconcilia(conn_id, [CDB, CX_AUTO])             # a antiga volta
    db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_religadas"] == 0
    assert _pockets(user_id)["Viagem"]["of_investment_id"] == outra, "o vínculo novo fica"
    # a antiga volta pelo caminho normal: caixinha automática do auto-import
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0

    # 2ª proteção: lápide FORJADA num pocket que JÁ está vinculado (ver docstring)
    _reconcilia(conn_id, [CDB])                            # a cx-auto some de novo
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pockets set of_tombstone_connection_id=%s, "
                "of_tombstone_provider_id='cx-auto' where id=%s and user_id=%s",
                (conn_id, pocket_id, user_id),
            )
        conn.commit()

    assert _reconcilia(conn_id, [CDB, CX_AUTO])["caixinhas_religadas"] == 0
    assert _pockets(user_id)["Viagem"]["of_investment_id"] == outra, (
        "pocket já vinculado não é roubado por uma lápide sobrevivente")


# B16 ─────────── desconectar mata a lápide (o `on delete set null`) ─────────

def test_desconectar_o_banco_mata_a_lapide(user_id):
    """A lápide referencia a conexão. Saindo a conexão, não há vínculo velho a
    restaurar: reconectar é conexão NOVA, e a posição entra como posição nova."""
    conn_id, pocket_id, _antes = _meta_vinculada_que_some(user_id)
    assert _lapide(user_id, pocket_id) == (conn_id, "cx-auto")

    db.disconnect_open_finance_connection(user_id, conn_id)

    assert _lapide(user_id, pocket_id) == (None, "cx-auto"), "o FK zerou a conexão"

    novo = _seed_connection(user_id, item="test-cx-item-reconectado")
    res = _reconcilia(novo, [CX_AUTO])
    db.sync_open_finance_caixinhas(novo, user_id)

    assert res["caixinhas_religadas"] == 0, "nada religa"
    assert _pockets(user_id)["Viagem"]["of_investment_id"] is None
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0
