"""`GET /api/v2/gastos/categoria`: os estabelecimentos (`db/estabelecimentos.py`) com Postgres real.

Chave pelo `raw` do Open Finance (CNPJ, recebedor, descrição), o isolamento da transação forjada, o `raw`
que nunca sai e o P1 (b): grupos de mesmo nome se juntam. Helpers de `test_api_v2_gastos_categoria.py`.
Cada correção carrega o controle NEGATIVO medido à mão (o conserto desligado em `db/estabelecimentos.py`,
vermelho) e o positivo no mesmo teste.
"""
from __future__ import annotations

import json

import pytest

import db
from conftest import usuario_pagante
from tests.test_api_app_sessao import completo
from tests._patrimonio_helpers import q
from tests.test_api_v2_gastos import M, _l
from tests.test_api_v2_gastos_categoria import URL, _banco, _cat, _dia, _nomes
from tests.test_api_v2_lancamentos import pede
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)


# ── 3 transação forjada ──────────────────────────────────────────────────────

def test_transacao_forjada_de_outro_usuario_nao_junta_estabelecimentos():
    a, b = usuario_pagante(), usuario_pagante()
    l1 = _l(a, 10, _dia(3), categoria="mercado", alvo="Alfa", source="open_finance")
    l2 = _l(a, 20, _dia(4), categoria="mercado", alvo="Beta", source="open_finance")
    _banco(a, l1, "1", cnpj="12.345.678/0001-95")
    _banco(b, l2, "forjada", cnpj="12345678000195")  # a conta é de B, o imported_launch_id é de A
    r = _cat(a)
    assert _nomes(r) == [("Beta", 1), ("Alfa", 1)]
    l3 = _l(a, 5, _dia(5), categoria="mercado", alvo="Gama", source="open_finance")
    _banco(a, l3, "3", cnpj="12345678000195")  # positivo: a ligação legítima junta
    assert ("Gama", 2) in _nomes(_cat(a))  # rótulo: empate, o mais recente


# ── 4 a 6 chave do estabelecimento ───────────────────────────────────────────

def test_cnpj_junta_descricoes_diferentes_e_o_rotulo_e_o_mais_frequente():
    a = usuario_pagante()
    for n, alvo in ((1, "IFOOD *REST A"), (2, "IFD*REST A"), (3, "IFOOD *REST A")):
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n),
               cnpj="12.345.678/0001-95", razao="RAZAO SOCIAL LTDA")
    assert _nomes(_cat(a)) == [("IFOOD *REST A", 3)]


def test_recebedor_do_pix_junta_descricoes_diferentes():
    a = usuario_pagante()
    for n, alvo in ((1, "PIX JOAO"), (2, "TRANSF 123 JOAO"), (3, "TRANSF 123 JOAO")):
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n),
               recebedor="  João da Silva ")
    _banco(a, _l(a, 10, _dia(4), categoria="mercado", alvo="PIX MARIA", source="open_finance"), "4",
           recebedor="Maria Souza")  # positivo: outro recebedor, outra linha
    assert _nomes(_cat(a)) == [("TRANSF 123 JOAO", 3), ("PIX MARIA", 1)]


def test_descricao_sem_raw_agrupa_pela_chave_normalizada():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado", alvo="UBER *TRIP")
    _l(a, 10, _dia(4), categoria="mercado", alvo="Uber Trip")
    _l(a, 3, _dia(5), categoria="mercado", alvo="Padaria")
    assert _nomes(_cat(a)) == [("Uber Trip", 2), ("Padaria", 1)]  # empate de rótulo: o mais recente


# ── 7 raw inesperado ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    {"merchant": {"cnpj": "00000000000000"}}, {"merchant": {"cnpj": "1234567800019"}},
    {"merchant": {"cnpj": "12.***.678/0001-**"}}, {"merchant": "texto"}, {"merchant": {"cnpj": 12345678000195}},
    {"merchant": {"cnpj": ["12345678000195"]}}, {"paymentData": {"receiver": {"name": {"x": 1}}}},
    {"paymentData": "x"}, {"paymentData": {"receiver": {"name": "***"}}}, [], "texto", None])
