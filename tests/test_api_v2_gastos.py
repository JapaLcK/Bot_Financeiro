"""`GET /api/v2/gastos` (`db/gastos.py`, "Para onde vai", visão do mês) com Postgres real.

Σ categorias = Saiu do Resumo (mesmas linhas do `/lancamentos`, sem interno e sem outra moeda), o contador
de saídas em outra moeda derivado, "sem categoria" num balde só, o catálogo com fallback neutro (e o seed
antes de ler), o corte do plano (comparação `null`, linha fora da grade, mês inteiro indisponível), o
corte por dia (31/03 contra fevereiro; fevereiro contra janeiro), os 3 maiores do dia, o arredondamento,
uma conexão só e o isolamento A × B, e o contrato com o app nativo (`/api/app/gastos`, "Seu dia": fixture e campos lidos
pelo zod do app, total do dia igual ao `saiu` do `/mes-detalhes`). Cada correção tem o seu controle NEGATIVO medido à mão (a linha do
conserto desligada em `db/gastos.py`) e reportado no PR; o positivo mora no mesmo teste.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

import db
import db.gastos as gastos
from api.v2.gastos import GastoCategoria, GastoDia, GastoDiaCategoria, GastoMaior, Gastos
from conftest import usuario_pagante
from db import resumo_mes
from db.categories import ensure_user_categories_seeded
from db.connection import CAT_NEUTRA_COR, CAT_NEUTRA_EMOJI, get_conn
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_app_sessao import cliente, completo
from tests.test_api_v2_lancamentos import pede
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import FIM, INICIO, SAIU, _lanc, semeia_a, semeia_b
from utils_date import _tz, now_tz

D = Decimal
M = f"{INICIO:%Y-%m}"
AGORA_REAL = now_tz()


def _ler(uid, agora=None, ano=INICIO.year, mes=INICIO.month):
    return gastos.ler_mes(uid, ano, mes, agora or AGORA_REAL)


def _l(uid, valor, dia: date, *, categoria=None, alvo=None, moeda=None, tipo="despesa", hora=12, **kw):
    """Lançamento direto em `dia`, às `hora` (mais de um no dia: a ordem do `listar` é a hora)."""
    criado = datetime.combine(dia, time(hora), tzinfo=_tz())
    lid = q("""insert into launches (user_id, tipo, valor, criado_em, categoria, alvo, source)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (uid, tipo, valor, criado, categoria, alvo, kw.get("source", "manual")))["id"]
    if moeda:
        q("update launches set currency = %s where id = %s returning id", (moeda, lid))
    return lid


def _por_chave(r):
    return {c["chave"]: c for c in r["categorias"]}


def _somas_fecham(r):
    assert sum(c["valor"] for c in r["categorias"]) == r["total"]
    assert sum(d["total"] for d in r["dias"]) + r["fora_da_grade"]["valor"] == r["total"]


# ── 1 soma ───────────────────────────────────────────────────────────────────

def test_soma_das_categorias_e_do_calendario_e_o_saiu_do_resumo():
    a = usuario_pagante()
    semeia_a(a)
    r = _ler(a)
    assert r["disponivel"] and r["total"] == SAIU == resumo_mes.totais_do_mes(a, INICIO)["saiu"]
    _somas_fecham(r)
    # a parcela 1/3: compra de 20 do mês anterior, fatura do mês; fora da grade mas dentro do total
    assert r["fora_da_grade"] == {"quantidade": 1, "valor": D(100)}
    assert sum(c["quantidade"] for c in r["categorias"]) == 8  # o interno de 500 ficou de fora
    assert r["saidas_em_outra_moeda"] == 0 and "outra_moeda" not in r["motivos"]


# ── 2 moeda ──────────────────────────────────────────────────────────────────

def test_entrada_e_guardado_em_dolar_nao_contam_como_saida_em_outra_moeda():
    a = usuario_pagante()
    _l(a, 500, INICIO.replace(day=3), tipo="receita", moeda="USD")
    _l(a, 200, INICIO.replace(day=3), tipo="deposito_caixinha", moeda="USD")
    _l(a, 10, INICIO.replace(day=4), categoria="mercado")
    r = _ler(a)
    assert r["saidas_em_outra_moeda"] == 0 and "outra_moeda" not in r["motivos"] and r["total"] == D(10)


