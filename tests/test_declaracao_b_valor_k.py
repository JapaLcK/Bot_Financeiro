"""#1404 (PR-2 da #1315): dígito + "k" ("1,5k", "2 k") na declaração pelo banco vai para a confirmação do PR-1, com o
valor exato na pergunta, "R$ 1.500,00" (DP-10, DP-k-1, DP-k-2, 1-B, 2-A e 3-A do dono, ADR 0006). "kk", "mi",
"centavos" e "k" sem dígito seguem T10, e o áudio de vários gastos com "cartão" também (1-B). Tudo pelo
`handle_incoming` com Postgres (`manda_ts`, `fala`); só o Whisper e a IA são trocados.

Controles negativos (rodados, nos relatos do Coder r1 a r8): `_ABREVIADO_RE` com o ramo `kk?` de volta, ou o `_numeros`
sem o "k" colado, ou o `_DIGITOS_RE` sem o `k?` deixam vermelhos os grupos 1-4 / 1-4 / 7; o P1 sem o `cartao`, o grupo
9; o `_pedaco` sem o guarda do separador depois do "k", ou o guarda de volta como `continue` no `_numeros` (r2), o grupo
6 (3-A) e o das portas; o guarda de volta ANTES do T10/T11/T14 (r3), o grupo 10; o guarda lendo o texto sem a data (r4),
os casos com data do grupo 6; o guarda de volta a vírgula ou ponto + dígito (r5), os da Q1 do grupo 6; o guarda sem
exigir o não-espaço depois do separador, os positivos da Q1; o guarda de volta a vírgula ou ponto + não-espaço (r6), os
de B' (reticências, emoji, aspas, formatação) e a correção "foi 1,5k..."; o guarda com `\\w` cru, o "1,5k._" do itálico;
o `_numeros` sem o `k_dig`, os positivos do grupo 6; o `so_valor` sem juntar o "k" separado ou o `_DIGITOS_RE` da r1,
o grupo 7; o guarda lendo o pedaço sem a forma dita (r7) ou o fallback com o texto limpo, os da forma colada (M2-1, r8).
Positivo: os grupos 1, 5, 6 (o "300" sem "k", o "nba 2k25", a placa, a data depois do "1,5k", a pontuação seguida de
espaço ou fim, a forma depois do espaço) e 8 e o "1,5 mi" do grupo 9.
O que estes testes nunca pegam: a transcrição real ("um vírgula cinco k" no Whisper), o modelo real da IA, a Meta.
"""
from __future__ import annotations

import uuid

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_confirma as dc
from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_ts, n_lancamentos,  # noqa: F401
                                 pendencia, q)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from utils_date import extract_date_from_text


def pergunta(o_que: str) -> str:
    return f"🐷 Só confirmando: registrar *{o_que}*? Responde *sim* ou *não*."


def valores(uid) -> list[float]:
    return [float(d["valor"]) for d in declaracoes(uid)]


def nada(uid) -> bool:
    return declaracoes(uid) == [] and n_lancamentos(uid) == 0 and carteira(uid) == 200.0 and pendencia(uid) != dc.TIPO


MERCADO_1500 = pergunta("despesa de R$ 1.500,00 em mercado, hoje, pelo banco")


# ── 1: o caminho da issue, em texto e em áudio de uma frase ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("como", ["texto", "audio"])
def test_1_5k_pergunta_r_1500_e_o_sim_grava_uma_vez(ana, liga, monkeypatch, como):
    frase, w = "gastei 1,5k no mercado pelo banco", "wamid.k-" + uuid.uuid4().hex
    r = manda_ts(ana, frase, w) if como == "texto" else fala(monkeypatch, ana, frase, w)
    assert len(r) == 1 and r[0].endswith(MERCADO_1500), r  # o áudio vem com o "🎙️ Entendi" na frente
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0
    s = "wamid.sim-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_ts(ana, "sim", s)[0] and valores(ana) == [1500.0]
    assert manda_ts(ana, "sim", s) == [tx.REPETIDA.format(seq=1)]
    again = manda_ts(ana, frase, w) if como == "texto" else fala(monkeypatch, ana, frase, w)
    assert again == [tx.REPETIDA.format(seq=1)] and valores(ana) == [1500.0]


# ── 2: as portas que remontam o texto ───────────────────────────────────────────────────────────────────────────────
def test_resposta_a_pergunta_de_forma(ana, liga):
    manda_ts(ana, "gastei 1,5k no mercado")
    assert manda_ts(ana, "pix")[-1].endswith(MERCADO_1500)
    assert declaracoes(ana) == []
    manda_ts(ana, "sim")
    assert valores(ana) == [1500.0]


