"""PR-B (ADR 0006): a FORMA dita pelo usuário ("pelo banco", "via pix", "no débito") não entra no alvo da declaração.

O alvo é a evidência de comércio que a conciliação automática exige: "mercado pelo banco" não casava com
"SUPERMERCADO XYZ" e a frase que o texto T4 sugere ("gastei 50,12 no mercado pelo banco") caía justo aí. Cada caso
entra pelo `handle_incoming`, com o extrato já lá, e confere o alvo GRAVADO e o par.

Controle negativo: `banco=False` em `fp.limpar(text, banco=...)` (`launches.add`) deixa vermelhos os casos de forma
dita. Positivo: "no mercado" + resposta "banco" (a forma não está no texto) e "em dinheiro vivo" (Carteira) seguem iguais.
"""
from __future__ import annotations

import pytest

import db
from core.handlers import forma_pagamento as fp
from tests._declaracao_b import ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import BASE, t
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import sincroniza
from utils_date import today_tz

HOJE = (today_tz() - BASE).days


@pytest.fixture
def com_extrato(ana, liga):
    """Ana com a transação 'SUPERMERCADO XYZ' R$ 50 já no extrato, no dia de hoje."""
    cid, _ = _bank(ana)
    sincroniza(cid, ana, t("T1", -50, HOJE, "SUPERMERCADO XYZ"))
    db.import_open_finance_launches(ana)
    return ana


def alvos(uid) -> list[tuple[str, bool]]:
    return [(d["alvo"], d["matched_transaction_id"] is not None) for d in declaracoes(uid)]


@pytest.mark.parametrize("forma", ["pelo banco", "via pix", "no pix", "no débito", "por transferência",
                                   "no cartão de débito", "pela transferência", "com pix", "no banco"])
def test_forma_dita_nao_entra_no_alvo_e_casa(com_extrato, forma):
    manda_wamid(com_extrato, f"gastei 50 no mercado {forma}")
    assert alvos(com_extrato) == [("mercado", True)]


def test_audio_com_forma_dita(com_extrato, monkeypatch):
    fala(monkeypatch, com_extrato, "gastei 50 no mercado pelo banco")
    assert alvos(com_extrato) == [("mercado", True)]


def test_duas_frases_com_a_forma_no_fim_recusam(com_extrato):
    """E1 (#1310): um gasto por mensagem na declaração."""
    assert "um gasto por mensagem" in manda_wamid(com_extrato, "gastei 50 no mercado e 30 na padaria pelo banco")[0]
    assert declaracoes(com_extrato) == []


def test_forma_sem_comercio_pergunta_o_que_foi_e_depois_casa(com_extrato):
    assert "Em que você gastou R$ 50,00?" in manda_wamid(com_extrato, "gastei 50 pelo banco")[0]
    manda_wamid(com_extrato, "mercado")
    assert alvos(com_extrato) == [("mercado", True)]


def test_positivo_forma_so_na_resposta_continua_casando(com_extrato):
    manda_wamid(com_extrato, "gastei 50 no mercado")
    manda_wamid(com_extrato, "banco")
    assert alvos(com_extrato) == [("mercado", True)]


