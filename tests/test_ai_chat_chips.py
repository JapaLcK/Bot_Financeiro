"""Chips de continuação (PR C): a 2ª chamada à OpenAI, depois do turno, só no app."""
import json
import threading

import pytest

import db
from _cards_apoio import mundo  # noqa: F401  (fixture)
from _ia_stream_helpers import conta, esgota_a_cota, eventos_sse, executar, linhas, openai_falso, rodada
from core.services.ai_chat import chips, runner, streaming
from test_senha_obrigatoria import H


def _chips(*itens):
    return json.dumps({"chips": list(itens)})


CINCO = ["Quanto no mercado?", "E em setembro?", "Qual a maior despesa?", "Como está meu saldo?", "Sobrou 5º"]


def _stream(c, texto="oi"):
    return eventos_sse(c.post("/ai/chat/stream", json={"message": texto}, headers=H))


def _fim(ev):
    assert [e["tipo"] for e in ev].count("fim") == 1
    return next(e for e in ev if e["tipo"] == "fim")


def test_chips_saem_no_fim(monkeypatch):
    uid, c = conta()
    reg = openai_falso(monkeypatch, rodada("Gastou pouco."), chips=[_chips(*CINCO)])
    fim = _fim(_stream(c, "como foi o mês?"))
    assert fim["chips"] == CINCO[:4]
    (kw,) = reg.chips
    assert kw["model"] == "gpt-4o-mini" and "tools" not in kw and "stream" not in kw
    assert kw["response_format"]["type"] == "json_schema"
    assert kw["max_tokens"] == 120 and kw["temperature"] == 0.5
    assert reg.clientes[-1]["timeout"] == 4.0 and reg.clientes[-1]["max_retries"] == 0
    assert fim["usage"]["used"] == 1
    com_chips = [(l["role"], l["content"]) for l in linhas(uid)]
    # mesmas linhas de um turno sem chips (falha dos chips = sem chips)
    uid2, c2 = conta()
    openai_falso(monkeypatch, rodada("Gastou pouco."))
    _stream(c2, "como foi o mês?")
    assert com_chips == [(l["role"], l["content"]) for l in linhas(uid2)]


def test_chip_de_confirmacao_e_descartado(monkeypatch):
    uid, c = conta()
    openai_falso(monkeypatch, rodada("Ok."),
                 chips=[_chips("Sim!", "pode sim", "Não", "E em setembro?", "Quanto no mercado?")])
    assert _fim(_stream(c))["chips"] == ["E em setembro?", "Quanto no mercado?"]


def test_pendencia_armada_no_turno_nao_chama_os_chips(monkeypatch, pro_user_id):
    db.add_launch_and_update_balance(pro_user_id, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    reg = openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada("Apago tudo?"),
                       chips=[_chips(*CINCO)])
    r, _, _ = executar(pro_user_id, "apaga tudo")
    assert db.ai_get_pending_action(pro_user_id) is not None
    assert reg.chips == [] and r.chips == []


def test_terminal_msg_nao_chama_os_chips(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "receita", "valor": 200, "alvo": "freela"})),
                       chips=[_chips(*CINCO)])
    r, _, _ = executar(pro_user_id, "recebi 200 de freela")
    assert "Receita registrada" in r.reply
    assert reg.chips == [] and r.chips == []


def test_cancelado_depois_do_runner_nao_chama_os_chips(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada("Oi!"), chips=[_chips(*CINCO)])
    real = runner.chat_detalhado
    cancela = threading.Event()

    def embrulho(*a, **k):
        r = real(*a, **k)
        cancela.set()
        return r

    monkeypatch.setattr(runner, "chat_detalhado", embrulho)
    r, _, _ = executar(pro_user_id, "oi", cancela)
    assert r.sugerivel and reg.chips == [] and r.chips == []


@pytest.mark.parametrize("falha", [RuntimeError("fora"), TimeoutError(), "não é json",
                                   '{"chips":"x"}', _chips("só um")])
def test_falha_dos_chips_vira_lista_vazia(monkeypatch, falha):
    uid, c = conta()
    openai_falso(monkeypatch, rodada("Oi!"), chips=[falha])
    ev = _stream(c)
    assert "erro" not in {e["tipo"] for e in ev}
    fim = _fim(ev)
    assert fim["chips"] == [] and fim["usage"]["used"] == 1


def test_privacidade_do_prompt_dos_chips(monkeypatch, mundo):  # noqa: F811
    a, _ = mundo
    reg = openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "categorias"})), rodada("Veja o card."),
                       chips=[_chips(*CINCO)])
    _, ev, _ = executar(a, "minhas categorias")
    assert [e["tipo"] for e in ev if e["tipo"] == "bloco"] == ["bloco"]  # o bloco existiu e tinha dados
    (kw,) = reg.chips
    # só pergunta, resposta e o ID do card: nada do bloco, do histórico nem do outro usuário
    assert kw["messages"][1]["content"] == "Pergunta: minhas categorias\nResposta: Veja o card.\nCards mostrados: categorias"
    assert "viagem" not in json.dumps(kw) and "SEGREDO-B" not in json.dumps(kw)