def test_resposta_a_pergunta_de_valor(ana, liga):
    manda_ts(ana, "paguei o aluguel")
    manda_ts(ana, "banco")
    r = manda_ts(ana, "1,5k")
    assert "R$ 1.500,00" in r[-1] and r[-1].startswith("🐷 Só confirmando"), r
    manda_ts(ana, "sim")
    assert valores(ana) == [1500.0]


# ── 3: formas com dígito (só a pergunta; nada grava antes do "sim") ─────────────────────────────────────────────────
@pytest.mark.parametrize("frase,o_que", [
    ("gastei 1.5k no mercado pelo banco", "despesa de R$ 1.500,00 em mercado"),
    ("gastei 2k no mercado pelo banco", "despesa de R$ 2.000,00 em mercado"),
    ("gastei 2K no mercado pelo banco", "despesa de R$ 2.000,00 em mercado"),
    ("gastei R$ 1,5k no mercado pelo banco", "despesa de R$ 1.500,00 em mercado"),
    ("gastei R$1,5k no mercado pelo banco", "despesa de R$ 1.500,00 em mercado"),
    ("gastei 1,55k no mercado pelo banco", "despesa de R$ 1.550,00 em mercado"),
    ("gastei 0,5k no mercado pelo banco", "despesa de R$ 500,00 em mercado"),
    ("gastei 2k no mercado kkk pelo banco", "despesa de R$ 2.000,00 em mercado"),
    ("gastei 1,5 k no mercado pelo banco", "despesa de R$ 1.500,00 em mercado"),  # DP-k-1 A
    ("gastei 2 k no mercado pelo banco", "despesa de R$ 2.000,00 em mercado"),
    ("gastei 2,2  k no mercado pelo banco", "despesa de R$ 2.200,00 em mercado"),
    ("gastei R$ 1,5 k no mercado pelo banco", "despesa de R$ 1.500,00 em mercado"),
    ("gastei 50 k no mercado pelo banco", "despesa de R$ 50.000,00 em mercado"),
    ("paguei o aluguel 1,5k pelo banco", "R$ 1.500,00"),
    ("recebi 2k de salário pelo banco", "receita de R$ 2.000,00"),
])
def test_forma_com_digito_pergunta(ana, liga, frase, o_que):
    r = manda_ts(ana, frase)
    assert len(r) == 1 and r[0].startswith("🐷 Só confirmando: registrar *") and o_que in r[0], r
    assert pendencia(ana) == dc.TIPO and declaracoes(ana) == [] and n_lancamentos(ana) == 0


# ── 4: o falso positivo aceito pelo dono ("anel 18k" pergunta por R$ 18.000,00) ─────────────────────────────────────
@pytest.mark.parametrize("frase,o_que", [
    ("comprei um anel 18k pelo banco", "despesa de R$ 18.000,00 em um anel 18k, hoje, pelo banco"),
    ("gastei 10k na corrida pelo banco", "despesa de R$ 10.000,00 em corrida, hoje, pelo banco"),
])
def test_anel_18k_pergunta_r_18000_e_o_nao_nao_grava(ana, liga, frase, o_que):
    w = "wamid.anel-" + uuid.uuid4().hex
    assert manda_ts(ana, frase, w) == [pergunta(o_que)]
    assert manda_ts(ana, "não") == [tx.NAO_FIZ_NADA]
    assert nada(ana) and pendencia(ana) is None
    assert manda_ts(ana, frase, w) == [tx.ACAO_ENCERRADA] and nada(ana)


# ── 5: o que o parser lê errado segue T10 (positivo da restrição) ───────────────────────────────────────────────────
@pytest.mark.parametrize("frase,trecho", [
    ("gastei 2kk no mercado pelo banco", "2kk"), ("gastei 2 kk no mercado pelo banco", "2 kk"),
    ("gastei 1,5kk no mercado pelo banco", "1,5kk"), ("gastei 1,5 mi no mercado pelo banco", "1,5 mi"),
    ("gastei 50 centavos no mercado pelo banco", "50 centavos"), ("gastei 99 centavos no mercado pelo banco", "99 centavos"),
    ("gastei 25 cents no mercado pelo banco", "25 cents"), ("gastei dois k no mercado pelo banco", "dois k"),
    ("gastei k no mercado pelo banco", "gastei k"), ("gastei kk no mercado pelo banco", "gastei kk"),
    ("paguei 30 no uber k pelo banco", "uber k"), ("gastei 1,5k no mercado kk pelo banco", "mercado kk"),
])
def test_abreviado_ambiguo_segue_t10(ana, liga, frase, trecho):
    assert manda_ts(ana, frase) == [tx.valor_abreviado(trecho)]
    assert nada(ana)


