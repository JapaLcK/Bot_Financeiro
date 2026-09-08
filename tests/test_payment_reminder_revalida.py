"""
tests/test_payment_reminder_revalida.py — o lembrete de pagamento × o estado que
MUDOU depois da query.

`db.dunning.list_payment_reminder_candidates` devolve UM snapshot e o `for row
in rows` não tem `LIMIT`. Quem pagasse depois da query — em especial enquanto as
linhas ANTERIORES do lote são processadas — recebia "a cobrança continua
pendente" e a dedupe registrava o envio como sucesso. E-mail errado para cliente
PAGANTE, que é a categoria que este caminho existe para consertar (célula nº 28
de `docs/dunning_estados_eventos.md`).

O conserto é `db.dunning.lembrete_ainda_vale`, chamado como ÚLTIMA coisa
antes do envio. Duas coisas que ele deliberadamente NÃO é, e cada uma tem teste
aqui:

  • não é `get_auth_user` — aquele tem cache de 10 s
    (`db_support._auth_user_cache`) e pode mentir exatamente nesta corrida;
  • não é claim atômico com gravação antecipada da dedupe — o `_fire_email` do
    webhook grava a chave DEPOIS de o envio confirmar, de propósito, e um claim
    que gravasse antes reintroduziria o ciclo mudo da rodada 1.

Arquivo próprio porque `test_payment_reminder.py` está a poucas linhas do teto
de 350 de `tests/test_max_lines_python.py`; os helpers vêm por IMPORT dele —
uma fonte só (§0.7). Mesmo arranjo do `test_payment_reminder_janela.py`.

CONTROLE NEGATIVO DECLARADO — em `core/services/payment_reminder.py`, apague o
bloco `if not await loop.run_in_executor(None, lembrete_ainda_vale, ...)`:
    VERMELHO: test_pagou_durante_o_lote_nao_recebe_lembrete
              test_status_saiu_da_lista_durante_o_lote_nao_recebe_lembrete
    VERDE:    test_lembrete_legitimo_continua_saindo e todo o
              `test_payment_reminder.py` / `_janela.py` — a fixture deles não
              mexe no estado depois da query, que é o que prova que a injeção
              mede a REVALIDAÇÃO e não o funil.

CONTROLE POSITIVO: `test_lembrete_legitimo_continua_saindo`. Sem ele o arquivo
passaria num `lembrete_ainda_vale` que devolvesse sempre False — ou seja num
lembrete que nunca sai, que é pior que o bug.
"""
from __future__ import annotations

import pytest

from db.connection import get_conn
from test_payment_reminder import (
    _espia,
    _inadimplente,
    _limpar_eventos,
    _tick,
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Mesmo ambiente do arquivo irmão: flag ligada (ela nasce DESLIGADA em
    produção), sem template de WhatsApp, e `system_event_logs` garantida."""
    from _billing_grants_helpers import garantir_system_event_logs
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    garantir_system_event_logs()


def _mexer_depois_da_query(monkeypatch, sql: str, uid: int) -> list:
    """Injeta a mudança de estado no ponto EXATO onde ela cabe em produção: o
    funil já respondeu, e o `for row in rows` ainda não chegou nesta linha.

    O envelope chama o funil REAL e devolve as linhas REAIS — o snapshot fica
    igual ao de produção, e o que muda é só o banco por baixo dele. Devolve a
    lista de chamadas para o teste provar que a injeção aconteceu (sem isso, um
    funil que devolvesse vazio faria o teste passar medindo nada).
    """
    from db import dunning as db_dunning
    real = db_dunning.list_payment_reminder_candidates
    chamadas: list = []

    def _envelope(grace_days):
        rows = real(grace_days)
        chamadas.append(len(rows))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uid,))
            conn.commit()
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(uid)
        return rows

    monkeypatch.setattr(db_dunning, "list_payment_reminder_candidates", _envelope)
    return chamadas


def _eventos_de_dedupe(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from system_event_logs"
                " where event_type = 'payment_reminder_sent' and user_id = %s",
                (uid,))
            return cur.fetchone()["n"]


def test_pagou_durante_o_lote_nao_recebe_lembrete(user_id, monkeypatch):
    """O `invoice.paid` cai depois da query: o relógio zera e o lembrete morre.

    É a corrida do apontamento, com o estado REAL no banco — não um funil
    fabricado. E a asserção é DUPLA de propósito: nada de e-mail **e** nada de
    chave de dedupe. Gravar a dedupe sem enviar é o que calaria o lembrete
    legítimo do tick seguinte, quando a conta ainda estiver em atraso.
    """
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    chamadas = _mexer_depois_da_query(
        monkeypatch,
        "update auth_accounts set past_due_since = null where user_id = %s",
        user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1, \
        "o funil não devolveu a conta — a injeção mediria nada"
    assert enviados == [], "lembrete saiu para quem pagou durante o lote"
    assert _eventos_de_dedupe(user_id) == 0, \
        "dedupe gravada sem envio: o tick seguinte ficaria mudo"


def test_status_saiu_da_lista_durante_o_lote_nao_recebe_lembrete(user_id, monkeypatch):
    """O outro lado do MESMO predicado: o status sai de
    `PAST_DUE_PAYMENT_STATUSES` durante o lote.

    Em produção esse caminho é o `set_payment_status_impl`, que zera o relógio
    no mesmo UPDATE — aqui o status é trocado SEM tocar no relógio, de propósito,
    para que a revalidação seja medida pelos DOIS termos e não só pelo relógio.
    Um predicado que checasse só `past_due_since is not null` passaria no teste
    anterior e reprovaria neste.
    """
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    chamadas = _mexer_depois_da_query(
        monkeypatch,
        "update auth_accounts set last_payment_status = 'active' where user_id = %s",
        user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1
    assert enviados == [], "lembrete saiu com o status já fora da lista"
    assert _eventos_de_dedupe(user_id) == 0


def test_lembrete_legitimo_continua_saindo(user_id, monkeypatch):
    """POSITIVO: sem mudança de estado no meio do lote, o lembrete sai e a
    dedupe é gravada. Sem este caso, uma revalidação que recusasse TUDO passaria
    nos dois testes acima."""
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    # Envelope que NÃO mexe em nada: o mesmo caminho, a mesma quantidade de
    # chamadas, só sem a corrida. É o que separa "a revalidação discrimina" de
    # "a revalidação bloqueia".
    chamadas = _mexer_depois_da_query(monkeypatch, "select %s", user_id)
    _tick()
    assert chamadas and chamadas[0] >= 1
    assert enviados != [], "o lembrete legítimo deixou de sair"
    assert _eventos_de_dedupe(user_id) == 1