def test_saida_em_dolar_fica_fora_conta_e_levanta_o_motivo():
    a = usuario_pagante()
    _l(a, 100, INICIO.replace(day=3), categoria="mercado", moeda="USD")
    _l(a, 10, INICIO.replace(day=4), categoria="mercado")
    r = _ler(a)
    assert r["saidas_em_outra_moeda"] == 1 and "outra_moeda" in r["motivos"]
    assert r["total"] == D(10) != resumo_mes.totais_do_mes(a, INICIO)["saiu"] == D(110)


# ── 3 sem categoria ──────────────────────────────────────────────────────────

def _cartao_sem_categoria(uid, valor, dia):
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, valor, None, "x", dia)


def test_sem_categoria_e_um_balde_so_com_o_visual_do_catalogo():
    a = usuario_pagante()
    ensure_user_categories_seeded(a)  # antes: o 1º seed importaria "Sem Categoria" dos lançamentos
    q("insert into user_categories (user_id, name, emoji, color) values (%s, 'Sem categoria', '🤷', '#123456') returning id", (a,))
    _l(a, 10, INICIO.replace(day=3))
    _l(a, 20, INICIO.replace(day=3), categoria="Sem Categoria")
    _cartao_sem_categoria(a, 5, INICIO.replace(day=5))
    r = _ler(a)
    assert [c["chave"] for c in r["categorias"]] == ["sem categoria"]
    c = r["categorias"][0]
    assert (c["valor"], c["quantidade"], c["nome"], c["emoji"], c["cor"]) == (D(35), 3, "Sem categoria", "🤷", "#123456")


def test_sem_categoria_sem_catalogo_usa_a_constante_neutra():
    a = usuario_pagante()
    _l(a, 10, INICIO.replace(day=3))
    _cartao_sem_categoria(a, 5, INICIO.replace(day=5))
    c, = _ler(a)["categorias"]
    assert (c["chave"], c["nome"], c["valor"], c["emoji"], c["cor"]) == (
        "sem categoria", "sem categoria", D(15), CAT_NEUTRA_EMOJI, CAT_NEUTRA_COR)


# ── 4 catálogo ───────────────────────────────────────────────────────────────

def test_categoria_fora_do_catalogo_sai_neutra_e_no_primeiro_acesso_o_seed_vem_antes():
    a = usuario_pagante()
    assert q("select count(*) as n from user_categories where user_id = %s", (a,))["n"] == 0
    _l(a, 10, INICIO.replace(day=3), categoria="alimentação")
    _l(a, 20, INICIO.replace(day=4), categoria="viagem a lisboa")
    por = _por_chave(_ler(a))  # 1º acesso: nenhum /categorias antes
    assert (por["alimentacao"]["emoji"], por["alimentacao"]["cor"]) == ("🍔", "#f59e0b")
    # fora do catálogo o seed importa só o que já estava nos lançamentos: aqui entrou depois
    _l(a, 5, INICIO.replace(day=5), categoria="categoria nova")
    nova = _por_chave(_ler(a))["categoria nova"]
    assert (nova["nome"], nova["emoji"], nova["cor"]) == ("categoria nova", CAT_NEUTRA_EMOJI, CAT_NEUTRA_COR)


# ── 5 A × B ──────────────────────────────────────────────────────────────────

def test_b_nao_aparece_para_a_nem_empresta_o_visual():
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    ensure_user_categories_seeded(b)
    q("update user_categories set emoji = '🥑' where user_id = %s and name = 'alimentação' returning id", (b,))
    _l(a, 10, INICIO.replace(day=3), categoria="alimentação")
    _l(b, 777, INICIO.replace(day=3), categoria="alimentação")
    ra, rb = _ler(a), _ler(b)
    assert ra["total"] == SAIU + 10  # nada dos 7777 / 777 / 888 do B
    assert _por_chave(ra)["alimentacao"]["emoji"] == "🍔" and _por_chave(rb)["alimentacao"]["emoji"] == "🥑"
    assert rb["total"] == D(7777 + 888 + 777)


# ── 6 corte do plano ─────────────────────────────────────────────────────────

