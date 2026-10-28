"""PR-B (ADR 0006): a declaração só grava se o VALOR foi lido de forma inequívoca (`core/handlers/declaracao_valor.py`;
tabela forma x canal em `.time-dev/pr-b/enumeracao-valor.md`). Cada caso roda por TEXTO e por ÁUDIO (`handle_incoming`,
Postgres real) e a recusa nunca grava, nunca consome a chave `wa:<hash>:<n>` e nunca deixa pergunta de pé.

Controle negativo: `declaracao_valor.recusa` devolvendo None deixa vermelhos os grupos de recusa; a guarda de decisão BANCO
de `rotear_partes` tirada deixa vermelho o grupo de cenários sem declaração. Positivos: valores claros e dois pedaços com
descrição gravam; Carteira, flag desligada e fora da lista seguem como antes.
"""
from __future__ import annotations

import pytest

from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos,  # noqa: F401
                                 pendencia, q, sem_pergunta)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests.test_bank_movements import _bank


def chaves(uid) -> int:
    return q("select count(*) n from launches where user_id=%s and external_id like 'wa:%%'", (uid,))["n"]


def diz(ana, modo, monkeypatch, texto) -> str:
    return "\n".join(manda_wamid(ana, texto) if modo == "texto" else fala(monkeypatch, ana, texto))


