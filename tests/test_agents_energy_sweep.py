"""Queda de plano desliga agente acima do orçamento de energia.

O `agents_energy_budget` só era cobrado na ATIVAÇÃO. A queda para um tier SEM
energia (Grátis e Essencial, orçamento 0) já era tratada — os runners filtram
por `agent_kind_allowed`, que é `agents_energy_budget(uid) > 0`. O buraco era
de pago para pago, hoje um caso só na escada: Pro (14, cabem os 7) → Plus (4).

Controles negativos, medidos:

  - "pausa sempre" (tirar a guarda de orçamento E o `break` do laço) → o teste
    do "dentro do orçamento" e o de ordem ficam vermelhos;
  - "nunca pausa" (esvaziar o laço) → o do Pro→Plus fica vermelho.

Tirar SÓ o `if usada <= budget: continue` de cima não vale como controle: o
`break` no topo do laço faz o mesmo trabalho, então a injeção é no-op e os
quatro testes passam. Foi a primeira tentativa aqui, e ela não media nada.
"""
import pytest

from core.services import agents_energy_sweep as sweep
from core.services import plan_service
from core.services.plan_limits import agent_energy_cost
from db import activate_agent, list_agents

# 1+1+2+2+2+3+3 = 14 → exatamente o orçamento do Pro
TODOS = ("reporter", "carteiro", "xerife", "barao", "faria_limer", "detetive", "cofre")


def _liga_todos(user_id: int) -> None:
    for kind in TODOS:
        activate_agent(user_id, kind)          # sem energy_budget: sem gate na ativação
    assert sum(agent_energy_cost(k) for k in TODOS) == 14


def _ativos(user_id: int) -> set[str]:
    return {a["kind"] for a in list_agents(user_id) if a["status"] == "active"}


@pytest.fixture
def so_este_usuario(user_id, monkeypatch):
    """v2 ON + lister escopado ao usuário de teste.

    O `PLANS_V2_ENABLED=1` não é decoração: o `tests/conftest.py:21` põe `0` na
    suíte inteira, então sem isto a varredura sai no primeiro `if` e TODO teste
    daqui passaria por vazio — inclusive os que afirmam que ela pausou algo.
    A varredura real percorre a tabela toda e o DB de teste é compartilhado.
    """
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    from db import list_users_with_active_agents as real
    monkeypatch.setattr(
        sweep, "list_users_with_active_agents",
        lambda: [r for r in real() if int(r["user_id"]) == user_id],
    )
    monkeypatch.setattr(plan_service, "agents_beta_tester", lambda u, email=None: False)
    return user_id


def test_flag_off_tick_inerte(so_este_usuario, monkeypatch):
    """O freio de emergência desliga a varredura."""
    _liga_todos(so_este_usuario)
    monkeypatch.setattr(plan_service, "agents_energy_budget", lambda u: 0)
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")

    res = sweep.enforce_agents_energy_budget()

    assert res["disabled"] is True
    assert len(_ativos(so_este_usuario)) == 7


def test_pro_cai_para_plus_e_sobram_os_mais_antigos(so_este_usuario, monkeypatch):
    _liga_todos(so_este_usuario)
    monkeypatch.setattr(plan_service, "agents_energy_budget", lambda u: 4)

    res = sweep.enforce_agents_energy_budget()

    restantes = _ativos(so_este_usuario)
    usada = sum(agent_energy_cost(k) for k in restantes)
    assert usada <= 4, f"ficou acima do orçamento: {usada} com {sorted(restantes)}"
    assert res["paused"] == 7 - len(restantes)
    # Cai o mais RECENTE: os primeiros ativados são os que sobram.
    assert restantes == {"reporter", "carteiro", "xerife"}, (
        "deviam sobrar os três primeiros ativados (1+1+2 = 4)"
    )


def test_dentro_do_orcamento_nao_desliga_nada(so_este_usuario, monkeypatch):
    """Positivo: sem ele, pausar sempre passaria no grupo."""
    _liga_todos(so_este_usuario)
    monkeypatch.setattr(plan_service, "agents_energy_budget", lambda u: 14)

    res = sweep.enforce_agents_energy_budget()

    assert res["paused"] == 0
    assert len(_ativos(so_este_usuario)) == 7


def test_tester_do_beta_fica_intocado(user_id, monkeypatch):
    """O `agent_kind_allowed` já isenta o tester nos runners. Sem a mesma saída
    aqui, a varredura desligaria justamente quem está testando o beta."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    from db import list_users_with_active_agents as real
    monkeypatch.setattr(
        sweep, "list_users_with_active_agents",
        lambda: [r for r in real() if int(r["user_id"]) == user_id],
    )
    _liga_todos(user_id)
    monkeypatch.setattr(plan_service, "agents_beta_tester", lambda u, email=None: True)
    monkeypatch.setattr(plan_service, "agents_energy_budget", lambda u: 0)

    res = sweep.enforce_agents_energy_budget()

    assert res["paused"] == 0
    assert len(_ativos(user_id)) == 7
