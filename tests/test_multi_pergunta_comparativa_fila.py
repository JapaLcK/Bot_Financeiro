"""Pergunta comparativa respondendo a uma pergunta de valor: recusa, nada grava.

Com "🐷 Faltou o valor de *aluguel*" de pé, "gastei mais em 2025 ou 2026?"
gravava R$ 2.025 no aluguel (porta 3, fila do multi), na luz (porta 2,
`clarification`) ou pagava a conta com R$ 2.025 e debitava o saldo (porta 4,
botão "Já paguei", em `test_multi_pergunta_comparativa_ja_paguei.py`). Decisão
do dono: recusa e pergunta de novo, pendência de pé.
"""
from __future__ import annotations

import types

import pytest

import core.handle_incoming as hi
import core.handlers.launches as L
import core.intent_router as IR
import db
from core.intent_router import abandona_pergunta_de_valor
from core.types import IncomingMessage
from tests.test_handle_incoming_routing import _Audio, _msg, _valores

_ALUGUEL = {"tipo": "despesa", "desc": "aluguel"}
_LUZ = {"tipo": "despesa", "desc": "luz"}
_RECUSA_ALUGUEL = ("Isso parece uma pergunta, não o valor de *aluguel*.\n\n"
                   "🐷 Faltou o valor de *aluguel*. Quanto foi? (só o número)")

# E5 (P2: sem número também recusa), E6, E6b, E9a-c, nas três portas. E9* só o
# `any(...)` de `contains_comparative_question` pega: no texto inteiro dá False.
_PERGUNTAS = [
    "gastei mais esse mês que no passado?",
    "gastei mais em 2025 ou 2026?",
    "gastei mais que 5 mil esse mes?",
    "gastei mais nos ultimos 3 meses?",
    "gastei mais de 100 no mercado?",
    "gastei mais que devia 50 no bar",
    "paguei a luz e gastei mais em 2025 ou 2026?",
    "gastei 30 no uber e gastei mais em 2025 ou 2026?",
    "1200 e gastei mais em 2025 ou 2026?",
]


def _pendencia(fila):
    return {"action_type": "multi_launch_values", "created_at": "t0",
            "payload": {"queue": list(fila), "platform": "whatsapp"}}


def _resolve(text, fila):
    # Mesmo `outro_comando` do `route()`.
    return L.resolve_multi_launch_value(
        1, text, _pendencia(fila), "whatsapp",
        outro_comando=abandona_pergunta_de_valor(text))


class _Explode:
    """Qualquer toque no banco é a fila sendo mexida."""
    def __getattr__(self, nome):
        raise AssertionError(f"db.{nome} chamado: a fila foi mexida")


@pytest.fixture
def sem_banco(monkeypatch):
    monkeypatch.setattr(L, "db", _Explode())
    monkeypatch.setattr(L, "add_from_entities",
                        lambda *a, **k: pytest.fail("gravou lançamento"))


@pytest.mark.parametrize("fila", [[_ALUGUEL], [_ALUGUEL, _LUZ]], ids=["1item", "2itens"])
@pytest.mark.parametrize("text", _PERGUNTAS)
def test_fila_recusa_pergunta_comparativa(sem_banco, text, fila):
    assert _resolve(text, fila) == _RECUSA_ALUGUEL


def test_fila_recusa_receita_usa_a_pergunta_de_receita(sem_banco):
    r = _resolve("gastei mais em 2025 ou 2026?", [{"tipo": "receita", "desc": "salário"}])
    assert r == ("Isso parece uma pergunta, não o valor de *salário*.\n\n"
                 "🐷 Faltou o valor de *salário*. Quanto você recebeu? (só o número)")


@pytest.fixture
def banco_que_grava(monkeypatch):
    """Positivo: o CAS vence, a fila acaba, o registro é capturado."""
    gravados, consumidos = [], []
    monkeypatch.setattr(L, "db", types.SimpleNamespace(
        advance_pending_action=lambda *a, **k: True,
        get_pending_action=lambda u: None,
        consume_pending_action=lambda u, p: consumidos.append(p) or True))
    monkeypatch.setattr(L, "add_from_entities",
                        lambda uid, **k: gravados.append((k["valor"], k["alvo"])) or "✅")
    return gravados, consumidos


