"""PR-B (ADR 0006), G-rejeição: o que o escritor da declaração recusa vira mensagem do Piggy, nunca 500 nem código.

Mais de 2 casas é RECUSA (T4), nunca arredondamento; categoria interna é T6; nada grava e nenhuma pendência fica armada.
Controle negativo: tirar a tradução de `ValueError` (`declaracao_banco.recusa` devolvendo None) faz V2/C1 subirem a
exceção `DECLARACAO_*` até o handler. Positivo do grupo: V1, V3 e C2 gravam.
"""
from __future__ import annotations

import uuid

import pytest

import db
from tests._declaracao_b import (ana, carteira, declaracoes, liga, manda_wamid, n_lancamentos,  # noqa: F401
                                 pendencia,
                                 sem_pergunta)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401


def test_duas_casas_grava(ana, liga):
    manda_wamid(ana, "gastei 50,12 no mercado no pix")
    assert [float(d["valor"]) for d in declaracoes(ana)] == [50.12]


@pytest.mark.parametrize("texto,valor", [
    ("gastei 1,1 mil no aluguel no pix", 1100.0), ("gastei 19,99 na padaria no pix", 19.99),
    ("gastei R$ 1.234,56 no mercado no pix", 1234.56), ("gastei 33,3 no mercado no pix", 33.3),
    ("gastei 50 e 12 no mercado no pix", None),
])
def test_entradas_reais_nao_estouram_o_limite_de_casas(ana, liga, texto, valor):
    r = manda_wamid(ana, texto)[0]
    assert "2 casas" not in r, r
    if valor is not None:
        assert [float(d["valor"]) for d in declaracoes(ana)] == [valor]


@pytest.mark.parametrize("texto", ["gastei 50,123 no mercado no pix", "gastei 0,001 no mercado no pix"])
def test_tres_casas_recusa_com_a_mensagem_e_nao_grava(ana, liga, texto):
    r = manda_wamid(ana, texto)[0]
    assert r.startswith("🐷 Não registrei: o valor aceita no máximo 2 casas decimais"), r
    assert "DECLARACAO" not in r and "gastei 50,12 no mercado pelo banco" in r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and sem_pergunta(ana)


def test_tres_casas_citam_o_valor_do_usuario_com_virgula(ana, liga):
    assert "(você mandou 50,123)" in manda_wamid(ana, "gastei 50,123 no mercado no pix")[0]


def test_tres_casas_na_resposta_a_pergunta_tambem_recusa(ana, liga):
    manda_wamid(ana, "gastei 50,123 no mercado")
    r = manda_wamid(ana, "banco")[0]
    assert "2 casas" in r and declaracoes(ana) == [] and sem_pergunta(ana)


def test_um_recusa_outro_grava(ana, liga):
    """V6: o pedaço com 3 casas é recusado (T4) e o outro é declarado."""
    r = manda_wamid(ana, "gastei 50,123 no mercado e 30 na padaria no pix")[0]
    assert "2 casas" in r and "registrada pelo banco" in r and "R$ 30,00" in r, r
    assert [float(d["valor"]) for d in declaracoes(ana)] == [30.0]


def test_carteira_aceita_tres_casas_como_hoje(ana):
    """Fora do PR: a recusa de 3 casas é só da declaração (a Carteira aceita hoje; `launches.valor` é numeric)."""
    manda_wamid(ana, "gastei 50,123 no mercado em dinheiro")
    assert n_lancamentos(ana) == 1 and declaracoes(ana) == []


@pytest.mark.parametrize("texto", ["gastei 200 no mercado #investimento no pix",
                                   "gastei 200 no mercado #transferencia_interna no pix"])
def test_categoria_interna_recusa_com_mensagem(ana, liga, texto):
    r = manda_wamid(ana, texto)[0]
    assert r.startswith("🐷 Não registrei como pelo banco: isso parece uma movimentação interna"), r
    assert "DECLARACAO" not in r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0 and sem_pergunta(ana)


