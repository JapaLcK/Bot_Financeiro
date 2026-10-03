"""`GET`/`POST /api/v2/guia` (`api/v2/guia.py`, `db/guia.py`) pelo monólito real: sessão,
CSRF do pai, banco real.

Disponibilidade do passo 1 (Saiu > 0 no mês corrente OU no anterior; senão o motivo),
conclusão só com todos os passos (`?&`), carimbo do 1º `feito` nunca reescrito, 422 no
envelope, 403 sem CSRF, dispensa e reabertura, export LGPD. Isolamento A/B:
`tests/test_api_v2_isolamento.py`.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de
from api.v2.guia import IDS, Guia
from conftest import usuario_pagante
from db.privacy import build_user_export_zip
from tests._patrimonio_helpers import conexao, lancamento, q
from utils_date import now_tz

URL = "/api/v2/guia"


@pytest.fixture
def uid(monkeypatch):
    u = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    return u


def cliente(quem, com_csrf=True):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return c, (csrf(c) if com_csrf else {})


def ler(quem, **params) -> dict:
    c, _ = cliente(quem)
    r = c.get(URL, params=params)
    assert r.status_code == 200, r.text
    return Guia.model_validate(r.json()).model_dump()


def post(quem, acao, passo=None, **params):
    c, h = cliente(quem)
    return c.post(URL, json={"acao": acao, "passo": passo}, headers=h, params=params)


def feito(quem, passo) -> dict:
    r = post(quem, "feito", passo)
    assert r.status_code == 200, r.text
    return r.json()


def linha(quem):
    return q("select * from guia_painel where user_id = %s", (quem,))


def saiu_em(quem, mes_atras: int):
    """Uma despesa de 30 no dia 15 do mês corrente (0) ou do anterior (1), ao meio-dia."""
    dia = now_tz().date().replace(day=1)
    for _ in range(mes_atras):
        dia = (dia - timedelta(days=1)).replace(day=1)
    lid = lancamento(quem)
    q("update launches set criado_em = %s where id = %s and user_id = %s",
      (datetime(dia.year, dia.month, 15, 12), lid, quem))


def disponiveis(g):
    return [(p["id"], p["disponivel"], p["motivo"]) for p in g["passos"]]


# ── disponibilidade do passo 1 ─────────────────────────────────────────────────

def test_saiu_no_mes_corrente_oferece(uid):
    saiu_em(uid, 0)
    g = ler(uid)
    assert (g["estado"], g["motivo"]) == ("oferecer", None)
    assert disponiveis(g) == [(i, True, None) for i in IDS]


def test_saiu_so_no_mes_anterior_tambem_oferece(uid):
    saiu_em(uid, 1)
    g = ler(uid)
    assert (g["estado"], disponiveis(g)[0]) == ("oferecer", ("resumo.saiu", True, None))


def test_sem_saiu_passo_1_sem_dados_e_2_e_3_disponiveis(uid):
    saiu_em(uid, 2)  # dois meses atrás não conta
    g = ler(uid)
    assert (g["estado"], g["motivo"]) == ("indisponivel", "sem_dados")
    assert disponiveis(g) == [("resumo.saiu", False, "sem_dados"),
                              ("gastos.categoria", True, None), ("piggy.pergunta", True, None)]


@pytest.mark.parametrize("status,motivo", [("UPDATING", "sincronizando"), ("ERROR", "conexao_com_erro"),
                                           ("LOGIN_ERROR", "conexao_com_erro"), ("UPDATED", "sem_dados")])
def test_motivo_pela_conexao(uid, status, motivo):
    conexao(uid, f"item-{uid}", status=status)
    assert ler(uid)["motivo"] == motivo


def test_sincronizando_vence_o_erro(uid):
    conexao(uid, f"a-{uid}", status="ERROR")
    conexao(uid, f"b-{uid}", status="UPDATING")
    assert ler(uid)["motivo"] == "sincronizando"


# ── conclusão ────────────────────────────────────────────────────────────────

def test_dois_de_tres_nao_concluem_e_tres_concluem(uid):
    saiu_em(uid, 0)
    feito(uid, "resumo.saiu")
    g = feito(uid, "gastos.categoria")
    assert g["estado"] == "em_andamento"
    assert [p["feito"] for p in g["passos"]] == [True, True, False]
    assert linha(uid)["concluido_em"] is None
    assert feito(uid, "piggy.pergunta")["estado"] == "concluido"
    assert linha(uid)["concluido_em"] is not None


def test_dois_e_tres_sem_dados_ficam_em_andamento(uid):
    feito(uid, "gastos.categoria")
    g = feito(uid, "piggy.pergunta")
    assert (g["estado"], g["motivo"]) == ("em_andamento", "sem_dados")
    saiu_em(uid, 0)  # os dados chegam: segue retomando, agora com o passo 1 disponível
    g = ler(uid)
    assert (g["estado"], g["motivo"]) == ("em_andamento", None)
    assert [(p["disponivel"], p["feito"]) for p in g["passos"]][0] == (True, False)


def test_visto_e_passo_1_feito_retoma_sem_o_convite(uid):
    """O convite (`oferecer`) é só para quem nunca começou: começar e sair não o reexibe."""
    saiu_em(uid, 0)
    assert post(uid, "visto").json()["estado"] == "em_andamento"
    assert feito(uid, "resumo.saiu")["estado"] == "em_andamento"
    assert ler(uid)["estado"] == "em_andamento"


def test_visto_sem_feito_nao_reoferece(uid):
    """Decisão do dono (2026-10-03): o convite aparece só para quem nunca o viu. Controle
    positivo: o mesmo usuário, antes do `visto`, recebe `oferecer`."""
    saiu_em(uid, 0)
    assert ler(uid)["estado"] == "oferecer"
    post(uid, "visto")
    assert ler(uid)["estado"] == "em_andamento"


def test_reabrir_pela_ajuda_sem_convite_carimba_a_oferta(uid):
    """Abriu pela Ajuda sem nunca ter visto o convite: a oferta conta, o convite não vem depois."""
    saiu_em(uid, 0)
    assert post(uid, "reabrir").json()["estado"] == "em_andamento"
    assert linha(uid)["oferecido_em"] is not None
    assert ler(uid)["estado"] == "em_andamento"


def test_segundo_feito_nao_reescreve_o_carimbo(uid):
    feito(uid, "gastos.categoria")
    antes = linha(uid)["feitos"]["gastos.categoria"]
    feito(uid, "gastos.categoria")
    assert linha(uid)["feitos"] == {"gastos.categoria": antes}


def test_visto_carimba_uma_vez(uid):
    assert post(uid, "visto").status_code == 200
    antes = linha(uid)["oferecido_em"]
    assert antes is not None
    post(uid, "visto")
    assert linha(uid)["oferecido_em"] == antes


def test_visto_depois_de_feito_nao_carimba_a_oferta(uid):
    """Outra aba/dispositivo manda `visto` depois do 1º `feito`: `oferecido_em` gravado aí
    seria posterior ao feito e a mediana `feitos - oferecido_em` sairia negativa."""
    feito(uid, "gastos.categoria")
    assert post(uid, "visto").status_code == 200
    assert linha(uid)["oferecido_em"] is None


def test_visto_antes_de_feito_fica_anterior_a_todo_feito(uid):
    saiu_em(uid, 0)
    post(uid, "visto")
    feito(uid, "resumo.saiu")
    post(uid, "visto")
    l = linha(uid)
    assert l["oferecido_em"] <= datetime.fromisoformat(l["feitos"]["resumo.saiu"])


def test_feito_do_passo_1_sem_dados_e_409_e_nao_grava(uid):
    """Sem Saiu o passo 1 não existe para o usuário: o carimbo dele é o numerador do
    "tempo até o 1º valor percebido" e não pode nascer sem valor na tela."""
    r = post(uid, "feito", "resumo.saiu")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "passo_indisponivel"
    assert linha(uid) is None
    feito(uid, "gastos.categoria")
    antes = linha(uid)
    assert post(uid, "feito", "resumo.saiu").status_code == 409
    assert linha(uid) == antes


def test_feito_do_passo_1_com_saiu_grava(uid):
    saiu_em(uid, 0)
    g = feito(uid, "resumo.saiu")
    assert [p["feito"] for p in g["passos"]] == [True, False, False]
    assert list(linha(uid)["feitos"]) == ["resumo.saiu"]


# ── validação ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("corpo", [{"acao": "feito"}, {"acao": "feito", "passo": None},
                                   {"acao": "feito", "passo": "resumo.entrou"}, {"acao": "pular"},
                                   {}], ids=["sem_passo", "passo_null", "passo_desconhecido",
                                             "acao_desconhecida", "vazio"])
def test_invalido_e_422_no_envelope_e_nao_grava(uid, corpo):
    c, h = cliente(uid)
    r = c.post(URL, json=corpo, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert linha(uid) is None


def test_post_sem_csrf_e_403(uid):
    c, _ = cliente(uid, com_csrf=False)
    r = c.post(URL, json={"acao": "dispensar"})
    assert (r.status_code, r.json()) == (403, {"detail": "Token CSRF inválido ou ausente."})
    assert linha(uid) is None


# ── dispensa e reabertura ────────────────────────────────────────────────────

def test_dispensar_e_reabrir_mantem_os_feitos(uid):
    saiu_em(uid, 0)
    feito(uid, "resumo.saiu")
    assert post(uid, "dispensar").json()["estado"] == "dispensado"
    assert ler(uid)["estado"] == "dispensado"
    g = post(uid, "reabrir").json()
    assert g["estado"] == "em_andamento"
    assert [p["feito"] for p in g["passos"]] == [True, False, False]
    assert linha(uid)["dispensado_em"] is None
    assert linha(uid)["oferecido_em"] is None  # reabrir depois de feito não carimba a oferta


def test_concluido_vence_a_dispensa(uid):
    saiu_em(uid, 0)
    for p in IDS:
        feito(uid, p)
    assert post(uid, "dispensar").json()["estado"] == "concluido"


def test_export_lgpd_leva_o_guia(uid):
    feito(uid, "gastos.categoria")
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]
    assert [list(r["feitos"]) for r in dados["guia_do_painel"]] == [["gastos.categoria"]]
