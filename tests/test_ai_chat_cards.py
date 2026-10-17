"""PR B1: `show_card`. Banco, sessão e rotas reais; só a OpenAI é falsa.

Paridade: o bloco que o chat manda é IGUAL ao JSON que o endpoint do widget devolve
(o envelope `{"ok": True, ...}` das 5 rotas legadas mora na rota e no leitor: é este
teste que os mantém iguais). Controle negativo: `test_paridade_pega_leitor_trocado`.
Gates, teto e eventos do stream: `test_ai_chat_cards_turno.py`.
"""
import json
from contextlib import contextmanager
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

import db
from _cards_apoio import (CASOS, M, M0, _http, _numeros, _reais, mostrar, mundo,  # noqa: F401  (mundo é fixture)
                          valores_de_b_em)
from conftest import usuario_pagante
from core.services.ai_chat import cards
from db.mes_detalhes import ler
from tests._patrimonio_helpers import conexao, posicao
from tests.test_resumo_mes_regra import INICIO
from utils_date import now_tz


def test_o_registro_cobre_os_18_ids():
    assert set(CASOS) == set(cards.IDS) == set(cards.registro()) and len(cards.IDS) == 18


def _sem_relogio(o):
    """A previsão carimba `calculado_em` com o instante da leitura: duas leituras nunca o repetem."""
    return {k: v for k, v in o.items() if k != "calculado_em"} if isinstance(o, dict) else o


def confere_paridade(uid, card):
    args, abas = CASOS[card]
    r, blocos, _ = mostrar(uid, card=card, **args)
    assert "error" not in r, r
    (bloco,) = blocos
    assert bloco["card"] == card and len(bloco["abas"]) == len(abas)
    c = _http(uid)
    for aba, esperado in zip(bloco["abas"], abas):
        assert set(aba["recursos"]) == set(esperado)
        for rec, (rota, query) in esperado.items():
            resp = c.get(rota.format(uid=uid), params=query)
            assert resp.status_code == 200, (rota, resp.text)
            assert _sem_relogio(aba["recursos"][rec]) == _sem_relogio(resp.json()), (card, rec)
    return r, bloco


@pytest.mark.parametrize("card", [c for c in CASOS if c != "periodo"])  # sem rota
def test_paridade_http_x_bloco(mundo, card):
    a, _ = mundo
    _, bloco = confere_paridade(a, card)
    if card in ("metas", "caixinhas", "fatura", "parcelas", "categorias", "contas"):  # a fixture não é vazia
        assert any(aba["recursos"] for aba in bloco["abas"])
        dado = json.dumps(bloco)
        assert {"metas": "Casa nova", "caixinhas": "viagem", "fatura": "Nubank", "parcelas": "casa", "categorias": "mercado",
                "contas": "carteira"}[card] in dado


def test_paridade_pega_leitor_trocado(mundo, monkeypatch):
    """Controle negativo: o leitor de `categorias` volta a ser `get_top_expense_categories`."""
    a, _ = mundo
    confere_paridade(a, "categorias")  # positivo: o leitor real passa
    real = cards.registro()

    def errado(uid, args):
        top = db.get_top_expense_categories(uid, INICIO, INICIO + timedelta(days=27))
        return [(m, {**o, "detalhes": {**o["detalhes"].model_dump(mode="json"), "categorias": top}})
                for m, o in real["categorias"].ler(uid, args)]

    monkeypatch.setattr(cards, "registro", lambda: {**real, "categorias": cards.Card(errado, real["categorias"].visao)})
    with pytest.raises(AssertionError):
        confere_paridade(a, "categorias")


def test_categorias_leva_as_contas_em_cada_aba_e_a_visao_nao(mundo):
    """E2: o app mostra a sincronização mais antiga a partir do PRÓPRIO bloco (nunca do provider); o modelo não vê `contas`."""
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="categorias", meses=[M, M0])
    assert len(bloco["abas"]) == len(r["abas"]) == 2
    assert all(set(aba["recursos"]) == {"detalhes", "contas"} for aba in bloco["abas"])
    assert all("contas" not in aba for aba in r["abas"])


