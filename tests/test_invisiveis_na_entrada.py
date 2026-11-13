"""#1553: caractere de formato invisível (Unicode Cf) sai na entrada da mensagem (`utils_text.sem_invisiveis`), com a flag
`DECLARACAO_BANCO` ligada ou desligada. Na `main`, "gastei 1<ZWSP>500 no mercado em dinheiro" gravava R$ 1,00 na
Carteira, "comprei 1<ZWSP>500 no cartão nubank" lançava R$ 1,00 na fatura e "1<ZWSP>500" respondendo ao "✅ Já paguei"
pagava a conta com R$ 1,00 (o `parse_money` para no invisível). Duas chamadas: `wa_runtime.process_message` (o WhatsApp,
inclusive os ramos que respondem antes do núcleo, como a porta 4) e a primeira linha do `handle_incoming` (Discord e o
resto). A declaração pelo banco está em `tests/test_declaracao_b_invisiveis.py`.

Controles negativos (relatos do Coder e do Tester em `.time-dev/1553/`, pasta no `.gitignore` que não viaja no git):
`sem_invisiveis` devolvendo o texto intacto deixa vermelhos a Carteira, o cartão, a porta 4, o Discord e a mensagem só
de invisível; a chamada do `process_message` desligada, a porta 4: os 7 `test_ja_paguei_paga_1500` e os 4
`test_so_invisivel_nao_mexe_no_ja_paguei` (a porta 4 lê o invisível e responde "Não peguei o valor…"); a mensagem só de
invisível na confirmação viva e na pergunta de valor segue verde, porque chega ao `handle_incoming`, que também limpa; a
do `handle_incoming`
desligada, a Carteira, o cartão, o Discord e a mensagem só de invisível pelo núcleo; o ZWJ/ZWNJ sem a regra do valor,
os casos `zwj`/`zwnj` e a tabela do helper. Mensagem só de invisível (M6 A do dono): nos três casos pelo WhatsApp a
resposta é a genérica da branch (`NAO_ENTENDI`) e a pendência fica idêntica, linha inteira; os três falham na `main`
(confirmação cancelada, "Já paguei" com "Não peguei o valor…", pergunta de valor repetida e recriada). Positivos: a
frase sem invisível e o NBSP iguais à `main`, o "sim" e o "132,50" depois da mensagem só de invisível. O que nunca pegam: se a Meta entrega Cf no
`text.body`, a IA real, o áudio (não verificado).
"""
from __future__ import annotations

import re
import time
import uuid

import pytest

import db
import db.bills as B
from conftest import usuario_pagante
from core.handlers import declaracao_confirma as dc
from tests._declaracao_b import ana, carteira, declaracoes, manda_ts, n_lancamentos, q  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests.test_bill_amount_pending import _manda_texto_no_wa, _monta_conta_variavel, _toca_ja_paguei

INVISIVEIS = {"zwsp": "\u200b", "shy": "\u00ad", "wj": "\u2060", "bom": "\ufeff", "lrm": "\u200e",
              "zwj": "\u200d", "zwnj": "\u200c"}  # os dois juntores: ajuste do dono, dentro do valor também saem
FAMILIA = "\U0001F468\u200d\U0001F469\u200d\U0001F467"  # 👨\u200d👩\u200d👧
INGLATERRA = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
ESCOCIA = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
GALES = "\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F"
NAO_ENTENDI = ["Nao entendi. Digite ajuda para ver os comandos."]  # `wa_runtime`, resposta ao núcleo sem saída (M6 A)


