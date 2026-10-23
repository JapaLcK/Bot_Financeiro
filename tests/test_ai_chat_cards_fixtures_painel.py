"""§0.7 do PR 4 da Etapa 7: os recursos `detalhes` (cards resumo, categorias, calendario) e `periodo` do bloco não são da /api/v2, então o /painel os tipa à mão
(`webapp/src/dashboard/lib/ia-cards.ts`) e os testes do navegador desenham as FIXTURES `tests/fixtures/ai_chat_recurso_detalhes.json` e `ai_chat_bloco_periodo.json`.
Aqui o Pydantic confere as duas coisas: a fixture é o dump EXATO do modelo, o bloco real do servidor tem a forma dela e a lista de campos do `.ts` é a do modelo.

CONTROLES (mutação, medida; remeça se mexer):
  negativos: fixture com um campo a menos -> test_controle_fixture_sem_campo_fica_vermelho; `.ts` com um campo renomeado -> test_controle_tipo_ts_com_campo_trocado_fica_vermelho;
             bloco real de `categorias` sem uma chave de `detalhes` (leitor trocado) -> test_controle_bloco_real_sem_chave_fica_vermelho
  positivos: o estado atual passa (os testes sem `controle` no nome)."""
import json
import re
from pathlib import Path

import pytest

from _cards_apoio import M, mostrar, mundo  # noqa: F401  (mundo é fixture)
from api.nativo.mes_detalhes import Categoria, CategoriaDoPeriodo, Dia, Guardado, MesDetalhes, MesDoPeriodo, Origens, Periodo
from test_ai_chat_periodo import _conforme

FIXTURES = Path(__file__).parent / "fixtures"
TS = Path(__file__).resolve().parent.parent / "webapp" / "src" / "dashboard" / "lib" / "ia-cards.ts"
DETALHES = json.loads((FIXTURES / "ai_chat_recurso_detalhes.json").read_text(encoding="utf-8"))
PERIODO = json.loads((FIXTURES / "ai_chat_bloco_periodo.json").read_text(encoding="utf-8"))


def dump(modelo, fixture):
    return modelo.model_validate(fixture).model_dump(mode="json")


def test_fixture_de_detalhes_e_o_dump_exato_do_modelo():
    assert dump(MesDetalhes, DETALHES) == DETALHES


def test_fixture_do_periodo_e_o_dump_exato_do_modelo():
    assert dump(Periodo, PERIODO) == PERIODO


def test_a_fixture_de_detalhes_cobre_o_que_os_cards_desenham():
    """Mais de 5 categorias (a "Outras"), uma sem chave ("Sem categoria"), um dia de outro ano e um depois do corte: o que os testes do navegador atacam."""
    assert len(DETALHES["categorias"]) > 5 and any(c["categoria"] is None for c in DETALHES["categorias"])
    assert any(d["dia"][:4] != DETALHES["mes"][:4] for d in DETALHES["dias"]) and any(d["dia"] > DETALHES["ate"] for d in DETALHES["dias"])
    assert sum(float(c["valor"]) for c in DETALHES["categorias"]) == float(DETALHES["saiu"])


def test_bloco_real_de_categorias_tem_a_forma_da_fixture(mundo):
    a, _ = mundo
    _, (bloco,), _ = mostrar(a, card="categorias", meses=[M])
    _conforme(bloco["abas"][0]["recursos"]["detalhes"], DETALHES, "detalhes")


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
    for tipo, modelos in {"MesDetalhes": (MesDetalhes, Categoria, Dia, Guardado, Origens), "Periodo": (Periodo, MesDoPeriodo, CategoriaDoPeriodo)}.items():
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