def test_raw_malformado_ou_cnpj_invalido_nao_da_500_e_cai_na_chave_seguinte(raw):
    a = usuario_pagante()
    for n, alvo in ((1, "Loja Um"), (2, "Loja Dois")):  # mesma chave inválida nas duas: não pode juntá-las
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n), raw=raw)
    assert sorted(_nomes(_cat(a))) == [("Loja Dois", 1), ("Loja Um", 1)]


# ── 8 o raw nunca sai ────────────────────────────────────────────────────────

def test_cnpj_recebedor_e_razao_social_nunca_saem_na_resposta_http(libera):
    a = completo(usuario_pagante())
    for n, alvo in ((1, "Loja Um"), (2, "Loja Dois")):
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n),
               cnpj="12.345.678/0001-95", recebedor="Fulano Recebedor Secreto", razao="RAZAO SOCIAL SECRETA LTDA")
    libera(a)
    r = pede(a, URL, mes=M, categoria="mercado")
    assert r.status_code == 200, r.text
    corpo = json.dumps(r.json(), ensure_ascii=False)
    for segredo in ("12345678", "12.345.678", "SECRETA", "Secreto", "merchant", "raw", f'"{a}"'):
        assert segredo not in corpo
    assert [e["quantidade"] for e in r.json()["estabelecimentos"]] == [2]  # positivo: o raw foi usado
    assert set(r.json()["ultimas"][0]) == {"data", "descricao", "mensagem", "origem", "valor"}


# ── 22 P1 (b): grupos de mesmo nome se juntam ────────────────────────────────

def test_grupos_com_o_mesmo_nome_depois_de_rotular_viram_uma_linha():
    a = usuario_pagante()
    for n in (1, 2):
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo="UBER *TRIP", source="open_finance"), str(n),
               cnpj="12345678000195")
    _l(a, 10, _dia(3), categoria="mercado", alvo="Uber Trip")
    assert _nomes(_cat(a)) == [("UBER *TRIP", 3)]
    b = usuario_pagante()
    for n, pessoa in enumerate(("Ana Lima", "Bruno Reis", "Carla Dias"), 1):
        _banco(b, _l(b, 10, _dia(n), categoria="mercado", alvo="Pix enviado", source="open_finance"), str(n),
               recebedor=pessoa)
    c = usuario_pagante()  # rótulos sem letra (merchant_key ""): só juntam se o NOME for igual
    for n, (alvo, cnpj) in enumerate((("***", "12345678000195"), ("###", "98765432000110"), ("***", "11222333000181")), 3):
        _banco(c, _l(c, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n), cnpj=cnpj)
    assert _nomes(_cat(b)) == [("Pix enviado", 3)] and sorted(_nomes(_cat(c))) == [("###", 1), ("***", 2)]


# ── descrição sem letra nem dígito (sem raw) ─────────────────────────────────

def test_descricao_sem_letra_nem_digito_nao_colapsa_e_igual_ainda_junta():
    a, b, c = usuario_pagante(), usuario_pagante(), usuario_pagante()
    alvos = ("***", "###", "🍕", "🍔", "---")
    for n, alvo in enumerate(alvos, 1):
        _l(a, 10, _dia(n), categoria="mercado", alvo=alvo)
    assert sorted(_nomes(_cat(a))) == sorted((x, 1) for x in alvos)  # negativo: a chave `merchant_key` sozinha dá 1 linha
    for n, alvo in enumerate(("***", "###", "***"), 1):
        _l(b, 10, _dia(n), categoria="mercado", alvo=alvo)
    assert sorted(_nomes(_cat(b))) == [("###", 1), ("***", 2)]  # positivo: idênticas juntam
    for n, alvo in enumerate((None, "", "  "), 1):
        _l(c, 10, _dia(n), categoria="mercado", alvo=alvo)
    assert _nomes(_cat(c)) == [("Sem descrição", 3)]


