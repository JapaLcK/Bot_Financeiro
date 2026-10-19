"""`comparacao.serie`: gasto (DESPESA) acumulado por dia das duas janelas. Relógio e semente de `test_comparacao_periodos`
(hoje = 12/10/2026, cartão pela FATURA, closing_day 10). Controles por mutação do GRUPO: sem a checagem "última == total"
o caso da outra moeda fica vermelho; sem o preso em [0, N-1] ficam vermelhos o 1 e o 3; sem o corte em hoje, o `len(atual) == 15`.
Os casos 1 a 4 são o controle positivo: a checagem não recusa a série legítima."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from conftest import usuario_pagante
from core.services import comparacao_periodos as cp
from tests.test_api_v2_gastos import _l
from tests.test_comparacao_periodos import AGORA, _relogio, semeia  # noqa: F401 (fixture autouse)


def _esc(n: int, passos: dict[int, int]) -> list[D]:
    """Série acumulada de `n` posições a partir de `{posição: valor do dia}`."""
    acc, out = D(0), []
    for i in range(n):
        acc += passos.get(i, 0)
        out.append(D(acc))
    return out


# outubro: 70 + parcela de 100 (compras anteriores à janela, na fatura 10/10) prendem no dia 1; a compra de 999 (15/10) é da fatura de novembro
ATUAL = {0: 170, 2: 600, 3: 45, 4: 300, 5: 20, 7: 100, 14: 777}
# setembro: parcela de 100 (compra de agosto, fatura 10/09) no dia 1; 150 de 03/09 na posição 2
ANTERIOR = {0: 100, 1: 1000, 2: 250, 7: 200, 19: 1800}


def _serie(uid):
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    return r, r["serie"]


def test_ultima_posicao_e_o_total_e_a_forma_dos_dois_lados():
    uid = usuario_pagante("plus")
    semeia(uid)
    r, s = _serie(uid)
    assert s["atual"][-1] == r["despesa"]["total_atual"] == D("2012.00")
    assert s["anterior"][-1] == r["despesa"]["total_anterior"] == D("3350.00")
    assert len(s["anterior"]) == 30 and len(s["atual"]) == 15   # a linha para no degrau do dia 15, sem cauda até o dia 31
    assert s["atual"] == _esc(15, ATUAL) and s["anterior"] == _esc(30, ANTERIOR)


def test_dia_sem_lancamento_repete_o_acumulado_e_o_interno_fica_fora():
    uid = usuario_pagante("plus")
    semeia(uid)
    a = _serie(uid)[1]["atual"]
    assert a[5] == a[6] == a[7] - 100 == D("1135")   # 500 interno no dia 7 (posição 6) não entra
    assert a[8:14] == [a[7]] * 6


def test_cartao_anterior_a_janela_prende_no_dia_1_e_a_compra_da_fatura_seguinte_nao_entra():
    uid = usuario_pagante("plus")
    semeia(uid)
    s = _serie(uid)[1]
    assert s["atual"][0] == D("170")                          # 70 (20/09) + parcela de 100 (20/08), ambas na fatura de 10/10
    assert D("999") not in {b - a for a, b in zip(s["atual"], s["atual"][1:])}   # 999 (15/10) é da fatura de novembro
    assert s["anterior"][2] - s["anterior"][1] == D("250")    # 100 (lançamento de 03/09) + 150 (cartão de 03/09)
    assert s["anterior"][0] == D("100")


def test_outra_moeda_zera_a_serie_e_nao_mexe_no_total():
    uid = usuario_pagante("plus")
    semeia(uid)
    antes = cp.comparar_mes(uid, 2026, 10, AGORA)["despesa"]
    _l(uid, 50, date(2026, 10, 9), categoria="Viagem", moeda="USD")
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["serie"] is None and r["despesa"]["total_atual"] == antes["total_atual"] + 50   # o total legado soma a outra moeda; a série não


def test_periodo_livre_com_cartao_pela_data_nao_tem_serie():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_intervalos(uid, (date(2026, 10, 1), date(2026, 10, 12)), (date(2026, 9, 1), date(2026, 9, 12)), AGORA)
    assert r["despesa"] is not None and r["serie"] is None


def test_periodo_livre_so_com_lancamentos_nao_tem_serie():
    uid = usuario_pagante("plus")                  # sem cartão: só a guarda `por_compra` tira a série (controle da guarda)
    _l(uid, 10, date(2026, 10, 3), categoria="Mercado")
    _l(uid, 4, date(2026, 9, 3), categoria="Mercado")
    r = cp.comparar_intervalos(uid, (date(2026, 10, 1), date(2026, 10, 12)), (date(2026, 9, 1), date(2026, 9, 12)), AGORA)
    assert r["despesa"]["total_atual"] == D("10.00") and r["serie"] is None


def test_dois_bimestres_inteiros_pela_fatura_tem_dias_posicoes_e_fecham_no_total():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_intervalos(uid, (date(2026, 8, 1), date(2026, 9, 30)), (date(2026, 6, 1), date(2026, 7, 31)), AGORA, por_fatura=True)
    s = r["serie"]
    assert (len(s["atual"]), len(s["anterior"])) == (61, 61)
    assert r["despesa"]["total_atual"] > 0 and s["atual"][-1] == r["despesa"]["total_atual"] and s["anterior"][-1] == r["despesa"]["total_anterior"]


def test_isolamento_a_serie_de_um_usuario_nao_ve_o_outro():
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    semeia(a)
    antes = cp.comparar_mes(a, 2026, 10, AGORA)["serie"]
    semeia(b, fator=2)
    sb = cp.comparar_mes(b, 2026, 10, AGORA)["serie"]
    assert cp.comparar_mes(a, 2026, 10, AGORA)["serie"] == antes
    assert sb == {lado: [2 * v for v in antes[lado]] for lado in antes}


def test_falha_na_leitura_da_serie_nao_derruba_totais_nem_motivos(monkeypatch):
    import db.contas_hoje as contas
    uid = usuario_pagante("plus")
    semeia(uid)
    def motivos(cur, u, **k):
        cur.execute("select 1")                    # precisa de transação viva depois do erro da série
        return {"motivos": ["conciliacao_pendente"]}
    monkeypatch.setattr(contas, "listar", motivos)
    ref = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert ref["serie"] is not None and ref["motivos"] == ["conciliacao_pendente"]

    def quebra(cur, *a, **k):
        cur.execute("select 1/0")                  # erro de SQL: aborta a transação se não houver savepoint

    monkeypatch.setattr(cp.lancamentos, "listar", quebra)
    r = cp.comparar_mes(uid, 2026, 10, AGORA)      # os motivos são lidos DEPOIS: sem o savepoint, "transaction is aborted"
    assert r["serie"] is None and r["despesa"] == ref["despesa"] and r["motivos"] == ["conciliacao_pendente"]
    assert r["categorias"] == ref["categorias"]