def test_corte_no_meio_do_mes_nao_compara_e_manda_a_linha_antiga_para_fora_da_grade(monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    corte = INICIO.replace(day=5)
    of = _l(a, 25, INICIO.replace(day=9), source="open_finance")
    q("update launches set posted_at = %s where id = %s returning id", (INICIO.replace(day=2), of))
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: corte)
    r = _ler(a)
    assert all(c["anterior_ate_corte"] is None for c in r["categorias"])
    assert "inicio_do_historico" in r["motivos"] and r["historico_desde"] == corte.isoformat()
    # a parcela 1/3 (compra no mês anterior, fatura do mês) e a linha com posted_at antes do corte
    assert r["fora_da_grade"] == {"quantidade": 2, "valor": D(125)}
    with get_conn() as conn, conn.cursor() as cur:
        assert r["total"] == resumo_mes.totais(cur, a, corte, FIM)["saiu"] == D(385)  # 360 do plano + os 25
    _somas_fecham(r)
    assert all(d["dia"] >= corte.isoformat() for d in r["dias"])


def test_corte_dentro_do_mes_anterior_deixa_o_anterior_null(monkeypatch):
    a = usuario_pagante()
    _l(a, 10, INICIO.replace(day=3), categoria="mercado")
    _l(a, 40, INICIO - timedelta(days=3), categoria="mercado")  # depois do corte, no mês anterior
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: INICIO - timedelta(days=10))
    r = _ler(a)
    assert r["categorias"][0]["anterior_ate_corte"] is None and r["historico_desde"] is None
    assert "inicio_do_historico" in r["motivos"] and r["total"] == D(10)
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: None)  # positivo
    r = _ler(a)
    assert r["categorias"][0]["anterior_ate_corte"] == D(40) and "inicio_do_historico" not in r["motivos"]


# ── 7 mês inteiro antes do corte ─────────────────────────────────────────────

def test_mes_inteiro_antes_do_corte_e_indisponivel_nunca_zero(monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    corte = FIM + timedelta(days=3)
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: corte)
    r = _ler(a)
    assert r["disponivel"] is False and r["total"] is None
    assert r["categorias"] == [] and r["dias"] == [] and r["fora_da_grade"] == {"quantidade": 0, "valor": D(0)}
    assert r["historico_desde"] == corte.isoformat() and "inicio_do_historico" in r["motivos"]
    Gastos.model_validate(r)


# ── 8 corte por dia ──────────────────────────────────────────────────────────

def _agora(ano, mes, dia):
    return datetime(ano, mes, dia, 12, tzinfo=_tz())


def test_corte_por_dia_corrente_31_de_marco_contra_fim_de_fevereiro(monkeypatch):
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: None)
    a = usuario_pagante()
    _l(a, 100, date(2027, 3, 10), categoria="mercado")
    _l(a, 40, date(2027, 2, 28), categoria="mercado")
    _l(a, 10, date(2027, 2, 5), categoria="mercado")
    c, = gastos.ler_mes(a, 2027, 3, _agora(2027, 3, 31))["categorias"]
    assert (c["valor_ate_corte"], c["anterior_ate_corte"]) == (D(100), D(50))


def test_dia_futuro_do_mes_corrente_entra_no_valor_e_nao_no_corte(monkeypatch):
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: None)
    a = usuario_pagante()
    _l(a, 100, date(2027, 3, 10), categoria="mercado")
    _l(a, 30, date(2027, 3, 20), categoria="mercado")  # agendado
    _l(a, 5, date(2027, 2, 10), categoria="mercado")
    _l(a, 7, date(2027, 2, 20), categoria="mercado")   # depois do mesmo dia do mês anterior
    r = gastos.ler_mes(a, 2027, 3, _agora(2027, 3, 15))
    c, = r["categorias"]
    assert r["dia_de_corte"] == 15 and r["hoje"] == "2027-03-15"
    assert (c["valor"], c["valor_ate_corte"], c["anterior_ate_corte"]) == (D(130), D(100), D(5))


