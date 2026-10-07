"""Complementos nativos com banco real: cobertura, números oficiais e isolamento."""
from datetime import timedelta
from decimal import Decimal

import pytest

import db
from api.nativo.mes_detalhes import MesDetalhes
from api.nativo.patrimonio import Patrimonio
from api.nativo.rendimento import Rendimento
from conftest import usuario_pagante
from core.services.pluggy_sync import normalize_pluggy_account
from db.of_snapshots import grava_fotos_posicoes
from db.patrimonio import gravar_foto
from test_api_app_sessao import cliente, completo
from tests._patrimonio_helpers import (AGORA, caixinha, conexao, conta, investimento_manual,
                                       foto, posicao, q)
from tests.test_resumo_mes_regra import ENTROU, INICIO, SAIU, _em, _lanc, semeia_a
from utils_date import today_tz

D = Decimal
MES = f"{INICIO:%Y-%m}"


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    return completo(usuario_pagante(plan="pro_max"))


def leitura(quem, path, **params):
    r = cliente(quem).get("/api/app/" + path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def snapshot(cid, pid, rate, rate_type="CDI", *, dia=AGORA, confirmada=True):
    with db.get_conn() as conn, conn.cursor() as cur:
        grava_fotos_posicoes(cur, cid, [{"provider_investment_id": pid,
                                        "raw": {"rate": rate, "rateType": rate_type}}],
                            dia, confirmada)
        conn.commit()


def test_patrimonio_publico_reusa_calculo_sem_double_count_e_sem_ids(uid):
    db.set_balance(uid, D("100"))
    cid = conexao(uid, f"secret-item-{uid}")
    conta(cid, "secret-provider-account", "1000")
    inv = posicao(cid, "secret-provider-position", "500.005")
    caixinha(uid, "Espelhada", "500.005", of_investment_id=inv)
    caixinha(uid, "Manual", "50")
    investimento_manual(uid, "CDB manual", "70")
    outro = completo(usuario_pagante())
    conta(conexao(outro, f"item-b-{outro}"), "secret-provider-account", "99999")
    r = leitura(uid, "patrimonio", uid=outro, user_id=outro)
    Patrimonio.model_validate(r)
    assert r["partes"] == {"carteira": "100.00", "bancos": "1000.00",
                           "investimentos_banco": "500.01", "caixinhas": "50.00",
                           "investimentos_manuais": "70.00"}
    assert r["total"] == "1720.01"
    assert r["historico"] == [] and r["historico_desde"] is None
    assert "secret-" not in str(r) and "base" not in r and "raw" not in str(r)


def test_patrimonio_historico_so_fotos_existentes_e_janela(uid, monkeypatch):
    import api.nativo.patrimonio as rota

    ontem = today_tz() - timedelta(days=1)
    antigo = ontem - timedelta(days=365)
    assert gravar_foto(uid, antigo)
    assert gravar_foto(uid, ontem)
    outro = completo(usuario_pagante())
    db.set_balance(outro, D("999"))
    assert gravar_foto(outro, ontem)
    monkeypatch.setattr(rota, "history_earliest_date", lambda uid, agora: ontem)
    r = leitura(uid, "patrimonio")
    assert [f["dia"] for f in r["historico"]] == [ontem.isoformat()]
    assert r["historico_desde"] == ontem.isoformat()
    assert r["historico"][0]["total"] == "0.00"


@pytest.mark.parametrize("saldo_banco,diverge", [("1.005", True), ("1.000", False)],
                         ids=["partes-divergem", "partes-exatas"])
def test_patrimonio_preserva_total_calculado_e_avisa_partes_atual_e_foto(uid, saldo_banco, diverge):
    db.add_launch_and_update_balance(uid, "receita", D("1.005"), "Recebimento", None)
    cid = conexao(uid, f"writer-patrimonio-{uid}")
    db.save_open_finance_sync(cid, [normalize_pluggy_account({
        "id": "conta-real", "type": "BANK", "currencyCode": "BRL", "balance": saldo_banco})])
    oficial = foto(uid)
    assert oficial["total"] == D("1.005") + D(saldo_banco)
    assert gravar_foto(uid, today_tz())
    r = leitura(uid, "patrimonio")
    assert r["partes"]["carteira"] == "1.01"
    assert r["partes"]["bancos"] == ("1.01" if diverge else "1.00")
    assert r["total"] == "2.01", "total financeiro oficial não é soma de partes quantizadas"
    assert ("arredondamento_por_grupo" in r["motivos"]) is diverge
    assert len(r["historico"]) == 1
    historico = r["historico"][0]
    assert historico["total"] == r["total"] and historico["partes"] == r["partes"]
    assert ("arredondamento_por_grupo" in historico["motivos"]) is diverge
    if diverge:
        db.save_open_finance_sync(cid, [normalize_pluggy_account({
            "id": "conta-real", "type": "BANK", "currencyCode": "BRL", "balance": "1.000"})])
        depois = leitura(uid, "patrimonio")
        assert "arredondamento_por_grupo" not in depois["motivos"]
        assert "arredondamento_por_grupo" in depois["historico"][0]["motivos"]
        assert depois["historico"][0]["partes"]["bancos"] == "1.01", "foto não é reescrita pelo sync"


def test_rendimento_contratado_snapshot_latest_own_confirmado_sem_yield(uid):
    pid = "secret-provider-rate"
    velha = conexao(uid, f"item-old-{uid}")
    posicao(velha, pid, "50")
    snapshot(velha, pid, 70)
    atual = conexao(uid, f"item-new-{uid}")
    posicao(atual, pid, "100")
    snapshot(atual, pid, "105.125", dia=AGORA - timedelta(days=1))
    snapshot(atual, pid, 900, dia=AGORA, confirmada=False)
    outro = completo(usuario_pagante())
    cb = conexao(outro, f"item-b-{outro}")
    posicao(cb, pid, "999")
    snapshot(cb, pid, 999)
    r = leitura(uid, "rendimento", user_id=outro)
    Rendimento.model_validate(r)
    assert r["tipo"] == "contratado" and len(r["itens"]) == 1
    item = r["itens"][0]
    assert (item["taxa"], item["tipo_taxa"]) == ("105.125", "CDI")
    assert "taxa_de_coleta_anterior" in item["motivos"]
    assert pid not in str(r) and "last_month" not in str(r) and "raw" not in str(r)


@pytest.mark.parametrize("taxa,tipo", [(None, "CDI"), ("NaN", "CDI"), (100, None)])
def test_taxa_ausente_invalida_ou_sem_unidade_nao_inventa_100(uid, taxa, tipo):
    cid = conexao(uid, f"item-{uid}")
    posicao(cid, "inv", "100")
    snapshot(cid, "inv", taxa, tipo)
    item = leitura(uid, "rendimento")["itens"][0]
    assert item["taxa"] is None and item["tipo_taxa"] is None
    assert "taxa_contratada_ausente" in item["motivos"]


def test_nao_cdi_preserva_unidade_e_posicoes_inativas_nao_entram(uid):
    cid = conexao(uid, f"item-{uid}")
    posicao(cid, "inv", "100")
    snapshot(cid, "inv", "12.5", "PRE_FIXED")
    posicao(cid, "usd", "50", moeda="USD", code="USD")
    posicao(cid, "retirada", "99", status="TOTAL_WITHDRAWAL")
    posicao(conexao(uid, f"paused-{uid}", status="PAUSED"), "paused", "100")
    r = leitura(uid, "rendimento")
    assert len(r["itens"]) == 1
    assert (r["itens"][0]["taxa"], r["itens"][0]["tipo_taxa"]) == ("12.5", "PRE_FIXED")


def test_mes_completo_manual_cartao_parcela_interno_e_conciliacao(uid):
    semeia_a(uid)
    r = leitura(uid, "mes-detalhes", mes=MES)
    MesDetalhes.model_validate(r)
    assert (D(r["entrou"]), D(r["saiu"])) == (ENTROU, SAIU)
    assert sum(D(c["valor"]) for c in r["categorias"]) == SAIU
    assert sum(D(d["saiu"]) for d in r["dias"]) == SAIU
    assert sum(D(d["entrou"]) for d in r["dias"]) == ENTROU
    assert r["guardado"] == {"aportes": "200.00", "saques": "0.00", "liquido": "200.00",
                             "cobertura": "movimentos_registrados"}
    assert "conciliacao_pendente" in r["motivos"]
    assert any(d["dia"] < INICIO.isoformat() for d in r["dias"]), "parcela guarda dia da compra"


@pytest.mark.parametrize("categorias,dias,avisa", [
    (("a", "b"), (3, 4), True),
    (("a", "b"), (3, 3), True),
    (("a", "a"), (3, 4), True),
    (("a", "a"), (3, 3), False),
], ids=["ambos-divergem", "categorias-divergem", "dias-divergem", "grupos-exatos"])
def test_arredondamento_dos_grupos_e_explicito_com_writer_real(uid, categorias, dias, avisa):
    for categoria, dia in zip(categorias, dias):
        db.add_launch_and_update_balance(uid, "despesa", D("1.005"), "Gasto", None,
                                         categoria=categoria, criado_em=_em(dia))
    r = leitura(uid, "mes-detalhes", mes=MES)
    oficial = leitura(uid, "resumo-do-mes", mes=MES)
    assert D(oficial["saiu"]) == D("2.010"), "o writer e a regra preservam precisão real"
    assert r["saiu"] == "2.01" and r["fora_do_total"] == 0
    assert sum(D(c["valor"]) for c in r["categorias"]) == (D("2.02") if categorias[0] != categorias[1] else D("2.01"))
    assert sum(D(d["saiu"]) for d in r["dias"]) == (D("2.02") if dias[0] != dias[1] else D("2.01"))
    assert ("arredondamento_por_grupo" in r["motivos"]) is avisa


def test_arredondamento_de_entradas_diarias_tambem_e_explicito(uid):
    for dia in (3, 4):
        db.add_launch_and_update_balance(uid, "receita", D("1.005"), "Recebimento", None,
                                         criado_em=_em(dia))
    r = leitura(uid, "mes-detalhes", mes=MES)
    assert r["entrou"] == "2.01" and not r["categorias"]
    assert sum(D(d["entrou"]) for d in r["dias"]) == D("2.02")
    assert "arredondamento_por_grupo" in r["motivos"]


def test_mes_nao_trunca_100_linhas_nem_50_categorias_e_isola_usuario(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.executemany("""insert into launches (user_id,tipo,valor,categoria,criado_em)
                           values (%s,'despesa',1,%s,%s)""",
                        [(uid, f"categoria {i:03}", INICIO.replace(day=3)) for i in range(130)])
        conn.commit()
    outro = completo(usuario_pagante())
    _lanc(outro, "despesa", 999, 3)
    r = leitura(uid, "mes-detalhes", mes=MES, user_id=outro, uid=outro)
    assert r["saiu"] == "130.00" and len(r["categorias"]) == 130
    assert sum(c["quantidade"] for c in r["categorias"]) == 130
    assert sum(D(d["saiu"]) for d in r["dias"]) == D(130)


def test_mes_moeda_diferente_tem_cobertura_explicita_sem_donut_falso(uid):
    _lanc(uid, "despesa", 10, 3)
    ident = _lanc(uid, "despesa", 100, 3)
    q("update launches set currency='USD' where user_id=%s and id=%s", (uid, ident))
    r = leitura(uid, "mes-detalhes", mes=MES)
    assert r["saiu"] == "110.00", "preserva regra oficial legada do resumo"
    assert sum(D(c["valor"]) for c in r["categorias"]) == D(10)
    assert r["fora_do_total"] == 1 and "outra_moeda" in r["motivos"]


def test_guardado_distingue_saques_e_saldo_mes_e_nao_inclui_pagamento_fatura(uid):
    _lanc(uid, "receita", 1000, 3)
    _lanc(uid, "despesa", 800, 3, interno=True)
    _lanc(uid, "aporte_investimento", 200, 3, interno=True)
    _lanc(uid, "saque_caixinha", 30, 3, interno=True)
    r = leitura(uid, "mes-detalhes", mes=MES)
    assert r["saiu"] == "0.00" and r["entrou"] == "1000.00"
    assert r["guardado"]["liquido"] == "170.00"


def test_guardado_nao_soma_aporte_em_outra_moeda(uid):
    _lanc(uid, "deposito_caixinha", 10, 3, interno=True)
    ident = _lanc(uid, "aporte_investimento", 100, 3, interno=True)
    q("update launches set currency='USD' where user_id=%s and id=%s", (uid, ident))
    r = leitura(uid, "mes-detalhes", mes=MES)
    assert r["guardado"]["aportes"] == "10.00"
    assert r["fora_do_total"] == 1 and "outra_moeda" in r["motivos"]


def test_mes_janela_do_plano_corta_todos_agregados(uid, monkeypatch):
    from core.services import plan_service

    _lanc(uid, "despesa", 10, 3)
    _lanc(uid, "deposito_caixinha", 20, 3, interno=True)
    _lanc(uid, "despesa", 30, 7)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, agora=None: INICIO.replace(day=5))
    import db.mes_detalhes as leitor
    monkeypatch.setattr(leitor, "history_earliest_date", plan_service.history_earliest_date)
    r = leitura(uid, "mes-detalhes", mes=MES)
    assert r["saiu"] == "30.00" and r["guardado"]["aportes"] == "0.00"
    assert "inicio_do_historico" in r["motivos"]
    assert sum(D(c["valor"]) for c in r["categorias"]) == D(30)


@pytest.mark.parametrize("mes", ["2026-13", "2026-1", "9999-01"])
def test_mes_detalhes_valida_mes_com_regra_compartilhada(uid, mes):
    r = cliente(uid).get("/api/app/mes-detalhes", params={"mes": mes})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
