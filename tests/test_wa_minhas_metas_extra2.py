"""PR C2 (Metas), passada 2 do Tester. Controles (injetados e restaurados): sem o `$` de `_METAS_PERGUNTA` ->
`test_ancora_*`; `metas?` no regex -> `test_singular_*`; sem "pras" no regex -> `test_pras_*`; `bloco()` fora do
`try` de `texto_whatsapp` -> `test_texto_*`; veto de catálogo por contenção para todos os intents ->
`test_n_com_pendencia_*`; teto sem a folga de 100 em `texto_whatsapp` -> `test_folga_*`;
`_pede_metas` sempre False -> `test_forma_*`.
"""
from __future__ import annotations

import pytest

import core.handlers.launches as launches
import db
from conftest import usuario_pagante
from core.handlers.launches import _WPP_MAX_CHARS, _wpp_len
from core.intent_classifier import classify
from core.intent_router import _AVISO_PERGUNTA_CANCELADA
from core.services import metas_conversa as mc
from tests._pendencia_credito_helpers import AVISO, arma_pay_bill_choice, diga as diga_cartao, novo_uid
from tests.test_ai_chat_metas import nova
from tests.test_wa_minhas_metas import (  # noqa: F401  (fixtures)
    _falso, _mais_longa, chave, com_meta, diga, sem_pendencia, tier3)
from tests.test_wa_minhas_metas_extra import com_caixinha, saldo_de


@pytest.mark.parametrize("frase", [
    "como estão minhas metas de viagem", "como estão as metas do mês", "quanto falta pra minhas metas de 2028",
    "quanto falta para as metas de gastos", "como anda minhas metas hoje", "quanto falta pras metas de 2028",
])
def test_ancora_pergunta_com_cauda_nao_e_metas_list(chave, frase):
    """Sem o `$`, a pergunta com cauda (outro assunto, um período) listaria TODAS as metas."""
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert classify(frase, user_id=k, allow_ai=False).intent != "metas.list", frase


@pytest.mark.parametrize("frase", [
    "quanto falta pra meta", "quanto falta para a meta", "como anda meta", "como estao as meta", "quanto falta pra minha meta",
])
def test_singular_e_resto_da_regex_nao_e_metas_list(chave, frase):
    k = usuario_pagante(plan="pro_max")
    chave(k)
    assert classify(frase, user_id=k, allow_ai=False).intent != "metas.list", frase


@pytest.mark.parametrize("frase", ["quanto falta pras metas", "quanto falta pras minhas metas", "Quanto falta pras metas?"])
def test_pras_e_metas_list_so_para_k(chave, frase):
    k, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(k)
    assert classify(frase, user_id=k, allow_ai=False).intent == "metas.list"
    assert classify(frase, user_id=n, allow_ai=False).intent != "metas.list"


def test_n_com_pendencia_comando_de_saldo_ainda_abandona(chave, tier3):
    """N idêntico à `main`: caixinha "saldo" + "saquei 200" + "meu saldo" abandona (o saque NÃO executa)."""
    n = usuario_pagante(plan="pro_max")
    chave()
    db.add_launch_and_update_balance(n, "receita", 1000.0, alvo="salário", nota="s", categoria="salário",
                                     is_internal_movement=False)
    db.create_pocket(n, "saldo")
    db.pocket_deposit_from_account(n, "saldo", 300.0)
    diga(n, "saquei 200", "meu saldo")
    assert saldo_de(n, "saldo") == 300.0


def test_texto_com_nome_nulo_nao_lanca(monkeypatch):
    """`texto_whatsapp` promete nunca lançar: o `bloco()` roda dentro do mesmo `try` da leitura."""
    monkeypatch.setattr(mc, "ler", lambda uid: {"metas": [{}]})
    monkeypatch.setattr(mc, "para_conversa", lambda d: {"metas": [{"nome": None, "resumo": "r", "avisos": []}],
                                                        "sem_metas": False, "mais": None})
    assert mc.texto_whatsapp(1) == "Não consegui ler suas metas agora. Tenta de novo daqui a pouco."


def test_cartao_chamado_metas_k_abandona_em_vez_de_pagar_limite_declarado(chave, tier3):
    """Limite declarado: não há veto de catálogo nas pendências de cartão (cartão chamado `Conta` já perde a
    resposta `conta` na `main`). K com cartão "Metas": a resposta "metas" ABANDONA (fail-safe, nada é pago;
    N pagaria) e "listar metas" lista; em nenhum dos dois a fatura é paga."""
    k1, k2 = novo_uid(), novo_uid()
    for k in (k1, k2):  # comprar no cartão à mão é recusado depois da chave: arma antes
        arma_pay_bill_choice(k, "Metas")
    chave(k1, k2)
    for k in (k1, k2):
        nova(k, "Viagem A", "1000", "300")
    r = diga_cartao(k1, "metas")
    assert AVISO in r and "Suas metas" in r and len(db.list_open_bills(k1)) == 1
    r = diga_cartao(k2, "listar metas")
    assert AVISO in r and "Suas metas" in r and len(db.list_open_bills(k2)) == 1


def test_folga_o_aviso_de_pergunta_cancelada_nao_estoura_os_4096(monkeypatch):
    """O aviso entra NA FRENTE da lista (`route` põe `_AVISO_PERGUNTA_CANCELADA + resposta`): a lista tem de
    deixar folga. Texto natural entre 4096 - aviso e 4096: sem a folga o total passa dos 4096."""
    avisos = ["outra_moeda", "banco_desatualizado", "moeda_presumida", "conta_fora_do_ultimo_sync", "saldo_ausente"]

    def metas(pad):
        return [{**_mais_longa(), "nome": "x" * pad if i == 0 else f"M{i}", "motivos": avisos} for i in range(7)]
    with monkeypatch.context() as m:
        m.setattr(launches, "_WPP_MAX_CHARS", 10 ** 9)  # mede o texto natural, sem corte
        for pad in range(2, 600):
            _falso(m, *metas(pad))
            if _WPP_MAX_CHARS - _wpp_len(_AVISO_PERGUNTA_CANCELADA) < _wpp_len(mc.texto_whatsapp(1)) <= _WPP_MAX_CHARS:
                break
        else:
            pytest.fail("não achou texto natural na janela do aviso")
    _falso(monkeypatch, *metas(pad))
    r = mc.texto_whatsapp(1)
    assert "e mais" in r and _wpp_len(_AVISO_PERGUNTA_CANCELADA + r) <= _WPP_MAX_CHARS


def test_forma_de_pagamento_pendente_e_abandonada_por_minhas_metas_sem_gravar(chave, tier3, monkeypatch):
    """`payment_method_choice` ("dinheiro vivo ou banco?") abandona com o aviso, como "minhas caixinhas"."""
    monkeypatch.setattr("core.handlers.forma_pagamento.regra_ativa", lambda u: True)
    k = com_meta(chave)
    r0, r1 = diga(k, "gastei 500 no mercado", "minhas metas")
    assert "dinheiro vivo" in r0 and "Cancelei a pergunta anterior" in r1 and "Suas metas" in r1
    assert sem_pendencia(k) and db.list_launches(k, limit=50) == []