# ── o helper: tabela de entrada e saída ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("entrada,saida", [
    ("1\u200b500", "1500"), ("1\u00ad500", "1500"), ("1\u2060500", "1500"), ("\ufeffgastei", "gastei"),
    ("1\u200e500", "1500"), ("a\u200fb", "ab"), ("a\u202eb", "ab"), ("a\u2066b\u2069", "ab"), ("a\u2061b", "ab"),
    ("2k,\u200b5 de maio", "2k,5 de maio"),
    # ZWJ/ZWNJ: saem encostados em dígito, ponto, vírgula, espaço, no começo e no fim; ficam entre outros caracteres
    ("1\u200d500", "1500"), ("1\u200c500", "1500"), ("1,\u200d50", "1,50"), ("1\u200d\u200d500", "1500"),
    ("1 \u200d 500", "1  500"), ("\u200d", ""), ("\u200c\u200d", ""), ("\u200b\u200d\u200b", ""),
    (FAMILIA, FAMILIA), (f"mercado {FAMILIA}", f"mercado {FAMILIA}"), ("می\u200cخواهم", "می\u200cخواهم"),
    ("\U0001F44D\U0001F3FD", "\U0001F44D\U0001F3FD"), ("❤\ufe0f", "❤\ufe0f"),
    ("\U0001F3F3\ufe0f\u200d\U0001F308", "\U0001F3F3\ufe0f\u200d\U0001F308"),
    # limite aceito pelo dono: as tags (Cf) saem e as três bandeiras viram 🏴
    (INGLATERRA, "\U0001F3F4"), (ESCOCIA, "\U0001F3F4"), (GALES, "\U0001F3F4"),
    ("1\u00a0500", "1\u00a0500"), ("1\u2009500", "1\u2009500"), ("1\u202f500", "1\u202f500"),  # Q3 A: espaço fica
    (None, ""), ("", ""), ("gastei 50 no mercado", "gastei 50 no mercado"),
])
def test_helper(entrada, saida):
    from utils_text import sem_invisiveis  # no teste, não no topo: o resto do arquivo roda no código antigo
    assert sem_invisiveis(entrada) == saida
    assert sem_invisiveis(saida) == saida  # idempotente: o WhatsApp passa pelas duas chamadas


# ── a regra do ZWJ/ZWNJ em tempo linear (#1553 r2): a regex da r1, sem a âncora no começo da corrida, era quadrática ──
_REGRA_R1 = re.compile(r"(?<![^\x00-\x7f\s\d])[\u200c\u200d]+|[\u200c\u200d]+(?![^\x00-\x7f\s\d])")


@pytest.mark.parametrize("z", ["\u200d", "\u200c"], ids=["zwj", "zwnj"])
@pytest.mark.parametrize("molde", [  # relato do Coder r1 e ataques 1 e 2 do Tester r1; {z} é o juntor
    "1{z}500", "1,{z}50", "1,5 mi{z}l", "2 mi{z}lhões", "R{z}$1500", "R$ {z}1500", "R$1500{z} reais", "rea{z}is",
    "1,5{z}k", "2{z}mil", "mil e {z}quinhentos", "três{z} mil", "trê{z}s", "tr{z}ês", "1{z}{z}500", "1{z}\u200b500",
    "1 {z} 500", "{z}", "\u200b{z}\u200b", "{z}gastei", "gastei{z}", "1500{z}👍", "👍{z}1500", "👍{z}a", "a-{z}-b",
    "*{z}*", "😀{z}\n", "😀\u00a0{z}😀", f"{FAMILIA}1500", f"1500{FAMILIA}", f"mercado {FAMILIA}", "😀{z}😀{z}😀",
    "🏳\ufe0f{z}🌈", "می{z}خواهم", "کتاب{z}ها", "ل{z}ا", "א{z}ב", "क्{z}ष", "ශ්{z}රී", "র{z}্য", "10{z}ها", "۱۰{z}ها",
    "ണ്{z}", "ണ്{z} ", "می{z}", "می{z}.", "Auf{z}lage", "¹{z}⁵⁰⁰", "①{z}⑤", "１{z}５００",
    "😀" + "{z}" * 50 + "😀", "😀" + "{z}" * 50 + "a", "a" + "{z}" * 50 + "😀", "{z}" * 50, "😀" + "{z}" * 50,
    "😀" + "\u200c\u200d" * 25 + "😀", "😀{z}{z}{z}\u200b{z}{z}😀", "😀" + "{z}" * 50 + "1",
])
def test_regra_do_juntor_igual_a_da_r1(molde, z):
    from utils_text import sem_cf, sem_invisiveis
    texto = molde.replace("{z}", z)
    assert sem_invisiveis(texto) == _REGRA_R1.sub("", sem_cf(texto))


