"""Tabela classes de entrada x saída de `db.categories.categoria_no_plano` (a mesma do docstring) e de
`category_service.infer_category`, para conta de CONDOMÍNIO e PESSOAL. Uma linha vermelha = a tabela mente."""
from __future__ import annotations

import uuid

import pytest

import db
from conftest import _cleanup_user, promote_to_pro
from core.services import category_service
from db.categories import (
    add_category_rule, categoria_da_conta, categoria_no_plano, create_user_category, set_user_category_archived,
)
from scripts.ativar_conta_condominio import ativar
from utils_text import INTERNAL_MOVEMENT_CATEGORIES

P = [(n, None) for n in ("energia elétrica", "taxa condominial (ordinária)", "manutenção predial",
                         "manutenção de elevador", "jardinagem", "fundo de obras", "☕", "🍕", "outros")]
P1 = [("☕", None), ("outros", None)]

SISTEMA = sorted(INTERNAL_MOVEMENT_CATEGORIES) + ["rendimentos", "criptomoedas"]
LINHAS_PLANO = [
    (P, "energia elétrica", "energia elétrica"),
    (P, "ENERGIA Eletrica", "energia elétrica"),
    (P, "taxa condominial (ordinária)", "taxa condominial (ordinária)"),
    (P, "energia elétrica (despesas fixas)", "energia elétrica"),
    (P, "jardinagem geral", "jardinagem"),
    (P, "elevadores", "manutenção de elevador"),
    (P, "manutenção", "outros"),
    (P, "piscina", "outros"),
    (P, "reforma antiga", "outros"),
    (P, "fundo de obras", "fundo de obras"),
    (P, "fundo de obras (reserva)", "fundo de obras"),
    (P, "outros", "outros"),
    (P, "OUTROS", "outros"),
    (P, "", "outros"),
    (P, "   ", "outros"),
    (P, "🍕", "🍕"),
    (P, "☕", "☕"),
    (P, "☕ ", "☕"),
    (P1, "☕", "☕"),
    (P1, "🍕", "outros"),
    (P, "12345", "outros"),
    (P, "123 energia", "energia elétrica"),
] + [(P, v, c) for c in SISTEMA for v in (c, c.replace("_", " "), c.replace("_", " ").title())]


@pytest.mark.parametrize("plano,raw,esperado", LINHAS_PLANO)
def test_categoria_no_plano(plano, raw, esperado):
    assert categoria_no_plano(plano, raw) == esperado


@pytest.fixture()
def contas():
    ids = []
    for condo in (True, False):
        uid = 100_000_000 + int(uuid.uuid4().int % 1_900_000_000)
        db.ensure_user(uid)
        promote_to_pro(uid)
        if condo:
            ativar(uid)
        ids.append(uid)
    yield ids
    for u in ids:
        _cleanup_user(u)


def test_categoria_da_conta_pessoal_devolve_raw(contas):
    _, pessoal = contas
    for _, raw, _ in LINHAS_PLANO:
        assert categoria_da_conta(pessoal, raw) == raw


def test_categoria_arquivada_cai_em_outros_no_condominio(contas):
    condo, pessoal = contas
    for u in (condo, pessoal):
        c = create_user_category(u, "reforma antiga")
        set_user_category_archived(u, c["id"], True)
    assert categoria_da_conta(condo, "reforma antiga") == "outros"
    assert categoria_da_conta(pessoal, "reforma antiga") == "reforma antiga"


def test_infer_category_condominio_e_pessoal(contas, monkeypatch):
    condo, pessoal = contas
    add_category_rule(condo, "zeladoria", "energia elétrica")
    add_category_rule(pessoal, "zeladoria", "moradia")
    create_user_category(condo, "reserva especial")
    create_user_category(pessoal, "reserva especial")

    def inf(u, t, **kw):
        r = category_service.infer_category(u, t, **kw)
        return r.category, r.reason

    # (texto, condomínio, pessoal)
    linhas = [
        ("pagamento zeladoria", ("energia elétrica", "user_rule"), ("moradia", "user_rule")),       # regra do usuário
        ("aplicacao cdb", ("investimento_aporte", "local_rule"), ("investimento_aporte", "local_rule")),  # local, SISTEMA
        ("gastei no uber", ("outros", "default"), ("transporte", "local_rule")),                    # local, despesa pessoal
        ("aporte na reserva especial", ("reserva especial", "user_category"), ("reserva especial", "user_category")),  # custom vence local
        ("pagamento reserva", ("reserva especial", "user_category"), ("reserva especial", "user_category")),  # custom
        ("xyzabc", ("outros", "default"), ("outros", "default")),                                    # nada
    ]
    for texto, esp_condo, esp_pessoal in linhas:
        assert inf(condo, texto, allow_ai=False) == esp_condo, texto
        assert inf(pessoal, texto, allow_ai=False) == esp_pessoal, texto

    # IA (mock): só entra quando nada acima casou; devolve o que o modelo disse
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr("core.services.plan_service.plan_gate_ok", lambda *a, **k: True)
    monkeypatch.setattr("ai_router.classify_category_with_gpt", lambda *a, **k: "lazer")
    assert inf(condo, "xyzabc") == ("lazer", "ai")
    assert inf(pessoal, "xyzabc") == ("lazer", "ai")
    assert inf(condo, "aplicacao cdb") == ("investimento_aporte", "local_rule")  # a IA não é chamada antes


def test_infer_category_condominio_so_movimentacao_interna(contas):
    """Passo C do condomínio roda só as regras de INTERNAL_MOVEMENT_CATEGORIES (sem custom 'reserva' no caminho)."""
    condo, pessoal = contas
    linhas = [
        ("aplicacao cdb", ("investimento_aporte", "local_rule"), ("investimento_aporte", "local_rule")),
        ("fundo de reserva aplicacao", ("investimento_aporte", "local_rule"), ("investimento_aporte", "local_rule")),
        # rendimentos/criptomoedas são SISTEMA p/ passthrough, mas suas regras não rodam no passo C do condomínio
        ("multa e juros boleto agua", None, ("rendimentos", "local_rule")),
        ("juros por atraso pagamento energia", None, ("rendimentos", "local_rule")),
        ("sol nascente portaria", None, ("criptomoedas", "local_rule")),
        ("pagamento ada servicos", None, ("criptomoedas", "local_rule")),
    ]
    for texto, esp_condo, esp_pessoal in linhas:
        for u, esp in ((condo, esp_condo), (pessoal, esp_pessoal)):
            r = category_service.infer_category(u, texto, allow_ai=False)
            if esp is None:  # condomínio: o plano pode ter categoria própria ("juros de mora e multas"); só não pode ser regra local
                assert r.reason != "local_rule" and r.category not in ("rendimentos", "criptomoedas"), (texto, r)
            else:
                assert (r.category, r.reason) == esp, texto