# ── 6: as recusas que o P1 escondia ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("frase,texto", [
    ("comprei um anel 18k de 500 pelo banco", tx.VALOR_INCERTO),
    ("comprei ouro 18k de R$ 500 pelo banco", tx.VALOR_INCERTO),
    ("gastei 18k em ouro 18k pelo banco", tx.VALOR_INCERTO),
    ("gastei 500 numa tv 4k pelo banco", tx.VALOR_INCERTO),
    ("gastei 2k2 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,50 no mercado pelo banco", tx.VALOR_INCERTO),  # 3-A: o ",50" não vira descrição
    ("gastei 1,5k.50 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 2k,5 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5 k,50 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k 50 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k50 no mercado pelo banco", tx.VALOR_INCERTO),
    # 3-A com data ou hora logo depois do separador: o guarda lê o pedaço COM a data (M-1 da r5)
    ("gastei 1,5k,50 de maio no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 2k,5 de maio no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 2k.5 de maio no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,05/11 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,18:30 no mercado pelo banco", tx.VALOR_INCERTO),
    # B' (rodadas 6 e 7): letra ou número colado à vírgula ou ao ponto depois do "k" recusa, seja data ou palavra
    ("gastei 1,5k,dia 5 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,ontem no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 2k.dia 5 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,no mercado pelo banco", tx.VALOR_INCERTO), ("gastei 2k.500 no mercado pelo banco", tx.VALOR_INCERTO),
    ("gastei 1,5k,à vista no mercado pelo banco", tx.VALOR_INCERTO),
    # 3-A com OUTRO número na frase: recusa, o "1,5k,50" não vira descrição da confirmação do 300 (D1 r2)
    ("gastei 300 no mercado 1,5k,50 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 no mercado 1,5 k,50 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 no mercado 1,5k.50 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 no mercado 2k.500 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 no mercado (1,5k,50) pelo banco", tx.VALOR_INCERTO),
    ("paguei 300 de luz 150 k,5 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 na ração 15 k,5 pelo banco", tx.VALOR_INCERTO),
    ("paguei 300 de aluguel, total 1,5k,50 pelo banco", tx.VALOR_INCERTO),
    ("gastei 300 no mercado 2 k9 pelo banco", tx.VALOR_INCERTO),  # O1: T12 como na base
    ("gastei 1k e 500 no mercado pelo banco", tx.valor_ambiguo("gastei 1k e 500 no mercado")),
    ("gastei 1,5k e 200 no mercado pelo banco", tx.valor_ambiguo("gastei 1,5k e 200 no mercado")),
    ("gastei 1,5k no mercado e 2k na padaria pelo banco", tx.UM_POR_MENSAGEM),
    ("gastei uns 1,5k no mercado pelo banco", tx.APROXIMADO),
    ("gastei 1,5k no mercado em 3x pelo banco", tx.PARCELADO),
])
def test_outras_recusas_com_k(ana, liga, frase, texto):
    assert manda_ts(ana, frase) == [texto]
    assert nada(ana)


_0511 = extract_date_from_text("05/11")[0]  # o ano que o parser dá ao "05/11" hoje


