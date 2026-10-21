"""`GET /api/v2/patrimonio` e `/api/app/patrimonio` com banco real: trechos, variação, teto de
12 meses, isolamento e as duas portas. A regra da quebra e a montagem pura estão em
`tests/test_patrimonio_quebra.py`."""
from datetime import timedelta
from decimal import Decimal

import pytest

import api.v2.patrimonio as rota
from api.nativo.app import app as app_nativo
from api.v2.patrimonio import Patrimonio
from conftest import usuario_pagante
from core.services.ai_chat import cards
from db.patrimonio import gravar_foto
from test_api_app_sessao import cliente, completo
from tests._patrimonio_helpers import conexao, conta, posicao, q
from utils_date import today_tz

HOJE = today_tz()


def dia(n):
    return HOJE - timedelta(days=n)


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    u = completo(usuario_pagante(plan="pro_max"))
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    return u


def get(quem, ns="v2"):
    r = cliente(quem).get(f"/api/{ns}/patrimonio")
    assert r.status_code == 200, r.text
    return Patrimonio.model_validate(r.json()) and r.json()


def banco(uid, item="item", saldo=1000):
    cid = conexao(uid, f"{item}-{uid}")
    conta(cid, f"conta-{item}-{uid}", saldo)
    return cid


def test_sem_historico_so_hoje_e_variacao_nula(uid):
    r = get(uid)
    assert r["historico"] == [] and r["trechos"] == [{"desde": HOJE.isoformat(), "ate": HOJE.isoformat()}]
    assert r["variacao"] is None


def test_continuo_com_cdb_novo_tem_variacao_exata(uid):
    cid = banco(uid)
    assert gravar_foto(uid, dia(2))
    posicao(cid, f"cdb-{uid}", 200)  # CDB novo no mesmo banco: não quebra
    assert gravar_foto(uid, dia(1))
    r = get(uid)
    assert r["trechos"] == [{"desde": dia(2).isoformat(), "ate": HOJE.isoformat()}]
    assert r["variacao"] == {"valor": "200.00", "desde": dia(2).isoformat(), "ate": HOJE.isoformat()}


def test_pausar_o_banco_quebra_e_a_variacao_e_do_trecho_atual(uid):
    cid = banco(uid)
    assert gravar_foto(uid, dia(3))
    assert gravar_foto(uid, dia(2))
    q("update open_finance_connections set status='PAUSED' where id=%s", (cid,))
    assert gravar_foto(uid, dia(1))
    r = get(uid)
    assert r["trechos"] == [{"desde": dia(3).isoformat(), "ate": dia(2).isoformat()},
                            {"desde": dia(1).isoformat(), "ate": HOJE.isoformat()}]
    assert r["variacao"] == {"valor": "0.00", "desde": dia(1).isoformat(), "ate": HOJE.isoformat()}
    assert len(r["historico"]) == 3  # a quebra não apaga o histórico


def test_foto_de_hoje_nao_duplica_o_dia_na_serie(uid):
    banco(uid)
    assert gravar_foto(uid, HOJE)
    r = get(uid)
    assert len(r["historico"]) == 1 and r["variacao"] is None
    assert r["trechos"] == [{"desde": HOJE.isoformat(), "ate": HOJE.isoformat()}]


def test_teto_de_12_meses_e_a_janela_do_plano_continua_cortando(uid, monkeypatch):
    assert gravar_foto(uid, dia(400)) and gravar_foto(uid, dia(100)) and gravar_foto(uid, dia(30))
    r = get(uid)  # pro_max: sem limite de plano, só o teto
    assert [f["dia"] for f in r["historico"]] == [dia(100).isoformat(), dia(30).isoformat()]
    assert r["historico_desde"] == dia(100).isoformat()
    monkeypatch.setattr(rota, "history_earliest_date", lambda u, agora: dia(50))
    assert [f["dia"] for f in get(uid)["historico"]] == [dia(30).isoformat()]


def test_isolamento_a_nao_ve_a_foto_nem_o_trecho_de_b(uid):
    b = completo(usuario_pagante(plan="pro_max"))
    assert gravar_foto(b, dia(5))
    cid = banco(uid)
    assert gravar_foto(uid, dia(3))
    q("update open_finance_connections set status='PAUSED' where id=%s", (cid,))
    r = get(uid)
    assert [f["dia"] for f in r["historico"]] == [dia(3).isoformat()]
    assert r["trechos"] == [{"desde": dia(3).isoformat(), "ate": dia(3).isoformat()},
                            {"desde": HOJE.isoformat(), "ate": HOJE.isoformat()}]
    assert r["variacao"] is None
    sem_foto = completo(usuario_pagante(plan="pro_max"))
    assert get(sem_foto, "app")["trechos"] == [{"desde": HOJE.isoformat(), "ate": HOJE.isoformat()}]


def test_foto_de_dia_futuro_fica_fora_do_historico_e_dos_trechos(uid):
    banco(uid)
    assert gravar_foto(uid, dia(1)) and gravar_foto(uid, dia(-1))
    r = get(uid)
    assert [f["dia"] for f in r["historico"]] == [dia(1).isoformat()]
    assert r["trechos"] == [{"desde": dia(1).isoformat(), "ate": HOJE.isoformat()}]


def test_duas_portas_mesmo_corpo_chave_do_painel_e_onboarding(uid, monkeypatch):
    banco(uid)
    assert gravar_foto(uid, dia(1))
    assert get(uid, "app") == get(uid, "v2")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "1")
    r = cliente(uid).get("/api/v2/patrimonio")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "dashboard_v2_disabled")
    assert cliente(uid).get("/api/app/patrimonio").status_code == 200
    sem = usuario_pagante()
    r = cliente(sem).get("/api/app/patrimonio")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "open_finance_onboarding_required")


def test_o_app_nativo_monta_uma_rota_de_patrimonio_so():
    # Contagem crua (a árvore de `_andar_nas_rotas` poda o mesmo router incluído duas vezes):
    # rota solta (FastAPI antigo) ou router incluído (0.141: `_IncludedRouter`).
    rs = app_nativo.router.routes
    assert sum(getattr(r, "path", None) == "/patrimonio" for r in rs) \
        + sum(getattr(r, "original_router", None) is rota.router for r in rs) == 1


def test_card_do_chat_segue_com_as_mesmas_chaves(uid):
    banco(uid)
    assert gravar_foto(uid, dia(2)) and gravar_foto(uid, dia(1))
    _, v = cards.montar("patrimonio", uid, {})
    aba = v["abas"][0]
    assert set(aba) == {"total", "partes", "motivos", "historico_desde", "historico"}
    assert len(aba["historico"]) == 2
    assert all(set(f) == {"dia", "total", "partes", "motivos"} for f in aba["historico"])
    _, w = cards.montar("wealth", uid, {})
    assert set(w["abas"][0]) == {"total", "partes", "motivos"}