def test_corrida_longa_de_juntor_em_tempo_linear():
    # Com a _REGRA_R1 no lugar, esta chamada leva mais de um minuto (controle no relato do Coder r2, fora do git): o
    # worker do WhatsApp é único e a limpeza roda antes de saber de quem é a mensagem. Com a âncora, milissegundos.
    from utils_text import sem_invisiveis
    texto = "😀" + "\u200d" * 65536 + "😀"
    inicio = time.perf_counter()
    assert sem_invisiveis(texto) == texto
    assert time.perf_counter() - inicio < 1


def _flag(monkeypatch, ligada):
    if ligada:
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
    else:
        monkeypatch.delenv("DECLARACAO_BANCO", raising=False)


def _lancamentos(uid):
    return [(float(x["valor"]), x["alvo"]) for x in q(
        "select valor, alvo from launches where user_id=%s and coalesce(source,'manual')<>'open_finance' order by id",
        (uid,), fetch=True)]


# ── Carteira e cartão: com a flag desligada (produção) e ligada ─────────────────────────────────────────────────────
@pytest.mark.parametrize("ligada", [False, True], ids=["flag_off", "flag_on"])
@pytest.mark.parametrize("inv", list(INVISIVEIS))
@pytest.mark.parametrize("molde", ["gastei 1{i}500 no mercado em dinheiro", "gastei 1,5{i}k no mercado em dinheiro",
                                   "gastei 1,5 mi{i}l no mercado em dinheiro"])
def test_carteira_grava_1500(monkeypatch, ana, molde, inv, ligada):
    _flag(monkeypatch, ligada)
    manda_ts(ana, molde.format(i=INVISIVEIS[inv]))
    assert _lancamentos(ana) == [(1500.0, "mercado")] and carteira(ana) == -1300.0


@pytest.mark.parametrize("ligada", [False, True], ids=["flag_off", "flag_on"])
@pytest.mark.parametrize("inv", ["zwsp", "bom", "lrm", "zwj"])
def test_cartao_lanca_1500(monkeypatch, ana, inv, ligada):
    _flag(monkeypatch, ligada)
    db.create_card(ana, "nubank", 10, 17)
    manda_ts(ana, f"comprei 1{INVISIVEIS[inv]}500 no cartão nubank")
    compras = q("select valor from credit_transactions where user_id=%s", (ana,), fetch=True)
    assert [float(c["valor"]) for c in compras] == [1500.0]


def test_positivos_iguais_a_main(monkeypatch, ana):
    _flag(monkeypatch, False)
    manda_ts(ana, "gastei 1500 no mercado em dinheiro")
    assert _lancamentos(ana) == [(1500.0, "mercado")]
    # Q3 A: o NBSP não é Cf e fica; a Carteira segue perguntando, sem gravar nada (como na `main`)
    assert manda_ts(ana, "gastei 1\u00a0500 no mercado em dinheiro") == ["🐷 Quanto foi no *500 no mercado*?"]
    assert len(_lancamentos(ana)) == 1


# ── a porta 4 (resposta ao "✅ Já paguei"), só pelo `process_message`: ela responde antes do `handle_incoming` ──────────
@pytest.mark.parametrize("inv", list(INVISIVEIS))
def test_ja_paguei_paga_1500(monkeypatch, inv):
    uid = usuario_pagante()
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))
    _manda_texto_no_wa(monkeypatch, uid, f"1{INVISIVEIS[inv]}500")
    paga = B.list_bills(uid, include_paid=True)[0]
    assert paga["status"] == "paid" and float(paga["paid_amount"]) == 1500.0, paga