@pytest.mark.parametrize("frase,o_que,valor", [  # positivo da 3-A: sem vírgula ou ponto depois do "k", como na base
    ("gastei 300 no mercado pelo banco", "despesa de R$ 300,00 em mercado, hoje, pelo banco", 300.0),
    # o "k" seguido de dígito é código (`k_dig`), como na base (L-k-8)
    ("gastei 60 no nba 2k25 pelo banco", "despesa de R$ 60,00 em nba 2k25, hoje, pelo banco", 60.0),
    ("paguei 1200 de ipva do carro abc1k23 pelo banco", "despesa de R$ 1.200,00 em ipva do carro abc1k23, hoje, pelo banco",
     1200.0),
    ("gastei 300 no mercado 1,5k50 pelo banco", "despesa de R$ 300,00 em mercado 1,5k50, hoje, pelo banco", 300.0),
    # data depois do "1,5k" sem separador colado: confirma como o "1500" (o "dia 5" o parser deixa na descrição)
    ("gastei 1,5k no mercado dia 5 pelo banco", "despesa de R$ 1.500,00 em mercado dia 5, hoje, pelo banco", 1500.0),
    ("gastei 1,5k no mercado 05/11 pelo banco", f"despesa de R$ 1.500,00 em mercado, {_0511:%d/%m/%Y}, pelo banco", 1500.0),
    # Q1 em B: pontuação depois do "k" seguida de espaço ou do fim do pedaço não recusa
    ("gastei 1,5k no mercado pelo banco.", "despesa de R$ 1.500,00 em mercado, hoje, pelo banco", 1500.0),
    ("gastei no mercado 1,5k. pelo banco", "despesa de R$ 1.500,00 em mercado 1,5k, hoje, pelo banco", 1500.0),
    ("gastei 1,5k, no mercado pelo banco", "despesa de R$ 1.500,00 em no mercado, hoje, pelo banco", 1500.0),
    ("gastei 1,5k, pix no mercado", "despesa de R$ 1.500,00 em no mercado, hoje, pelo banco", 1500.0),  # M2-1: espaço
    ("gastei 1,5k! no mercado pelo banco", "despesa de R$ 1.500,00 em ! no mercado, hoje, pelo banco", 1500.0)])
def test_sem_k_na_frase_segue_confirmando(ana, liga, frase, o_que, valor):
    assert manda_ts(ana, frase) == [pergunta(o_que)]
    s = "wamid.sim-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_ts(ana, "sim", s)[0] and valores(ana) == [valor]
    manda_ts(ana, "sim", s)
    assert valores(ana) == [valor]


# B' (rodada 7): reticências, emoji, aspas e formatação depois do "k." seguem para a confirmação; o valor e a data são
# os mesmos, a descrição é a que o parser deixa (a mesma do "1500" na base)
@pytest.mark.parametrize("frase", [
    "gastei 1,5k… no mercado pelo banco", "gastei 1,5k... no mercado pelo banco", "gastei no mercado pelo banco 1,5k...",
    "gastei 1,5k.🙏🏻 no mercado pelo banco", "gastei no mercado pelo banco 1,5k.🙏🏻", "gastei no mercado pelo banco 1,5k,🙂",
    "*gastei no mercado pelo banco 1,5k.*", "“gastei no mercado pelo banco 1,5k.”",
    "gastei no mercado pelo banco 1,5k._",  # o "_" do itálico é formatação: `[^\W_]`, não `\w`
    "*gastei 1,5k no mercado pelo banco.*", "“gastei 1,5k no mercado pelo banco.”"])  # os 2 últimos: o "k" sem separador
def test_pontuacao_sem_letra_depois_do_k_confirma(ana, liga, frase):
    r = manda_ts(ana, frase)
    assert len(r) == 1 and r[0].startswith("🐷 Só confirmando: registrar *despesa de R$ 1.500,00 em "), r
    assert r[0].endswith(", hoje, pelo banco*? Responde *sim* ou *não*.") and declaracoes(ana) == [], r
    s = "wamid.sim-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_ts(ana, "sim", s)[0] and valores(ana) == [1500.0]
    manda_ts(ana, "sim", s)
    assert valores(ana) == [1500.0]


# M2-1 (r8): o guarda lê a mensagem como veio; sem a forma dita, "2k,pix,5 de maio" vira "2k,,5 de maio" e confirmava
@pytest.mark.parametrize("frase", [
    "gastei 2k,pix,5 de maio no mercado", "gastei 2k.pix.5 de maio no mercado", "gastei 2k,no pix,5 de maio no mercado",
    "gastei 1,5k,pix,05/11 no mercado", "gastei 1,5k,pix,ontem no mercado", "gastei 1,5k,pix no mercado",
    "gastei 1,5k.pix no mercado", "gastei 1,5k,pelo banco, no mercado", "gastei 1,5k,no débito no mercado",
    "gastei 1,5 k,no pix no mercado", "gastei no mercado 1,5k.pix", "gastei no mercado 1,5k,pelo banco"])
def test_forma_colada_ao_separador_recusa(ana, liga, frase):
    assert manda_ts(ana, frase) == [tx.VALOR_INCERTO] and nada(ana)
    assert manda_ts(ana, "sim") and nada(ana)  # o "sim" depois não grava nada


