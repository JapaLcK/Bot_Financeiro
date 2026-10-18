"""Contrato dos kwargs que cada chamada ao chat da OpenAI manda (cliente falso,
nada chama a rede).

(a) o modelo PADRÃO (hoje gpt-6-luna, texto e visão) manda os kwargs da Luna; com o
    modelo antigo (gpt-4o-mini / gpt-4o) os kwargs são os LITERAIS de antes de
    `core/services/modelo_openai.py` existir (ramo fallback, cobertura por chamada);
(b) com o modelo trocado por gpt-5.4-mini, `temperature`/`max_tokens` somem e
    entram `reasoning_effort` e (onde havia teto) `max_completion_tokens`.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

RAIZ = Path(__file__).resolve().parent.parent
JSON_OBJ = {"type": "json_object"}
FORA = {"messages", "tools", "tool_choice", "timeout", "stream"}  # não são do helper


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    if os.environ.get("OPENAI_MODEL"):
        pytest.fail("OPENAI_MODEL está exportado: os MODEL dos módulos já a leram e os literais de hoje não valem")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "chave-falsa-so-para-teste")


def _cliente(conteudo):
    reg = []

    def create(**kw):
        reg.append(kw)
        msg = SimpleNamespace(content=conteudo, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    return reg, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _sdk_falso(mp, conteudo):
    reg, cli = _cliente(conteudo)
    mp.setattr(openai, "OpenAI", lambda **kw: cli)
    return reg


# Cada chamada devolve os kwargs que mandou à OpenAI.
def _router(mp):
    import ai_router
    reg, cli = _cliente("outros")
    mp.setattr(ai_router, "_client", cli)
    ai_router.classify_category_with_gpt("xyzzy qualquer coisa")
    return reg[0]


def _patterns(mp):
    from core import ai_patterns
    reg = _sdk_falso(mp, '{"items": []}')
    ai_patterns._call_llm("sistema", {"a": 1})
    return reg[0]


def _greeting(mp):
    from core.handlers import greeting
    reg = _sdk_falso(mp, "Oi!")
    greeting._greeting_with_ai("oi", "oi")
    return reg[0]


def _intent(mp):
    from core import intent_classifier
    reg = _sdk_falso(mp, "{}")
    intent_classifier._classify_llm_call("qual o meu saldo", None)
    return reg[0]


def _runner(mp):
    from _ia_stream_helpers import openai_falso, rodada
    from core.services.ai_chat import runner
    reg = openai_falso(mp, rodada("ok"))
    runner._run_tool_loop(openai.OpenAI(), 1, [{"role": "user", "content": "oi"}])
    return reg.creates[0]


def _chips(mp):
    from core.services.ai_chat import chips
    reg = _sdk_falso(mp, '{"chips": []}')
    chips.sugerir("p", "r", [])
    return reg[0]


def _demo(mp):
    from core.services.demo import conversa
    reg = _sdk_falso(mp, "ok")
    conversa._chamar_modelo([{"role": "user", "content": "oi"}])
    return reg[0]


def _media(mp):
    from core.services import media_service
    reg = _sdk_falso(mp, "{}")
    media_service.analyze_image(b"imagem", "a.png")
    return reg[0]


def _news(mp):
    from core.services import news_bot
    reg, cli = _cliente('{"relevante": false}')
    news_bot._summarize(cli, {"source": "s", "title": "t", "snippet": None})
    return reg[0]


def _chips_formato():
    from core.services.ai_chat import chips
    return chips._FORMATO


def _runner_teto():
    from core.services.ai_chat import runner
    return runner.MAX_TOKENS


# (id, chamada, módulo.atributo do modelo, modelo de hoje, kwargs de hoje, teto, tem tools)
CASOS = [
    ("ai_router", _router, "ai_router.MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0, "max_tokens": 10}, 10, False),
    ("ai_patterns", _patterns, "core.ai_patterns.MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0.4, "response_format": JSON_OBJ}, None, False),
    ("greeting", _greeting, "core.handlers.greeting.CHAT_MODEL", "gpt-4o-mini",
     lambda: {"temperature": 1.0, "max_tokens": 100}, 100, False),
    ("intent_classifier", _intent, "core.intent_classifier.CHAT_MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0, "response_format": JSON_OBJ}, None, False),
    ("runner", _runner, "core.services.ai_chat.runner.MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0.3, "max_tokens": _runner_teto()}, "runner", True),
    ("chips", _chips, "core.services.ai_chat.chips.MODELO_CHIPS", "gpt-4o-mini",
     lambda: {"response_format": _chips_formato(), "max_tokens": 120, "temperature": 0.5}, 120, False),
    ("demo", _demo, "core.services.ai_chat.runner.MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0.3, "max_tokens": 400}, 400, False),
    ("media_service", _media, "core.services.media_service.VISION_MODEL", "gpt-4o",
     lambda: {"temperature": 0, "response_format": JSON_OBJ, "max_tokens": 500}, 500, False),
    ("news_bot", _news, "core.services.news_bot.MODEL", "gpt-4o-mini",
     lambda: {"temperature": 0.4, "response_format": JSON_OBJ}, None, False),
]
IDS = [c[0] for c in CASOS]


def _kwargs(chamada, mp):
    kw = chamada(mp)
    return {k: v for k, v in kw.items() if k not in FORA}


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_a_default_e_a_luna_effort_none_max_completion_tokens_e_temperature(monkeypatch, caso):
    # sem setattr: é o default de produção que está sendo medido
    _, chamada, _, _, esperado, teto, _ = caso
    resto = {k: v for k, v in esperado().items() if k != "max_tokens"}  # temperature fica
    esperado_a = {"model": "gpt-6-luna", **resto, "reasoning_effort": "none"}
    if teto == "runner":
        teto = _runner_teto()
    if teto is not None:
        esperado_a["max_completion_tokens"] = teto  # exato, sem o max(…, 2048) do 5.4-mini
    assert _kwargs(chamada, monkeypatch) == esperado_a


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_a2_modelo_antigo_cai_no_fallback_max_tokens_e_temperature(monkeypatch, caso):
    import importlib
    _, chamada, onde, modelo, esperado, _, _ = caso
    modulo, attr = onde.rsplit(".", 1)
    monkeypatch.setattr(importlib.import_module(modulo), attr, modelo)
    assert _kwargs(chamada, monkeypatch) == {"model": modelo, **esperado()}


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_b_gpt_5_4_mini_sem_temperature_nem_max_tokens(monkeypatch, caso):
    import importlib
    _, chamada, onde, _, esperado, teto, tools = caso
    modulo, attr = onde.rsplit(".", 1)
    monkeypatch.setattr(importlib.import_module(modulo), attr, "gpt-5.4-mini")
    resto = {k: v for k, v in esperado().items() if k not in ("temperature", "max_tokens")}
    esperado_b = {"model": "gpt-5.4-mini", **resto,
                  "reasoning_effort": "none" if tools else "low"}
    if teto == "runner":
        teto = _runner_teto()
    if teto is not None:
        esperado_b["max_completion_tokens"] = max(teto, 2048)
    assert _kwargs(chamada, monkeypatch) == esperado_b


def test_gpt_6_luna_so_o_alias_exato():
    from core.services.modelo_openai import completion_options as co
    assert co("gpt-6-luna", 100, 0.3, with_tools=True) == co("gpt-6-luna", 100, 0.3) == {
        "model": "gpt-6-luna", "max_completion_tokens": 100, "reasoning_effort": "none", "temperature": 0.3}
    assert co("gpt-6-luna", None, 0) == {"model": "gpt-6-luna", "reasoning_effort": "none", "temperature": 0}
    # nome fora do contrato cai no fallback (falha visível na API), não herda o ramo da Luna
    assert co("gpt-6-luna-2027", 100, 0.3) == {"model": "gpt-6-luna-2027", "max_tokens": 100, "temperature": 0.3}


def test_helper_budget_none_e_inteiro():
    from core.services.modelo_openai import completion_options as co
    assert co("gpt-4o-mini", None, 0.3) == {"model": "gpt-4o-mini", "temperature": 0.3}
    assert co("gpt-5.4-mini", None, 0.3) == {"model": "gpt-5.4-mini", "reasoning_effort": "low"}
    assert co("gpt-5.4-mini", None, 0.3, with_tools=True)["reasoning_effort"] == "none"
    # com teto inteiro: byte a byte o de antes
    assert co("gpt-4o-mini", 900, 0) == {"model": "gpt-4o-mini", "max_tokens": 900, "temperature": 0}
    assert co("gpt-5.4-mini", 100, 0) == {"model": "gpt-5.4-mini", "max_completion_tokens": 2048,
                                          "reasoning_effort": "low"}
    assert co("gpt-5.4-mini-2026", 5000, 0, with_tools=True) == {
        "model": "gpt-5.4-mini-2026", "max_completion_tokens": 5000, "reasoning_effort": "none"}


def _modelos(env_extra):
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_MODEL"} | env_extra
    out = subprocess.run(
        [sys.executable, "-c", "from core.services.modelo_openai import CHAT_MODEL, VISION_MODEL;"
         "print(CHAT_MODEL, VISION_MODEL)"],
        cwd=RAIZ, env=env | {"PYTHONPATH": str(RAIZ)}, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_modelos_padrao_e_env():
    assert _modelos({}) == ["gpt-6-luna", "gpt-6-luna"]
    assert _modelos({"OPENAI_MODEL": "outro"}) == ["outro", "gpt-6-luna"]


def test_todos_os_modulos_seguem_a_env_e_visao_nao():
    # um interpretador novo: setattr (test_b) não vê se `MODEL = CHAT_MODEL` virou literal
    codigo = (
        "import ai_router, core.ai_patterns as p, core.services.ai_chat.runner as r,"
        "core.services.news_bot as n, core.services.ai_chat.chips as c,"
        "core.handlers.greeting as g, core.intent_classifier as i, core.services.media_service as m;"
        "print(ai_router.MODEL, p.MODEL, r.MODEL, n.MODEL, c.MODELO_CHIPS,"
        "g.CHAT_MODEL, i.CHAT_MODEL, m.VISION_MODEL)")
    env = os.environ | {"OPENAI_MODEL": "modelo-sentinela-xyz", "PYTHONPATH": str(RAIZ)}
    out = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=env,
                         capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["modelo-sentinela-xyz"] * 7 + ["gpt-6-luna"]
