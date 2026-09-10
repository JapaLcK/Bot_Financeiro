"""Serialização por `(asaas_payment_id, effect)` — §17.1, pendência 5.

O cenário é o do docstring de `registrar_efeito`, e ele não é hipotético: dois
eventos **distintos** do mesmo `payment.id` (um `PAYMENT_CONFIRMED` e um
`PAYMENT_RECEIVED`, ou uma reentrega com `event_id` novo) travam linhas de
OUTBOX diferentes. O `select … for update skip locked` de `reservar_evento`
serializa por `event_id`, e a chave do efeito é `(payment_id, effect)` — outra
chave. Sem guarda, os dois leem `efeito_registrado is False`, os dois executam:
**dois e-mails, dois `purchase` no GA4, dois `Purchase` na CAPI** sobre o mesmo
dinheiro. O `on conflict do nothing` só desempata DEPOIS.

A guarda é `db.pix_effects.lock_efeito` (`pg_advisory_lock(hashtext(...))`, o
mesmo precedente do `_billing_user_lock`), segurando consulta + execução +
registro no mesmo escopo.

CONTROLES NEGATIVOS MEDIDOS:

  * troque `lock_efeito` por um `contextlib.nullcontext` (consulta → executa →
    registra sem guarda) → `test_dois_eventos_do_mesmo_pagamento_executam_uma_vez`
    VERMELHO com contador 2.

POSITIVO, e ele é o que importa aqui: `test_pagamentos_diferentes_nao_serializam`.

**Ele NÃO media isso até 2026-09-09, e a prosa que estava aqui afirmava que sim.**
Medido: trocar a chave por `"pix_effect:GLOBAL"` deixava os DOIS testes verdes. A
razão é que um lock global **serializa** e não duplica — os contadores dão
`{"ga4": 2, "capi": 2, "email": 2}` igual, só que uma passada depois da outra. Um
teste que só afirma CONTADOR é estruturalmente cego ao lock grosso demais, que é
justamente o modo de falha de um `pg_advisory_lock` mal chaveado.

O conserto é asserir **SOBREPOSIÇÃO**: um `threading.Barrier(2)` DENTRO do efeito
falso. Com a chave certa as duas threads se encontram lá dentro; com a chave
global a segunda ainda espera o lock e a barreira ESTOURA. Barreira e não timer —
relógio vira flake, encontro não.

CEGUEIRA DECLARADA: duas THREADS do mesmo processo, não dois processos. O
advisory lock é do Postgres e vale entre processos; o que este arquivo prova é a
chave e o escopo, não o alcance entre workers do Railway.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, nova_cobranca
from db.webhook_outbox import registrar_evento


def _corpo(event_id: str, tipo: str, cobranca: dict) -> dict:
    return {"id": event_id, "event": tipo,
            "payment": {"id": cobranca["asaas_payment_id"],
                        "externalReference": cobranca["external_reference"],
                        "value": cobranca["amount_cents"] / 100, "status": "RECEIVED"}}


@pytest.fixture()
def contadores(monkeypatch):
    """Efeitos externos trocados por contadores que DORMEM.

    O `sleep` é o que abre a janela: sem ele as duas passadas quase nunca se
    cruzam, e o teste ficaria verde por sorte de escalonamento — verde por sorte
    é o que faz uma corrida voltar seis meses depois.
    """
    import core.services.pix_drain_effects as ef

    contagem = {"ga4": 0, "capi": 0, "email": 0}
    trava = threading.Lock()

    def _conta(nome):
        def _efeito(cobranca, evt):
            time.sleep(0.05)
            with trava:
                contagem[nome] += 1
        return _efeito

    monkeypatch.setitem(ef.EXECUTORES, "ga4", _conta("ga4"))
    monkeypatch.setitem(ef.EXECUTORES, "capi", _conta("capi"))
    monkeypatch.setitem(ef.EXECUTORES, "email", _conta("email"))
    return contagem


def _drenar_em_paralelo(eventos: list[str]) -> list[BaseException]:
    from core.services.pix_drain import drenar_evento

    barreira = threading.Barrier(len(eventos), timeout=15)
    erros: list[BaseException] = []

    def _roda(event_id: str):
        try:
            barreira.wait()
            drenar_evento(event_id)
        except BaseException as exc:  # noqa: BLE001 — o teste decide
            erros.append(exc)

    fios = [threading.Thread(target=_roda, args=(e,)) for e in eventos]
    for f in fios:
        f.start()
    for f in fios:
        f.join(timeout=60)
    return erros


def _enfileirar(cobranca: dict, tipo: str) -> str:
    event_id = f"evt_{uuid.uuid4().hex[:16]}"
    registrar_evento(event_id, tipo, _corpo(event_id, tipo, cobranca),
                     int(time.time()) + 1)
    return event_id


def test_dois_eventos_do_mesmo_pagamento_executam_uma_vez(user_id, contadores):
    """D5-a — o caso que DISCRIMINA.

    `CONFIRMED` e `RECEIVED` do MESMO `payment.id`, drenados em paralelo. Cada um
    trava uma linha de outbox diferente; quem os põe em fila é o `lock_efeito`.
    """
    garantir_system_event_logs()
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    eventos = [_enfileirar(cobranca, "PAYMENT_CONFIRMED"),
               _enfileirar(cobranca, "PAYMENT_RECEIVED")]

    erros = _drenar_em_paralelo(eventos)
    assert not erros, erros
    assert contadores == {"ga4": 1, "capi": 1, "email": 1}, (
        "efeito externo executado duas vezes sobre o mesmo dinheiro"
    )
    assert efeitos(cobranca["asaas_payment_id"]) == {
        "stripe_cancel", "grant", "ga4", "capi", "email"}


def test_pagamentos_diferentes_nao_serializam(user_id, contadores, monkeypatch):
    """D5-b, o POSITIVO que importa: dois PAGAMENTOS distintos SE SOBREPÕEM.

    **A asserção é a sobreposição, não o contador**, e a diferença foi medida:
    com `"pix_effect:GLOBAL"` no lugar da chave, a versão anterior deste teste
    (que só olhava `contadores == {"ga4": 2, …}`) ficava VERDE — lock global
    serializa, não duplica. A barreira exige que as duas threads estejam DENTRO
    do efeito ao mesmo tempo, que é a propriedade que a chave de fato decide.

    `usuário` diferente por cobrança porque `uniq_pix_charge_ativa` só admite uma
    cobrança ativa por dono.
    """
    import core.services.pix_drain_effects as ef

    from db import ensure_user

    garantir_system_event_logs()
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    conta(user_id, "free", None)
    conta(outro, "free", None)

    # O `timeout` é o que transforma "não se encontraram" em vermelho em vez de
    # travar a suíte: 15 s é o mesmo teto do gate de largada em
    # `_drenar_em_paralelo`. Estourando, `wait()` levanta `BrokenBarrierError`
    # dentro do efeito, o dreno converte em falha do evento (nunca levanta), e
    # `cruzaram` fica vazio — a asserção abaixo é quem reporta.
    encontro = threading.Barrier(2, timeout=15)
    cruzaram: list[str] = []
    conta_ga4 = ef.EXECUTORES["ga4"]   # o contador que a fixture instalou

    def _ga4_com_encontro(cobranca, evt):
        encontro.wait()
        cruzaram.append(str(cobranca["asaas_payment_id"]))
        conta_ga4(cobranca, evt)

    monkeypatch.setitem(ef.EXECUTORES, "ga4", _ga4_com_encontro)

    try:
        a, b = nova_cobranca(user_id), nova_cobranca(outro)
        eventos = [_enfileirar(a, "PAYMENT_RECEIVED"),
                   _enfileirar(b, "PAYMENT_RECEIVED")]
        erros = _drenar_em_paralelo(eventos)
        assert not erros, erros
        assert len(set(cruzaram)) == 2, (
            "as duas passadas NÃO se sobrepuseram dentro do efeito: o lock está "
            "serializando pagamentos diferentes, ou seja a chave está grossa "
            f"demais (global?). Encontraram-se: {cruzaram}"
        )
        assert contadores == {"ga4": 2, "capi": 2, "email": 2}
    finally:
        from conftest import _cleanup_user  # type: ignore[attr-defined]

        _cleanup_user(outro)
