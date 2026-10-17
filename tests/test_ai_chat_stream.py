"""Chat do app em streaming (PR A), parte 1: emissor, eventos e endpoint.

Banco e runner reais; só a OpenAI é falsa (`_ia_stream_helpers`). Parar, cota e
trava do turno: `test_ai_chat_parar.py` e `test_ai_chat_turno.py`; 👍👎:
`test_ai_chat_feedback.py`.
"""
import json
import threading
from pathlib import Path

import pytest

import db
from core.services.ai_chat import runner, streaming
from core.services.ai_chat.tools import SCHEMAS
from frontend.routes import ai_chat as rota
from _cards_apoio import CASOS, mostrar, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import (conta, esgota_a_cota, eventos_sse, executar, linhas,
                                openai_falso, rodada)
from test_quiz_conta import _navegador
from test_senha_obrigatoria import H


def test_sem_emissor_a_chamada_a_openai_e_a_de_sempre(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == "Oi!"
    (kw,) = reg.creates
    assert "stream" not in kw and set(kw) == {"model", "temperature", "messages", "tools", "max_tokens"}
    assert kw["tools"] is SCHEMAS


def test_passo_delta_e_id_da_resposta_gravada(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada(None, ("get_period_summary", {"period": "month"})),
                 rodada("Gastou pouco este mês."))
    r, ev, _ = executar(pro_user_id, "como foi o mês?")
    assert ev[0] == {"tipo": "passo", "previa": None, "texto": "Resumindo o período"}
    assert {e["tipo"] for e in ev[1:]} == {"delta"}
    assert "".join(e["texto"] for e in ev[1:]) == "Gastou pouco este mês." == r.reply
    ultima = linhas(pro_user_id)[-1]
    assert (ultima["role"], ultima["content"], ultima["id"]) == ("assistant", r.reply, r.message_id)
    assert r.sugerivel and not r.interrompida


def test_reinicio_quando_a_rodada_vira_tool_call(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada("Vou olhar seu saldo.", ("list_pockets", {})), rodada("Pronto."))
    r, ev, _ = executar(pro_user_id, "saldo?")
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("reinicio") < tipos.index("passo") and tipos.count("reinicio") == 1
    assert r.reply == "Pronto."


def test_ponto_b_o_stream_para_no_chunk_seguinte_ao_cancelamento(monkeypatch, pro_user_id):
    cancela = threading.Event()
    reg = openai_falso(monkeypatch, rodada("x" * 100, a_cada_chunk=lambda n: n == 2 and cancela.set()))
    r, ev, _ = executar(pro_user_id, "oi", cancela)
    assert r.interrompida
    assert reg.fluxos[0].lidos == 2, "devia parar no chunk que chegou depois do cancelamento"
    assert reg.fluxos[0].fechado and len(ev) == 1


def test_sugerivel_falso_com_pendencia_armada_no_turno(monkeypatch, pro_user_id):
    db.add_launch_and_update_balance(pro_user_id, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada("Apago tudo? Sim ou não."))
    r, _, _ = executar(pro_user_id, "apaga tudo")
    assert r.reply and db.ai_get_pending_action(pro_user_id) is not None
    assert r.sugerivel is False


def test_erro_da_openai_nao_oferece_feedback(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada(erro=RuntimeError("fora")))
    r, _, _ = executar(pro_user_id, "oi")
    assert r.reply == runner.ERROR_MSG.removeprefix("🐷 ") and r.message_id is None


# ── o endpoint ──────────────────────────────────────────────────────────────

def test_endpoint_stream_fim_e_headers(monkeypatch):
    uid, c = conta()
    openai_falso(monkeypatch, rodada("Tudo certo por aqui."))
    r = c.post("/ai/chat/stream", json={"message": "oi"}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no" and r.headers["cache-control"] == "no-cache"
    ev = eventos_sse(r)
    fim = ev[-1]
    assert {e["tipo"] for e in ev[:-1]} == {"delta"} and fim["tipo"] == "fim"
    assert fim["reply"] == "Tudo certo por aqui." and fim["chips"] == []
    assert fim["usage"]["used"] == 1 and fim["message_id"] == linhas(uid)[-1]["id"]


def test_endpoint_manda_ping_enquanto_espera(monkeypatch):
    _, c = conta()
    monkeypatch.setattr(rota, "PING_S", 0.05)
    openai_falso(monkeypatch, rodada("lento" * 4), pausa=0.15)
    r = c.post("/ai/chat/stream", json={"message": "oi"}, headers=H)
    assert ": ping\n\n" in r.text and eventos_sse(r)[-1]["tipo"] == "fim"


def test_endpoint_fim_do_erro_da_openai_sem_message_id(monkeypatch):
    _, c = conta()
    openai_falso(monkeypatch, rodada(erro=RuntimeError("fora")))
    fim = eventos_sse(c.post("/ai/chat/stream", json={"message": "oi"}, headers=H))[-1]
    assert fim["reply"] == runner.ERROR_MSG.removeprefix("🐷 ") and fim["message_id"] is None


def test_endpoint_erro_do_turno_vira_evento_erro(monkeypatch):
    _, c = conta()

    def quebra(*a, **k):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(streaming, "executar", quebra)
    ev = eventos_sse(c.post("/ai/chat/stream", json={"message": "oi"}, headers=H))
    assert ev == [{"tipo": "erro", "mensagem": rota.ERRO}]


@pytest.mark.parametrize("corpo", [{"message": "  "}, {"message": "a" * 2001}])
def test_endpoint_400_antes_do_stream(corpo):
    _, c = conta()
    r = c.post("/ai/chat/stream", json=corpo, headers=H)
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/json")


def test_endpoint_gate_403_e_sem_sessao_401():
    uid, c = conta()
    esgota_a_cota(uid)
    r = c.post("/ai/chat/stream", json={"message": "oi"}, headers=H)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "pro_required"
    assert _navegador().post("/ai/chat/stream", json={"message": "oi"}, headers=H).status_code == 401


def test_endpoint_rate_limit_antes_do_stream(monkeypatch):
    _, c = conta()
    openai_falso(monkeypatch, *[rodada("ok") for _ in range(12)])
    codigos = [c.post("/ai/chat/stream", json={"message": "oi"}, headers=H).status_code for _ in range(13)]
    assert codigos[:12] == [200] * 12 and codigos[12] == 429


# ── contrato com o app: `tests/fixtures/ai_chat_eventos.json` é lido também pelo jest (lerEvento) ──

def _forma(e):
    """Chaves do evento e, por dentro, as do `bloco`/da aba e do `usage`: é o que o zod do app exige."""
    f = {"chaves": sorted(e)}
    if "bloco" in e:
        f |= {"bloco": sorted(e["bloco"]), "aba": sorted(e["bloco"]["abas"][0])}
    if "usage" in e:
        f["usage"] = sorted(e["usage"])
    return f


def test_eventos_reais_tem_as_chaves_da_fixture_do_app(monkeypatch, mundo):
    exemplo = json.loads((Path(__file__).parent / "fixtures" / "ai_chat_eventos.json").read_text(encoding="utf-8"))
    a, _ = mundo
    # passo + delta + reinicio + fim: um turno que vira tool call depois de texto
    openai_falso(monkeypatch, rodada("Vou olhar.", ("list_pockets", {})), rodada("Pronto."))
    _, ev, _ = executar(a, "saldo?")
    reais = {e["tipo"]: e for e in ev}
    reais["bloco"] = next(e for e in mostrar(a, card="contas", **CASOS["contas"][0])[2] if e["tipo"] == "bloco")
    # fim e erro saem do endpoint
    uid, c = conta()
    openai_falso(monkeypatch, rodada("Tudo certo."))
    reais["fim"] = eventos_sse(c.post("/ai/chat/stream", json={"message": "oi"}, headers=H))[-1]
    monkeypatch.setattr(streaming, "executar", lambda *a, **k: 1 / 0)
    reais["erro"] = eventos_sse(c.post("/ai/chat/stream", json={"message": "oi"}, headers=H))[-1]
    assert set(reais) == set(exemplo), "tipo novo ou removido: atualize a fixture E o zod do app"
    for tipo, real in reais.items():
        assert _forma(real) == _forma(exemplo[tipo]), tipo