def test_fevereiro_passado_contra_janeiro_conta_os_dias_29_a_31(monkeypatch):
    monkeypatch.setattr(gastos, "history_earliest_date", lambda uid, agora=None: None)
    a = usuario_pagante()
    _l(a, 20, date(2027, 2, 27), categoria="mercado")
    _l(a, 1, date(2027, 1, 2), categoria="mercado")
    _l(a, 9, date(2027, 1, 31), categoria="mercado")
    r = gastos.ler_mes(a, 2027, 2, _agora(2027, 3, 15))
    c, = r["categorias"]
    assert r["dia_de_corte"] == 28 and (c["valor_ate_corte"], c["anterior_ate_corte"]) == (D(20), D(10))


# ── 9 maiores do dia ─────────────────────────────────────────────────────────

def test_tres_maiores_do_dia_desempatam_pelo_mais_recente_e_descricao_nunca_nula():
    a = usuario_pagante()
    dia = INICIO.replace(day=10)
    for hora, valor, alvo in ((8, 10, "a10"), (9, 30, "b30"), (10, 30, "c30"), (11, 20, "d20")):
        _l(a, valor, dia, alvo=alvo, categoria="mercado", hora=hora)
    _l(a, 5, INICIO.replace(day=11), categoria="mercado")  # despesa antiga, alvo NULL
    r = _ler(a)
    d10, d11 = r["dias"]
    assert [(m["descricao"], m["valor"]) for m in d10["maiores"]] == [("c30", D(30)), ("b30", D(30)), ("d20", D(20))]
    assert d10["total"] == D(90) and d10["maiores"][0]["categoria"] == "mercado"
    assert d11["maiores"][0]["descricao"] == "Sem descrição"


# ── 10 arredondamento ────────────────────────────────────────────────────────

def _http(quem, **params):
    r = pede(quem, "/api/v2/gastos", **params)
    assert r.status_code == 200, r.text
    return Gastos.model_validate(r.json())


def test_arredondamento_por_grupo_levanta_o_motivo_e_o_mesmo_grupo_nao(libera):
    a, b = usuario_pagante(), usuario_pagante()
    dia = INICIO.replace(day=10)
    _l(a, D("1.005"), dia, categoria="mercado")
    _l(a, D("1.005"), dia, categoria="lazer")
    _l(b, D("1.005"), dia, categoria="mercado")
    _l(b, D("1.005"), dia, categoria="mercado")
    libera(a, b)
    assert "arredondamento_por_grupo" in _http(a, mes=M).motivos
    assert "arredondamento_por_grupo" not in _http(b, mes=M).motivos  # positivo: mesma categoria e dia


# ── 11 motivos de linha ──────────────────────────────────────────────────────

def test_motivos_de_linha_conciliacao_pendente_e_transacao_pendente_do_banco():
    a = usuario_pagante()
    semeia_a(a)
    assert "conciliacao_pendente" in _ler(a)["motivos"]
    b = usuario_pagante()
    lid = _l(b, 30, INICIO.replace(day=8), source="open_finance")
    acc = conta(conexao(b, f"item-{b}"), "acc-b", "1000")
    tx = tx_banco(acc, "tx-pend", "-30", imported_launch_id=lid, raw=Jsonb({"status": "PENDING"}))
    assert "transacao_pendente" in _ler(b)["motivos"]
    q("update open_finance_transactions set raw = %s where id = %s returning id", (Jsonb({"status": "POSTED"}), tx))
    assert "transacao_pendente" not in _ler(b)["motivos"]  # positivo


# ── 12 guardado ──────────────────────────────────────────────────────────────

def test_guardado_nunca_entra_nas_categorias():
    a = usuario_pagante()
    semeia_a(a)  # já tem um deposito_caixinha de 200
    _l(a, 300, INICIO.replace(day=6), tipo="aporte_investimento")
    r = _ler(a)
    assert r["total"] == SAIU and sum(c["quantidade"] for c in r["categorias"]) == 8


# ── 13 uma conexão só ────────────────────────────────────────────────────────

