"""Exclusão de conta (S2), falha fechada: item de OUTRO usuário (assinatura ou checkout,
`metadata.finbot_user_id`) em qualquer customer levanta ANTES da primeira escrita --
nada é expirado nem cancelado, em nenhum customer. Reusa o fake `Loja` de
test_conta_exclusao_stripe.

Controle (CLAUDE.md §3), medido: sem a fase de leitura antes das escritas (ordem antiga) ->
h3b, h5; só o checkout sem checar o dono -> h4; as duas -> h3, h3b, h4, h5. Positivo: o
teste n do arquivo irmão (dois customers, tudo da própria conta).
"""
from __future__ import annotations

from test_conta_exclusao_stripe import (  # noqa: F401
    _completo, _conta, _del, _deleting, _segunda_linha, _status, c, contas, emails, loja,
)


def _sem_escrita(loja, uid):
    assert loja.eventos == [] and _status(uid) == "scheduled"


def test_h3_checkout_de_outro_usuario_no_customer_compartilhado_nada_e_expirado(c, contas, loja):
    a, b = _conta(contas, "cus_A3"), _conta(contas, "cus_B3")
    loja.nova("sub_alheia", "cus_B3", dono=a.uid)
    loja.sessao("cs_alheia", "cus_B3")
    loja.sessoes["cs_alheia"]["metadata"] = {"finbot_user_id": str(a.uid)}
    _deleting(_del(c, b, _completo(b)))
    _sem_escrita(loja, b.uid)
    assert loja.sessoes["cs_alheia"]["status"] == "open"


def test_h3b_assinatura_alheia_e_checkout_sem_marca_nada_e_expirado(c, contas, loja):
    a, b = _conta(contas, "cus_A3b"), _conta(contas, "cus_B3b")
    loja.nova("sub_alheia", "cus_B3b", dono=a.uid)
    loja.sessao("cs_sem_marca", "cus_B3b")
    _deleting(_del(c, b, _completo(b)))
    _sem_escrita(loja, b.uid)
    assert loja.sessoes["cs_sem_marca"]["status"] == "open"


def test_h4_so_checkout_de_outro_usuario_tambem_levanta(c, contas, loja):
    a, b = _conta(contas, "cus_A4"), _conta(contas, "cus_B4")
    loja.sessao("cs_alheia", "cus_B4")
    loja.sessoes["cs_alheia"]["metadata"] = {"finbot_user_id": str(a.uid)}
    loja.sessao("cs_minha", "cus_B4")
    loja.sessoes["cs_minha"]["metadata"] = {"finbot_user_id": str(b.uid)}
    _deleting(_del(c, b, _completo(b)))
    _sem_escrita(loja, b.uid)
    assert {s["status"] for s in loja.sessoes.values()} == {"open"}


def test_h5_dois_customers_o_segundo_com_assinatura_alheia_nada_do_primeiro_e_tocado(c, contas, loja):
    a, b = _conta(contas, "cus_A5"), _conta(contas, "cus_B5a")
    _segunda_linha(b, "cus_B5b")
    loja.nova("sub_1", "cus_B5a", dono=b.uid)
    loja.sessao("cs_1", "cus_B5a")
    loja.nova("sub_alheia", "cus_B5b", dono=a.uid)
    _deleting(_del(c, b, _completo(b)))
    _sem_escrita(loja, b.uid)
    assert loja.subs["sub_1"]["status"] == "active" and loja.sessoes["cs_1"]["status"] == "open"