@pytest.mark.parametrize("text,valor", [
    ("1200", 1200.0), ("1200 foi isso", 1200.0),
    ("gastei mais ou menos 1200", 1200.0), ("paguei mais 1500", 1500.0),
])
def test_fila_resposta_legitima_ainda_grava(banco_que_grava, text, valor):
    gravados, _ = banco_que_grava
    assert _resolve(text, [_ALUGUEL]) == "✅"
    assert gravados == [(valor, "aluguel")]


def test_fila_cancelar_e_comando_continuam(banco_que_grava):
    gravados, consumidos = banco_que_grava
    assert _resolve("cancelar", [_ALUGUEL, _LUZ]) == "❌ Beleza, deixei de lado: aluguel, luz."
    assert _resolve("saldo", [_ALUGUEL]) is None  # outro_comando: o comando roda
    assert gravados == [] and len(consumidos) == 2


# ── Porta 2: `clarification` "Quanto foi no *luz*?" ───────────────────────────

_CLARIF = {"action_type": "clarification", "payload": {
    "intent": "launches.add", "entities": {"tipo": "despesa"},
    "question": "🐷 Quanto foi no *luz*?", "orig_text": "paguei a luz"}}


@pytest.fixture
def porta2(monkeypatch):
    recriadas, executados = [], []
    clarif = dict(_CLARIF)
    monkeypatch.setattr(IR, "db", types.SimpleNamespace(
        consume_pending_action=lambda u, p: True,
        create_pending_action_if_absent=lambda u, t, p: recriadas.append((t, p)) or True))
    monkeypatch.setattr("core.intent_classifier.classify_with_context", lambda *a, **k: None)
    monkeypatch.setattr(IR, "_alvos_existentes", lambda u, i: [])  # sem banco, sem log de ERROR
    monkeypatch.setattr(IR, "_execute", lambda intent, uid, text, *a: executados.append(text) or "✅")
    def responde(resp, **payload):
        clarif["payload"] = {**_CLARIF["payload"], **payload}
        return IR._resolve_clarification(clarif, resp, 1, "whatsapp", ""), recriadas, executados
    return responde


@pytest.mark.parametrize("text", _PERGUNTAS)
def test_clarification_recusa_pergunta_comparativa(porta2, text):
    r, recriadas, executados = porta2(text)
    assert r == "Isso parece uma pergunta, não o valor.\n\n🐷 Quanto foi no *luz*?"
    assert recriadas == [("clarification", _CLARIF["payload"])] and executados == []


def test_clarification_recusa_no_recorrente(porta2):
    r, _, executados = porta2("gastei mais em 2025 ou 2026?", intent="recurring.add",
                              question="Qual o valor desse recorrente?")
    assert r == "Isso parece uma pergunta, não o valor.\n\nQual o valor desse recorrente?"
    assert executados == []


def test_clarification_que_nao_pede_valor_nao_recusa(porta2):
    # A clarification genérica da IA pode pedir outra coisa ("de qual mês?").
    r, recriadas, _ = porta2("gastei mais em 2025 ou 2026?", intent="launches.list",
                             question="De qual mês?")
    assert r == "✅" and recriadas == []


def test_clarification_valor_legitimo_ainda_grava(porta2):
    r, recriadas, executados = porta2("132")
    assert r == "✅" and recriadas == [] and "132" in executados[0]


# Pergunta de HANDLER (`falta`): caixinha, aporte, resgate e saque executavam
# com 2025.
_HANDLER = [
    ("pockets.deposit", {"pocket_name": "viagem"}, "Qual o valor? Tente: *coloquei 200 na caixinha viagem*"),
    ("pockets.withdraw", {"pocket_name": "viagem"}, "Qual o valor? Tente: *retirei 100 da caixinha viagem*"),
    ("investments.deposit", {"investment_name": "cdb"}, "Qual valor você quer aportar?"),
    ("investments.withdraw", {"investment_name": "cdb"}, "Qual valor você quer resgatar?"),
    ("funds.withdraw", {}, "Qual o valor? Tente: *saquei 200 da reserva de emergência*"),
]


@pytest.mark.parametrize("intent,ents,pergunta", _HANDLER, ids=[h[0] for h in _HANDLER])
@pytest.mark.parametrize("text", ["gastei mais em 2025 ou 2026?",
                                  "paguei a luz e gastei mais em 2025 ou 2026?"])