def test_uma_conexao_so_para_os_dois_meses(monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    _l(a, 10, INICIO - timedelta(days=5), categoria="mercado")
    ensure_user_categories_seeded(a)  # o seed abre a dele (escreve): fica fora da conta
    abertas = []
    real = gastos.get_conn
    monkeypatch.setattr(gastos, "get_conn", lambda *x, **k: abertas.append(1) or real(*x, **k))
    r = _ler(a)
    assert len(abertas) == 1 and r["categorias"][0]["anterior_ate_corte"] is not None


# ── 14 validação do mês e rota ───────────────────────────────────────────────

@pytest.mark.parametrize("mes", ["2999-01", "2026-13", "26-01", "abcd-ef"])
def test_mes_futuro_ou_fora_do_formato_e_422(libera, mes):
    a = usuario_pagante()
    libera(a)
    assert pede(a, "/api/v2/gastos", mes=mes).status_code == 422


def test_rota_devolve_dinheiro_como_texto_e_isola_o_usuario(libera):
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    libera(a, b)
    corpo = pede(a, "/api/v2/gastos", mes=M, user_id=b).json()
    g = Gastos.model_validate(corpo)
    assert g.total == SAIU and isinstance(corpo["total"], str) and isinstance(corpo["categorias"][0]["valor"], str)
    assert g.motivos == ["conciliacao_pendente"] and g.historico_desde is None
    assert D(pede(b, "/api/v2/gastos", mes=M).json()["total"]) == D(8665)


# ── 15 contrato com o app nativo (`/api/app/gastos`, "Seu dia") ──────────────
# O zod do app (`app/src/api/schemas/painel.ts::gastosSchema`) lê SÓ alguns campos; o Pydantic ignora campo a mais, então a fixture
# validar não basta: o servidor REMOVER `quantidade` deixaria a fixture válida e o CI verde. Daí a lista de campos lidos (JSON
# versionado ao lado da fixture, conferido contra o zod pelo jest do app) contra `model_fields` de CADA modelo aninhado.

FIXTURES = Path(__file__).parent / "fixtures"
APP_GASTOS = json.loads((FIXTURES / "app_gastos.json").read_text(encoding="utf-8"))
LIDOS = json.loads((FIXTURES / "app_gastos_campos_lidos.json").read_text(encoding="utf-8"))
MODELOS = {"Gastos": Gastos, "GastoDia": GastoDia, "GastoMaior": GastoMaior,
           "GastoDiaCategoria": GastoDiaCategoria, "GastoCategoria": GastoCategoria}


def test_fixture_do_app_e_exatamente_o_que_o_gastos_do_pydantic_devolve():
    g = Gastos.model_validate(APP_GASTOS)
    assert json.loads(g.model_dump_json()) == APP_GASTOS  # dinheiro em texto e na escala original ("1.137", "0.010", "91.010")
    d = APP_GASTOS["dias"][0]
    for nivel, modelo in ((APP_GASTOS, Gastos), (d, GastoDia), (d["maiores"][0], GastoMaior),
                          (d["categorias"][0], GastoDiaCategoria), (APP_GASTOS["categorias"][0], GastoCategoria)):
        assert set(nivel) == set(modelo.model_fields), modelo.__name__  # nem campo a menos nem a mais na fixture


def test_campos_que_o_zod_do_app_le_existem_em_cada_modelo_aninhado():
    assert set(LIDOS) == set(MODELOS)
    for nome, campos in LIDOS.items():
        assert set(campos) <= set(MODELOS[nome].model_fields), (nome, set(campos) - set(MODELOS[nome].model_fields))


def test_total_do_dia_de_gastos_bate_com_o_saiu_do_dia_de_mes_detalhes():
    """A linha que o app toca (`/mes-detalhes`) e o dia da folha (`/gastos`) dizem o mesmo número, dia a dia, na grade."""
    a = completo(usuario_pagante())
    semeia_a(a)
    c = cliente(a)
    g, md = c.get("/api/app/gastos", params={"mes": M}), c.get("/api/app/mes-detalhes", params={"mes": M})
    assert g.status_code == md.status_code == 200, (g.text, md.text)
    por_dia = {x["dia"]: D(x["total"]) for x in g.json()["dias"]}
    saiu = {x["dia"]: D(x["saiu"]) for x in md.json()["dias"] if D(x["saiu"]) > 0}
    assert por_dia and por_dia == {k: v for k, v in saiu.items() if k.startswith(M)}
    # o que o calendário nativo tem a mais é o dia de outro mês (a parcela): fora da grade do /gastos, dentro do total
    assert sum(v for k, v in saiu.items() if not k.startswith(M)) == D(g.json()["fora_da_grade"]["valor"]) > 0
