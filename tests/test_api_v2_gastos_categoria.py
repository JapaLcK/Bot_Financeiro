"""`GET /api/v2/gastos/categoria` (`db/gastos.py::ler_categoria`, `db/estabelecimentos.py`) com Postgres real.

O detalhe de UMA categoria do mês, das MESMAS linhas do `/gastos`: total, compras, valor médio, últimas,
série de 3 meses (null no mês que o corte do plano toca) e os 5 estabelecimentos pela chave do `raw` do
Open Finance (CNPJ, recebedor, descrição). Cada correção carrega o controle NEGATIVO medido à mão (o
conserto desligado em `db/gastos.py` / `db/estabelecimentos.py`, vermelho) e o positivo no mesmo teste.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

import db
import db.gastos as gastos
from api.v2.gastos import GastosDaCategoria
from conftest import usuario_pagante
from db import CATEGORY_NAME_MAX_LEN
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_app_sessao import cliente, completo
from tests.test_api_v2_gastos import AGORA_REAL, M, _agora, _l
from tests.test_api_v2_gastos_bordas import corte  # noqa: F401 (fixture)
from tests.test_api_v2_lancamentos import pede
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import FIM, INICIO

D = Decimal
URL = "/api/v2/gastos/categoria"


def _cat(uid, categoria="mercado", agora=None, ano=INICIO.year, mes=INICIO.month):
    return gastos.ler_categoria(uid, ano, mes, categoria, agora or AGORA_REAL)


def _dia(n):
    return INICIO.replace(day=n)


def _antes(d: date, meses: int) -> date:
    for _ in range(meses):
        d = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
    return d


def _banco(uid, lid, ident, *, cnpj=None, recebedor=None, razao=None, raw=None, col="imported_launch_id"):
    """Transação do Open Finance ligada à linha `lid`, com o `raw` que a Pluggy mandaria."""
    if raw is None:
        raw = {}
        if cnpj is not None or razao:
            raw["merchant"] = {k: v for k, v in (("cnpj", cnpj), ("businessName", razao)) if v is not None}
        if recebedor is not None:
            raw["paymentData"] = {"receiver": {"name": recebedor}}
    acc = conta(conexao(uid, f"item-{uid}-{ident}"), f"acc-{uid}-{ident}", "1000")
    tx_banco(acc, f"tx-{uid}-{ident}", "-10", raw=Jsonb(raw), **{col: lid})
    return acc


def _nomes(r):
    return [(e["nome"], e["quantidade"]) for e in r["estabelecimentos"]]


# ── 1 soma ───────────────────────────────────────────────────────────────────

def test_total_e_serie_do_mes_sao_o_valor_da_mesma_chave_em_ler_mes():
    from tests.test_resumo_mes_regra import semeia_a
    a = usuario_pagante()
    semeia_a(a)
    mes = gastos.ler_mes(a, INICIO.year, INICIO.month, AGORA_REAL)
    assert len(mes["categorias"]) >= 3
    for c in mes["categorias"]:
        d = _cat(a, c["chave"])
        assert d["total"] == c["valor"] == d["serie"][2]["valor"] and d["compras"] == c["quantidade"]
        assert {k: d["categoria"][k] for k in ("chave", "nome", "emoji", "icone", "cor")} == {
            k: c[k] for k in ("chave", "nome", "emoji", "icone", "cor")}
        assert sum(x["total"] for x in d["dias"]) + d["fora_da_grade"]["valor"] == d["total"]
        assert all(m["categoria"] == c["chave"] for x in d["dias"] for m in x["maiores"])
        assert sum(e["valor"] for e in d["estabelecimentos"]) <= d["total"]
        assert sum(e["quantidade"] for e in d["estabelecimentos"]) <= d["compras"]
    assert any(d["fora_da_grade"]["quantidade"] for d in (_cat(a, c["chave"]) for c in mes["categorias"]))


# ── 2 isolamento ─────────────────────────────────────────────────────────────

def test_linhas_de_outro_usuario_na_mesma_categoria_nao_aparecem():
    a, b = usuario_pagante(), usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado", alvo="Padaria A")
    _l(b, 7777, _dia(3), categoria="mercado", alvo="Padaria B")
    ra, rb = _cat(a), _cat(b)
    assert (ra["total"], ra["compras"], _nomes(ra), [u["descricao"] for u in ra["ultimas"]]) == (
        D(10), 1, [("Padaria A", 1)], ["Padaria A"])
    assert (rb["total"], _nomes(rb)) == (D(7777), [("Padaria B", 1)])  # positivo


# ── 9 chave ──────────────────────────────────────────────────────────────────

def test_chave_casa_pela_regra_de_categoria_e_sem_categoria_junta_nulo_e_vazio():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="Alimentação")
    _l(a, 5, _dia(4), categoria="alimentacao")
    _l(a, 7, _dia(5), categoria=None)
    _l(a, 3, _dia(6), categoria="")
    assert _cat(a, "Alimentação") == _cat(a, "alimentacao") and _cat(a, "alimentacao")["total"] == D(15)
    assert _cat(a, "alimentacao")["categoria"]["chave"] == "alimentacao"
    sem = _cat(a, "Sem Categoria")
    assert (sem["total"], sem["compras"], sem["categoria"]["chave"]) == (D(10), 2, "sem categoria")
    xpto = _cat(a, "xpto")["categoria"]  # fora do catálogo: nome = chave, visual neutro
    assert (xpto["nome"], xpto["emoji"], xpto["cor"]) == ("xpto", db.connection.CAT_NEUTRA_EMOJI,
                                                        db.connection.CAT_NEUTRA_COR)


# ── 10 indisponível ──────────────────────────────────────────────────────────

def test_mes_todo_antes_do_corte_e_indisponivel_e_nunca_zero(corte):
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado")
    corte(FIM)
    r = _cat(a)
    assert not r["disponivel"] and (r["total"], r["compras"], r["valor_medio"]) == (None, None, None)
    assert r["ultimas"] == r["estabelecimentos"] == r["dias"] == []
    assert r["fora_da_grade"] == {"quantidade": 0, "valor": D(0)} and r["saidas_em_outra_moeda"] == 0
    assert [s["valor"] for s in r["serie"]] == [None] * 3 and r["motivos"] == ["inicio_do_historico"]
    assert r["serie"][2]["mes"] == f"{INICIO:%Y-%m}" and r["categoria"]["chave"] == "mercado"
    GastosDaCategoria.model_validate(r)


# ── 11 corte no meio ─────────────────────────────────────────────────────────

def test_corte_no_meio_do_mes_da_serie_anula_so_esse_mes(corte):
    a = usuario_pagante()
    for meses, valor in ((2, 3), (1, 5), (0, 7)):
        _l(a, valor, _antes(INICIO, meses).replace(day=5), categoria="mercado")
        _l(a, valor * 10, _antes(INICIO, meses).replace(day=25), categoria="mercado")
    corte(_antes(INICIO, 2).replace(day=15))
    r = _cat(a)
    assert [s["valor"] for s in r["serie"]] == [None, D(55), D(77)]
    assert "inicio_do_historico" in r["motivos"] and r["historico_desde"] is None and r["total"] == D(77)


def test_corte_no_meio_do_mes_pedido_deixa_total_parcial_e_serie_nula(corte):
    a = usuario_pagante()
    _l(a, 100, _dia(5), categoria="mercado")
    _l(a, 30, _dia(20), categoria="mercado")
    corte(_dia(10))
    r = _cat(a)
    assert (r["total"], r["compras"]) == (D(30), 1) and [s["valor"] for s in r["serie"]] == [None] * 3
    assert r["historico_desde"] == _dia(10).isoformat() and "inicio_do_historico" in r["motivos"]


# ── 12 valor médio ───────────────────────────────────────────────────────────

def test_valor_medio_arredonda_meio_centavo_para_cima():
    a, b = usuario_pagante(), usuario_pagante()
    for v in (50, 30, 20):
        _l(a, v, _dia(3), categoria="mercado")
    for v in ("0.02", "0.03"):
        _l(b, D(v), _dia(3), categoria="mercado")
    assert _cat(a)["valor_medio"] == D("33.33") and _cat(b)["valor_medio"] == D("0.03")  # HALF_EVEN daria 0.02


# ── 13 guardado e interno ────────────────────────────────────────────────────

def test_guardado_e_interno_ficam_fora_de_total_ultimas_e_estabelecimentos():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado", alvo="Padaria")
    _l(a, 200, _dia(4), categoria="mercado", alvo="Caixinha", tipo="deposito_caixinha")
    q("update launches set is_internal_movement = true where id = %s returning id",
      (_l(a, 500, _dia(5), categoria="mercado", alvo="Entre contas"),))
    r = _cat(a)
    assert (r["total"], r["compras"], _nomes(r), [u["descricao"] for u in r["ultimas"]]) == (
        D(10), 1, [("Padaria", 1)], ["Padaria"])


# ── 14 sem gasto ─────────────────────────────────────────────────────────────

def test_categoria_sem_gasto_e_zero_e_nunca_404():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="lazer")
    r = _cat(a)
    assert r["disponivel"] and (r["total"], r["compras"], r["valor_medio"]) == (D(0), 0, None)
    assert r["ultimas"] == r["estabelecimentos"] == r["dias"] == [] and [s["valor"] for s in r["serie"]] == [D(0)] * 3
    assert r["fora_da_grade"] == {"quantidade": 0, "valor": D(0)}


# ── 15 outra moeda ───────────────────────────────────────────────────────────

def test_saidas_em_outra_moeda_contam_so_a_categoria():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado")
    _l(a, 40, _dia(4), categoria="mercado", moeda="USD")
    _l(a, 90, _dia(4), categoria="casa")
    com, sem = _cat(a), _cat(a, "casa")
    assert (com["saidas_em_outra_moeda"], com["total"], "outra_moeda" in com["motivos"]) == (1, D(10), True)
    assert (sem["saidas_em_outra_moeda"], "outra_moeda" in sem["motivos"]) == (0, False)  # só o mês tem USD


# ── 16 mês corrente e passado ────────────────────────────────────────────────

def test_data_futura_entra_no_mes_corrente_e_o_mes_passado_tem_a_serie_inteira(corte):
    a = usuario_pagante()
    _l(a, 5, date(2027, 2, 5), categoria="mercado")
    _l(a, 100, date(2027, 3, 10), categoria="mercado")
    _l(a, 30, date(2027, 3, 20), categoria="mercado", alvo="Agendado")
    r = _cat(a, agora=_agora(2027, 3, 15), ano=2027, mes=3)
    assert (r["hoje"], r["total"], r["ultimas"][0]["descricao"], r["ultimas"][0]["data"]) == (
        "2027-03-15", D(130), "Agendado", "2027-03-20")
    assert [(s["mes"], s["valor"]) for s in r["serie"]] == [("2027-01", D(0)), ("2027-02", D(5)), ("2027-03", D(130))]
    p = _cat(a, agora=_agora(2027, 3, 15), ano=2027, mes=2)
    assert [s["mes"] for s in p["serie"]] == ["2026-12", "2027-01", "2027-02"] and None not in {s["valor"] for s in p["serie"]}


# ── 17 sem descrição ─────────────────────────────────────────────────────────

def test_alvo_nulo_vira_sem_descricao_em_ultimas_maiores_e_estabelecimento():
    a = usuario_pagante()
    _l(a, 5, _dia(3), categoria="mercado")
    r = _cat(a)
    assert r["ultimas"][0]["descricao"] == r["dias"][0]["maiores"][0]["descricao"] == "Sem descrição"
    assert _nomes(r) == [("Sem descrição", 1)]


# ── 18 ordem, limite e cartão ────────────────────────────────────────────────

def test_empate_dos_estabelecimentos_ultimas_limitadas_e_compra_de_cartao_do_banco():
    a = usuario_pagante()
    for n, (alvo, v) in enumerate((("Zeta", 10), ("Beta", 5), ("Beta", 5), ("Alfa", 10), ("Omega", 1)), 3):
        _l(a, v, _dia(n), categoria="mercado", alvo=alvo)
    assert _nomes(_cat(a)) == [("Beta", 2), ("Alfa", 1), ("Zeta", 1), ("Omega", 1)]  # valor, quantidade, nome
    b = usuario_pagante()
    for n in range(8):
        _l(b, 1 + n, _dia(3 + n), categoria="mercado", alvo=f"L{n}")
    ult = _cat(b)["ultimas"]
    assert len(ult) == 6 and [u["descricao"] for u in ult] == [f"L{n}" for n in (7, 6, 5, 4, 3, 2)]
    c = usuario_pagante()
    cartao = db.create_card(c, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(c, cartao, 40, "mercado", "iFood X", _dia(5))
    ct = q("select id from credit_transactions where user_id = %s", (c,))["id"]
    q("update credit_transactions set source = 'open_finance' where id = %s returning id", (ct,))
    _banco(c, ct, "ct", cnpj="12345678000195", col="imported_credit_tx_id")
    _banco(c, _l(c, 10, _dia(6), categoria="mercado", alvo="IFD*X", source="open_finance"), "l", cnpj="12345678000195")
    r = _cat(c)
    assert _nomes(r) == [("IFD*X", 2)]  # rótulo: empate, o mais recente
    assert {u["origem"] for u in r["ultimas"]} == {"cartao", "banco"}


# ── 19 uma transação ─────────────────────────────────────────────────────────

def test_uma_conexao_so_para_os_quatro_meses_e_os_estabelecimentos(monkeypatch):
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="mercado", alvo="Padaria")
    _l(a, 10, _antes(INICIO, 1).replace(day=3), categoria="mercado")
    abertas = []
    real = gastos.get_conn
    monkeypatch.setattr(gastos, "get_conn", lambda *x, **k: abertas.append(1) or real(*x, **k))
    r = _cat(a)
    assert len(abertas) == 1 and r["serie"][1]["valor"] == D(10) and _nomes(r) == [("Padaria", 1)]


# ── 20 e 21 rota ─────────────────────────────────────────────────────────────

def test_arredondamento_por_grupo_so_com_dias_diferentes(libera):
    a, b = usuario_pagante(), usuario_pagante()
    _l(a, D("1.005"), _dia(3), categoria="mercado")
    _l(a, D("1.005"), _dia(4), categoria="mercado")
    _l(b, D("1.005"), _dia(3), categoria="mercado")
    _l(b, D("1.005"), _dia(3), categoria="mercado")
    libera(a, b)
    assert "arredondamento_por_grupo" in pede(a, URL, mes=M, categoria="mercado").json()["motivos"]
    assert "arredondamento_por_grupo" not in pede(b, URL, mes=M, categoria="mercado").json()["motivos"]  # positivo


def test_rota_nos_dois_prefixos_e_validacao_da_entrada(libera, monkeypatch):
    a = completo(usuario_pagante())
    _l(a, 10, _dia(3), categoria="mercado")
    libera(a)
    r = pede(a, URL, mes=M, categoria="mercado", user_id=999)
    corpo = GastosDaCategoria.model_validate(r.json())
    assert r.status_code == 200 and corpo.total == D(10) and isinstance(r.json()["total"], str)
    ap = cliente(a).get("/api/app/gastos/categoria", params={"mes": M, "categoria": "mercado"})
    assert ap.status_code == 200 and ap.json() == r.json()
    for params in ({"mes": M}, {"mes": M, "categoria": ""}, {"mes": M, "categoria": "x" * (CATEGORY_NAME_MAX_LEN + 1)},
                   {"mes": "2999-01", "categoria": "mercado"}):
        assert pede(a, URL, **params).status_code == 422, params
    assert pede(a, URL, mes=M, categoria="x" * CATEGORY_NAME_MAX_LEN).status_code == 200  # positivo: no limite
