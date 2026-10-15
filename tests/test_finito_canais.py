"""CHECK `finito_*` nos canais (ponta a ponta) e na borda da Pluggy.

Cada canal roda COM a guarda da porta contornada (`valor_valido` trocado por "sempre
verdadeiro" no módulo que a usa), para que só o BANCO possa barrar o valor. Controles:
com o CHECK derrubado a escrita absurda PASSA (prova que foi o banco que barrou);
com ele recolocado, é barrada. O log leva o nome da constraint e nunca "Failing row".
"""
import logging
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from conftest import usuario_pagante
from core.services.pluggy_sync import _to_decimal, normalize_pluggy_account
from db import get_conn
from db.of_snapshots import _numero
from test_valor_nao_finito_painel import _cliente
from tests._patrimonio_helpers import conexao, q
from utils_text import VALOR_MAX

GRANDE = "100000000000000000000"  # 1e20 > VALOR_MAX
AVISO_WA = "Não consegui registrar esse valor. Confere o número e manda de novo."


def _linhas(sql, uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid,))
        return cur.fetchall()


def _saldo(uid):
    return _linhas("select balance from accounts where user_id=%s", uid)[0]["balance"]


def _diga(uid, texto):
    from core.types import IncomingMessage
    import core.handle_incoming as HI
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="x", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


@pytest.fixture
def eventos(monkeypatch):
    """Registra o que `log_system_event` receberia (monólito e v2), sem ir ao banco."""
    visto = []

    async def _grava(*a, **k):
        visto.append(repr((a, k)))

    import api.v2.erros as erros
    import frontend.finance_bot_websocket_custom as dashboard
    monkeypatch.setattr(erros, "log_system_event", _grava)
    monkeypatch.setattr(dashboard, "log_system_event", _grava)
    return visto


def _sem_pii(texto):
    assert "finito_" in texto
    assert "Failing row" not in texto and GRANDE not in texto


# ── WhatsApp: duas mensagens pelo handle_incoming ────────────────────────────

def test_whatsapp_valor_absurdo_barrado_pelo_banco_e_a_conversa_segue(monkeypatch, caplog, sem_check_finito):
    import core.handlers.launches as L
    monkeypatch.setattr(L, "valor_valido", lambda v: True)  # guarda da porta contornada
    uid = usuario_pagante()
    db.set_balance(uid, Decimal(100))

    with caplog.at_level(logging.WARNING):
        resposta = _diga(uid, f"gastei {GRANDE} no mercado")
    assert AVISO_WA in resposta, resposta
    assert _linhas("select 1 from launches where user_id=%s", uid) == [] and _saldo(uid) == 100
    _sem_pii(caplog.text)

    # a conversa segue: o banco não ficou com transação suja
    assert "Despesa registrada" in _diga(uid, "gastei 50 no mercado")
    assert len(_linhas("select 1 from launches where user_id=%s", uid)) == 1 and _saldo(uid) == 50

    # controle negativo: sem o CHECK a escrita absurda PASSA (foi o banco que barrou)
    sem_check_finito("launches", "valor")
    sem_check_finito("accounts", "balance")
    _diga(uid, f"gastei {GRANDE} na farmacia")
    assert len(_linhas("select 1 from launches where user_id=%s", uid)) == 2


# ── Rota do monólito ─────────────────────────────────────────────────────────

def test_rota_do_monolito_responde_400_e_nao_grava(monkeypatch, caplog, eventos, sem_check_finito):
    monkeypatch.setattr("utils_text.valor_valido", lambda v: True)  # a rota importa na chamada
    uid = usuario_pagante()
    c, h = _cliente(uid)
    corpo = f'{{"tipo":"despesa","valor":{GRANDE},"alvo":"mercado","nota":"x"}}'

    with caplog.at_level(logging.WARNING):
        r = c.post(f"/launches/{uid}", headers=h, content=corpo)
    assert r.status_code == 400 and r.json() == {"detail": "Valor inválido: confira o número."}, r.text
    assert _linhas("select 1 from launches where user_id=%s", uid) == [] and _saldo(uid) == 0
    _sem_pii(" ".join(eventos) + caplog.text)
    assert "Failing row" not in r.text and any("finito_" in e for e in eventos)

    sem_check_finito("launches", "valor")
    sem_check_finito("accounts", "balance")
    r = c.post(f"/launches/{uid}", headers=h, content=corpo)
    assert r.status_code == 200, r.text  # controle negativo: sem o CHECK a escrita passa
    assert len(_linhas("select 1 from launches where user_id=%s", uid)) == 1


# ── /api/v2 (e /api/app, que usa o mesmo criar_app) ──────────────────────────

def _app_de_teste():
    from api.v2.app import criar_app
    app = criar_app("/api/teste")

    @app.get("/envenena/{uid}")
    def envenena(uid: int):  # escrita de verdade, sem guarda nenhuma
        with get_conn() as conn:
            conn.execute("update accounts set balance = 'NaN' where user_id = %s", (uid,))
        return {"ok": True}

    @app.get("/outro_check/{uid}")
    def outro_check(uid: int):  # CheckViolation que NÃO é nosso segue para o 500
        with get_conn() as conn:
            conn.execute("alter table accounts add constraint zz_alheio check (balance > 123456)")
        return {}
    return TestClient(app, raise_server_exceptions=False)