@contextmanager
def _quebra(modulo, nome):
    """`modulo.nome` levanta; o `registro` (@cache) importa os leitores na 1ª chamada, então é refeito dos dois lados."""
    def boom(**kw):
        raise RuntimeError("falha de teste")
    try:
        with pytest.MonkeyPatch.context() as m:
            m.setattr(modulo, nome, boom)
            cards.registro.cache_clear()
            yield
    finally:
        cards.registro.cache_clear()


def test_categorias_sem_contas_quando_contas_falha_e_loga(mundo, caplog):
    """`contas` é enfeite (sincronização): se cair, o card sai sem ele e a falha fica no log; a visão não muda."""
    import api.v2.contas as v2
    a, _ = mundo
    ok = mostrar(a, card="categorias", meses=[M, M0])[0]
    with _quebra(v2, "contas"):
        r, (bloco,), _ = mostrar(a, card="categorias", meses=[M, M0])
    assert all(set(aba["recursos"]) == {"detalhes"} for aba in bloco["abas"]) and len(bloco["abas"]) == 2
    assert r == ok
    assert any("contas indisponível" in x.getMessage() and x.exc_info for x in caplog.records)


def test_categorias_falha_de_mes_detalhes_continua_falhando(mundo):
    """Só `contas` é engolido: sem `mes_detalhes` não há card."""
    import api.nativo.mes_detalhes as nativo
    a, _ = mundo
    with _quebra(nativo, "mes_detalhes"), pytest.raises(RuntimeError):
        mostrar(a, card="categorias", meses=[M])


@pytest.mark.parametrize("card", list(CASOS))
def test_visao_do_modelo_esta_no_bloco(mundo, card):
    a, _ = mundo
    if card == "metas":  # a meta da fixture não tem banco (`atual` null, sem R$): dá dinheiro a ela
        posicao(conexao(a, "item-visao-metas", banco="Nubank"), "p1", "1234.567")
    args, _ = CASOS[card]
    r, (bloco,), _ = mostrar(a, card=card, **args)
    assert r["card"] == card and len(r["abas"]) == len(bloco["abas"])
    if card == "metas":
        # o dinheiro vai como texto pronto com "R$" (o ai_guard só aceita assim), nunca float: então
        # cada R$ do texto tem de ser, em módulo (o ritmo sai com palavra, sem sinal), um Decimal do bloco
        reais = _reais(r)
        do_bloco = {abs(Decimal(str(n))).quantize(Decimal("0.01"), ROUND_HALF_UP) for n in _numeros(bloco)}
        assert reais and reais <= do_bloco and not any(isinstance(x, float) for x in _numeros(r))
    else:
        # o resumo (F3b) traz derivados das mesmas linhas do bloco; `tests/test_ai_chat_f3b.py` testa cada derivação
        derivados = {"renda": {"tendencia", "entrou", "saiu", "saldo"}, "categorias": {"saldo", "categorias_total"},
                     "periodo": {"categorias_total"}}.get(card, set())
        sem_derivados = [{k: v for k, v in a.items() if k not in derivados} for a in r["abas"]]
        assert _numeros(sem_derivados) <= _numeros(bloco)
        assert any(isinstance(x, float) for x in _numeros(r)) or card in ("assinaturas", "rendimento", "piggy"), \
            "a visão do modelo tem número como número"
    json.dumps(r)  # serializa como o runner serializa


def _ids(o, achados=None):
    achados = set() if achados is None else achados
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "id" and isinstance(v, int):
                achados.add(v)
            _ids(v, achados)
    elif isinstance(o, list):
        for v in o:
            _ids(v, achados)
    return achados


def confere_isolamento(a, b, card):
    args, _ = CASOS[card]
    ra, (bloco_a,), _ = mostrar(a, card=card, **args)
    rb, (bloco_b,), _ = mostrar(b, card=card, **args)
    texto = json.dumps([bloco_a, ra])
    assert "SEGREDO" not in texto, card  # texto do usuário B (uppercase: não colide com id, data nem hex)
    # Os valores de B (`semeia_b`) valem como NÚMERO exato, nunca como trecho de texto: "7777" aparece por
    # acaso nos microssegundos de `calculado_em` (".777712") e em ids, e isso já derrubou o CI (hero).
    assert not valores_de_b_em([bloco_a, ra]), (card, valores_de_b_em([bloco_a, ra]))
    assert not (_ids(bloco_a) & _ids(bloco_b)), card
    if card in ("metas", "caixinhas", "fatura"):
        assert _ids(bloco_a) and _ids(bloco_b)


