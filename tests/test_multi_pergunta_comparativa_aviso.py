"""Pedaço comparativo de multi-lançamento: não grava, mas AVISA (texto e áudio).

"gastei 30 no uber e gastei mais que devia 50 no bar": a main gravava 30 + 50;
o pulo do pedaço comparativo (para não gravar R$ 2.025 em "... e gastei mais em
2025 ou 2026?") descartava os 50 em silêncio. Decisão do dono: continua sem
gravar, e a resposta diz qual pedaço ficou de fora e como mandar de novo.
"""
from __future__ import annotations

import types

import pytest

import core.handle_incoming as hi
import core.handlers.launches as L
import db
import parsers
from core.types import IncomingMessage
from tests.test_handle_incoming_routing import _Audio, _msg, _valores

# (frase, valores gravados). O pedaço pulado é o que vem depois do 1º " e ".
_ACHADO = [
    ("gastei 30 no uber e gastei mais que devia 50 no bar", [30]),
    ("recebi 600 da mae e gastei mais que o normal 80 no shopping", [600]),
    ("paguei 100 de luz e paguei mais caro que o normal 250 na gasolina", [100]),
    ("gastei 30 no uber e paguei menos que o combinado 80 pro pedreiro", [30]),
    ("gastei 50 no mercado e gastei mais no uber 30 antes do almoço", [50]),
    ("gastei 50 no mercado e gastei muito no bar 80 que fui antes", [50]),
    ("gastei 50 no mercado e gastei muito no bar ontem, uns 80?", [50]),
    ("gastei 50 no mercado e gastei demais no bar 80?", [50]),
    ("gastei 30 no uber e gastei muito ou pouco 50 no mercado", [30]),
    # Todos os pedaços pulados: não cai no single com o texto inteiro (R$ 2.025).
    ("paguei e gastei mais em 2025 ou 2026?", []),
]


def _aviso(pedaco: str) -> str:
    return (f'ℹ️ Não registrei "{pedaco}" porque parece uma pergunta. '
            "Se era gasto, me manda só o valor e o lugar, tipo *gastei 50 no bar*.")


def _aviso_com_pergunta(pedaco: str) -> str:
    # Com a pergunta do valor de pé, mandar o gasto antes de responder gravaria
    # no item da fila (R$ 50 no aluguel) — o aviso manda responder primeiro.
    return (f'ℹ️ Não registrei "{pedaco}" porque parece uma pergunta. '
            "Se era gasto, depois de responder a pergunta acima, "
            "me manda só o valor e o lugar, tipo *gastei 50 no bar*.")


_COM_ALUGUEL = "gastei 30 no uber e paguei o aluguel e gastei mais que devia 50 no bar"
_PERGUNTA_ALUGUEL = "🐷 Faltou o valor de *aluguel*. Quanto foi? (só o número)"


@pytest.fixture
def add_sem_banco(monkeypatch):
    """`launches.add` com o registro e a pendência capturados (sem Postgres)."""
    gravados: list[float] = []
    monkeypatch.setattr(parsers, "infer_category",
                        lambda **k: types.SimpleNamespace(category="outros", reason="stub"))
    monkeypatch.setattr("core.handlers.credit.try_handle_natural_credit_purchase",
                        lambda uid, text: None)
    monkeypatch.setattr(L, "register_if_recurring", lambda *a, **k: None)
    monkeypatch.setattr("core.handlers.forma_pagamento.regra_ativa", lambda u: False)  # sem banco conectado (Q40)
    monkeypatch.setattr(L, "_register_parsed",
                        lambda uid, p, part, pl, **k: gravados.append(float(p["valor"])) or "ok")
    monkeypatch.setattr(L.db, "set_pending_action", lambda *a, **k: None)
    return lambda text: (L.add(1, text, {}), gravados)