# ── ordem, limite, prioridade e id repetido ──────────────────────────────────

def test_so_os_5_primeiros_estabelecimentos_saem():
    a = usuario_pagante()
    for n in range(6):
        _l(a, 10 + n, _dia(1 + n), categoria="mercado", alvo=f"Loja{n}")
    assert _nomes(_cat(a)) == [(f"Loja{n}", 1) for n in (5, 4, 3, 2, 1)]


def test_empate_de_valor_e_quantidade_desempata_pelo_nome_e_nao_pela_recencia():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado", alvo="Alfa")
    _l(a, 10, _dia(5), categoria="mercado", alvo="Zeta")  # a mais recente: vem primeiro no `listar`
    assert _nomes(_cat(a)) == [("Alfa", 1), ("Zeta", 1)]


def test_cnpj_vence_o_recebedor():
    a = usuario_pagante()
    for n, (alvo, rec) in enumerate((("Aaa", "Recebedor Um"), ("Bbb", "Recebedor Dois")), 1):
        _banco(a, _l(a, 10, _dia(n), categoria="mercado", alvo=alvo, source="open_finance"), str(n),
               cnpj="12345678000195", recebedor=rec)
    assert [e["quantidade"] for e in _cat(a)["estabelecimentos"]] == [2]


def test_reconexao_do_mesmo_usuario_vale_a_transacao_mais_nova():
    a = usuario_pagante()
    l1 = _l(a, 10, _dia(3), categoria="mercado", alvo="Alfa", source="open_finance")
    l2 = _l(a, 20, _dia(4), categoria="mercado", alvo="Beta", source="open_finance")
    _banco(a, l1, "velha", cnpj="11111111000191")
    _banco(a, l1, "nova", cnpj="22222222000191")  # mesma linha, outra conexão, id maior: a que vale
    _banco(a, l2, "l2", cnpj="22222222000191")
    assert _nomes(_cat(a)) == [("Beta", 2)]


def test_id_repetido_entre_lancamento_e_compra_de_cartao_nao_mistura_o_raw():
    a, b = usuario_pagante(), usuario_pagante()
    cartoes = {}
    for u in (a, b):
        lid = _l(u, 10, _dia(1), categoria="mercado", alvo="LojaL", source="open_finance")
        cartoes[u] = db.create_card(u, "Nubank", closing_day=10, due_day=17)
        db.add_credit_purchase(u, cartoes[u], 20, "mercado", "CartaoLoja", _dia(2))
        q("update credit_transactions set id = %s where user_id = %s returning id", (lid, u))  # mesmo número
        _banco(u, lid, "l", cnpj="12345678000195")
    _banco(b, q("select id from credit_transactions where user_id = %s", (b,))["id"], "c", cnpj="12345678000195",
           col="imported_credit_tx_id")
    assert sorted(_nomes(_cat(a))) == [("CartaoLoja", 1), ("LojaL", 1)]  # só o lançamento tem raw
    assert [e["quantidade"] for e in _cat(b)["estabelecimentos"]] == [2]  # positivo: o mesmo CNPJ nos dois junta


def test_desempate_do_rotulo_depois_de_juntar_e_pela_ordem_do_listar():
    a = usuario_pagante()
    # índices do `listar` (do mais recente): 0 Z, 1 X, 2 Y, 3 Y, 4 X; Q = {0,2,3} e P = {1,4} juntam depois de
    # rotulados (Y e X têm o mesmo `merchant_key`). X e Y empatam em 2: vale o que aparece primeiro, X (índice 1).
    for dia, alvo, cnpj in ((20, "uber trip", "11111111000191"), (19, "Uber Trip", "22222222000191"),
                            (18, "UBER *TRIP", "11111111000191"), (17, "UBER *TRIP", "11111111000191"),
                            (16, "Uber Trip", "22222222000191")):
        _banco(a, _l(a, 10, _dia(dia), categoria="mercado", alvo=alvo, source="open_finance"), str(dia), cnpj=cnpj)
    assert _nomes(_cat(a)) == [("Uber Trip", 5)]
