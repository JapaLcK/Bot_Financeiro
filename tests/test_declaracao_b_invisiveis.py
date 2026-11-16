"""#1553: caractere de formato invisível (Unicode Cf) no meio do valor, na declaração pelo banco (flag ligada). Ele sai na
entrada da mensagem (`utils_text.sem_invisiveis`, primeira linha do `handle_incoming`), antes do leitor estrito e do
parser, que passam a ler o mesmo texto: "2k,<ZWSP>5 de maio" é "2k,5 de maio" (T12) e "1<ZWSP>500" é "1500". Tudo pelo
`handle_incoming` com Postgres (`manda_ts`); só a IA é trocada. A Carteira, o cartão, a porta 4 do WhatsApp, o Discord e
a mensagem só de invisível estão em `tests/test_invisiveis_na_entrada.py`.

Controles negativos (relato do Coder r1 em `.time-dev/1553/`, pasta no `.gitignore` que não viaja no git):
`sem_invisiveis` devolvendo o texto intacto deixa vermelhos os grupos 1, 2, 3, 4 e 5; a chamada do `handle_incoming`
desligada, os mesmos; o ZWJ e o ZWNJ sem a regra do valor (só a exceção da Cf), o grupo 5. Positivo: grupo 6 (sem invisível, igual à `main`; emoji com ZWJ
inteiro). O que estes testes nunca pegam: se a Meta entrega Cf no `text.body` (o caractere é injetado direto), a IA real
e o áudio (a transcrição não foi verificada quanto a invisíveis).
"""
from __future__ import annotations

import uuid

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_confirma as dc
from tests._declaracao_b import ana, carteira, declaracoes, liga, manda_ts, n_lancamentos, pendencia  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401

# ZWSP, soft hyphen, WJ, BOM (os da issue) e LRM (marca bidi de copiar e colar)
INVISIVEIS = {"zwsp": "\u200b", "shy": "\u00ad", "wj": "\u2060", "bom": "\ufeff", "lrm": "\u200e"}
ZWJ, ZWNJ = "\u200d", "\u200c"
FAMILIA = "\U0001F468\u200d\U0001F469\u200d\U0001F467"  # 👨\u200d👩\u200d👧


def pergunta(o_que: str) -> str:
    return f"🐷 Só confirmando: registrar *{o_que}*? Responde *sim* ou *não*."


def valores(uid) -> list[float]:
    return [float(d["valor"]) for d in declaracoes(uid)]


def nada(uid) -> bool:
    return declaracoes(uid) == [] and n_lancamentos(uid) == 0 and carteira(uid) == 200.0 and pendencia(uid) != dc.TIPO


MERCADO_1500 = pergunta("despesa de R$ 1.500,00 em mercado, hoje, pelo banco")


def sim(uid) -> list[str]:
    return manda_ts(uid, "sim", "wamid.sim-" + uuid.uuid4().hex)


# ── 1: a issue. Com o invisível, a mesma recusa da frase sem ele (T12), nunca R$ 2.000,00 «5 de maio», hoje ──────────
@pytest.mark.parametrize("inv", list(INVISIVEIS))
@pytest.mark.parametrize("molde", ["gastei 2k,{i}5 de maio no mercado pelo banco",
                                   "gastei 2000,{i}5 de maio no mercado pelo banco"])
def test_1_issue_da_t12_como_sem_o_invisivel(ana, liga, molde, inv):
    assert manda_ts(ana, molde.format(i="")) == [tx.VALOR_INCERTO]  # a referência: a frase sem o invisível
    assert manda_ts(ana, molde.format(i=INVISIVEIS[inv])) == [tx.VALOR_INCERTO]
    assert nada(ana)


# ── 2: o número inteiro, não o pedaço antes do invisível; o "sim" grava o valor exato e a descrição sem ele ─────────────
@pytest.mark.parametrize("inv", list(INVISIVEIS))
def test_2_1_invisivel_500_confirma_1500_e_grava_1500(ana, liga, inv):
    assert manda_ts(ana, f"gastei 1{INVISIVEIS[inv]}500 no mercado pelo banco") == [MERCADO_1500]
    assert declaracoes(ana) == []
    assert "registrada pelo banco" in sim(ana)[0]
    d = declaracoes(ana)
    assert [float(x["valor"]) for x in d] == [1500.0] and d[0]["alvo"] == "mercado", d
    assert carteira(ana) == 200.0


# ── 3: o invisível em outras posições do valor ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("inv", list(INVISIVEIS))
@pytest.mark.parametrize("molde,esperada", [
    ("gastei 1,5{i}k no mercado pelo banco", MERCADO_1500),
    ("gastei 1,{i}50 no mercado pelo banco", pergunta("despesa de R$ 1,50 em mercado, hoje, pelo banco")),
    ("gastei R${i}1500 no mercado pelo banco", MERCADO_1500),
    ("{i}gastei 50 no mercado pelo banco", pergunta("despesa de R$ 50,00 em mercado, hoje, pelo banco")),
])
def test_3_outras_posicoes(ana, liga, molde, esperada, inv):
    assert manda_ts(ana, molde.format(i=INVISIVEIS[inv])) == [esperada]
    assert declaracoes(ana) == []


# ── 4: a correção "sim, mas foi 1<invisível>500" abre a confirmação nova de R$ 1.500,00 (DP-k-2) ──────────────────────
@pytest.mark.parametrize("inv", ["zwsp", "bom"])
def test_4_correcao(ana, liga, inv):
    manda_ts(ana, "gastei 50 no mercado pelo banco")
    r = manda_ts(ana, f"sim, mas foi 1{INVISIVEIS[inv]}500")
    assert r[-1].endswith(MERCADO_1500), r
    assert declaracoes(ana) == []
    sim(ana)
    assert valores(ana) == [1500.0]


# ── 5: ajuste do dono — ZWJ e ZWNJ ficam nos emojis, mas dentro do valor saem: nunca R$ 1,00 ───────────────────────────
@pytest.mark.parametrize("juntor", [ZWJ, ZWNJ], ids=["zwj", "zwnj"])
def test_5_zwj_e_zwnj_entre_digitos_nao_leem_parcial(ana, liga, juntor):
    assert manda_ts(ana, f"gastei 1{juntor}500 no mercado pelo banco") == [MERCADO_1500]
    sim(ana)
    assert valores(ana) == [1500.0]


# ── 6: positivos — sem invisível, igual à `main`; o emoji composto com ZWJ chega inteiro à pergunta e à descrição ──────
def test_6_sem_invisivel_igual_a_main(ana, liga):
    assert manda_ts(ana, "gastei 1500 no mercado pelo banco") == [MERCADO_1500]
    sim(ana)
    assert valores(ana) == [1500.0]


def test_6_emoji_de_familia_fica_inteiro(ana, liga):
    r = manda_ts(ana, f"gastei 50 no mercado {FAMILIA} pelo banco")
    assert r == [pergunta(f"despesa de R$ 50,00 em mercado {FAMILIA}, hoje, pelo banco")], r
    sim(ana)
    d = declaracoes(ana)
    assert [float(x["valor"]) for x in d] == [50.0] and d[0]["alvo"] == f"mercado {FAMILIA}", d