def test_v2_check_finito_vira_envelope_400_e_o_log_nao_traz_a_linha(eventos, caplog, sem_check_finito):
    uid = usuario_pagante()
    c = _app_de_teste()
    with caplog.at_level(logging.WARNING):
        r = c.get(f"/envenena/{uid}")
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "valor_invalido", "message": "Valor inválido: confira o número."}}
    assert _saldo(uid) == 0
    _sem_pii(" ".join(eventos) + caplog.text)

    sem_check_finito("accounts", "balance")
    assert c.get(f"/envenena/{uid}").status_code == 200  # controle negativo
    assert str(_saldo(uid)) == "NaN"


def test_v2_check_alheio_continua_500(eventos):
    uid = usuario_pagante()
    r = _app_de_teste().get(f"/outro_check/{uid}")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    q("alter table accounts drop constraint if exists zz_alheio")


def test_api_app_usa_o_mesmo_tratador():
    from api.nativo.app import app
    from psycopg.errors import CheckViolation
    from api.v2 import erros
    assert app.exception_handlers[CheckViolation] is erros.valor_finito_erro


# ── Tool da IA ───────────────────────────────────────────────────────────────

def test_tool_da_ia_nao_grava_valor_absurdo(monkeypatch, sem_check_finito):
    import core.handlers.launches as L
    import core.services.ai_chat.tools.launches as T
    monkeypatch.setattr(L, "valor_valido", lambda v: True)
    monkeypatch.setattr(T, "valor_valido", lambda v: True)
    uid = usuario_pagante()
    db.set_balance(uid, Decimal(100))
    args = {"tipo": "despesa", "valor": float(GRANDE), "alvo": "x", "forma_pagamento": "dinheiro"}
    try:
        T._add_launch_execute(uid, args)
    except Exception as exc:  # a tool pode levantar ou responder: o que importa é o estado
        from db.finito_checks import violacao_finito
        assert violacao_finito(exc)
    assert _linhas("select 1 from launches where user_id=%s", uid) == [] and _saldo(uid) == 100

    sem_check_finito("launches", "valor")  # controle negativo: sem o CHECK a escrita passa
    sem_check_finito("accounts", "balance")
    T._add_launch_execute(uid, args)
    assert len(_linhas("select 1 from launches where user_id=%s", uid)) == 1


# ── Tarefa de fundo ──────────────────────────────────────────────────────────

def test_tarefa_de_fundo_roda_o_ensure_e_loga_o_resumo(caplog):
    import asyncio
    import frontend.finance_bot_websocket_custom as dashboard
    with caplog.at_level(logging.INFO):
        asyncio.run(dashboard._tarefa_finito_checks())
    assert "[finito_checks]" in caplog.text and "criadas=0" in caplog.text, caplog.text


# ── Borda da Pluggy / snapshots / extrato ────────────────────────────────────

@pytest.mark.parametrize("v", ["NaN", float("nan"), "Infinity", "-Infinity", 10**400, "1e20", 1e20,
                               VALOR_MAX + 1, "abc", None, "1e1000000", pytest.param("1" * 1_100_000, id="um-milhao-de-digitos")])
def test_to_decimal_e_numero_recusam_nao_finito_e_absurdo(v):
    assert _to_decimal(v) == 0 and _numero(v) is None
    assert _to_decimal(v, "7") == 7  # cai no default, como "abc"


@pytest.mark.parametrize("v", ["0", 0, "-5.5", 1234.56, VALOR_MAX, -VALOR_MAX, "9999999999999.99"])
def test_to_decimal_e_numero_aceitam_o_que_cabe(v):
    assert _to_decimal(v) == Decimal(str(v)) and _numero(v) == Decimal(str(v))


def test_bank_movements_amount_recusa_acima_do_limite():
    from db.bank_movements import _amount
    assert _amount(VALOR_MAX + 1) is None and _amount("NaN") is None and _amount("1e20") is None
    assert _amount(VALOR_MAX) == VALOR_MAX
    assert _amount("1e1000000") is None and _amount("1" * 1_100_000) is None  # abs() estouraria Overflow


def test_sync_com_conta_nan_termina_e_grava_as_validas():
    uid = usuario_pagante()
    cid = conexao(uid, f"item-finito-{uid}")
    contas = [normalize_pluggy_account({"id": i, "balance": b, "currencyCode": "BRL"})
              for i, b in (("a", 10), ("b", "NaN"), ("c", 20), ("d", "1e20"))]
    db.save_open_finance_sync(cid, contas)
    saldos = {r["provider_account_id"]: r["balance"] for r in q(
        "select provider_account_id, balance from open_finance_accounts where connection_id=%s",
        (cid,), fetch=True)}
    assert saldos == {"a": 10, "b": 0, "c": 20, "d": 0}


def test_valor_valido_e_finito_comparam_decimal_exato_sem_passar_por_float():
    """OFX preserva o Decimal: via float, 10000000000000.0001 vira VALOR_MAX e passaria pela guarda
    (o CHECK do banco o recusa e a importação já commitada voltaria erro)."""
    from utils_text import valor_finito, valor_valido
    justo, alem = Decimal("10000000000000"), Decimal("10000000000000.0001")
    assert valor_valido(justo) and valor_finito(justo) and valor_finito(-justo)
    assert not valor_valido(alem) and not valor_finito(alem) and not valor_finito(-alem)
    assert not valor_valido(Decimal("NaN")) and not valor_finito(Decimal("-Infinity"))
    assert not valor_finito(Decimal("1e1000000")) and valor_finito(Decimal("0")) and not valor_valido(Decimal("0"))