def test_validar():
    ok = ["Quanto no mercado?", "E em setembro?"]
    assert chips.validar(ok) == ok
    assert chips.validar(ok + ["x" * 41]) == ok
    assert chips.validar(["x" * 40, "z"]) == ["x" * 40, "z"]
    assert chips.validar(["😀" * 20, "ok1"]) == ["😀" * 20, "ok1"]   # 40 em UTF-16
    assert chips.validar(["😀" * 21, "a", "b"]) == ["a", "b"]         # 42 em UTF-16
    assert chips.validar(["a\nb", "c", "d"]) == ["c", "d"]
    assert chips.validar(["Mercado?", "MERCADO?", "Luz?"]) == ["Mercado?", "Luz?"]
    assert chips.validar(["", "  ", 3, None, "a", "b"]) == ["a", "b"]
    assert chips.validar("abc") == [] and chips.validar(None) == [] and chips.validar({"a": 1}) == []
    assert chips.validar(["só um"]) == [] and chips.validar(["Sim", "a"]) == []
    assert chips.validar(["\ud800x", "a", "b"]) == ["a", "b"]         # surrogate solto: só o item cai
    assert chips.validar(["Meta ❤️‍🔥?", "a"]) == ["Meta ❤️‍🔥?", "a"]   # ZWJ (Cf) passa
    for invisivel in ("‍", "‍‍", "️", "́"):   # Cf/Mn: sem L/N/P/S
        assert chips.validar([invisivel, "c", "d"]) == ["c", "d"], repr(invisivel)
    assert chips.validar(["‍", "‍‍", "️", "́"]) == []
    for ruim in ("a\rb", "a\tb", "a\u202eb", "a\u2028b"):          # Cc, Cc, Cf!=ZWJ, Zl
        assert chips.validar([ruim, "c", "d"]) == ["c", "d"], repr(ruim)


@pytest.mark.parametrize("ia_primeiro", [False, True])
def test_sem_emissor_nenhuma_chamada_de_chip(monkeypatch, pro_user_id, ia_primeiro):
    reg = openai_falso(monkeypatch, rodada("Oi!"), chips=[_chips(*CINCO)])
    assert runner.chat(pro_user_id, "oi", platform="whatsapp", ia_primeiro=ia_primeiro) == "Oi!"
    assert len(reg.creates) == 1 and reg.chips == []


def test_sem_emissor_no_ai_chat(monkeypatch):
    uid, c = conta()
    reg = openai_falso(monkeypatch, rodada("Oi!"), chips=[_chips(*CINCO)])
    assert c.post("/ai/chat", json={"message": "oi"}, headers=H).status_code == 200
    assert len(reg.creates) == 1 and reg.chips == []


def _pendencia(uid, monkeypatch):
    db.add_launch_and_update_balance(uid, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    reg = openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada("Apago tudo?"))
    executar(uid, "apaga tudo")
    assert db.ai_get_pending_action(uid) is not None
    return reg


@pytest.mark.parametrize("caso", ["vazio", "erro_openai", "limite", "ocupado", "sim", "nao", "max_loops"])
def test_caminhos_sem_chips(monkeypatch, caso):
    uid, _ = conta()
    ia = [_chips(*CINCO)]  # se algum caminho pedir chips, a chamada fica registrada em reg.chips
    if caso == "vazio":
        reg = openai_falso(monkeypatch, rodada(""), chips=ia)
    elif caso == "erro_openai":
        reg = openai_falso(monkeypatch, *[rodada(erro=RuntimeError("fora")) for _ in range(4)], chips=ia)
    elif caso == "limite":
        esgota_a_cota(uid)
        reg = openai_falso(monkeypatch, rodada("Oi!"), chips=ia)
    elif caso == "ocupado":
        monkeypatch.setattr(runner, "TURNO_ESPERA_S", 0.3)
        reg = openai_falso(monkeypatch, rodada("Oi!"), chips=ia)
    elif caso == "max_loops":
        monkeypatch.setattr(runner, "MAX_TOOL_LOOPS", 1)
        reg = openai_falso(monkeypatch, rodada(None, ("get_balance", {})), chips=ia)
    else:
        _pendencia(uid, monkeypatch)
        reg = openai_falso(monkeypatch, rodada("Feito."), rodada("Ok."), chips=ia)
    if caso == "ocupado":
        with runner.trava_do_turno(uid, 1):
            r, _, _ = executar(uid, "oi")
        assert r.reply == runner._OCUPADO.removeprefix("🐷 ")  # o stream não leva o 🐷
    else:
        r, _, _ = executar(uid, "sim" if caso == "sim" else "não" if caso == "nao" else "oi")
    assert reg.chips == [] and r.chips == []