@pytest.mark.parametrize("texto", ["gastei 200 em cdb no pix", "gastei 200 no tesouro direto no pix"])
def test_categoria_interna_so_inferida_registra_sem_categoria(ana, liga, texto):
    """M4: a categoria interna que o sistema INFERIU não recusa; vira "outros" (o que a coluna guarda sem categoria)."""
    r = manda_wamid(ana, texto)[0]
    assert "registrada pelo banco" in r and "movimentação interna" not in r, r
    (d,) = declaracoes(ana)
    assert d["categoria"] == "outros" and carteira(ana) == 200.0


def test_categoria_interna_na_imagem_confirmada_registra_sem_categoria(ana, liga):
    """O recibo lido por OCR traz a categoria do sistema, não a do usuário: M4 vale também aqui."""
    db.set_pending_action(ana, "confirm_media_launch", {
        "tipo": "despesa", "valor": 50.0, "alvo": "x", "categoria": "investimento_aporte", "data": None,
        "platform": "whatsapp", "text": "t"})
    manda_wamid(ana, "sim")
    r = manda_wamid(ana, "banco")[0]
    assert "registrada pelo banco" in r, r
    assert [d["categoria"] for d in declaracoes(ana)] == ["outros"]


def test_categoria_comum_grava(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado no pix")
    assert declaracoes(ana)[0]["categoria"] == "mercado"


def test_mensagens_de_hoje_para_valor_zero_intactas(ana, liga):
    assert manda_wamid(ana, "gastei 0 no mercado no pix") == ["Não consegui identificar o valor. Tente: *gastei 50 no mercado*"]
    assert declaracoes(ana) == []


def _no_teto(monkeypatch):
    from core.services import plan_service
    from core.services.plan_limits import PlanLimitExceeded

    def estoura(uid):
        raise PlanLimitExceeded("launches_month", "UPGRADE-TESTE")
    monkeypatch.setattr(plan_service, "check_can_create_launch", estoura)


def test_teto_do_plano_gratis_nao_grava_declaracao(ana, liga, monkeypatch):
    """L1: o teto mensal de lançamentos vale para a declaração (a mesma `PlanLimitExceeded` da Carteira)."""
    _no_teto(monkeypatch)
    assert manda_wamid(ana, "gastei 50 no mercado no pix") == ["UPGRADE-TESTE"]
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_reentrega_no_teto_diz_ja_registrei_e_chave_nova_continua_barrada(ana, liga, monkeypatch):
    """A declaração que lotou o mês já está gravada: a reentrega do MESMO wamid diz T3, não "faça upgrade". Uma chave
    nova (outro wamid) segue sujeita ao teto."""
    w = "wamid.teto-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 50 no mercado no pix", w)[0]
    _no_teto(monkeypatch)
    assert "já tinha registrado" in manda_wamid(ana, "gastei 50 no mercado no pix", w)[0]
    assert manda_wamid(ana, "gastei 50 no mercado no pix") == ["UPGRADE-TESTE"]
    assert len(declaracoes(ana)) == 1


