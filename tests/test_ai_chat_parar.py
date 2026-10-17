"""Chat do app (PR A), parte 2: parar. Pendência, histórico e cota.

Controles: sem o ponto (c) o "sim" executa (`..._sem_o_ponto_c_...`); sem o
`raise` do `terminal_msg` o "sim" seguinte registra o lançamento; sem a volta da
vaga antes da 1ª chamada a cota fica queimada.
"""
import asyncio
import dataclasses
import json
import threading

from core.services.ai_chat import runner, streaming
from core.services.ai_chat.tools import get_tool
from frontend.routes import ai_chat as rota
import db
from _ia_stream_helpers import executar, linhas, openai_falso, rodada

PARADA = "O usuário interrompeu esta resposta."


def _arma_e_desconecta(monkeypatch, uid):
    """Mensagem 1: a IA arma `delete_all_launches`; o cliente cai quando a
    pergunta de confirmação termina de chegar."""
    db.add_launch_and_update_balance(uid, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    cancela = threading.Event()
    openai_falso(monkeypatch,
                 rodada(None, ("delete_all_launches", {})),
                 rodada("Apago tudo mesmo? Responde sim ou não.", ao_fim=cancela.set),
                 rodada("Não entendi o que confirmar."))
    return executar(uid, "apaga tudo", cancela)


def test_parar_na_pergunta_desarma_e_o_sim_nao_executa(monkeypatch, pro_user_id):
    r, ev, _ = _arma_e_desconecta(monkeypatch, pro_user_id)
    assert r.interrompida and r.reply is None
    assert db.ai_get_pending_action(pro_user_id) is None
    assert linhas(pro_user_id)[-1]["content"] == PARADA
    runner.chat(pro_user_id, "sim", monthly_limit=100)  # mensagem 2, sem emissor
    assert db.count_launches(pro_user_id) == 1, "o 'sim' executou uma pergunta que o usuário nunca viu"


def test_controle_negativo_sem_o_ponto_c_o_sim_executa(monkeypatch, pro_user_id):
    monkeypatch.setattr(streaming, "cancelado", lambda: False)
    r, _, _ = _arma_e_desconecta(monkeypatch, pro_user_id)
    assert not r.interrompida and db.ai_get_pending_action(pro_user_id) is not None
    runner.chat(pro_user_id, "sim", monthly_limit=100)
    assert db.count_launches(pro_user_id) == 0, "o controle devia reproduzir o 'sim' que executa"


def test_parar_com_a_pergunta_vinda_de_uma_tool_o_sim_nao_registra(monkeypatch, pro_user_id):
    """O cancelamento chega durante a tool que arma o `_CONFIRMA` (terminal_msg)."""
    real = get_tool("add_launch")
    monkeypatch.setattr(runner, "get_tool", lambda n: dataclasses.replace(
        real, confirmar_se=lambda uid, args: True) if n == "add_launch" else get_tool(n))
    cancela, despacho = threading.Event(), runner._dispatch_tool
    monkeypatch.setattr(runner, "_dispatch_tool", lambda *a: (cancela.set(), despacho(*a))[1])
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 20, "alvo": "mercado"})),
                 rodada("Não há nada para confirmar."))
    r, _, _ = executar(pro_user_id, "gastei 20 no mercado", cancela)
    assert r.interrompida and db.ai_get_pending_action(pro_user_id) is None
    monkeypatch.setattr(runner, "_dispatch_tool", despacho)
    runner.chat(pro_user_id, "sim", monthly_limit=100)
    assert db.count_launches(pro_user_id) == 0
    assert db.ai_get_usage_this_month(pro_user_id) == 2  # a vaga do turno parado ficou (a OpenAI foi chamada)


def test_cancelar_no_meio_da_rodada_nao_deixa_tool_calls_orfao(monkeypatch, pro_user_id):
    """O cancelamento chega entre as duas tools da rodada: as duas rodam e são
    gravadas; só então o topo do loop interrompe. A OpenAI falsa recusa o
    histórico órfão no turno seguinte (sem emissor, como o WhatsApp)."""
    cancela, real = threading.Event(), runner._dispatch_tool
    monkeypatch.setattr(runner, "_dispatch_tool", lambda *a: (cancela.set(), real(*a))[1])
    openai_falso(monkeypatch, rodada(None, ("get_balance", {}), ("list_categories", {})),
                 rodada("Segue a conversa."))
    r, _, _ = executar(pro_user_id, "saldo e categorias", cancela)
    assert r.interrompida
    assert [x["role"] for x in linhas(pro_user_id)] == ["user", "assistant", "tool", "tool", "system"]
    monkeypatch.setattr(runner, "_dispatch_tool", real)
    assert runner.chat(pro_user_id, "e agora?", monthly_limit=100) == "Segue a conversa."


# ── cota ────────────────────────────────────────────────────────────────────

def test_parar_depois_da_openai_nao_devolve_a_vaga_e_erro_devolve(monkeypatch, pro_user_id):
    cancela = threading.Event()
    openai_falso(monkeypatch, rodada("Texto que ninguém lê.", ao_fim=cancela.set))
    r, _, _ = executar(pro_user_id, "oi", cancela)
    assert r.interrompida and db.ai_get_usage_this_month(pro_user_id) == 1

    openai_falso(monkeypatch, rodada(erro=RuntimeError("openai fora")))
    r, _, _ = executar(pro_user_id, "oi de novo")
    assert r.reply == runner.ERROR_MSG.removeprefix("🐷 ") and not r.sugerivel
    assert db.ai_get_usage_this_month(pro_user_id) == 1, "o erro devia devolver a vaga"


def test_cancelado_antes_de_comecar_nao_reserva_nem_grava(monkeypatch, pro_user_id):
    cancela = threading.Event()
    cancela.set()
    reg = openai_falso(monkeypatch)
    r, ev, _ = executar(pro_user_id, "oi", cancela)
    assert r.interrompida and ev == [] and reg.creates == []
    assert db.ai_get_usage_this_month(pro_user_id) == 0 and linhas(pro_user_id) == []


def test_cancelado_entre_a_reserva_e_a_openai_devolve_a_vaga(monkeypatch, pro_user_id):
    cancela, real = threading.Event(), db.ai_append_message
    # o cancelamento chega logo depois de gravar a mensagem do usuário, antes da 1ª chamada
    monkeypatch.setattr(db, "ai_append_message",
                        lambda *a, **k: (real(*a, **k), cancela.set())[0])
    reg = openai_falso(monkeypatch)
    r, _, _ = executar(pro_user_id, "oi", cancela)
    assert r.interrompida and reg.creates == []
    assert db.ai_get_usage_this_month(pro_user_id) == 0, "a OpenAI nunca foi chamada: a vaga volta"


# ── a desconexão de verdade, pelo gerador do endpoint ───────────────────────

def test_desconectar_interrompe_o_turno_sem_gravar_o_parcial(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada("x" * 400), pausa=0.05)

    async def cenario():
        gen = rota._fluxo(pro_user_id, "oi", 100)
        assert json.loads((await gen.__anext__())[6:])["tipo"] == "delta"
        await gen.aclose()  # o app abortou
        await asyncio.wait_for(asyncio.gather(*rota._TURNOS), 10)

    asyncio.run(cenario())
    assert linhas(pro_user_id)[-1]["content"] == PARADA
    assert not [x for x in linhas(pro_user_id) if x["role"] == "assistant"]
    assert db.ai_get_usage_this_month(pro_user_id) == 1
