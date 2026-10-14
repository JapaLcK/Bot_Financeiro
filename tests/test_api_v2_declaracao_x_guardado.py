"""A declaração de despesa/receita (PR-A) convive com o guardado (P8) na lista v2: o MESMO usuário tem
um depósito de caixinha pago pelo banco (declaração de APORTE, é guardado) e despesa/receita declaradas
(fluxo, NÃO é guardado). Com e sem `guardado=1` a declarada continua `origem=banco`,
`pode=[categoria, descricao, apagar]`, `guardado=false`, e o guardado continua `pode=[]` e sem instituição.
Controles negativos: `DECLARADA_SQL` sem o recorte por tipo captura o aporte (vermelho no guardado);
sem o ramo `declarada` do `PODE_SQL`, o fluxo perde o `pode` (vermelho nos dois modos).
"""
from __future__ import annotations

from decimal import Decimal

from conftest import usuario_pagante
from tests.test_api_v2_lancamentos import todos as paginas
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_bank_movements import _bank, _deposit
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza, tx

PODE = ["categoria", "descricao", "apagar"]


def _mundo(libera_):
    uid = usuario_pagante()
    cid, source = _bank(uid)
    aporte = _deposit(uid, source, 120)  # declaração de aporte: guardado
    casada = declara(uid, 50)
    sem_par = declara(uid, 30, "receita")
    sincroniza(cid, uid, tx())  # casa a despesa de 50 com o extrato
    libera_(uid)
    return uid, aporte, casada, sem_par


def _por_id(itens):
    return {i["id"]: i for i in itens}


def _confere_fluxo(itens, casada, sem_par):
    c, s = itens[f"l{casada}"], itens[f"l{sem_par}"]
    assert (c["origem"], c["pode"], c["fundido"], c["guardado"], c["interno"]) == ("banco", PODE, True, False, False)
    assert c["instituicao"] == "Nubank" and "conciliacao_pendente" not in c["motivos"]
    assert Decimal(str(c["valor"])) == 50
    assert (s["origem"], s["pode"], s["fundido"], s["guardado"], s["interno"]) == ("banco", PODE, False, False, False)
    assert s["instituicao"] is None and "conciliacao_pendente" in s["motivos"] and s["tipo"] == "entrada"


def test_sem_guardado_a_lista_so_tem_o_fluxo_e_ele_esta_certo(libera):
    uid, aporte, casada, sem_par = _mundo(libera)
    itens = _por_id(paginas(uid))
    assert f"l{aporte}" not in itens  # opt-in: o app antigo não vê guardado
    _confere_fluxo(itens, casada, sem_par)


def test_com_guardado_o_aporte_aparece_como_guardado_e_o_fluxo_nao_muda(libera):
    uid, aporte, casada, sem_par = _mundo(libera)
    sem, com = _por_id(paginas(uid)), _por_id(paginas(uid, guardado=1))
    g = com[f"l{aporte}"]
    assert (g["guardado"], g["interno"], g["pode"], g["origem"], g["instituicao"], g["fundido"]) == (
        True, True, [], "registro_antigo", None, False)
    _confere_fluxo(com, casada, sem_par)
    for lid in (casada, sem_par):  # a declarada é a MESMA linha nos dois modos
        assert com[f"l{lid}"] == sem[f"l{lid}"]


def test_filtro_de_categoria_devolve_a_declarada_e_nunca_o_guardado(libera):
    uid, aporte, casada, sem_par = _mundo(libera)
    ids = {i["id"] for i in paginas(uid, guardado=1, categoria="mercado")}
    assert f"l{casada}" in ids and f"l{sem_par}" in ids and f"l{aporte}" not in ids