def test_positivo_dinheiro_vivo_e_carteira_sem_declarar(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado em dinheiro vivo")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1 and carteira(ana) == 150.0
    assert q("select alvo from launches where user_id=%s and source is distinct from 'open_finance'", (ana,))["alvo"] == "mercado"


def test_flag_desligada_nao_toca_no_texto(ana):
    manda_wamid(ana, "gastei 50 no mercado pelo banco")
    assert n_lancamentos(ana) == 0 and declaracoes(ana) == []


@pytest.mark.parametrize("comercio", ["banco de dados", "Pixar", "Banco Inter", "Banco do Brasil"])
def test_nome_de_comercio_com_palavra_de_forma_fica_inteiro(com_extrato, comercio):
    r = manda_wamid(com_extrato, f"gastei 50 em {comercio}")
    if "dinheiro vivo ou passou pelo banco" in r[0]:  # sem forma dita: o Piggy pergunta
        manda_wamid(com_extrato, "banco")
    assert [d["alvo"].lower() for d in declaracoes(com_extrato)] == [comercio.lower()]


def test_no_banco_inter_direto_fica_inteiro(com_extrato):
    manda_wamid(com_extrato, "gastei 50 no Banco Inter")
    assert [d["alvo"].lower() for d in declaracoes(com_extrato)] == ["banco inter"]


@pytest.mark.parametrize("dito,limpo", [
    ("gastei 50 no mercado pelo banco", "gastei 50 no mercado"),
    ("gastei 50 no mercado, pelo banco.", "gastei 50 no mercado,."),
    ("gastei 50 no mercado via pix", "gastei 50 no mercado"),
    ("gastei 50 no mercado no pix", "gastei 50 no mercado"),
    ("gastei 50 no mercado no débito", "gastei 50 no mercado"),
    ("gastei 50 no mercado por transferência", "gastei 50 no mercado"),
    ("gastei 50 no Pixar", "gastei 50 no Pixar"),
    ("gastei 50 em banco de dados", "gastei 50 em banco de dados"),
    ("gastei 50 no banco de dados", "gastei 50 no banco de dados"),
    ("gastei 50 no Banco Inter", "gastei 50 no Banco Inter"),
    ("gastei 50 pelo banco", "gastei 50"),
])
def test_limpar_banco(dito, limpo):
    assert fp.limpar(dito, banco=True) == limpo
    assert fp.limpar(dito) == dito  # sem o parâmetro (Carteira, flag desligada) não muda nada


def test_exemplo_do_texto_t4_grava_o_alvo_limpo(ana, liga):
    """O que o T4 sugere ao usuário ("gastei 50,12 no mercado pelo banco") tem de funcionar como disse."""
    manda_wamid(ana, "gastei 50,12 no mercado pelo banco")
    assert [d["alvo"] for d in declaracoes(ana)] == ["mercado"]


# --- uma só lista de formas: o que `detectar` chama de banco, `limpar(banco=True)` tira do alvo ---
FORMAS = ["pelo banco", "no banco", "pix", "PIX", "no pix", "via pix", "pelo pix", "pelo pix mesmo", "usando o pix",
          "de pix", "com pix", "TED", "por TED", "no débito", "débito", "no débito automático", "no cartão",
          "no cartão de débito", "cartão de crédito", "no cartão de crédito", "por transferência",
          "via transferência bancária", "pela transferência"]
COMERCIOS = ["mercado", "padaria", "uber"]
FRASES = [(f"gastei 50 no {c} {f}", f"gastei 50 no {c}") for f in FORMAS for c in COMERCIOS] \
    + [(f"gastei 50 {f} no {c}", f"gastei 50 no {c}") for f in FORMAS for c in COMERCIOS]


@pytest.mark.parametrize("dito,limpo", FRASES)
def test_toda_forma_detectada_sai_do_alvo(dito, limpo):
    assert fp.detectar(dito) == fp.BANCO
    assert fp.limpar(dito, banco=True) == limpo


@pytest.mark.parametrize("frase", ["gastei 50 no Pixar", "gastei 50 no pixel", "gastei 50 em banco de dados",
                                   "gastei 50 no Banco Inter", "gastei 50 no Banco Inter no mercado",
                                   "gastei 50 pelo Banco do Brasil", "gastei 50 na bancada",
                                   "gastei 50 em cartão de visita", "gastei 50 no Tedx"])
def test_nome_de_comercio_nao_perde_pedaco(frase):
    assert fp.limpar(frase, banco=True) == frase


@pytest.mark.parametrize("dito", ["gastei 50 pelo banco no mercado", "gastei 50 usando o pix no mercado",
                                  "gastei 50 no mercado pelo pix mesmo", "gastei 50 no mercado TED",
                                  "gastei 50 no mercado no débito automático", "gastei 50 no mercado via transferência bancária"])
def test_forma_em_qualquer_posicao_pelo_handle_incoming(com_extrato, dito):
    manda_wamid(com_extrato, dito)
    assert alvos(com_extrato) == [("mercado", True)]


def test_audio_com_pedaco_sem_valor_tambem_limpa_a_forma(ana, liga, monkeypatch):
    """`rotear_partes` enfileira o pedaço sem valor: a descrição que vira alvo também sai sem a forma dita."""
    fala(monkeypatch, ana, "gastei 40 no mercado e paguei o aluguel pelo banco")
    assert [i["desc"] for i in db.get_pending_action(ana)["payload"]["queue"]] == ["aluguel"]
    manda_wamid(ana, "1500")
    assert sorted(d["alvo"] for d in declaracoes(ana)) == ["aluguel", "mercado"]


def test_caminho_entities_tambem_limpa_a_forma(com_extrato):
    """M2: OCR e IA entregam `entities` com o alvo já sujo ("mercado pelo pix"); a resposta "banco" declara com "mercado"."""
    fp.perguntar(com_extrato, {"fluxo": "entities", "platform": "whatsapp",
                               "entities": {"tipo": "despesa", "valor": 50.0, "alvo": "mercado pelo pix"}}, "?")
    manda_wamid(com_extrato, "banco")
    assert alvos(com_extrato) == [("mercado", True)]


def test_caminho_entities_resposta_sem_cabecalho_repetido(com_extrato):
    fp.perguntar(com_extrato, {"fluxo": "entities", "platform": "whatsapp",
                               "entities": {"tipo": "despesa", "valor": 50.0, "alvo": "mercado"}}, "?")
    (r,) = manda_wamid(com_extrato, "banco")
    assert r.startswith("💸") and "Lançamento registrado" not in r, r


# --- A1: palavra de enchimento no FIM do alvo da declaração (lista fechada) ---
@pytest.mark.parametrize("dito", [
    "gastei 50 no mercado no pix mesmo", "gastei 50 no mercado no pix tbm", "gastei 50 no mercado pelo pix também",
    "gastei 50 no mercado pelo pix tambem", "gastei 50 no mercado pelo banco mesmo.", "gastei 50 no mercado mesmo pelo pix",
    "gastei 50 no mercado tb no pix", "gastei 50 no mercado aqui no pix", "gastei 50 no mercado no pix aqui mesmo",
    "gastei 50 no mercado PIX TBM",
])
def test_enchimento_no_fim_do_alvo_sai(dito):
    assert fp.detectar(dito) == fp.BANCO
    assert fp.limpar(dito, banco=True).rstrip(".") == "gastei 50 no mercado"


@pytest.mark.parametrize("dito", ["gastei 50 no mercado mesmo no pix", "gastei 50 no mercado tbm no pix",
                                  "gastei 50 no mercado aqui pelo banco"])
def test_enchimento_pelo_handle_incoming_casa_sozinho(com_extrato, dito):
    manda_wamid(com_extrato, dito)
    assert alvos(com_extrato) == [("mercado", True)]


def test_enchimento_so_na_declaracao_e_so_no_fim():
    """Positivo: a Carteira (`banco=False`) e o meio do alvo ficam como estão; "hoje"/"ontem" são data, não entram."""
    assert fp.limpar("gastei 50 no mercado mesmo") == "gastei 50 no mercado mesmo"
    assert fp.limpar("gastei 50 no mercado mesmo da esquina no pix", banco=True) == "gastei 50 no mercado mesmo da esquina"
    assert fp.limpar("gastei 50 no mercado hoje no pix", banco=True) == "gastei 50 no mercado hoje"
