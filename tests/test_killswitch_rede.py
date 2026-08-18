"""O kill switch de rede (`_block_outbound_network`, em conftest.py) precisa
cobrir os caminhos que o código de PRODUÇÃO realmente usa — não só os que
alguém lembrou de listar.

Sem essa cobertura, rodar a suíte numa máquina com credenciais no ambiente
pode tocar a conta Pluggy de verdade ou gastar quota da OpenAI. A fixture
existe justamente para impedir isso, então o furo é silencioso: nada falha,
a chamada só sai.

Cada teste aqui parte de um inventário do que produção usa
(`grep -rhoE "httpx\\.(Client|AsyncClient|...)" --include=*.py`), não de uma
lista escrita de memória.
"""
import httpx
import pytest
import requests

# Importado no TOPO de propósito: acontece durante a COLETA, antes de a fixture
# rodar — que é exatamente como o furo se manifesta. `core/handle_incoming.py:38`
# faz `from ai_router import _internal_user_id`, então vários testes já puxam
# este módulo na coleta sem saber. Mover para dentro do teste esconderia o bug.
import ai_router

# Host reservado pela RFC 2606: se a chamada escapar do bloqueio, ela morre em
# resolução de DNS em vez de bater em serviço real de alguém.
ALVO = "http://host-que-nao-existe.invalid/x"


def _bloqueado(fn) -> bool:
    """True se o kill switch pegou; False se a chamada chegou a sair."""
    try:
        fn()
    except RuntimeError as exc:
        return "blocked" in str(exc).lower()
    except Exception:
        return False  # erro de rede == escapou do bloqueio
    return False


def test_httpx_client_sincrono_e_bloqueado():
    """`httpx.Client` é a forma MAIS usada em produção (8 ocorrências) —
    core/services/pluggy.py inteiro depende dela, com GET/POST/PATCH/DELETE
    autenticados contra a conta bancária do usuário."""
    def chamada():
        with httpx.Client(timeout=1) as client:
            client.get(ALVO)

    assert _bloqueado(chamada), "httpx.Client escapou do kill switch"


def test_httpx_client_bloqueia_todos_os_verbos():
    """Bloquear verbo a verbo deixa buracos: o Pluggy usa PATCH e DELETE, que
    não estavam na lista original. Client.get/post/... chamam self.request,
    que chama self.send — o bloqueio no send cobre todos de uma vez."""
    for verbo in ("get", "post", "put", "patch", "delete"):
        def chamada(v=verbo):
            with httpx.Client(timeout=1) as client:
                getattr(client, v)(ALVO)

        assert _bloqueado(chamada), f"httpx.Client.{verbo} escapou"


def test_requests_session_e_bloqueada():
    """`requests.Session()` mantém o mesmo furo do httpx.Client: os verbos do
    módulo estavam cobertos, os da sessão não."""
    def chamada():
        with requests.Session() as s:
            s.get(ALVO, timeout=1)

    assert _bloqueado(chamada), "requests.Session escapou do kill switch"


def test_openai_do_ai_router_e_o_dublê():
    """`ai_router.py:20` faz `from openai import OpenAI`, o que copia o nome
    para dentro do módulo no import. Trocar `openai.OpenAI` na fixture não
    alcança essa cópia — o patch precisa ser no consumidor.

    Sem isso, com OPENAI_API_KEY no ambiente, `_get_client()` constrói o
    cliente real e a chamada é cobrada.
    """
    cliente = ai_router.OpenAI(api_key="sk-nao-usar")
    assert type(cliente).__name__ == "_FakeOpenAIClient", (
        f"ai_router.OpenAI construiu {type(cliente).__name__}, não o dublê"
    )


def test_openai_client_cacheado_nao_vaza_entre_testes():
    """`_get_client()` guarda o cliente num global. Se um teste anterior o
    tivesse preenchido com o cliente real, o patch da fixture não adiantaria
    — ela precisa zerar o cache também."""
    assert ai_router._client is None or type(ai_router._client).__name__ == "_FakeOpenAIClient"