def test_handler_recusa_pergunta_comparativa_no_valor(porta2, intent, ents, pergunta, text):
    r, recriadas, executados = porta2(text, intent=intent, entities=ents,
                                      falta="amount", question=pergunta)
    assert r == f"Isso parece uma pergunta, não o valor.\n\n{pergunta}"
    assert executados == [] and recriadas[0][1]["falta"] == "amount"


@pytest.mark.parametrize("intent,ents,pergunta", _HANDLER, ids=[h[0] for h in _HANDLER])
def test_handler_valor_legitimo_ainda_executa(porta2, intent, ents, pergunta):
    r, recriadas, executados = porta2("1200", intent=intent, entities=ents,
                                      falta="amount", question=pergunta)
    assert r == "✅" and recriadas == [] and len(executados) == 1


def test_handler_resposta_com_nome_ainda_executa(porta2, monkeypatch):
    monkeypatch.setattr(IR, "_alvos_existentes", lambda u, i: ["viagem"])
    r, _, executados = porta2("guardar 50 na viagem", intent="pockets.deposit",
                              entities={"pocket_name": "viagem"}, falta="amount",
                              question="Qual o valor?")
    assert r == "✅" and len(executados) == 1


# Pedindo o NOME também: `_funde_a_resposta` lia o ano como quantia e trocava a
# guardada (R$ 50 → R$ 2.025). Decisão do dono: mesma recusa.
_NOME = [
    ("pockets.deposit", "pocket_name", "Qual caixinha?", 50.0, "viagem", "viagem"),
    ("funds.withdraw", "target_name", "De qual caixinha ou investimento?", 100.0, "viagem", "na viagem"),
    ("investments.withdraw", "investment_name", "De qual investimento?", 50.0, "tesouro", "tesouro"),
]


@pytest.mark.parametrize("intent,falta,pergunta,valor,alvo,_", _NOME, ids=[n[0] for n in _NOME])
def test_handler_pedindo_o_nome_recusa(porta2, monkeypatch, intent, falta, pergunta, valor, alvo, _):
    monkeypatch.setattr(IR, "_alvos_existentes", lambda u, i: [alvo])
    payload = dict(intent=intent, entities={"amount": valor}, falta=falta, question=pergunta)
    r, recriadas, executados = porta2(f"gastei mais na {alvo} em 2025 ou 2026?", **payload)
    assert r == f"Isso parece uma pergunta, não o nome.\n\n{pergunta}"
    assert executados == [] and recriadas == [("clarification", {**_CLARIF["payload"], **payload})]


@pytest.mark.parametrize("intent,falta,pergunta,valor,alvo,resposta", _NOME, ids=[n[0] for n in _NOME])
def test_handler_nome_legitimo_executa_com_o_valor_guardado(
        porta2, monkeypatch, intent, falta, pergunta, valor, alvo, resposta):
    monkeypatch.setattr(IR, "_alvos_existentes", lambda u, i: [alvo])
    ents = []
    monkeypatch.setattr(IR, "_execute", lambda i, u, t, e, *a: ents.append(e) or "✅")
    r, recriadas, _ = porta2(resposta, intent=intent, entities={"amount": valor},
                             falta=falta, question=pergunta)
    assert r == "✅" and recriadas == [], r
    assert (ents[0]["amount"], ents[0][falta]) == (valor, alvo), ents


# ── Áudio com a fila de pé: não divide, cai inteiro na recusa da porta 3 ──────

@pytest.fixture
def audio_fila(monkeypatch, sem_banco):
    """`_handle_audio` com a fila [aluguel, luz] viva; cada pedaço vai à porta 3."""
    lidas, pedacos = [], []
    fila = [_ALUGUEL, _LUZ]
    monkeypatch.setattr("core.services.plan_service.feature_enabled", lambda *a: True)
    monkeypatch.setattr(hi, "db", types.SimpleNamespace(
        ensure_user=lambda u: None, update_last_activity=lambda u: None,
        latest_launch_id=lambda u: 1,
        get_pending_action=lambda u: lidas.append(u) or _pendencia(fila)))
    monkeypatch.setattr(hi, "_process_audio_transaction",
                        lambda uid, part, msg, pl: pedacos.append(part) or _resolve(part, fila))

    def ouvir(fala):
        monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: fala)
        msg = IncomingMessage(platform="whatsapp", user_id=5, text="", message_id="m",
                              attachments=[_Audio()], external_id="e", raw={})
        return hi._handle_audio(msg, "whatsapp")[0].text, pedacos, lidas
    return ouvir