# --- A2: valor abreviado ("2,2k", "50 centavos") em declaração: recusa sem gravar (T10) ---
@pytest.mark.parametrize("texto,trecho", [
    ("gastei 2,2k no mercado no pix", "2,2k"), ("gastei 2k no mercado pelo banco", "2k"),
    ("gastei 2K no mercado no pix", "2K"), ("gastei 1.5k na padaria via pix", "1.5k"),
    ("gastei 50 centavos na padaria no pix", "50 centavos"), ("paguei R$2,2k de aluguel no pix", "2,2k"),
    ("gastei 30 reais e 50 centavos no mercado no pix", "50 centavos"),
    ("gastei trinta reais e cinquenta centavos no mercado no pix", "cinquenta centavos"),
    ("gastei cinquenta centavos na padaria no pix", "cinquenta centavos"),
    ("gastei trinta e cinco reais e cinquenta centavos no mercado no pix", "cinquenta centavos"),
    ("gastei 1,5 mi no mercado no pix", "1,5 mi"), ("gastei 2kk no mercado no pix", "2kk"),
    ("gastei 25 cents no mercado no pix", "25 cents"), ("gastei 2,2  k no mercado no pix", "2,2  k"),
    ("gastei dois k no mercado no pix", "dois k"),
    ("gastei 10k na corrida no pix", "10k"), ("gastei 99 centavos na padaria no pix", "99 centavos"),  # falso positivo seguro
    ("gastei 18k no anel no pix", "18k"),
    ("gastei 2k no mercado e 30 na padaria no pix", "2k"),  # duas despesas: a mensagem inteira volta, nada grava
])
def test_valor_abreviado_recusa_sem_gravar(ana, liga, texto, trecho):
    r = manda_wamid(ana, texto)[0]
    assert r.startswith(f"🐷 Não registrei: não tenho certeza do valor (você mandou {trecho})."), r
    assert "DECLARACAO" not in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


def test_valor_abreviado_na_resposta_a_pergunta_de_forma(ana, liga):
    manda_wamid(ana, "gastei 2,2k no mercado")
    r = manda_wamid(ana, "pix")[0]
    assert "você mandou 2,2k" in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_valor_abreviado_no_audio(ana, liga, monkeypatch):
    from tests._declaracao_b import fala
    r = fala(monkeypatch, ana, "gastei 2k no mercado no pix")
    assert any("você mandou 2k" in x for x in r) and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_valor_abreviado_na_fila_devolve_o_item(ana, liga, monkeypatch):
    """A resposta "2,2k" ao "quanto foi o aluguel?" não grava 2,20: o item volta e o número inteiro grava."""
    from tests._declaracao_b import fala
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    assert pendencia(ana) == "multi_launch_values"
    r = manda_wamid(ana, "2,2k")[0]
    assert "você mandou 2,2" in r and "aluguel" in r and declaracoes(ana) == [], r
    assert "registrada pelo banco" in manda_wamid(ana, "2200")[0]
    assert [float(d["valor"]) for d in declaracoes(ana)] == [2200.0]


@pytest.mark.parametrize("texto,valor", [
    ("gastei 2200 no mercado no pix", 2200.0), ("gastei 2 mil no mercado no pix", 2000.0),
    ("gastei 10 km de uber no pix", 10.0), ("gastei 1,5 mil na padaria no pix", 1500.0),
    ("gastei 30 em arroz 5 kg no pix", 30.0),
])
def test_positivo_valor_por_extenso_ou_numero_grava(ana, liga, texto, valor):
    manda_wamid(ana, texto)
    assert [float(d["valor"]) for d in declaracoes(ana)] == [valor]


def test_valor_abreviado_na_carteira_e_com_a_flag_desligada_nao_muda(ana, monkeypatch):
    """A recusa é só da declaração: em dinheiro grava como hoje; sem a flag, a recusa de hoje."""
    assert "Não registrei" in manda_wamid(ana, "gastei 2,2k no mercado no pix")[0]
    manda_wamid(ana, "gastei 2,2k no mercado em dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1


@pytest.mark.parametrize("fala_", ["gastei dois mil e duzentos e cinquenta centavos no mercado no pix",
                                   "gastei trinta reais e cinquenta centavos no mercado no pix",
                                   "gastei dois k no mercado no pix"])
def test_valor_abreviado_por_extenso_no_audio(ana, liga, monkeypatch, fala_):
    """Transcrição por extenso (o Whisper escreve assim): nenhum pedaço grava errado; o que recusa diz T10."""
    from tests._declaracao_b import fala
    r = fala(monkeypatch, ana, fala_)
    assert any("não tenho certeza do valor" in x for x in r), r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0
