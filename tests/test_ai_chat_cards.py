"""PR B1: `show_card`. Banco, sessão e rotas reais; só a OpenAI é falsa.

Paridade: o bloco que o chat manda é IGUAL ao JSON que o endpoint do widget devolve
(o envelope `{"ok": True, ...}` das 5 rotas legadas mora na rota e no leitor: é este
teste que os mantém iguais). Controle negativo: `test_paridade_pega_leitor_trocado`.
Gates, teto e eventos do stream: `test_ai_chat_cards_turno.py`.
"""
import json
from datetime import timedelta
import re
from decimal import Decimal

import pytest

import db
from _cards_apoio import CASOS, M, M0, _http, mostrar, mundo  # noqa: F401  (mundo é fixture)
from conftest import usuario_pagante
from core.services.ai_chat import cards
from db.mes_detalhes import ler
from tests.test_resumo_mes_regra import INICIO
from utils_date import now_tz


def test_o_registro_cobre_os_16_cards():
    assert set(CASOS) == set(cards.IDS) == set(cards.registro()) and len(cards.IDS) == 16


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


@pytest.mark.parametrize("card", list(CASOS))
def test_paridade_http_x_bloco(mundo, card):
    a, _ = mundo
    _, bloco = confere_paridade(a, card)
    if card in ("metas", "fatura", "parcelas", "categorias", "contas"):  # a fixture não é vazia
        assert any(aba["recursos"] for aba in bloco["abas"])
        dado = json.dumps(bloco)
        assert {"metas": "viagem", "fatura": "Nubank", "parcelas": "casa", "categorias": "mercado",
                "contas": "carteira"}[card] in dado


def test_paridade_pega_leitor_trocado(mundo, monkeypatch):
    """Controle negativo: o leitor de `categorias` volta a ser `get_top_expense_categories`."""
    a, _ = mundo
    confere_paridade(a, "categorias")  # positivo: o leitor real passa
    real = cards.registro()

    def errado(uid, args):
        top = db.get_top_expense_categories(uid, INICIO, INICIO + timedelta(days=27))
        return [(m, {"detalhes": {**o["detalhes"].model_dump(mode="json"), "categorias": top}})
                for m, o in real["categorias"].ler(uid, args)]

    monkeypatch.setattr(cards, "registro", lambda: {**real, "categorias": cards.Card(errado, real["categorias"].visao)})
    with pytest.raises(AssertionError):
        confere_paridade(a, "categorias")


_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _numeros(o):
    if isinstance(o, bool):
        return set()
    if isinstance(o, (int, float)):
        return {float(o)}
    if isinstance(o, str):
        return {float(o)} if _NUM.match(o) else set()
    filhos = o.values() if isinstance(o, dict) else o if isinstance(o, list) else []
    return set().union(*map(_numeros, filhos)) if filhos else set()


@pytest.mark.parametrize("card", list(CASOS))
def test_visao_do_modelo_esta_no_bloco(mundo, card):
    a, _ = mundo
    args, _ = CASOS[card]
    r, (bloco,), _ = mostrar(a, card=card, **args)
    assert r["card"] == card and len(r["abas"]) == len(bloco["abas"])
    assert _numeros(r) <= _numeros(bloco)
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
    for marca in ("SEGREDO", "7777", "5555", "888.00"):
        assert marca not in texto, (card, marca)
    assert not (_ids(bloco_a) & _ids(bloco_b)), card
    if card in ("metas", "fatura"):
        assert _ids(bloco_a) and _ids(bloco_b)


@pytest.mark.parametrize("card", list(CASOS))
def test_bloco_de_a_nao_tem_nada_de_b(mundo, card):
    confere_isolamento(*mundo, card)


def test_isolamento_pega_leitor_que_le_o_outro_usuario(mundo, monkeypatch):
    """Controle negativo: o leitor de `metas` passa a ler as caixinhas de B."""
    a, b = mundo
    confere_isolamento(a, b, "metas")  # positivo
    real = cards.registro()
    from core.services.metas_status import status_metas
    vaza = cards.Card(lambda uid, args: [(None, {"metas": {"ok": True, "goals": status_metas(b)}})],
                      real["metas"].visao)
    monkeypatch.setattr(cards, "registro", lambda: {**real, "metas": vaza})
    with pytest.raises(AssertionError):
        confere_isolamento(a, b, "metas")


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
    r, (bloco,), _ = mostrar(a, card="categorias")
    (cat_b,) = [c for c in bloco["abas"][0]["recursos"]["detalhes"]["categorias"] if c["categoria"] == "arred_teste"]
    (cat_m,) = [c for c in r["abas"][0]["categorias"] if c["categoria"] == "arred_teste"]
    assert cat_b["valor"] == "10.01" and cat_m["valor"] == 10.01


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
        [(c["categoria"], float(c["valor"])) for c in cats]


def test_casa_falha_alto_quando_visao_e_bloco_divergem():
    with pytest.raises(AssertionError):
        cards._casa([Decimal(1), Decimal(2)], ["1.00"])
    with pytest.raises(AssertionError):
        cards._casa({1: Decimal(1)}, {"1": "1.00"})