@pytest.mark.parametrize("fala", [
    "gastei 30 no uber e gastei mais em 2025 ou 2026",
    "gastei 30 no uber e gastei 40 no bar e gastei mais que devia 50 no bar",
    "1200 e gastei mais em 2025 ou 2026",
])
def test_audio_com_fila_recusa_inteiro(audio_fila, fala):
    # `sem_banco`: qualquer pedaço que gravasse (R$ 30 no aluguel) falha aqui.
    texto, pedacos, _ = audio_fila(fala)
    assert pedacos == [fala]
    assert texto == f'🎙️ Entendi: "{fala}"\n\n{_RECUSA_ALUGUEL}'


def test_audio_sem_pergunta_segue_dividido_sem_ler_a_fila(audio_fila, monkeypatch):
    pedacos = []
    monkeypatch.setattr(hi, "_process_audio_transaction",
                        lambda uid, part, msg, pl: pedacos.append(part) or "✅")
    _, _, lidas = audio_fila("gastei 30 no uber e gastei 40 no bar")
    assert pedacos == ["gastei 30 no uber", "gastei 40 no bar"]
    assert lidas == []  # predicado falso: o banco nem é lido


# ── Conversas com banco (CI) ───────────────────────────────────────────────────

def _pendente(uid):
    p = db.get_pending_action(uid) or {}
    return p.get("action_type"), [i["desc"] for i in (p.get("payload") or {}).get("queue") or []]


def test_conversa_fila_recusa_e_depois_grava(pro_small_uid):
    uid = pro_small_uid
    hi.handle_incoming(_msg(uid, "paguei o aluguel e gastei mais em 2025 ou 2026?"))
    assert _valores(uid) == []
    out = hi.handle_incoming(_msg(uid, "gastei mais em 2025 ou 2026?"))  # gravava 2025
    assert _RECUSA_ALUGUEL in "\n".join(o.text for o in out)
    assert _valores(uid) == []
    assert _pendente(uid) == ("multi_launch_values", ["aluguel"])
    hi.handle_incoming(_msg(uid, "1200"))
    assert _valores(uid) == [1200]
    assert "aluguel" in db.list_launches(uid, limit=1)[0]["alvo"].lower()


def test_conversa_fila_e_audio_comparativo_nao_grava(pro_small_uid, monkeypatch):
    uid = pro_small_uid
    hi.handle_incoming(_msg(uid, "gastei 30 no uber e paguei o aluguel"))
    assert _valores(uid) == [30]
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: "gastei mais em 2025 ou 2026")
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text="", message_id="m",
                          attachments=[_Audio()], external_id="e", raw={})
    out = hi._handle_audio(msg, "whatsapp")
    assert "Isso parece uma pergunta, não o valor de *aluguel*." in "\n".join(o.text for o in out)
    assert _valores(uid) == [30]
    assert _pendente(uid) == ("multi_launch_values", ["aluguel"])
    # Multi com pedaço comparativo: dividido, o "gastei 40 no bar" ia pro aluguel.
    monkeypatch.setattr(hi, "transcribe_audio",
                        lambda data, fn: "gastei 40 no bar e gastei mais em 2025 ou 2026")
    out = hi._handle_audio(msg, "whatsapp")
    assert _RECUSA_ALUGUEL in "\n".join(o.text for o in out)
    assert _valores(uid) == [30]
    assert _pendente(uid) == ("multi_launch_values", ["aluguel"])


def test_conversa_clarification_recusa_e_depois_grava(pro_small_uid):
    uid = pro_small_uid
    hi.handle_incoming(_msg(uid, "paguei a luz"))
    out = hi.handle_incoming(_msg(uid, "gastei mais em 2025 ou 2026?"))
    assert "Isso parece uma pergunta, não o valor." in "\n".join(o.text for o in out)
    assert _valores(uid) == []
    assert (db.get_pending_action(uid) or {}).get("action_type") == "clarification"
    hi.handle_incoming(_msg(uid, "132"))
    assert _valores(uid) == [132]
