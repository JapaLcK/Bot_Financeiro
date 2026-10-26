"""Lembrete de WhatsApp da declaração "a conferir" (ADR 0006, A2c): quem está devido, reserva e soltura.

Invariante I9: o lembrete não muda lançamento, vínculo nem saldo; escreve só `bank_movement_declarations.lembrete_em`
(uma vez por declaração: desfazer, trocar e "foi em dinheiro" não o tocam). "Viva" = `DECLARADA_SQL` (o mesmo
conceito de `e_fluxo`, uma fonte só) e sem par. `limite_dia` é a meia-noite local do dia T-6: `declared_at < limite_dia`
<=> o dia do gasto, no fuso do app, é T-7 ou antes (7 dias ou mais). Quem calcula é o chamador, com o `now` injetado.
"""
from __future__ import annotations

from datetime import datetime

from .bank_declaracoes import DECLARADA_SQL
from .connection import get_conn

# Alias `dec` no alvo: o `DECLARADA_SQL` tem o próprio `d` numa subconsulta.
_VIVA = f"{DECLARADA_SQL} and dec.matched_transaction_id is null and dec.lembrete_em is null and dec.declared_at < %s"


def list_declaracao_lembretes_devidos(limite_dia: datetime, janela_desde: datetime) -> list[dict]:
    """Declarações vivas, sem lembrete, de TODOS os usuários, que têm 7 dias ou mais e cujo dono falou com o
    Piggy desde `janela_desde` (texto livre só vale dentro da janela da Meta). Só ids: o conteúdo da mensagem sai
    do `returning` da reserva. `EXISTS` e não JOIN: `auth_accounts.user_id` não é único."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""select dec.user_id, dec.launch_id
                  from bank_movement_declarations dec
                  join launches on launches.id = dec.launch_id and launches.user_id = dec.user_id
                 where {_VIVA}
                   and exists (select 1 from auth_accounts a
                                where a.user_id = dec.user_id and a.last_activity_at >= %s)
                 order by dec.user_id, dec.declared_at, dec.launch_id""",
            (limite_dia, janela_desde))
        return [dict(r) for r in cur.fetchall()]


def claim_declaracao_lembretes(user_id: int, launch_ids: list[int], limite_dia: datetime) -> list[dict]:
    """Reserva os lembretes do usuário ANTES do envio, num UPDATE só (CAS: ainda viva, ainda sem lembrete), sob
    o mutex do usuário (o reconcile mexe nestas linhas depois dele; em lote sem ele a ordem de lock cruzaria
    com um sync). Devolve as linhas que ESTE processo reservou, mais antigas primeiro, já com o conteúdo."""
    from .bank_movements import _lock_user  # na chamada, para o teste de corrida alcançar o mutex
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        cur.execute(
            f"""update bank_movement_declarations dec set lembrete_em = now()
                  from launches
                 where dec.user_id = %s and dec.launch_id = any(%s)
                   and launches.id = dec.launch_id and launches.user_id = dec.user_id and {_VIVA}
             returning dec.launch_id, dec.amount, dec.declared_at,
                       launches.alvo, launches.tipo""",
            (int(user_id), [int(i) for i in launch_ids], limite_dia))
        reservadas = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return sorted(reservadas, key=lambda r: (r["declared_at"], r["launch_id"]))


def release_declaracao_lembretes(user_id: int, launch_ids: list[int]) -> None:
    """Nenhum destino aceitou a mensagem: devolve a reserva para a hora seguinte tentar. Mesmo mutex da reserva."""
    from .bank_movements import _lock_user
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        cur.execute("update bank_movement_declarations set lembrete_em = null where user_id = %s and launch_id = any(%s)",
                    (int(user_id), [int(i) for i in launch_ids]))
        conn.commit()