@pytest.mark.parametrize("card", list(CASOS))
def test_bloco_de_a_nao_tem_nada_de_b(mundo, card):
    confere_isolamento(*mundo, card)


def test_isolamento_pega_leitor_que_le_o_outro_usuario(mundo, monkeypatch):
    """Controle negativo: o leitor de `caixinhas` passa a ler as caixinhas de B."""
    a, b = mundo
    confere_isolamento(a, b, "caixinhas")  # positivo
    real = cards.registro()
    from core.services.metas_status import status_metas
    vaza = cards.Card(lambda uid, args: [(None, {"caixinhas": {"ok": True, "goals": status_metas(b)}})],
                      real["caixinhas"].visao)
    monkeypatch.setattr(cards, "registro", lambda: {**real, "caixinhas": vaza})
    with pytest.raises(AssertionError):
        confere_isolamento(a, b, "caixinhas")


def test_isolamento_pega_leitor_de_metas_que_le_o_outro_usuario(mundo, monkeypatch):
    """Controle negativo: o leitor de `metas` passa a ler as metas de B."""
    a, b = mundo
    confere_isolamento(a, b, "metas")  # positivo
    real = cards.registro()
    from api.v2.metas import listar
    vaza = cards.Card(lambda uid, args: [(None, {"metas": listar(uid=b)})], real["metas"].visao)
    monkeypatch.setattr(cards, "registro", lambda: {**real, "metas": vaza})
    with pytest.raises(AssertionError):
        confere_isolamento(a, b, "metas")


@pytest.mark.parametrize("card", ["hero", "compromissos"])
def test_isolamento_nao_confunde_relogio_com_dado_de_b(mundo, monkeypatch, card):
    """Regressão: `calculado_em` com microssegundos ".777712" contém "7777"; o isolamento não pode reprovar."""
    from core.services import previsao_v2
    real = previsao_v2.now_tz
    monkeypatch.setattr(previsao_v2, "now_tz", lambda *a, **k: real(*a, **k).replace(microsecond=777712))
    (_, (bloco, *_), _) = mostrar(mundo[0], card=card, **CASOS[card][0])
    assert ".777712" in json.dumps(bloco)  # o relógio travado chegou ao bloco
    confere_isolamento(*mundo, card)


@pytest.mark.parametrize("card", ["resumo", "categorias"])
def test_isolamento_pega_leitor_que_devolve_so_numeros_de_b(mundo, monkeypatch, card):
    """Controle negativo da marca numérica: o leitor passa a ler com o id de B; sem texto "SEGREDO"."""
    a, b = mundo
    confere_isolamento(a, b, card)  # positivo
    real = cards.registro()
    vaza = cards.Card(lambda uid, args: real[card].ler(b, args), real[card].visao)
    monkeypatch.setattr(cards, "registro", lambda: {**real, card: vaza})
    with pytest.raises(AssertionError):
        confere_isolamento(a, b, card)


def test_origens_somam_o_mesmo_que_categorias(mundo):
    a, _ = mundo
    d = ler(a, INICIO.year, INICIO.month, now_tz())
    assert sum(d["origens"].values()) == sum(c["valor"] for c in d["categorias"]) > 0
    # valores fixados pela fixture (semeia_a): OF 70+30, manual marcado na carteira 30, o resto legado
    assert d["origens"] == {"cartao": Decimal(0), "banco": Decimal(100), "carteira": Decimal(30),
                            "registro_antigo": Decimal(360)}
    (aba,) = mostrar(a, card="categorias", meses=[M])[1][0]["abas"]
    o = aba["recursos"]["detalhes"]["origens"]
    assert set(o) == {"cartao", "banco", "carteira", "registro_antigo"}
    assert o == {"cartao": "0.00", "banco": "100.00", "carteira": "30.00", "registro_antigo": "360.00"}
    assert sum(Decimal(v) for v in o.values()) == sum(Decimal(c["valor"]) for c in aba["recursos"]["detalhes"]["categorias"])


