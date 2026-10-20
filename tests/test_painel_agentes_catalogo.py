"""O catálogo de agentes do @ (AGENTES) do /painel (`webapp/src/dashboard/lib/agentes.ts`) e do app
(`app/src/features/painel/agentes.ts`) tem uma fonte de verdade só: os mesmos kinds e nomes de
`core.services.agent_chat.DOMAINS` (quem pode conversar) e dos `AGENT_CATALOG` disponíveis (a prateleira de /agents).
Os dois são projetos npm separados, então o catálogo fica duplicado e o tema também tem de bater entre eles.
Agente novo num lado sem o outro reprova aqui."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.services.agent_chat import DOMAINS
from frontend.routes.agents import AGENT_CATALOG

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "webapp/src/dashboard/lib/agentes.ts"
APP = RAIZ / "app/src/features/painel/agentes.ts"
TELAS = pytest.mark.parametrize("ts", [WEB, APP], ids=["web", "app"])


def _catalogo(ts: Path) -> list[tuple[str, str, str]]:
    bloco = re.search(r"export const AGENTES = \[(.*?)\n\];", ts.read_text(encoding="utf-8"), re.S).group(1)
    linha = re.compile(r'\s*\{ kind: "(\w+)", nome: "([^"]+)", tema: "([^"]+)" \},?')
    itens = [linha.fullmatch(l) for l in bloco.splitlines() if l.strip()]
    assert all(itens), f"linha de AGENTES fora do formato (uma entrada por linha, sem comentário) em {ts}"
    return [m.groups() for m in itens]


def _tela(ts: Path) -> list[tuple[str, str]]:
    return [(k, nome) for k, nome, _ in _catalogo(ts)]


@TELAS
def test_tela_igual_ao_dominio_do_chat(ts):
    assert dict(_tela(ts)) == {k: nome for k, (nome, _) in DOMAINS.items()}
    assert len(_tela(ts)) == len(DOMAINS)


@TELAS
def test_tela_igual_aos_agentes_disponiveis_da_prateleira(ts):
    disponiveis = {c["kind"]: c["nome"] for c in AGENT_CATALOG if c["disponivel"]}
    assert dict(_tela(ts)) == disponiveis


def test_app_igual_ao_painel():
    assert _catalogo(APP) == _catalogo(WEB)
