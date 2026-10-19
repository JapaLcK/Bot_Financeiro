"""O catálogo de agentes do @ do /painel (`webapp/src/dashboard/lib/agentes.ts`, AGENTES) tem uma fonte de verdade só:
os mesmos kinds e nomes de `core.services.agent_chat.DOMAINS` (quem pode conversar) e dos `AGENT_CATALOG` disponíveis
(a prateleira de /agents). Agente novo num lado sem o outro reprova aqui."""
from __future__ import annotations

import re
from pathlib import Path

from core.services.agent_chat import DOMAINS
from frontend.routes.agents import AGENT_CATALOG

TS = Path(__file__).resolve().parent.parent / "webapp/src/dashboard/lib/agentes.ts"


def _tela() -> list[tuple[str, str]]:
    bloco = re.search(r"export const AGENTES = \[(.*?)\n\];", TS.read_text(encoding="utf-8"), re.S).group(1)
    return re.findall(r'\{ kind: "(\w+)", nome: "([^"]+)", tema: "[^"]+" \}', bloco)


def test_tela_igual_ao_dominio_do_chat():
    assert dict(_tela()) == {k: nome for k, (nome, _) in DOMAINS.items()}
    assert len(_tela()) == len(DOMAINS)


def test_tela_igual_aos_agentes_disponiveis_da_prateleira():
    disponiveis = {c["kind"]: c["nome"] for c in AGENT_CATALOG if c["disponivel"]}
    assert dict(_tela()) == disponiveis