@pytest.mark.parametrize("trecho", ["1,5k,50", "2k.500"])
@pytest.mark.parametrize("porta", ["audio", "forma", "valor", "descricao", "descricao_1a", "correcao"])
def test_3a_recusa_em_todas_as_portas(ana, liga, monkeypatch, porta, trecho):  # o texto é o grupo 6
    if porta == "audio":
        r = fala(monkeypatch, ana, f"gastei {trecho} no mercado pelo banco")
    elif porta == "forma":
        manda_ts(ana, f"gastei {trecho} no mercado")
        r = manda_ts(ana, "banco")
    elif porta == "valor":
        manda_ts(ana, "paguei o aluguel pelo banco")
        r = manda_ts(ana, trecho)
    elif porta == "descricao":  # aqui quem recusa é a contagem (300 e o trecho)
        manda_ts(ana, "gastei 300 pelo banco")
        r = manda_ts(ana, f"mercado {trecho}")
    elif porta == "descricao_1a":  # o "k" na 1ª mensagem: só o guarda recusa (sem ele pergunta a descrição e confirma)
        r = manda_ts(ana, f"gastei {trecho} pelo banco")
        manda_ts(ana, "mercado")
    else:
        manda_ts(ana, "gastei 50 no mercado pelo banco")
        assert manda_ts(ana, f"foi {trecho}") == [tx.CORRECAO_INCOMPLETA]
        r = manda_ts(ana, "sim")
        assert r == [tx.ACAO_ENCERRADA], r
    assert porta == "correcao" or r[-1].endswith(tx.VALOR_INCERTO), r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and pendencia(ana) != dc.TIPO


@pytest.mark.parametrize("frase,recusa", [("mercado 1,5k,50 pelo banco", tx.VALOR_INCERTO), ("mercado 1,5k pelo banco", None),
                                          ("mercado 1,5k,pix", tx.VALOR_INCERTO), ("mercado 1,5k, pix", None)])  # M2-1
def test_3a_no_fallback_do_classificador(ana, liga, monkeypatch, frase, recusa):
    """O parser não lê "mercado …": grava o valor do classificador (1500), conferido pelo guarda; "1,5k" sozinho confirma."""
    import core.handle_incoming as hi
    import core.intent_router as ir
    from core.intent_classifier import IntentResult
    res = IntentResult(intent="launches.add", confidence=0.95, entities={"tipo": "despesa", "valor": 1500, "alvo": "mercado"})
    monkeypatch.setattr(hi, "classify", lambda *a, **k: res)
    monkeypatch.setattr(ir, "classify", lambda *a, **k: res)
    r = manda_ts(ana, frase)
    if recusa:
        assert r == [recusa] and nada(ana), r
        return
    assert len(r) == 1 and r[0].startswith("🐷 Só confirmando") and "R$ 1.500,00" in r[0], r
    assert declaracoes(ana) == [] and "registrada pelo banco" in manda_ts(ana, "sim")[0] and valores(ana) == [1500.0]


# ── 7: correção com "k" abre OUTRA confirmação, nunca grava direto (DP-k-2 A e 2-A) ─────────────────────────────────────
@pytest.mark.parametrize("correcao,valor,mostrado", [("sim, mas foi 1,5k", 1500.0, "R$ 1.500,00"),
                                                     ("foi 2k", 2000.0, "R$ 2.000,00"),
                                                     ("foi 1,5 k", 1500.0, "R$ 1.500,00"),  # 2-A
                                                     ("sim, mas foi 1,5 K", 1500.0, "R$ 1.500,00"),
                                                     ("foi 1.5k", 1500.0, "R$ 1.500,00"),
                                                     ("foi 1,500k", 1500.0, "R$ 1.500,00"),
                                                     ("foi 1,5k...", 1500.0, "R$ 1.500,00")])  # B' (rodada 7)
def test_correcao_com_k_abre_nova_confirmacao(ana, liga, correcao, valor, mostrado):
    manda_ts(ana, "gastei 50 no mercado pelo banco")
    a = db.get_pending_action(ana)["payload"]["livro_id"]
    r = manda_ts(ana, correcao)
    assert r[-1].endswith(pergunta(f"despesa de {mostrado} em mercado, hoje, pelo banco")), r
    nova = db.get_pending_action(ana)
    assert declaracoes(ana) == [] and nova["payload"]["livro_id"] != a
    assert q("select estado from ia_declaracao_livro where id=%s", (a,))["estado"] == "descartada"
    antes = str(int(nova["created_at"].timestamp()) - 1)  # um "sim" mandado antes da pergunta nova não vale por ela
    assert manda_ts(ana, "sim", ts=antes) == [f"{tx.ENVIADA_ANTES}\n\n{nova['payload']['question']}"]
    assert declaracoes(ana) == []
    s = "wamid.sim-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_ts(ana, "sim", s)[0] and valores(ana) == [valor]
    manda_ts(ana, "sim", s)
    assert valores(ana) == [valor]