# ── Discord: só pelo `handle_incoming` ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("inv", ["zwsp", "shy", "zwj"])
def test_discord_grava_1500(ana, inv):
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    hi.handle_incoming(IncomingMessage(
        platform="discord", user_id=ana, text=f"gastei 1{INVISIVEIS[inv]}500 no mercado em dinheiro",
        message_id=str(uuid.uuid4().int % 10**18), attachments=[], external_id=str(ana), raw={}))
    assert _lancamentos(ana) == [(1500.0, "mercado")]


# ── mensagem só de invisível: não grava, não confirma, não cancela, não consome nem renova a pendência (M6 A do dono) ──
SO_INVISIVEL = {"zwsp": "\u200b", "bom": "\ufeff", "zwj": "\u200d", "shy_wj": "\u00ad\u2060"}


def _pendencia(uid):
    """A linha inteira (tipo, payload, created_at, expires_at): renovar o prazo muda os dois carimbos."""
    return q("select * from pending_actions where user_id=%s", (uid,))


@pytest.mark.parametrize("canal", ["whatsapp", "nucleo"])
@pytest.mark.parametrize("inv", list(SO_INVISIVEL))
def test_so_invisivel_nao_mexe_na_confirmacao_viva(monkeypatch, ana, inv, canal):
    """Na `main` ela cancelava a confirmação da declaração ("🔕 Cancelei a pergunta anterior.") e ia à IA."""
    _flag(monkeypatch, True)
    manda_ts(ana, "gastei 50 no mercado pelo banco")
    antes = _pendencia(ana)
    assert antes["action_type"] == dc.TIPO
    r = (_manda_texto_no_wa(monkeypatch, ana, SO_INVISIVEL[inv]) if canal == "whatsapp"
         else manda_ts(ana, SO_INVISIVEL[inv]))
    assert _pendencia(ana) == antes, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and not any("Cancelei" in x for x in r), r
    assert canal == "nucleo" or r == NAO_ENTENDI, r
    manda_ts(ana, "sim", "wamid.sim-" + uuid.uuid4().hex)  # a pergunta seguiu viva: o "sim" grava
    assert [float(d["valor"]) for d in declaracoes(ana)] == [50.0]


@pytest.mark.parametrize("inv", list(SO_INVISIVEL))
def test_so_invisivel_nao_mexe_no_ja_paguei(monkeypatch, inv):
    """Na `main` a pendência ficava, mas a resposta era "Não peguei o valor. Manda só o número da conta de *Luz*…"."""
    uid = usuario_pagante()
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))
    antes = _pendencia(uid)
    r = _manda_texto_no_wa(monkeypatch, uid, SO_INVISIVEL[inv])
    assert _pendencia(uid) == antes and B.list_bills(uid, include_paid=True)[0]["status"] == "pending", r
    assert n_lancamentos(uid) == 0 and r == NAO_ENTENDI, r
    _manda_texto_no_wa(monkeypatch, uid, "132,50")  # a pergunta seguiu viva
    assert float(B.list_bills(uid, include_paid=True)[0]["paid_amount"]) == 132.5


@pytest.mark.parametrize("inv", list(SO_INVISIVEL))
def test_so_invisivel_nao_mexe_na_pergunta_de_valor(monkeypatch, ana, inv):
    """Na `main` ela repetia "🐷 Quanto foi no *aluguel*?" e recriava a pendência (prazo novo)."""
    manda_ts(ana, "paguei o aluguel")
    manda_ts(ana, "dinheiro")
    pergunta = _pendencia(ana)
    assert pergunta and pergunta["action_type"] == "clarification", pergunta
    r = _manda_texto_no_wa(monkeypatch, ana, SO_INVISIVEL[inv])
    assert _pendencia(ana) == pergunta and n_lancamentos(ana) == 0, r
    assert r == NAO_ENTENDI, r