@pytest.mark.parametrize("text,valores", _ACHADO)
def test_multi_pula_pedaco_comparativo_e_avisa(add_sem_banco, text, valores):
    resposta, gravados = add_sem_banco(text)
    assert gravados == valores
    assert resposta.count(_aviso(text.split(" e ", 1)[1])) == 1


def test_multi_com_pergunta_de_valor_avisa_depois_dela(add_sem_banco):
    resposta, gravados = add_sem_banco(_COM_ALUGUEL)
    assert gravados == [30]
    aviso = _aviso_com_pergunta("gastei mais que devia 50 no bar")
    assert _PERGUNTA_ALUGUEL in resposta and aviso in resposta, resposta
    assert resposta.index(_PERGUNTA_ALUGUEL) < resposta.index(aviso)  # "acima"


@pytest.fixture
def audio_sem_banco(monkeypatch):
    """`_handle_audio` com banco e rota de cada pedaço trocados por dublês."""
    monkeypatch.setattr("core.services.plan_service.feature_enabled", lambda *a: True)
    monkeypatch.setattr(L, "register_if_recurring", lambda *a, **k: None)
    monkeypatch.setattr("core.handlers.forma_pagamento.regra_ativa", lambda u: False)  # sem banco conectado (Q40)
    monkeypatch.setattr(hi, "db", types.SimpleNamespace(
        ensure_user=lambda u: None, update_last_activity=lambda u: None,
        latest_launch_id=lambda u: 1, set_pending_action=lambda *a: None,
        get_pending_action=lambda u: None))
    # Pedaço que não vira lançamento ("paguei") devolve o help genérico.
    monkeypatch.setattr(hi, "_process_audio_transaction",
                        lambda uid, part, msg, pl, *a: "🤔 Não entendi exatamente. Posso te ajudar com…")

    def ouvir(fala):
        monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: fala)
        msg = IncomingMessage(platform="whatsapp", user_id=5, text="", message_id="m",
                              attachments=[_Audio()], external_id="e", raw={})
        return hi._handle_audio(msg, "whatsapp")[0].text
    return ouvir


def test_audio_so_com_aviso_nao_diz_nao_entendi(audio_sem_banco):
    texto = audio_sem_banco("paguei e gastei mais em 2025 ou 2026")
    assert _aviso("gastei mais em 2025 ou 2026") in texto
    assert "Não entendi" not in texto and "não entendi o que registrar" not in texto, texto


def test_conversa_multi_grava_o_legitimo_e_avisa(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, "gastei 30 no uber e gastei mais que devia 50 no bar"))
    assert _valores(uid) == [30]
    assert _aviso("gastei mais que devia 50 no bar") in "\n".join(o.text for o in out)


def test_conversa_aviso_com_pergunta_e_gasto_depois_vai_pro_bar(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, _COM_ALUGUEL))
    texto = "\n".join(o.text for o in out)
    assert _valores(uid) == [30]
    assert "Faltou o valor de *aluguel*" in texto
    assert _aviso_com_pergunta("gastei mais que devia 50 no bar") in texto
    hi.handle_incoming(_msg(uid, "1200"))
    assert _valores(uid) == [30, 1200]
    hi.handle_incoming(_msg(uid, "gastei 50 no bar"))  # ia pro aluguel na fila
    assert _valores(uid) == [30, 50, 1200]
    por_valor = {float(r["valor"]): r["alvo"].lower() for r in db.list_launches(uid, limit=5)}
    assert "aluguel" in por_valor[1200] and "bar" in por_valor[50], por_valor


def test_conversa_multi_so_pergunta_nao_grava_e_avisa(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, "paguei e gastei mais em 2025 ou 2026?"))
    assert db.list_launches(uid, limit=5) == []
    assert _aviso("gastei mais em 2025 ou 2026?") in "\n".join(o.text for o in out)


