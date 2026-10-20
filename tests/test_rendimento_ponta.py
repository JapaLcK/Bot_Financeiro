"""Card `rendimento` (bloco `rendimentos`) com Postgres real: a foto-base, o isolamento e o contrato do payload.

`hoje` é injetado (`ler(..., hoje=)`): 20/10/2026, então o fim do mês passado é 30/09 e a janela da base é 24/09..30/09.
Controle negativo do isolamento (relato do PR): tirar `c.user_id = %(u)s` do `_DE` de `db/metas_ritmo.py` deixa
`test_isolamento_*` vermelho; reposto, verde. As regras de cada caso estão em `test_rendimentos_calculo.py`.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from psycopg.types.json import Jsonb

from api.nativo import rendimento as rota
from conftest import usuario_pagante
from db import investido
from db.connection import get_conn
from db.rendimento_contratado import ler
from test_api_app_sessao import completo
from tests._patrimonio_helpers import AGORA, conexao, posicao, q
from tests.test_api_app_complementos import leitura, snapshot, uid  # noqa: F401  (uid é fixture)

HOJE = date(2026, 10, 20)
FIM = date(2026, 9, 30)


def pos(cid, pid, saldo, aplicado=None, qtd=None, tipo="FIXED_INCOME", subtipo=None):
    iid = posicao(cid, pid, saldo, tipo=tipo, subtipo=subtipo)
    extra = {k: v for k, v in (("amountOriginal", aplicado), ("quantity", qtd)) if v is not None}
    q("update open_finance_investments set raw = raw || %s where id=%s", (Jsonb(extra), iid))
    return iid


def foto(cid, pid, dia, saldo, aplicado=None, qtd=None, confirmada=True):
    q("""insert into open_finance_investment_snapshots (connection_id, provider_investment_id, observed_on,
             observed_at, collection_confirmed, balance, amount_original, quantity)
         values (%s, %s, %s, %s, %s, %s, %s, %s)""", (cid, pid, dia, AGORA, confirmada, saldo, aplicado, qtd))


def rend(quem, hoje=HOJE):
    with get_conn() as conn, conn.cursor() as cur:
        r = ler(cur, quem, hoje=hoje)
        conn.rollback()
    return r["rendimentos"]


def mes_do(r):
    (c,) = r["categorias"]
    (a,) = c["ativos"]
    return a["mes"]


def test_foto_de_30_09_confirmada_vale_e_a_de_01_10_nao(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    foto(cid, "p", FIM, "1050", "1000")
    foto(cid, "p", date(2026, 10, 1), "5000", "1000")
    assert mes_do(rend(uid))["valor"] == D("50.00")
    assert rend(uid)["mes_desde"] == FIM


def test_30_09_nao_confirmada_cai_para_a_29_09_confirmada(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    foto(cid, "p", FIM, "1000", "1000", confirmada=False)
    foto(cid, "p", date(2026, 9, 29), "1040", "1000")
    assert mes_do(rend(uid))["valor"] == D("60.00")


def test_foto_antes_da_janela_e_sem_base(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    foto(cid, "p", date(2026, 9, 23), "1050", "1000")  # a janela começa em 24/09
    assert mes_do(rend(uid))["motivo"] == "sem_base"
    foto(cid, "p", date(2026, 9, 24), "1050", "1000")
    assert mes_do(rend(uid))["valor"] == D("50.00")


def test_reconexao_usa_a_foto_da_conexao_antiga(uid):
    antiga = conexao(uid, f"a-{uid}")
    pos(antiga, "p", "900", "800")
    foto(antiga, "p", FIM, "1050", "1000")
    nova = conexao(uid, f"c-{uid}")
    pos(nova, "p", "1100", "1000")  # a vencedora da identidade é a mais nova
    r = rend(uid)
    assert r["quantidade"] == 1 and mes_do(r)["valor"] == D("50.00")


def test_vencedora_pausada_fica_fora_e_a_foto_nao_conta(uid):
    antiga = conexao(uid, f"a-{uid}")
    pos(antiga, "p", "1100", "1000")
    foto(antiga, "p", FIM, "1050", "1000")
    pos(conexao(uid, f"c-{uid}", status="PAUSED"), "p", "1100", "1000")
    r = rend(uid)
    assert r["quantidade"] == 0 and r["categorias"] == [] and r["motivos"] == ["sem_posicoes"]


def test_isolamento_outro_usuario_com_o_mesmo_pid_nao_entra_na_base(uid):
    """B tem a MESMA identidade (provider_investment_id), conexão mais nova e foto mais recente: se o filtro de dono
    sumisse do recorte, a foto dele ganharia (observed_on desc) e o Mês de A mudaria."""
    ca = conexao(uid, f"a-{uid}")
    pos(ca, "pid-igual", "1100", "1000")
    foto(ca, "pid-igual", date(2026, 9, 28), "1050", "1000")
    outro = completo(usuario_pagante())
    cb = conexao(outro, f"b-{outro}")
    pos(cb, "pid-igual", "7777", "7000")
    foto(cb, "pid-igual", FIM, "9999", "9000")
    assert mes_do(rend(uid))["valor"] == D("50.00")
    rb = rend(outro)
    assert rb["saldo"] == D("7777.00") and mes_do(rb)["valor"] == (D("7777") - D("7000")) - (D("9999") - D("9000"))
    assert "pid-igual" not in str(rend(uid))


def test_acao_fica_em_rendimentos_e_fora_de_itens(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "acao", "1100", qtd="10", tipo="EQUITY", subtipo="STOCK")
    foto(cid, "acao", FIM, "1000", qtd="10")
    with get_conn() as conn, conn.cursor() as cur:
        r = ler(cur, uid, hoje=HOJE)
        conn.rollback()
    assert r["itens"] == [] and r["motivos"] == ["sem_posicoes_brl"]
    (c,) = r["rendimentos"]["categorias"]
    assert c["tipo"] == "acoes" and c["mes"]["valor"] == D("100.00") and c["total"]["valor"] is None


def test_contrato_http_acrescenta_so_o_bloco_e_o_saldo_bate_com_o_investido(uid):
    cid = conexao(uid, f"i-{uid}", banco="Nubank")
    pos(cid, "rf", "1100", "1000")
    pos(cid, "td", "500", "400", subtipo="TREASURY")
    snapshot(cid, "rf", "12.5", "PRE_FIXED")
    r = leitura(uid, "rendimento")
    assert set(r) == {"tipo", "itens", "motivos", "rendimentos"} and r["tipo"] == "contratado"
    assert set(r["itens"][0]) == {"nome", "instituicao", "taxa", "tipo_taxa", "observado_em", "motivos"}
    rr = r["rendimentos"]
    with get_conn() as conn, conn.cursor() as cur:
        total = investido.calcular(cur, uid)["total"]
        conn.rollback()
    assert rr["saldo"] == f"{total:.2f}" == "1600.00" and rr["quantidade"] == 2
    assert rr["total"]["valor"] == "200.00" and isinstance(rr["total"]["pct"], str)
    assert [c["tipo"] for c in rr["categorias"]] == ["renda_fixa", "tesouro"] and rr["motivos"] == []
    assert rr["categorias"][0]["ativos"][0]["saldo"] == "1100.00"
    assert rr["categorias"][0]["ativos"][0]["mes"]["motivo"] == "sem_base"


def test_saldo_ausente_o_saldo_do_topo_e_null_como_no_investido(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", None)
    rr = leitura(uid, "rendimento")["rendimentos"]
    with get_conn() as conn, conn.cursor() as cur:
        total = investido.calcular(cur, uid)["total"]
        conn.rollback()
    assert total is None and rr["saldo"] is None
    assert rr["categorias"][0]["ativos"][0]["total"]["motivo"] == "saldo_ausente"


def test_plano_sem_investimentos_esvazia_o_bloco_e_mantem_os_itens(uid, monkeypatch):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "rf", "1100", "1000")
    antes = leitura(uid, "rendimento")
    monkeypatch.setattr(rota, "plan_gate_ok", lambda u, f: f != "investments")
    r = leitura(uid, "rendimento")
    assert r["itens"] == antes["itens"] and r["motivos"] == antes["motivos"]
    assert r["rendimentos"]["motivos"] == ["plano_sem_investimentos"]
    assert r["rendimentos"]["categorias"] == [] and r["rendimentos"]["saldo"] is None
    assert antes["rendimentos"]["categorias"], "com o plano, o bloco vem cheio"


def test_sem_posicoes(uid):
    r = leitura(uid, "rendimento")
    assert r["itens"] == [] and r["rendimentos"]["motivos"] == ["sem_posicoes"]


def test_visao_do_modelo_sem_ativos_com_taxas_e_com_os_motivos_sem_dado(uid):
    from _cards_apoio import mostrar
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "rf", "1100", "1000")
    pos(cid, "acao", "500", qtd="5", tipo="EQUITY", subtipo="STOCK")
    snapshot(cid, "rf", "12.5", "PRE_FIXED")
    r, (bloco,), _ = mostrar(uid, card="rendimento")
    (v,) = r["abas"]
    assert set(v) == {"rendimentos", "taxas"} and "itens" not in v
    assert v["taxas"] == [{"nome": "CDB", "taxa": 12.5, "tipo_taxa": "PRE_FIXED"}]  # só nome, taxa e tipo
    rr = v["rendimentos"]
    assert all("ativos" not in c for c in rr["categorias"])
    assert rr["motivos_sem_dado"] == {"mes": ["sem_base"], "total": ["sem_custo"]}
    assert "ativos" in bloco["abas"][0]["recursos"]["rendimento"]["rendimentos"]["categorias"][0]


def test_base_a_foto_mais_nova_vence(uid):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    foto(cid, "p", date(2026, 9, 26), "1020", "1000")
    foto(cid, "p", date(2026, 9, 29), "1040", "1000")
    assert mes_do(rend(uid))["valor"] == D("60.00")  # (100) - (40); com a de 26/09 seria 80


def test_base_empate_no_mesmo_dia_vale_a_conexao_de_id_maior(uid):
    antiga = conexao(uid, f"a-{uid}")
    nova = conexao(uid, f"c-{uid}")
    pos(nova, "p", "1100", "1000")
    foto(antiga, "p", FIM, "1050", "1000")
    foto(nova, "p", FIM, "1040", "1000")
    assert mes_do(rend(uid))["valor"] == D("60.00")  # com a foto da antiga seria 50


@pytest.mark.parametrize("tipo,subtipo", [("MUTUAL_FUND", None), ("ETF", None), ("EQUITY", "REAL_ESTATE_FUND")])
def test_fundos_etf_fii_total_sem_custo_mesmo_com_aplicado(uid, tipo, subtipo):
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "f", "1100", "1000", qtd="10", tipo=tipo, subtipo=subtipo)
    foto(cid, "f", FIM, "1000", "900", qtd="10")
    (c,) = rend(uid)["categorias"]
    assert c["total"]["valor"] is None and c["ativos"][0]["total"]["motivo"] == "sem_custo"
    assert c["ativos"][0]["mes"]["valor"] == D("100.00")


def test_sincronizado_em_e_a_leitura_mais_antiga(uid):
    velha = AGORA - timedelta(hours=3)
    pos(conexao(uid, f"a-{uid}", sync=velha), "p1", "100", "90")
    pos(conexao(uid, f"b-{uid}"), "p2", "100", "90")
    assert rend(uid)["sincronizado_em"] == velha


def test_nenhuma_conexao_lida_o_saldo_do_topo_e_null_e_os_ativos_ficam(uid):
    pos(conexao(uid, f"a-{uid}", sync=None), "p", "100", "90")
    r = rend(uid)
    assert r["saldo"] is None and r["quantidade"] == 1


def test_logo_proprio_so_para_pluggy(uid):
    c1, c2 = conexao(uid, f"a-{uid}"), conexao(uid, f"b-{uid}")
    q("update open_finance_connections set institution_id='601' where id=%s", (c1,))
    q("update open_finance_connections set institution_id='601', provider='manual' where id=%s", (c2,))
    pos(c1, "p1", "200", "90")
    pos(c2, "p2", "100", "90")
    (c,) = rend(uid)["categorias"]
    assert {a["saldo"]: a["logo_proprio"] for a in c["ativos"]} == {D("200.00"): "/brand/bancos/60701190.png",
                                                                    D("100.00"): None}


def test_leitura_perto_da_meia_noite_conta_pelo_dia_de_sao_paulo(uid):
    cid = conexao(uid, f"i-{uid}")
    iid = pos(cid, "p", "1100", "1000")
    foto(cid, "p", FIM, "1050", "1000")
    # 02:30 UTC de 01/10 = 23:30 de 30/09 em SP: o dia da base, não depois dela
    q("update open_finance_investments set updated_at=%s where id=%s",
      (datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc), iid))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local time zone 'UTC'")  # a sessão do Postgres no Railway: `.date()` cru daria 01/10
        r = ler(cur, uid, hoje=HOJE)
        conn.rollback()
    (c,) = r["rendimentos"]["categorias"]
    assert c["ativos"][0]["mes"]["motivo"] == "sem_leitura_no_mes"


def test_calculo_novo_que_falha_nao_derruba_itens_nem_motivos(uid, monkeypatch):
    from _cards_apoio import mostrar
    from db import rendimento_contratado as mod
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    with get_conn() as conn, conn.cursor() as cur:
        esperado = ler(cur, uid, hoje=HOJE)
        conn.rollback()
    assert esperado["rendimentos"] is not None
    monkeypatch.setattr(mod, "q_posicao", lambda p: D("1E+26").quantize(D("0.01")))  # InvalidOperation, como saldo >= 1e26
    with get_conn() as conn, conn.cursor() as cur:
        r = ler(cur, uid, hoje=HOJE)
        conn.rollback()
    assert r["rendimentos"] is None and r["itens"] == esperado["itens"] and r["motivos"] == esperado["motivos"]
    assert leitura(uid, "rendimento")["rendimentos"] is None
    res, _, _ = mostrar(uid, card="rendimento")
    assert res["abas"][0] == {"rendimentos": None, "taxas": [{"nome": "CDB", "taxa": None, "tipo_taxa": None}]}


def test_foto_com_valor_nao_finito_vira_saldo_ausente_e_nao_excecao(uid, monkeypatch):
    from db import rendimento_contratado as mod
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000", qtd="10", tipo="MUTUAL_FUND")
    # o CHECK finito impede gravar NaN na foto: a base vem de uma consulta que devolve o que o banco recusaria
    monkeypatch.setattr(mod, "_BASE_SQL", "select 'p' as provider_investment_id, date '2026-09-30' as observed_on,"
                        " 'NaN'::numeric as balance, null::numeric as amount_original, 10::numeric as quantity")
    assert mes_do(rend(uid))["motivo"] == "saldo_ausente"


def test_sql_da_base_com_erro_nao_aborta_a_transacao_e_mantem_os_itens(uid, monkeypatch):
    from db import rendimento_contratado as mod
    cid = conexao(uid, f"i-{uid}")
    pos(cid, "p", "1100", "1000")
    normal = leitura(uid, "rendimento")
    assert normal["itens"] and normal["rendimentos"] is not None  # (b) o caminho normal segue verde
    monkeypatch.setattr(mod, "_BASE_SQL", "select nope from nada")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")  # como a rota
        r = ler(cur, uid, hoje=HOJE)
        cur.execute("select 1 as um")  # a transação não ficou abortada
        assert cur.fetchone()["um"] == 1
        conn.rollback()
    assert r["rendimentos"] is None and r["itens"] and r["motivos"] == []
    via_rota = leitura(uid, "rendimento")
    assert via_rota["rendimentos"] is None and via_rota["itens"] == normal["itens"]
