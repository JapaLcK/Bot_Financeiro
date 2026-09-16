"""Molde comum das duas metades do conserto da fusão Open Finance.

Um banco conectado com espelho autoritativo + mensagens reais pelo
`handle_incoming`. Mora aqui, e não duplicado nos dois arquivos de teste, pelo
CLAUDE.md §0.7 — os dois medem o MESMO cenário por ângulos diferentes
(`test_reconciliacao_devolve_o_debito.py` mede o dinheiro,
`test_saldo_total_no_lancamento.py` mede a frase).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

import db
import core.handle_incoming as hi
from core.types import IncomingMessage
from tests.conftest import promote_to_pro


@pytest.fixture
def uid_pro():
    """Pro com id < 1bi: `handle_incoming` só re-normaliza id > 2bi, então um id
    pequeno chega inteiro nos handlers (mesmo motivo do `pro_small_uid` de
    `tests/test_handle_incoming_routing.py:33`)."""
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return promote_to_pro(uid)


@pytest.fixture
def ia_fora(monkeypatch):
    """A IA não pode ser chamada nem bater em rede. Se a mensagem cair nela, a
    resposta vem marcada e o assert do teste falha citando o texto."""
    import core.services.ai_chat as ai_chat_mod
    chamadas: list[str] = []

    def _fake(user_id, text, **kwargs):
        chamadas.append(text)
        return f"[IA-NAO-DEVIA-SER-CHAMADA] {text}"

    monkeypatch.setattr(ai_chat_mod, "chat", _fake)
    return chamadas


def sincroniza(conexao_id: int, uid: int, saldo: str, transacoes=()) -> None:
    """Espelho autoritativo do banco (o `balance` do `on conflict` sobrescreve)."""
    db.save_open_finance_sync(conexao_id, [{
        "provider_account_id": f"acc-of-{uid}",
        "name": "Nubank Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
        "currency": "BRL", "balance": Decimal(saldo), "raw": {},
        "transactions": list(transacoes),
    }])


def conecta_banco(uid: int, saldo: str, transacoes=()) -> int:
    item = db.save_pluggy_open_finance_item(uid, {
        "id": f"item-of-{uid}", "connector": {"id": 612, "name": "Nubank"},
        "status": "UPDATED",
    })
    sincroniza(item["id"], uid, saldo, transacoes)
    return item["id"]


def tx(uid: int, valor: str, dia: date, descricao: str, ident: str = "1") -> dict:
    """Transação do extrato. `valor` negativo = saída."""
    return {
        "provider_transaction_id": f"of-tx-{uid}-{ident}",
        "description": descricao,
        "amount": Decimal(valor),
        "transaction_date": dia,
        "transacted_at": None,
        "category": None,
        "raw": {},
    }


def manda(uid: int, texto: str) -> str:
    """Uma mensagem de WhatsApp pelo caminho de produção inteiro."""
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=texto,
        message_id="1", attachments=[], external_id=str(uid), raw={},
    ))
    assert out, f"handler devolveu vazio para {texto!r}"
    return out[0].text


def consolidado(uid: int) -> tuple[float, float]:
    """(consolidado, carteira manual) — a MESMA fonte que o /saldo lê."""
    cb = db.get_consolidated_balance(uid)
    return float(cb["consolidated"] or 0), float(cb["manual"] or 0)


def ultimo_launch(uid: int) -> int:
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches where user_id=%s order by id desc limit 1",
                (uid,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def delta_conta(uid: int, launch_id: int):
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select efeitos->>'delta_conta' as d from launches where id=%s and user_id=%s",
                (launch_id, uid),
            )
            row = cur.fetchone()
        conn.commit()
    return None if not row or row["d"] is None else Decimal(row["d"])


def saldo_bruto(uid: int) -> Decimal:
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (uid,))
            row = cur.fetchone()
        conn.commit()
    return Decimal(str(row["balance"]))


def soma_delta_conta(uid: int) -> Decimal:
    """Invariante do `tests/test_account_reset.py:607`: saldo == soma dos deltas."""
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum((efeitos->>'delta_conta')::numeric), 0) as s "
                "from launches where user_id=%s and efeitos ? 'delta_conta'",
                (uid,),
            )
            s = cur.fetchone()["s"]
        conn.commit()
    return Decimal(str(s))