def test_origem_desconhecida_nao_derruba_o_mes(mundo, monkeypatch, caplog):
    """O SQL só produz 4 origens; se vier outra (ou nula), cai em registro_antigo e o mês abre."""
    from db import lancamentos
    a, _ = mundo
    real = lancamentos.listar

    def estranha(*args, **kw):
        linhas, mais = real(*args, **kw)
        for r, nova in zip((r for r in linhas if r["tipo"] == "saida" and not r["interno"]), (None, "zzz")):
            r["origem"] = nova
        return linhas, mais

    import db.mes_detalhes as leitor
    normal = ler(a, INICIO.year, INICIO.month, now_tz())
    monkeypatch.setattr(lancamentos, "listar", estranha)
    monkeypatch.setattr(leitor, "_AVISADAS", set())
    d = ler(a, INICIO.year, INICIO.month, now_tz())
    ler(a, INICIO.year, INICIO.month, now_tz())  # 2ª leitura: a mesma origem não avisa de novo
    avisos = [r.getMessage() for r in caplog.records if "origem desconhecida" in r.getMessage()]
    assert sorted(avisos) == sorted(f"mes_detalhes: origem desconhecida {o!r} contada em registro_antigo"
                                    for o in (None, "zzz"))
    assert sum(d["origens"].values()) == sum(c["valor"] for c in d["categorias"]) == normal["saiu"]
    assert d["origens"]["registro_antigo"] >= normal["origens"]["registro_antigo"]


def test_visao_arredonda_como_o_bloco(mundo):
    """`Dinheiro` só arredonda em json: 10.005 tem de ser 10.01 no bloco E na visão do modelo."""
    from api.nativo.mes_detalhes import Origens
    o = Origens(cartao=Decimal("10.005"), banco=0, carteira=0, registro_antigo=0)
    assert cards._fio(o)["cartao"] == "10.01" and cards._py(o)["cartao"] == 10.01
    a, _ = mundo
    db.add_launch_and_update_balance(a, "despesa", Decimal("10.005"), "x", None, categoria="arred_teste")
    r, (bloco,), _ = mostrar(a, card="categorias", categoria="arred_teste")  # fora do top 3 da visão: vem em `categoria_pedida`
    (cat_b,) = [c for c in bloco["abas"][0]["recursos"]["detalhes"]["categorias"] if c["categoria"] == "arred_teste"]
    assert cat_b["valor"] == "10.01" and r["abas"][0]["categoria_pedida"]["valor"] == 10.01


_DINHEIRO = {"valor", "total", "saiu", "entrou", "saldo", "aportes", "saques", "liquido"}


def _dinheiros(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in _DINHEIRO and v is not None and not isinstance(v, (dict, list)):
                yield v
            yield from _dinheiros(v)
    elif isinstance(o, list):
        for v in o:
            yield from _dinheiros(v)


@pytest.mark.parametrize("card", ["contas", "resumo", "categorias", "calendario", "patrimonio", "wealth"])
def test_visao_manda_dinheiro_como_numero_nao_como_texto(mundo, card):
    """O `ai_guard` só reconhece número JSON: texto "10.01" não é evidência."""
    a, _ = mundo
    r, _, _ = mostrar(a, card=card, **CASOS[card][0])
    valores = list(_dinheiros(r))
    assert valores and all(type(v) in (int, float) for v in valores), valores


def test_visao_mantem_cada_valor_na_linha_certa(mundo):
    """Valores distintos por categoria: um `_casa` que desalinha a lista (ex. `zip(p, reversed(j))`)
    trocaria os valores entre as linhas."""
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="categorias", meses=[M])
    cats = bloco["abas"][0]["recursos"]["detalhes"]["categorias"]
    assert len({c["valor"] for c in cats}) >= 3
    assert [(c["categoria"], c["valor"]) for c in r["abas"][0]["categorias"]] == \
        [(c["categoria"], float(c["valor"])) for c in cats[:3]]  # a visão é o resumo: as 3 maiores


def test_casa_falha_alto_quando_visao_e_bloco_divergem():
    with pytest.raises(AssertionError):
        cards._casa([Decimal(1), Decimal(2)], ["1.00"])
    with pytest.raises(AssertionError):
        cards._casa({1: Decimal(1)}, {"1": "1.00"})