RECUSAS = [
    # F1: "N e M" com moeda, símbolo, gíria, "mais", extenso + dígito; "e N" sem descrição
    "gastei R$ 2,00 e R$ 50,00 no mercado pelo banco", "gastei R$2 e R$50 no mercado pelo banco",
    "gastei 2 reais e R$ 50 no mercado pelo banco", "gastei 2 reais, 50 no mercado pelo banco",
    "gastei 2 contos e 50 no mercado pelo banco", "gastei 2 pila e 50 no mercado pelo banco",
    "gastei 2 mais 50 no mercado pelo banco", "gastei 2 reais mais 50 no mercado pelo banco",
    "gastei dois e 50 no mercado pelo banco", "gastei duas e 50 no mercado pelo banco",
    "paguei mil e 200 de aluguel pelo banco", "gastei 10 reais e paguei o aluguel pelo banco",
    # F2: valor lido pela metade, sem "e" entre dois dígitos
    "paguei mil e quinhentos de aluguel pelo banco", "paguei mil e duzentos de aluguel pelo banco",
    "paguei 1 mil e duzentos de aluguel pelo banco", "gastei 2 e meio no mercado pelo banco",
    "gastei dois e meio no mercado pelo banco", "gastei dois reais e meio no mercado pelo banco",
    "gastei três reais e meio no mercado pelo banco", "gastei dois reais cinquenta no mercado pelo banco",
    "gastei dois cinquenta no mercado pelo banco", "gastei 2 reais 50 no mercado pelo banco",
    "gastei 2 reais com 50 no mercado pelo banco", "gastei 2 e cinquenta no mercado pelo banco",
    "gastei doís e cinqüenta no mercado pelo banco",
    # F3: a ambiguidade cruza pedaços que têm descrição dos dois lados
    "gastei 100 no mercado dia 20 e 21 de outubro pelo banco", "gastei 50 hoje e 30 ontem pelo banco",
    "paguei 3x de 100 e 50 de juros pelo banco", "gastei 30 no mercado e 20 hoje pelo banco",
    "gastei dia 5 e 30 na padaria pelo banco",  # o splitter corta "gastei dia 5": sem a data tirada do pedaço, grava R$ 5
    # a descrição que sobra do parse começa com número (`_ALVO_NUMERO_RE`). "15 no 99" é o N9 do DP-10 (descrição só
    # numérica recusa): fixa o que HOJE acontece, o leitor estrito da issue #1310 pode mudar.
    "gastei 3 mil 200 no mercado pelo banco", "gastei 20 a 30 no mercado pelo banco", "gastei 15 no 99 pelo banco",
    # centena + "e" + qualquer coisa que não seja unidade, 11 a 19 ou dezena
    "gastei cento e meio no mercado pelo banco", "gastei duzentos e meio no mercado pelo banco",
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto", RECUSAS)
def test_valor_nao_inequivoco_recusa_sem_gravar(ana, liga, monkeypatch, modo, texto):
    r = diz(ana, modo, monkeypatch, texto)
    assert "Não registrei: não sei se *" in r and "Manda de novo só com números" in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0
    assert chaves(ana) == 0 and sem_pergunta(ana)


POSITIVOS = [
    ("gastei 2200 no mercado pelo banco", [2200.0]), ("gastei 12,90 no mercado pelo banco", [12.9]),
    ("gastei vinte e cinco no mercado pelo banco", [25.0]), ("gastei trinta e cinco reais no mercado pelo banco", [35.0]),
    ("gastei duzentos e cinquenta no mercado pelo banco", [250.0]), ("gastei cento e vinte no mercado pelo banco", [120.0]),
    ("gastei dois mil e quinhentos no mercado pelo banco", [2500.0]),
    ("gastei dois mil e duzentos reais no mercado pelo banco", [2200.0]),
    ("gastei 20 no mercado e 30 na padaria pelo banco", [20.0, 30.0]),
    ("gastei 25 no uber e 15 no mercado pelo banco", [15.0, 25.0]),
    ("gastei 80 pro joao e 20 pro pedro pelo banco", [20.0, 80.0]),
    ("paguei luz 120 e paguei internet 100 pelo banco", [100.0, 120.0]),
    ("gastei 20 reais no mercado e 30 reais na padaria pelo banco", [20.0, 30.0]),
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto,valores", POSITIVOS)
def test_positivo_valor_inequivoco_grava(ana, liga, monkeypatch, modo, texto, valores):
    diz(ana, modo, monkeypatch, texto)
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == valores
    assert chaves(ana) == len(valores)


@pytest.mark.parametrize("texto", ["gastei 50 no mercado e 30 na padaria no cartão",
                                   "gastei 50 no mercado e 30 na padaria no cartão de crédito",
                                   "gastei 50 no mercado pelo banco e 30 na padaria no cartão"])
@pytest.mark.parametrize("modo", ["texto", "audio"])
def test_cartao_numa_frase_de_varios_pedacos_e_t8_nos_dois_canais(ana, liga, monkeypatch, modo, texto):
    r = diz(ana, modo, monkeypatch, texto)
    assert "as compras no cartão de crédito conectado chegam sozinhas" in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and chaves(ana) == 0


def test_cartao_no_audio_com_a_flag_desligada_nao_muda(ana, monkeypatch):
    """Positivo: sem a flag o áudio de vários pedaços segue o caminho de sempre (nada declara, cada pedaço no seu fluxo)."""
    fala(monkeypatch, ana, "gastei 50 no mercado e 30 na padaria no cartão")
    assert declaracoes(ana) == [] and chaves(ana) == 0


# T2: a guarda "flag ligada + decisão BANCO" de `rotear_partes`. Fora dela o áudio "2 reais e 50" fica EXATAMENTE como antes
# (os resultados abaixo foram medidos antes da recusa existir).
def _cenario(uid_pro, monkeypatch, cen):
    if cen != "sem_banco":
        _bank(uid_pro)
    if cen == "fora_lista":
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
        monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", "999999999")
    elif cen not in ("flag_off",):
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
    return "em dinheiro" if cen in ("dinheiro",) else "pelo banco"


def _lancamentos(uid):
    return [(float(x["valor"]), x["alvo"]) for x in q(
        "select valor, alvo from launches where user_id=%s and coalesce(source,'manual')<>'open_finance' order by id",
        (uid,), fetch=True)]


@pytest.mark.parametrize("cen,lancamentos,diz_", [
    ("flag_off", [], "Não registrei: com banco conectado"), ("fora_lista", [], "Não registrei: com banco conectado"),
    ("sem_banco", [(2.0, "gastei 50 no mercado pelo banco")], "Em que você gastou R$ 2,00?"),
    ("dinheiro", [(2.0, "gastei 50 no mercado")], "Em que você gastou R$ 2,00?"),
])
def test_audio_2_reais_e_50_fora_da_declaracao_fica_como_antes(uid_pro, ia_fora, monkeypatch, cen, lancamentos, diz_):
    forma = _cenario(uid_pro, monkeypatch, cen)
    r = "\n".join(fala(monkeypatch, uid_pro, f"gastei 2 reais e 50 no mercado {forma}"))
    assert diz_ in r and "não sei se *" not in r, r
    assert _lancamentos(uid_pro) == lancamentos and declaracoes(uid_pro) == [] and chaves(uid_pro) == 0


def test_recusa_nao_consome_a_chave_da_proxima_mensagem(ana, liga):
    """A recusa não gasta o `<n>` da chave do turno: a mensagem seguinte (outro wamid) declara normalmente."""
    assert "não sei se *" in manda_wamid(ana, "gastei 2 reais e 50 no mercado pelo banco")[0]
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 2,50 no mercado pelo banco")[0]
    assert chaves(ana) == 1 and [float(d["valor"]) for d in declaracoes(ana)] == [2.5]


@pytest.mark.parametrize("texto", ["gastei 50" + " " * 16000 + "no mercado pelo banco", "gastei " + "1," * 8000,
                                   "pix " * 4000, "gastei 50 no mercado " + "e 1 " * 4000])
def test_texto_patologico_nao_trava_a_recusa(texto):
    """As regex são quadráticas em texto patológico: só os primeiros 1000 caracteres são analisados."""
    import time
    from core.handlers import declaracao_valor
    t0 = time.perf_counter()
    declaracao_valor.recusa(texto)
    assert time.perf_counter() - t0 < 0.5


# --- E1/E2: composição de numerais por extenso (tabela no ADR 0006); moeda coloquial; "mil e <extenso>" ---
RECUSAS_EXTENSO = [
    "gastei um real e cinquenta no mercado pelo banco", "gastei um real e meio no mercado pelo banco",
    "gastei vinte reais e cinquenta no mercado pelo banco", "gastei trinta reais e cinquenta no mercado pelo banco",
    "gastei dez reais e cinquenta no mercado pelo banco", "gastei dois reais e quinze no mercado pelo banco",
    "gastei doze e noventa no mercado pelo banco", "gastei quinze e cinquenta no mercado pelo banco",
    "gastei cem reais e cinquenta no mercado pelo banco", "gastei vinte e meio no mercado pelo banco",
    "gastei dez e meio no mercado pelo banco", "gastei treze reais e meio no mercado pelo banco",
    "paguei vinte contos e cinquenta pelo banco", "gastei vinte e dez no mercado pelo banco",
    "gastei duzentos reais e cinquenta no mercado pelo banco",
    # E2: "mil" sozinho, ou com prefixo em dígito + resto por extenso: o parser lê só o "mil"
    "paguei o aluguel mil e duzentos no pix", "gastei mil e duzentos pelo banco", "paguei mil e quinhentos no pix",
    "recebi mil e quinhentos pelo banco", "gastei 2 mil e quinhentos pelo banco", "gastei 1 mil e duzentos pelo banco",
    "gastei mil duzentos e cinquenta no mercado pelo banco", "gastei mil, duzentos no mercado pelo banco",
    "gastei mil reais e duzentos no mercado pelo banco", "gastei mil e quinhentos reais no pix",
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto", RECUSAS_EXTENSO)
def test_extenso_que_nao_compoe_um_valor_recusa(ana, liga, monkeypatch, modo, texto):
    r = diz(ana, modo, monkeypatch, texto)
    assert "Não registrei: não sei se *" in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and chaves(ana) == 0 and sem_pergunta(ana)


COMPOSTOS = [  # composição válida em português (e que o parser lê certo): continuam gravando
    ("gastei vinte e cinco no mercado pelo banco", 25.0), ("gastei vinte e um no mercado pelo banco", 21.0),
    ("gastei trinta e cinco reais no mercado pelo banco", 35.0), ("gastei duzentos e cinquenta no mercado pelo banco", 250.0),
    ("gastei cento e vinte no mercado pelo banco", 120.0), ("gastei cento e vinte e cinco no mercado pelo banco", 125.0),
    ("gastei cento e cinco no mercado pelo banco", 105.0), ("gastei dois mil e quinhentos no mercado pelo banco", 2500.0),
    ("gastei dois mil e quinhentos reais no mercado pelo banco", 2500.0),
    ("gastei dez mil e quinhentos no mercado pelo banco", 10500.0), ("gastei cem mil no mercado pelo banco", 100000.0),
    ("gastei mil no mercado pelo banco", 1000.0), ("gastei 2 mil no mercado pelo banco", 2000.0),
    ("gastei 1,5 mil no mercado pelo banco", 1500.0), ("gastei 2.500,00 no mercado pelo banco", 2500.0),
    ("gastei 10 de gorjeta pelo banco", 10.0), ("paguei 30 de gasolina no pix", 30.0), ("gastei 80 no 99 pop pelo banco", 80.0),
    ("gastei 50 no mercado 24h pelo banco", 50.0), ("paguei luz 2026 pelo banco", 2026.0),
    ("gastei 30 com 2 amigos pelo banco", 30.0), ("gastei 25 na 25 de março pelo banco", 25.0),
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto,valor", COMPOSTOS)
def test_positivo_composicao_valida_e_descricao_com_numero_gravam(ana, liga, monkeypatch, modo, texto, valor):
    diz(ana, modo, monkeypatch, texto)
    assert [float(d["valor"]) for d in declaracoes(ana)] == [valor]


@pytest.mark.parametrize("resposta", ["mil e duzentos", "2 mil e quinhentos", "dois e meio", "vinte reais e cinquenta"])
def test_fila_de_valores_recusa_o_valor_lido_pela_metade(ana, liga, monkeypatch, resposta):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    r = manda_wamid(ana, resposta)[0]
    assert "não sei se *" in r and "aluguel" in r and declaracoes(ana) == [], r
    assert pendencia(ana) == "multi_launch_values"
    assert "registrada pelo banco" in manda_wamid(ana, "1200")[0]


# --- E3: a data sai antes de procurar "N e M" ---
DATAS = [
    ("gastei 50 no mercado dia 5 e 30 na padaria pelo banco", [30.0, 50.0]),
    ("paguei 100 de luz dia 10 e 80 de água dia 12 pelo banco", [80.0, 100.0]),
    ("gastei 50 no mercado 05/10 e 30 na padaria 06/10 pelo banco", [30.0, 50.0]),
    ("recebi 100 da vó dia 5 e 50 do tio dia 6 pelo banco", [50.0, 100.0]),
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto,valores", DATAS)
def test_positivo_data_entre_dois_gastos_com_descricao_grava_os_dois(ana, liga, monkeypatch, modo, texto, valores):
    diz(ana, modo, monkeypatch, texto)
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == valores


# --- E4/E6: cartão de CRÉDITO nunca declara, em despesa, em receita e em qualquer pedaço da mensagem ---
CARTOES = [
    "recebi 100 de freela no cartão", "recebi 100 de freela no cartão de crédito",
    "gastei 50 no mercado no cartão e 30 na padaria no débito", "gastei 50 no mercado no débito e 30 na padaria no crédito",
    "gastei 50 no mercado no pix e 30 na padaria no crédito",
]


@pytest.mark.parametrize("modo", ["texto", "audio"])
@pytest.mark.parametrize("texto", CARTOES)
def test_cartao_de_credito_em_qualquer_pedaco_ou_em_receita_e_t8(ana, liga, monkeypatch, modo, texto):
    r = diz(ana, modo, monkeypatch, texto)
    assert "as compras no cartão de crédito conectado chegam sozinhas" in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and chaves(ana) == 0


@pytest.mark.parametrize("modo", ["texto", "audio"])
def test_positivo_cartao_de_debito_declara(ana, liga, monkeypatch, modo):
    diz(ana, modo, monkeypatch, "gastei 50 no mercado e 30 na padaria no cartão de débito")
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == [30.0, 50.0]


# --- E5: a guarda `declara` do cartão no áudio. Sem a flag (ou fora da lista) o áudio multipedaço com cartão fica como
# na main: UM aviso por pedaço (o texto "chegam sozinhos" antigo), nunca o T8 da declaração.
@pytest.mark.parametrize("cen", ["flag_off", "fora_lista"])
@pytest.mark.parametrize("frase", ["gastei 50 no mercado e 30 na padaria no cartão",
                                   "gastei 50 no mercado no cartão e 30 na padaria no débito"])
def test_audio_multipedaco_com_cartao_sem_flag_fica_como_na_main(ana, monkeypatch, cen, frase):
    if cen == "fora_lista":
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
        monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", "999999999")
    r = "\n".join(fala(monkeypatch, ana, frase))
    assert r.count("Não registrei: com banco conectado, Pix, cartão e débito chegam sozinhos") == 2, r
    assert "as compras no cartão de crédito conectado" not in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0
