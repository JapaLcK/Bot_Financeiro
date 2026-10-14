"""`GET /api/v2/gastos`: o total do mês anterior, a descrição/visual saneados e as bordas do corte.

Complementa `test_api_v2_gastos.py` (mesmos helpers). Cada teste carrega o controle negativo medido à mão
(a linha de `db/gastos.py`, `api/v2/gastos.py` ou `db/connection.py` indicada, mutada, deixa o teste
vermelho) e o positivo no mesmo teste.
"""
from __future__ import annotations

import importlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

import db
import db.gastos as gastos
from api.v2.gastos import Gastos
from conftest import usuario_pagante
from db.categories import ensure_user_categories_seeded
from db.connection import CAT_NEUTRA_COR, CAT_NEUTRA_EMOJI
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_gastos import M, _agora, _http, _l, _ler, _por_chave
from tests.test_api_v2_lancamentos import pede
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import FIM, INICIO

D = Decimal


@pytest.fixture
def corte(monkeypatch):
    def _corte(dia=None):
        monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: dia)
    _corte()
    return _corte


def _marco(uid):
    return gastos.ler_mes(uid, 2027, 3, _agora(2027, 3, 15))


# ── A: o total do anterior inclui o que só existe lá ─────────────────────────

def test_anterior_total_soma_tudo_do_anterior_ate_o_corte_e_a_lista_nao_ganha_categoria(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    _l(a, 500, date(2027, 2, 10), categoria="viagem")                 # só existe no anterior
    _l(a, 40, date(2027, 2, 12), categoria="mercado")
    _l(a, 7, date(2027, 2, 20), categoria="mercado")                  # depois do mesmo dia (15/02)
    _l(a, 100, date(2027, 2, 5), categoria="mercado", moeda="USD")    # outra moeda: fora
    r = _marco(a)
    c, = r["categorias"]
    assert (c["chave"], c["anterior_ate_corte"]) == ("mercado", D(40))
    assert r["anterior_total_ate_corte"] == D(540) != c["anterior_ate_corte"]
    Gastos.model_validate(r)
    corte(date(2027, 2, 10))  # positivo: o corte toca o anterior -> null
    r = _marco(a)
    assert r["anterior_total_ate_corte"] is None and r["categorias"][0]["anterior_ate_corte"] is None


def test_anterior_total_null_quando_o_mes_nao_esta_disponivel(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 2, 3), categoria="mercado")
    corte(date(2027, 4, 1))
    r = _marco(a)
    assert r["disponivel"] is False and r["anterior_total_ate_corte"] is None
    Gastos.model_validate(r)


# ── B: descrição só com espaços ──────────────────────────────────────────────

def test_descricao_so_com_espacos_vira_sem_descricao(corte):
    a = usuario_pagante()
    _l(a, 10, INICIO.replace(day=3), alvo="   ", categoria="mercado")
    cartao = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(a, cartao, 5, "mercado", "   ", INICIO.replace(day=5))
    _l(a, 1, INICIO.replace(day=6), alvo=" feira ", categoria="mercado")  # positivo: o texto real passa
    r = _ler(a)
    assert [m["descricao"] for d in r["dias"] for m in d["maiores"]] == [
        "Sem descrição", "Sem descrição", "feira"]


# ── C: emoji e cor do catálogo nunca vazios nem inválidos ────────────────────

def test_cor_e_emoji_invalidos_do_catalogo_saem_neutros_em_gastos_e_categorias(libera, corte):
    a = usuario_pagante()
    ensure_user_categories_seeded(a)
    esperado = {"vermelha": ("🥑", CAT_NEUTRA_COR), "vazia": (CAT_NEUTRA_EMOJI, CAT_NEUTRA_COR),
                "emojivazio": (CAT_NEUTRA_EMOJI, "#ABCDEF"), "curta": ("🥑", CAT_NEUTRA_COR),
                "boa": ("🥑", "#aBcDeF")}  # a última é o positivo: válida passa intacta
    # regex sem âncora de fim, `$` (casa antes do \n), `match` em vez de `fullmatch`, `#` opcional
    extras = (("sete", "#1234567"), ("oito", "#12345678"), ("quebrafim", "#aBcDeF\n"), ("quebraini", "\n#aBcDeF"),
              ("espaco", "#aBcDeF "), ("cr", "#aBcDeF\r"), ("semhash", "ABCDEF"))
    esperado.update({nome: ("🥑", CAT_NEUTRA_COR) for nome, _ in extras})
    for nome, emoji, cor in (("vermelha", "🥑", "red"), ("vazia", "   ", ""), ("emojivazio", "", "#ABCDEF"),
                             ("curta", "🥑", "#12345"), ("boa", "🥑", "#aBcDeF"),
                             *((nome, "🥑", cor) for nome, cor in extras)):
        q("insert into user_categories (user_id, name, emoji, color) values (%s, %s, %s, %s) returning id",
          (a, nome, emoji, cor))
        _l(a, 10, INICIO.replace(day=3), categoria=nome)
    libera(a)
    por = _por_chave(_ler(a))
    cats = {c["chave"]: c for c in pede(a, "/api/v2/categorias").json()["categorias"]}
    for nome, visual in esperado.items():
        assert (por[nome]["emoji"], por[nome]["cor"]) == visual, ("gastos", nome)
        assert (cats[nome]["emoji"], cats[nome]["cor"]) == visual, ("categorias", nome)