def test_audio_multi_grava_o_legitimo_e_avisa(pro_small_uid, monkeypatch):
    # Pagante: o áudio já é liberado no plano, sem mock do gate.
    monkeypatch.setattr(hi, "transcribe_audio",
                        lambda data, fn: "gastei 30 no uber e gastei mais que devia 50 no bar")
    msg = IncomingMessage(platform="whatsapp", user_id=pro_small_uid, text="",
                          message_id="m", attachments=[_Audio()], external_id="e", raw={})
    out = hi._handle_audio(msg, "whatsapp")
    assert _valores(pro_small_uid) == [30]
    assert _aviso("gastei mais que devia 50 no bar") in "\n".join(o.text for o in out)


def test_audio_com_pergunta_de_valor_avisa_depois_dela(audio_sem_banco):
    texto = audio_sem_banco(_COM_ALUGUEL)
    aviso = _aviso_com_pergunta("gastei mais que devia 50 no bar")
    assert _PERGUNTA_ALUGUEL in texto and aviso in texto, texto
    assert texto.index(_PERGUNTA_ALUGUEL) < texto.index(aviso)



# ── Q40 (banco conectado): o pedaço comparativo não pede a forma de pagamento ──

def test_audio_q40_so_comparativas_nao_pede_a_forma(audio_sem_banco, monkeypatch):
    from core.handlers import forma_pagamento as fp
    monkeypatch.setattr(fp, "regra_ativa", lambda u: True)
    monkeypatch.setattr(fp, "perguntar", lambda *a: pytest.fail("pediu a forma"))
    texto = audio_sem_banco("gastei mais em 2025 ou 2026 e gastei mais que devia 50 no bar")
    assert _aviso("gastei mais em 2025 ou 2026") in texto
    assert _aviso("gastei mais que devia 50 no bar") in texto


def test_audio_q40_resposta_da_forma_pula_o_comparativo(monkeypatch):
    # A pergunta de forma guarda as partes do áudio inteiras; a resposta as
    # roteia por `rotear_partes`, que pula o pedaço comparativo e avisa.
    from core.handlers import forma_pagamento as fp
    pedacos = []
    monkeypatch.setattr(fp, "db", types.SimpleNamespace(consume_pending_action=lambda u, p: True))
    monkeypatch.setattr(hi, "_process_audio_transaction",
                        lambda uid, part, *a: pedacos.append(part) or "✅")
    partes = ["gastei 30 no uber", "gastei mais em 2025 ou 2026"]
    corpo = fp.resolver(5, "dinheiro", {"action_type": "payment_method_choice", "payload": {
        "fluxo": "audio", "partes": partes, "platform": "whatsapp"}})
    assert pedacos == ["gastei 30 no uber"]
    assert corpo == "✅\n\n" + _aviso("gastei mais em 2025 ou 2026")


def test_texto_q40_resposta_cartao_pula_o_comparativo(monkeypatch):
    # "cartão" refaz cada pedaço com "… no cartão", fora do laço do multi: o
    # pedaço comparativo gravava CREDITO R$ 2.025.
    from core.handlers import forma_pagamento as fp
    recebidos = []
    monkeypatch.setattr(fp, "db", types.SimpleNamespace(consume_pending_action=lambda u, p: True))
    monkeypatch.setattr(L, "add", lambda uid, t, *a, **k: recebidos.append(t) or "✅")
    corpo = fp.resolver(5, "cartão", {"action_type": "payment_method_choice", "payload": {
        "fluxo": "texto", "text": "gastei 50 no mercado e gastei mais em 2025 ou 2026?",
        "platform": "whatsapp"}})
    assert recebidos == ["gastei 50 no mercado no cartão"]
    assert corpo == "✅\n\n" + _aviso("gastei mais em 2025 ou 2026?")


