"""§0.7 do PR 4 da Etapa 7 (e do PR 6, os cards legados): os recursos `detalhes` (cards resumo, categorias, calendario) e `periodo` do bloco não são da /api/v2, então o /painel os tipa à mão
(`webapp/src/dashboard/lib/ia-cards.ts`) e os testes do navegador desenham as FIXTURES `tests/fixtures/ai_chat_recurso_detalhes.json` e `ai_chat_bloco_periodo.json`.
Aqui o Pydantic confere as duas coisas: a fixture é o dump EXATO do modelo, o bloco real do servidor tem a forma dela e a lista de campos do `.ts` é a do modelo.
PR 6: o `rendimento` é Pydantic (`ai_chat_recurso_rendimento.json` = dump exato, campos do `.ts` conferidos). Os 5 legados (caixinhas, fatura, parcelas, renda, piggy) são dict de float
SEM modelo: as fixtures `ai_chat_recurso_{caixinhas,cartoes,parcelas,renda,insights}.json` são conferidas contra o bloco REAL (chaves e tipos), com dinheiro com casas (300.5, não 300:
o `_conforme` compara `type()`).

CONTROLES (mutação, medida; remeça se mexer):
  negativos: fixture legada sem uma chave -> test_controle_fixture_legada_sem_chave_fica_vermelho; fixture legada com float onde o real tem int -> test_controle_fixture_legada_com_tipo_trocado_fica_vermelho;
             fixture com um campo a menos -> test_controle_fixture_sem_campo_fica_vermelho; `.ts` com um campo renomeado -> test_controle_tipo_ts_com_campo_trocado_fica_vermelho;
             bloco real de `categorias` sem uma chave de `detalhes` (leitor trocado) -> test_controle_bloco_real_sem_chave_fica_vermelho
  positivos: o estado atual passa (os testes sem `controle` no nome)."""
import json
import re
from pathlib import Path

import pytest

from _cards_apoio import M, mostrar, mundo  # noqa: F401  (mundo é fixture)
from api.nativo.mes_detalhes import Categoria, CategoriaDoPeriodo, Dia, Guardado, MesDetalhes, MesDoPeriodo, Origens, Periodo
from api.nativo.rendimento import Ativo, PeriodoAtivo, Rendimento, Rendimentos, TaxaContratada
from api.nativo.rendimento import Categoria as CategoriaRend
from api.nativo.rendimento import Periodo as PeriodoRend
from test_ai_chat_periodo import _conforme

FIXTURES = Path(__file__).parent / "fixtures"
TS = Path(__file__).resolve().parent.parent / "webapp" / "src" / "dashboard" / "lib" / "ia-cards.ts"
DETALHES = json.loads((FIXTURES / "ai_chat_recurso_detalhes.json").read_text(encoding="utf-8"))
PERIODO = json.loads((FIXTURES / "ai_chat_bloco_periodo.json").read_text(encoding="utf-8"))
RENDIMENTO = json.loads((FIXTURES / "ai_chat_recurso_rendimento.json").read_text(encoding="utf-8"))
# card -> (recurso do bloco, fixture, args do show_card)
LEGADOS = {
    "caixinhas": ("caixinhas", "caixinhas", {}), "fatura": ("cartoes", "cartoes", {}), "parcelas": ("parcelas", "parcelas", {}),
    "renda": ("renda", "renda", {"quantidade_meses": 6}), "piggy": ("insights", "insights", {}),
}
LEGADO = {c: json.loads((FIXTURES / f"ai_chat_recurso_{f}.json").read_text(encoding="utf-8")) for c, (_, f, _) in LEGADOS.items()}


def dump(modelo, fixture):
    return modelo.model_validate(fixture).model_dump(mode="json")


def test_fixture_de_detalhes_e_o_dump_exato_do_modelo():
    assert dump(MesDetalhes, DETALHES) == DETALHES


def test_fixture_do_periodo_e_o_dump_exato_do_modelo():
    assert dump(Periodo, PERIODO) == PERIODO


def test_fixture_do_rendimento_e_o_dump_exato_do_modelo():
    assert dump(Rendimento, RENDIMENTO) == RENDIMENTO


def test_a_fixture_do_rendimento_cobre_o_que_o_card_desenha():
    """2+ categorias, uma com mais de 5 ativos, um ativo sem valor no mês e o Mês preenchido (o card abre em Mês); uma categoria sem valor no Total."""
    r = RENDIMENTO["rendimentos"]
    assert len(r["categorias"]) >= 2 and any(len(c["ativos"]) > 5 for c in r["categorias"]) and r["mes"]["valor"] is not None
    assert any(a["mes"]["valor"] is None for c in r["categorias"] for a in c["ativos"]) and any(c["total"]["valor"] is None for c in r["categorias"])


def test_a_fixture_de_detalhes_cobre_o_que_os_cards_desenham():
    """Mais de 5 categorias (a "Outras"), uma sem chave ("Sem categoria"), um dia de outro ano e um depois do corte: o que os testes do navegador atacam."""
    assert len(DETALHES["categorias"]) > 5 and any(c["categoria"] is None for c in DETALHES["categorias"])
    assert any(d["dia"][:4] != DETALHES["mes"][:4] for d in DETALHES["dias"]) and any(d["dia"] > DETALHES["ate"] for d in DETALHES["dias"])
    assert sum(float(c["valor"]) for c in DETALHES["categorias"]) == float(DETALHES["saiu"])


def test_bloco_real_de_categorias_tem_a_forma_da_fixture(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="categorias", meses=[M])
    _conforme(bloco["abas"][0]["recursos"]["detalhes"], DETALHES, "detalhes")