# ── E: bordas do corte e do calendário ───────────────────────────────────────

def test_corte_no_primeiro_dia_do_mes_seguinte_e_indisponivel_e_no_ultimo_dia_nao(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    corte(date(2027, 4, 1))
    assert _marco(a)["disponivel"] is False
    corte(date(2027, 3, 31))  # positivo
    assert _marco(a)["disponivel"] is True


def test_corte_no_primeiro_dia_do_anterior_ainda_compara_e_um_dia_depois_nao(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    _l(a, 40, date(2027, 2, 12), categoria="mercado")
    corte(date(2027, 2, 1))
    r = _marco(a)
    assert r["anterior_total_ate_corte"] == D(40) and "inicio_do_historico" not in r["motivos"]
    corte(date(2027, 2, 2))
    r = _marco(a)
    assert r["anterior_total_ate_corte"] is None and "inicio_do_historico" in r["motivos"]


def test_historico_desde_so_quando_o_corte_cai_depois_do_dia_1_do_mes_pedido(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    corte(date(2027, 3, 1))
    assert _marco(a)["historico_desde"] is None
    corte(date(2027, 3, 2))
    assert _marco(a)["historico_desde"] == "2027-03-02"


def test_janeiro_compara_com_dezembro_do_ano_anterior(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 1, 3), categoria="mercado")
    _l(a, 40, date(2026, 12, 31), categoria="mercado")
    r = gastos.ler_mes(a, 2027, 1, _agora(2027, 2, 15))
    assert r["categorias"][0]["anterior_ate_corte"] == D(40) == r["anterior_total_ate_corte"]


def test_categorias_empatam_pela_chave_e_nao_pela_ordem_do_dia(corte):
    a = usuario_pagante()
    _l(a, 30, date(2027, 3, 2), categoria="meio")
    _l(a, 10, date(2027, 3, 3), categoria="alfa")
    _l(a, 10, date(2027, 3, 5), categoria="zeta")  # a mais recente vem primeiro do listar
    assert [c["chave"] for c in _marco(a)["categorias"]] == ["meio", "alfa", "zeta"]


def test_saida_com_dia_depois_do_mes_vai_para_fora_da_grade_e_nao_vira_dia(corte):
    a = usuario_pagante()
    _l(a, 10, INICIO.replace(day=3), categoria="mercado")
    futura = _l(a, 25, INICIO.replace(day=9), categoria="mercado", source="open_finance")
    q("update launches set posted_at = %s where id = %s returning id", (FIM + timedelta(days=5), futura))
    r = _ler(a)
    assert r["total"] == D(35) and r["fora_da_grade"] == {"quantidade": 1, "valor": D(25)}
    assert all(d["dia"] < FIM.isoformat() for d in r["dias"]) and [d["total"] for d in r["dias"]] == [D(10)]


def test_saida_no_primeiro_dia_do_mes_seguinte_vai_para_fora_da_grade_e_continua_no_total(corte):
    a = usuario_pagante()
    _l(a, 10, INICIO.replace(day=3), categoria="mercado")
    no_fim = _l(a, 25, INICIO.replace(day=9), categoria="mercado", source="open_finance")
    q("update launches set posted_at = %s where id = %s returning id",
      (datetime.combine(FIM, time(12), tzinfo=timezone.utc), no_fim))
    r = _ler(a)
    assert r["total"] == D(35) and r["fora_da_grade"] == {"quantidade": 1, "valor": D(25)}
    assert [d["total"] for d in r["dias"]] == [D(10)] and all(d["dia"] < FIM.isoformat() for d in r["dias"])


# ── E: motivos ───────────────────────────────────────────────────────────────

def test_arredondamento_que_so_diverge_no_calendario_levanta_o_motivo(libera, corte):
    a = usuario_pagante()
    _l(a, D("1.005"), INICIO.replace(day=10), categoria="mercado")
    _l(a, D("1.005"), INICIO.replace(day=11), categoria="mercado")  # um grupo só na categoria: 2,01 fecha
    libera(a)
    assert "arredondamento_por_grupo" in _http(a, mes=M).motivos


def test_motivos_saem_na_ordem_de_motivos_gastos(corte):
    """Direto no `ler_mes`: com `arredondamento_por_grupo` a rota reordena e esconderia a ordem daqui."""
    a = usuario_pagante()
    corte(INICIO.replace(day=5))
    lid = _l(a, 30, INICIO.replace(day=8), source="open_finance")
    acc = conta(conexao(a, f"item-{a}"), "acc-a", "1000")
    tx_banco(acc, "tx-pend", "-30", imported_launch_id=lid, raw=Jsonb({"status": "PENDING"}))
    _l(a, 100, INICIO.replace(day=12), categoria="mercado", moeda="USD")
    assert _ler(a)["motivos"] == ["inicio_do_historico", "transacao_pendente", "outra_moeda"]


def test_motivo_do_bloco_de_contas_sobe_e_o_da_carteira_nao_vaza(corte):
    velho, limpo = usuario_pagante(), usuario_pagante()
    conta(conexao(velho, f"item-{velho}", sync=datetime.now(timezone.utc) - timedelta(hours=72)), "acc-v", "10")
    for u in (velho, limpo):
        _l(u, 10, INICIO.replace(day=3), categoria="mercado")
    assert _ler(velho)["motivos"] == ["banco_desatualizado"]
    assert _ler(limpo)["motivos"] == []


def test_pendente_do_mes_anterior_levanta_o_motivo_do_mes_pedido(corte):
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    lid = _l(a, 30, date(2027, 2, 8), source="open_finance")
    acc = conta(conexao(a, f"item-{a}"), "acc-a", "1000")
    tx = tx_banco(acc, "tx-pend", "-30", imported_launch_id=lid, raw=Jsonb({"status": "PENDING"}))
    assert "transacao_pendente" in _marco(a)["motivos"]
    q("update open_finance_transactions set raw = %s where id = %s returning id", (Jsonb({"status": "POSTED"}), tx))
    assert "transacao_pendente" not in _marco(a)["motivos"]  # positivo


def test_os_dois_meses_saem_do_mesmo_snapshot(corte, monkeypatch):
    """Lançamento que entra no mês anterior DEPOIS de o atual ser lido não aparece (repeatable read)."""
    a = usuario_pagante()
    _l(a, 10, date(2027, 3, 3), categoria="mercado")
    _l(a, 40, date(2027, 2, 12), categoria="mercado")
    real, vezes = gastos._mes, []

    def espia(cur, uid, ano, mes, corte_):
        r = real(cur, uid, ano, mes, corte_)
        if not vezes:
            vezes.append(1)
            _l(a, 999, date(2027, 2, 13), categoria="mercado")  # outra conexão, já commitada
        return r
    monkeypatch.setattr(gastos, "_mes", espia)
    assert _marco(a)["anterior_total_ate_corte"] == D(40)


def test_motivos_gastos_deriva_das_duas_fontes(monkeypatch):
    """Todo motivo de `resumo_mes.MOTIVOS` e de `MOTIVOS_ITEM` está na lista da API. Negativo: um motivo
    novo em `MOTIVOS_ITEM`, com o módulo reimportado, tem de aparecer (lista copiada à mão o perderia)."""
    from db import lancamentos, resumo_mes
    assert set(resumo_mes.MOTIVOS) | set(lancamentos.MOTIVOS_ITEM) <= set(gastos.MOTIVOS_GASTOS)
    try:
        with monkeypatch.context() as m:
            m.setattr(lancamentos, "MOTIVOS_ITEM", (*lancamentos.MOTIVOS_ITEM, "motivo_novo"))
            assert "motivo_novo" in importlib.reload(gastos).MOTIVOS_GASTOS
    finally:
        importlib.reload(gastos)