def test_audio_q40_fila_recusada_aviso_nao_cita_a_fila(monkeypatch):
    # Outra pendência ocupa a linha (o claim da fila falha): "depois de me
    # passar o valor de *aluguel* e *luz*" mandaria o "1200" para ela.
    from core.handlers import forma_pagamento as fp
    monkeypatch.setattr(L, "register_if_recurring", lambda *a, **k: None)
    monkeypatch.setattr(hi, "db", types.SimpleNamespace(claim_pending_action=lambda *a: False))
    corpo, _ = hi.rotear_partes(5, ["paguei o aluguel", "paguei a luz", "gastei mais em 2025 ou 2026"],
                                IncomingMessage(platform="whatsapp", user_id=5, text=""),
                                "whatsapp", fp.DINHEIRO)
    assert corpo == ("🐷 Não registrei *aluguel*, *luz*: antes tem outra pergunta minha esperando. "
                     "Responde ela e me manda de novo.\n\n" + _aviso("gastei mais em 2025 ou 2026"))

@pytest.mark.parametrize("fila,depois", [
    ([], ""),
    ([{"desc": "aluguel"}], "depois de responder a pergunta acima, "),
    ([{"desc": "aluguel"}, {"desc": "luz"}], "depois de me passar o valor de *aluguel* e *luz*, "),
    ([{"desc": "a"}, {"desc": "b"}, {"desc": "c"}], "depois de me passar o valor de *a*, *b* e *c*, "),
])
def test_aviso_cita_a_fila(fila, depois):
    assert L.aviso_pergunta_pulada(" x? ", fila) == (
        'ℹ️ Não registrei "x?" porque parece uma pergunta. '
        f"Se era gasto, {depois}me manda só o valor e o lugar, tipo *gastei 50 no bar*.")


def test_aviso_corta_trecho_longo_e_mantem_o_curto():
    # Citado inteiro, um trecho de 3000 levava a resposta acima dos 4096 do
    # WhatsApp e a Meta recusava tudo, confirmações dos gravados inclusive.
    longo = "gastei mais que no mês passado " * 100
    aviso = L.aviso_pergunta_pulada(longo)
    assert aviso.startswith('ℹ️ Não registrei "gastei mais que no mês passado gastei')
    assert '…" porque parece uma pergunta.' in aviso and len(aviso) < 200
    sem_espaco = L.aviso_pergunta_pulada("x" * 3000)
    assert f'"{"x" * 79}…"' in sem_espaco
    curto = "a" * 80  # no limite: sai igual, sem "…"
    assert L.aviso_pergunta_pulada(curto) == _aviso(curto)


# ── Pedaço pulado de RECEITA: a dica não manda regravar como gasto (Codex #570) ──
# Tipo pelo verbo, com a mesma regra do resto (`RECEITA_START_VERBS`).

def _aviso_receita(pedaco: str, depois: str = "") -> str:
    return (f'ℹ️ Não registrei "{pedaco}" porque parece uma pergunta. '
            f"Se era receita, {depois}me manda só o valor e de onde veio, tipo *recebi 500 do freela*.")


@pytest.mark.parametrize("pedaco", [
    "recebi mais que o normal 500 do freela", "Ganhei mais que o combinado 300?",
    "caiu mais que no mês passado 200", "entrou menos que devia 100 do aluguel",
])
@pytest.mark.parametrize("fila,depois", [
    ([], ""),
    ([{"desc": "aluguel"}], "depois de responder a pergunta acima, "),
    ([{"desc": "a"}, {"desc": "b"}], "depois de me passar o valor de *a* e *b*, "),
])
def test_aviso_de_receita_pede_receita(pedaco, fila, depois):
    assert L.aviso_pergunta_pulada(pedaco, fila) == _aviso_receita(pedaco, depois)


def test_multi_receita_pulada_avisa_como_receita(add_sem_banco):
    resposta, gravados = add_sem_banco("gastei 30 no uber e recebi mais que o normal 500 do freela")
    assert gravados == [30]
    assert _aviso_receita("recebi mais que o normal 500 do freela") in resposta, resposta
    assert "Se era gasto" not in resposta