@pytest.mark.parametrize("card", LEGADOS)
def test_bloco_real_legado_tem_a_forma_da_fixture(mundo, card):
    a, _ = mundo
    chave, _, args = LEGADOS[card]
    _, (bloco,), _ = mostrar(a, card=card, **args)
    _conforme(bloco["abas"][0]["recursos"][chave], LEGADO[card], chave)


def test_bloco_real_do_rendimento_tem_a_forma_da_fixture(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="rendimento")
    _conforme(bloco["abas"][0]["recursos"]["rendimento"], RENDIMENTO, "rendimento")


def test_a_fixture_legada_traz_o_que_os_cards_atacam():
    """Os casos que os testes do navegador exigem: meta com prazo, caixinha do banco sem plano, cartão sem limite, parcelamento todo pago, mês a mais (UTC) na renda."""
    g = LEGADO["caixinhas"]["goals"]
    assert any(x["is_goal"] and x["target_date"] for x in g) and any(x["source"] == "open_finance" and not x["of_plan_active"] for x in g) and any(not x["is_goal"] for x in g)
    assert any(c["credit_limit"] is None for c in LEGADO["fatura"]["cards"]) and any(c["credit_limit"] for c in LEGADO["fatura"]["cards"])
    ps = LEGADO["parcelas"]["installments"]
    assert any(all(p["is_paid"] for p in x["parcelas"]) for x in ps) and any(any(p["is_paid"] for p in x["parcelas"]) and any(p["is_next"] for p in x["parcelas"]) for x in ps)
    assert [m["month"] for m in LEGADO["renda"]["evolution"]][-1] == "2026-11" and len(LEGADO["renda"]["evolution"]) == 6 and len(LEGADO["piggy"]["insights"]) == 2


def _campos(modelo):
    return set(modelo.model_fields)


def _declaracao(ts: str, nome: str) -> str:
    achou = re.search(rf"export type {nome} = (.*?)(?=\nexport type |\Z)", ts, re.S)
    assert achou, f"{nome} sumiu do ia-cards.ts"
    return achou.group(1)


def campos_faltando_no_ts(ts: str) -> list[str]:
    """Todo campo de todo modelo tem de aparecer no tipo do `.ts` (a chave, com ou sem `?`). Limite: confere só NOMES, procurados no corpo inteiro do tipo de
    topo — não vê campo do aninhado que só exista no pai, nem o tipo de cada campo. Pega o caso que importa (campo novo no servidor); a fronteira de verdade
    são os `contrato*` dos cards, em tempo de execução."""
    faltam = []
    for tipo, modelos in {"MesDetalhes": (MesDetalhes, Categoria, Dia, Guardado, Origens), "Periodo": (Periodo, MesDoPeriodo, CategoriaDoPeriodo),
                          "Rendimento": (Rendimento, TaxaContratada, Rendimentos, CategoriaRend, PeriodoRend, PeriodoAtivo, Ativo)}.items():
        corpo = _declaracao(ts, tipo)
        faltam += [f"{tipo}.{c}" for m in modelos for c in _campos(m) if not re.search(rf"\b{c}\??:", corpo)]
    return faltam


def test_tipos_ts_tem_todos_os_campos_do_modelo():
    assert campos_faltando_no_ts(TS.read_text(encoding="utf-8")) == []


def test_controle_tipo_ts_com_campo_trocado_fica_vermelho():
    ts = TS.read_text(encoding="utf-8")
    assert "criterio_dias:" in ts and "percentual:" in ts  # o controle só vale se o trecho existe
    assert campos_faltando_no_ts(ts.replace("criterio_dias:", "criterio:")) == ["MesDetalhes.criterio_dias"]
    assert campos_faltando_no_ts(ts.replace("percentual:", "pct:")) == ["Periodo.percentual"]
    assert "sincronizado_em:" in ts and "logo_proprio:" in ts
    assert campos_faltando_no_ts(ts.replace("sincronizado_em:", "sinc:")) == ["Rendimento.sincronizado_em"]
    assert campos_faltando_no_ts(ts.replace("logo_proprio:", "logo:")) == ["Rendimento.logo_proprio"]


def test_controle_fixture_sem_campo_fica_vermelho():
    sem = {k: v for k, v in DETALHES.items() if k != "guardado"}
    with pytest.raises(Exception):
        dump(MesDetalhes, sem)
    extra = {**DETALHES, "novo": 1}
    assert dump(MesDetalhes, extra) != extra  # o dump descarta o campo a mais: a igualdade exata o pega


def test_controle_bloco_real_sem_chave_fica_vermelho(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="categorias", meses=[M])
    real = dict(bloco["abas"][0]["recursos"]["detalhes"])
    del real["dias"]
    with pytest.raises(AssertionError):
        _conforme(real, DETALHES, "detalhes")


def test_controle_fixture_legada_sem_chave_fica_vermelho(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="fatura")
    real = bloco["abas"][0]["recursos"]["cartoes"]
    sem = {**LEGADO["fatura"], "cards": [{k: v for k, v in LEGADO["fatura"]["cards"][0].items() if k != "credit_used"}]}
    with pytest.raises(AssertionError):
        _conforme(real, sem, "cartoes")
    _conforme(real, LEGADO["fatura"], "cartoes")  # o positivo: a fixture inteira passa


def test_controle_fixture_legada_com_tipo_trocado_fica_vermelho(mundo):
    """O dinheiro legado é float: uma fixture com `300` (int) no lugar de `300.5` não mede o tipo que o servidor manda."""
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="fatura")
    real = bloco["abas"][0]["recursos"]["cartoes"]
    inteiro = {**LEGADO["fatura"], "cards": [{**LEGADO["fatura"]["cards"][0], "credit_used": 1354}]}
    with pytest.raises(AssertionError):
        _conforme(real, inteiro, "cartoes")
