"""Q36 no cartão: com a chave, a recusa vem ANTES das pré-checagens de cada
entrada de compra manual (sem cartão, cartão sincronizado pelo OF, limite
estourado) — senão o usuário recebe "crie um cartão", o aviso de sync ou o de
limite, e criar o cartão não faz a compra passar (review Codex, PR #757).

Cada entrada × estado roda pelo canal de produção, com estado real; sem a
chave, a mensagem de antes continua (controle positivo).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from core.services.fonte_unica import MENSAGENS
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_fonte_unica_q36 import _app, _ia, _limiter, chave  # noqa: F401 (fixtures)
from tests.test_forma_pagamento_cartao_e_conciliado import _compra_na_conexao
from tests.test_manual_launches_carteira_piggy import _cobertura_open_finance, _connect_fake_bank
from utils_date import today_tz


def _nubank(u):
    cid = db.create_card(u, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(u, cid)
    return cid


ESTADOS = {
    "sem_cartao": lambda u: None,
    "sincronizado": lambda u: (_connect_fake_bank(u), _cobertura_open_finance(u, _nubank(u))),
    "limite": lambda u: db.set_card_limit(u, _nubank(u), 10),
}


def _launches(u, **extra):
    return _app(u, "post", "/launches/{uid}",
                json={"tipo": "credito", "valor": 50, "card_id": db.get_default_card_id(u), **extra})


ENTRADAS = {
    "wa_a_vista": lambda u: manda(u, "gastei 80 no cartão nubank no açougue"),
    "wa_credito_compacto": lambda u: manda(u, "credito 120 mercado"),
    "wa_credito_sem_valor": lambda u: manda(u, "credito mercado"),
    "wa_parcelar": lambda u: manda(u, "parcelar 300 em 3x tv no cartao nubank"),
    "wa_parcelar_sem_nome": lambda u: manda(u, "parcelar 300 em 3x no cartao nubank"),
    "ia": lambda u: _ia(u, "add_credit_purchase", {"valor": 45, "descricao": "farmácia"}),
    "app_a_vista": _launches,
    "app_parcelado": lambda u: _launches(u, parcelas=3),
}

# Sem a chave: o trecho da resposta de antes (o mesmo da origin/main).
_SEM_CARTAO_WA = "Crie com: criar cartao"
_SYNC = "sincronizado via Open Finance"
_LIMITE = "Excede o limite"
ANTES = {
    ("wa_a_vista", "sem_cartao"): _SEM_CARTAO_WA,
    ("wa_a_vista", "sincronizado"): "Não registrei",
    ("wa_a_vista", "limite"): _LIMITE,
    ("wa_credito_compacto", "sem_cartao"): "Você não tem cartão padrão",
    ("wa_credito_compacto", "sincronizado"): _SYNC,
    ("wa_credito_compacto", "limite"): _LIMITE,
    ("wa_credito_sem_valor", "sem_cartao"): "Não achei o valor",
    ("wa_credito_sem_valor", "sincronizado"): "Não achei o valor",
    ("wa_credito_sem_valor", "limite"): "Não achei o valor",
    ("wa_parcelar", "sem_cartao"): "Qual cartão?",
    ("wa_parcelar", "sincronizado"): "Parcelamento Registrado",
    ("wa_parcelar", "limite"): _LIMITE,
    ("wa_parcelar_sem_nome", "sem_cartao"): "Qual cartão?",
    ("wa_parcelar_sem_nome", "sincronizado"): "Qual é o nome dessa compra?",
    ("wa_parcelar_sem_nome", "limite"): _LIMITE,
    ("ia", "sem_cartao"): "Você não tem cartão padrão",
    ("ia", "sincronizado"): _SYNC,
    ("ia", "limite"): _LIMITE,
    ("app_a_vista", "sem_cartao"): "Selecione um cartão",
    ("app_a_vista", "sincronizado"): "sincronizados via Open Finance",
    ("app_a_vista", "limite"): "200 ",
    ("app_parcelado", "sem_cartao"): "Selecione um cartão",
    ("app_parcelado", "sincronizado"): "sincronizados via Open Finance",
    ("app_parcelado", "limite"): "200 ",
}


def _compras(u):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) n from credit_transactions where user_id=%s", (u,))
        return cur.fetchone()["n"]


@pytest.fixture(params=sorted(ESTADOS))
def estado(request):
    u = usuario_pagante()
    db.set_balance(u, Decimal("5000"))
    ESTADOS[request.param](u)
    return request.param, u


@pytest.mark.parametrize("entrada", sorted(ENTRADAS))
def test_com_a_chave_a_q36_vem_antes_das_pre_checagens(entrada, estado, chave, ia_fora):
    _nome, u = estado
    chave(u)
    resposta = ENTRADAS[entrada](u)
    assert resposta.removeprefix("🐷 ") == MENSAGENS["cartao"], resposta
    assert _compras(u) == 0
    # nada perguntado: nem o nome da compra, nem o "confirma?" da IA
    assert db.get_pending_action(u) is None
    assert db.ai_get_pending_action(u) is None


@pytest.mark.parametrize("entrada", sorted(ENTRADAS))
def test_sem_a_chave_a_pre_checagem_de_antes_continua(entrada, estado, chave, ia_fora):
    nome, u = estado
    chave(usuario_pagante())
    resposta = ENTRADAS[entrada](u)
    assert MENSAGENS["cartao"] not in resposta, resposta
    assert ANTES[entrada, nome] in resposta, resposta


def test_liberados_no_cartao_com_a_chave(chave, ia_fora):
    """Pagar fatura, criar e listar cartão, ver faturas, apagar compra e o
    import do Open Finance não são compra manual: seguem com a chave."""
    u = usuario_pagante()
    db.set_balance(u, Decimal("5000"))
    cid = _nubank(u)
    db.add_credit_purchase(u, cid, 100, "outros", "compra", today_tz())
    apagar, _due, _bill = db.add_credit_purchase(u, cid, 30, "outros", "outra", today_tz())
    chave(u)
    assert "registrado com sucesso" in manda(u, "criar cartao inter fecha 10 vence 17")
    db.clear_pending_action(u)  # as perguntas do cadastro (lembrete, limite) não importam aqui
    assert "Seus cartões" in manda(u, "cartoes")
    assert "Pagamento registrado" in manda(u, "pagar fatura nubank 50")
    assert "apagada" in manda(u, f"apagar CC{apagar}")
    assert _compras(u) == 1
    _compra_na_conexao(u, f"item-q36c-{u}")
    assert _compras(u) == 2
    assert not ia_fora, ia_fora
