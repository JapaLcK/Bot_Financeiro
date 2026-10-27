"""Apoio dos testes do `show_card` (B1): usuários A e B com dados, o cliente com sessão
e a chamada da tool com emissor."""
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

import db
from core.ai_patterns import generate_ai_insights
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from core.services.ai_chat import streaming
from core.services.ai_chat.tools import get_tool
from db import metas as dbmetas
from db.pockets import update_pocket_meta
from test_api_app_sessao import cliente, completo
from tests.test_resumo_mes_regra import INICIO, semeia_a, semeia_b
from utils_date import now_tz

M = f"{INICIO:%Y-%m}"
M0 = f"{INICIO - timedelta(days=1):%Y-%m}"
UM_MES = {"meses": [M]}
# card -> (args do show_card, [aba: {recurso: (rota, query)}])
CASOS = {
    "contas": ({}, [{"contas": ("/api/app/contas", {})}]),
    "hero": ({"dias": "30"}, [{"previsao": ("/api/app/previsao", {"dias": "30"})}]),
    "compromissos": ({"dias": "30"}, [{"previsao": ("/api/app/previsao", {"dias": "30"})}]),
    "resumo": (UM_MES, [{"resumo": ("/api/app/resumo-do-mes", {"mes": M}),
                         "detalhes": ("/api/app/mes-detalhes", {"mes": M})}]),
    "categorias": ({"meses": [M, M0]}, [{"detalhes": ("/api/app/mes-detalhes", {"mes": m}),
                                         "contas": ("/api/app/contas", {})} for m in (M, M0)]),
    "calendario": (UM_MES, [{"detalhes": ("/api/app/mes-detalhes", {"mes": M})}]),
    "simulador": (UM_MES, [{"detalhes": ("/api/app/mes-detalhes", {"mes": M}),
                            "previsao": ("/api/app/previsao", {"dias": "90"})}]),
    "patrimonio": ({}, [{"patrimonio": ("/api/app/patrimonio", {})}]),
    "wealth": ({}, [{"patrimonio": ("/api/app/patrimonio", {})}]),
    "rendimento": ({}, [{"rendimento": ("/api/app/rendimento", {})}]),
    "assinaturas": ({}, [{"assinaturas": ("/api/app/assinaturas", {})}]),
    "metas": ({}, [{"metas": ("/api/app/metas", {})}]),
    "caixinhas": ({}, [{"caixinhas": ("/goals/{uid}/status", {})}]),
    "fatura": ({}, [{"cartoes": ("/cards/{uid}/summary", {})}]),
    "parcelas": ({}, [{"parcelas": ("/installments/{uid}/list", {})}]),
    "renda": ({"quantidade_meses": 4}, [{"renda": ("/analytics/{uid}/evolution", {"months": 4})}]),
    "piggy": ({}, [{"insights": ("/insights/{uid}/current", {})}]),
    # sem rota HTTP (só do chat): a paridade com `mes-detalhes` mês a mês mora em test_ai_chat_periodo.py
    "periodo": ({"de": M0, "ate": M}, None),
}


_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _numeros(o, sem_ids=False):
    if isinstance(o, bool):
        return set()
    if isinstance(o, (int, float)):
        return {float(o)}
    if isinstance(o, str):
        return {float(o)} if _NUM.match(o) else set()
    if isinstance(o, dict):
        filhos = [v for k, v in o.items() if not (sem_ids and (k == "id" or k.endswith("_id")))]
    else:
        filhos = o if isinstance(o, list) else []
    return set().union(*(_numeros(f, sem_ids) for f in filhos)) if filhos else set()


def _reais(o):
    """Os `R$ 1.234,56` de todo texto do objeto, como Decimal em módulo."""
    if isinstance(o, str):
        return {abs(Decimal(x.replace(".", "").replace(",", "."))) for x in re.findall(r"R\$ (-?[\d.]+,\d\d)", o)}
    filhos = o.values() if isinstance(o, dict) else o if isinstance(o, list) else []
    return set().union(*map(_reais, filhos)) if filhos else set()


MARCAS_DE_B = {7777.0, 5555.0, 888.0}  # as de `semeia_b`: despesa, receita e compra no cartão


def valores_de_b_em(o, marcas=MARCAS_DE_B):
    """Quais `marcas` (números) aparecem em `o` como VALOR (em módulo): número, texto numérico ou `R$` — ids ficam de fora."""
    return ({abs(x) for x in _numeros(o, sem_ids=True)} | {float(x) for x in _reais(o)}) & marcas


def _semeia_metas_e_cartao(uid, nome):
    db.add_launch_and_update_balance(uid, "receita", 1000.0, None, "seed")
    _, pid, _ = db.create_pocket(uid, nome)
    update_pocket_meta(uid, pid, target_amount=1000.0, target_date=(now_tz().date() + timedelta(days=60)).isoformat())
    db.pocket_deposit_from_account(uid, nome, 300.0, "aporte")


def semeia_meta(uid, nome):
    """Uma meta de verdade (fonte: tudo investido, sem banco: `atual` null)."""
    return dbmetas.criar(uid, {"nome": nome, "tipo": "meta", "alvo": Decimal("1000"), "meses": None,
                               "data_alvo": None, **dbmetas.resolver_fonte(uid, "investido", None, None)})


@pytest.fixture
def mundo(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    a, b = (completo(usuario_pagante(plan="pro_max")) for _ in range(2))
    semeia_a(a)
    semeia_b(b)
    _semeia_metas_e_cartao(a, "viagem")
    _semeia_metas_e_cartao(b, "SEGREDO-B")
    semeia_meta(a, "Casa nova")
    semeia_meta(b, "SEGREDO-B meta")
    # a chave do /painel novo: o card `metas` e a tool `list_metas` só leem para quem a tem (P1-b).
    # Depois de semear: com a chave, a escrita manual de investimento é recusada (fonte única).
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    for uid in (a, b):
        generate_ai_insights(uid, force=False)  # o card piggy só lê o cache de 6 h
    return a, b


def _http(uid):
    c = cliente(uid)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "b1@t.com"))
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    return c


def mostrar(uid, **args):
    """(resultado da tool, blocos emitidos, eventos), pela tool real e com emissor."""
    ev = []
    t = streaming.EMISSOR.set(ev.append)
    try:
        r = get_tool("show_card").execute(uid, args)
    finally:
        streaming.EMISSOR.reset(t)
    return r, [e["bloco"] for e in ev if e["tipo"] == "bloco"], ev