@pytest.mark.parametrize("correcao", ["sim mas foi 1,5kk", "foi 1,5k ontem", "foi 5km", "foi 1,5 kk", "foi 1,5k,50",
                                       "foi 1,5k,dia 5"])
def test_correcao_com_k_ambigua_segue_incompleta(ana, liga, correcao):
    manda_ts(ana, "gastei 50 no mercado pelo banco")
    assert manda_ts(ana, correcao) == [tx.CORRECAO_INCOMPLETA]
    assert manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and nada(ana)


# ── 8: flag desligada e Carteira não mudam ──────────────────────────────────────────────────────────────────────────
def test_flag_desligada_nao_arma_e_carteira_grava_1500(ana):
    r = manda_ts(ana, "gastei 1,5k no mercado pelo banco")[0]
    assert "Só confirmando" not in r and pendencia(ana) != dc.TIPO and declaracoes(ana) == [], r
    manda_ts(ana, "gastei 1,5k no mercado em dinheiro")
    assert declaracoes(ana) == [] and carteira(ana) == 200.0 - 1500.0


def test_carteira_com_a_flag_ligada_grava_1500(ana, liga):
    manda_ts(ana, "gastei 1,5k no mercado em dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1 and carteira(ana) == 200.0 - 1500.0


# ── 9: áudio de dois gastos respondido com "cartão": dígito + "k" segue T10 (1-B do dono, substitui a DP-k-3 A) ───────
def _compras(uid) -> list[float]:
    return [float(r["valor"]) for r in q("select valor from credit_transactions where user_id=%s order by id", (uid,),
                                         fetch=True)]


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("frase,trecho", [("gastei 1,5k no mercado e paguei o aluguel", "1,5k"),
                                          ("gastei 1,5 k no mercado e paguei o aluguel", "1,5 k"),
                                          ("comprei um anel 18k e paguei o aluguel", "18k"),
                                          ("gastei 1.500k no mercado e paguei o aluguel", "1.500k"),
                                          ("gastei 1,5 mi no mercado e paguei o aluguel", "1,5 mi")])
def test_audio_com_k_respondido_com_cartao_segue_t10(ana, liga, monkeypatch, frase, trecho, manual):
    """A fatura grava sem confirmação: sem a recusa, o cartão manual gravaria R$ 1.500,00 / R$ 18.000,00 direto.
    Com cartão conectado também T10, como na base `8dfb4d71` (a recusa vem antes do T8)."""
    if manual:
        db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    fala(monkeypatch, ana, frase)
    assert manda_ts(ana, "cartão") == [tx.valor_abreviado(trecho)]
    assert _compras(ana) == [] and nada(ana)


# ── 10: o guarda da 3-A não passa na frente do T10/T11 com "cartão" (D1-r3: a fatura gravava R$ 4,00 / 1,20 / 2 + 50) ───
_GASTOS = {"gastei dois reais e cinquenta no mercado": tx.valor_ambiguo("dois reais e cinquenta"),
           "gastei 2 reais e 50 no mercado": tx.valor_ambiguo("2 reais e 50"),
           "gastei mil duzentos no mercado": tx.valor_ambiguo("mil duzentos"),
           "gastei dois cinquenta no mercado": tx.valor_ambiguo("dois cinquenta"),
           "gastei 1,200 no mercado": tx.valor_abreviado("1,200")}
_SUF = [" da tv 4k60", " 2 k9", " placa abc1k23", " placa abc1k,23"]  # o último casa o guarda: só a ordem o segura


@pytest.mark.parametrize("suf", _SUF)
@pytest.mark.parametrize("gasto,um", [(g, False) for g in _GASTOS] + [("gastei 2 reais e 50 no mercado", True)])
def test_audio_com_cartao_recusa_como_a_base(ana, liga, monkeypatch, gasto, um, suf):
    """O mesmo código da base `8dfb4d71`, com cartão manual, e a fatura vazia (o T12 não bloqueia no ramo do cartão)."""
    db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    fala(monkeypatch, ana, gasto + suf + ("" if um else " e paguei o aluguel"))
    assert manda_ts(ana, "cartão") == [_GASTOS[gasto]]
    assert _compras(ana) == [] and nada(ana)
