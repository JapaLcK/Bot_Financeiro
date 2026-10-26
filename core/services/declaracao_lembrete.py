"""Lembrete de WhatsApp da declaração "a conferir" há 7 dias (ADR 0006, A2c).

Roda como um passo do `run_recurring_charger_loop` (a cada hora). Manda UMA mensagem de texto livre por usuário,
das `bill_reminder_hour()` às 21:59, só para quem falou com o Piggy nas últimas 23 h (`_JANELA_WA`), listando as
declarações de despesa/receita "pelo banco" vivas e sem par há 7 dias ou mais (contados da data do gasto, no
fuso do app), cada uma uma vez só. Todas as saídas (escolher a transação, "foi em dinheiro", apagar) ficam no
app: o lembrete não oferece ação pelo WhatsApp (D9). Não muda lançamento, vínculo nem saldo (I9), e não lê flag
nenhuma.

Reserva em lote ANTES de enviar (`db/bank_lembrete.py`): dois processos não mandam duas vezes. Se nenhum destino
aceitar, solta a reserva e a hora seguinte tenta de novo; processo que morre entre reservar e enviar perde a
mensagem (o app segue mostrando "A conferir"). Quem está barrado (acesso, opt-out, sem telefone, exclusão de
conta agendada ou em processamento) NÃO reserva: o lembrete sai quando voltar. Falha de um usuário (inclusive
entre a reserva e o envio) não derruba os outros. Quem só usa o app e não fala com o Piggy nunca recebe.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta

from core.services.recurring_charger import _JANELA_WA, bill_reminder_hour
from db.bank_declaracoes import _dia
from utils_date import _tz, now_tz

LEMBRETE_DIAS = 7
_HORA_LIMITE = 22  # envia até 21:59; fixo, sem env (decisão do dono, D6)
_MAX_LINHAS = 5
_RODAPE = "\n\n💡É só um lembrete: não mudei nada nos seus lançamentos."


def _nome(alvo) -> str:
    return " ".join(str(alvo or "").split())[:60]


def _mensagem_declaracoes(rows: list[dict]) -> str:
    """Texto do dono (ADR 0006, D11): singular com 1 declaração (despesa ou receita), lista (até 5) com 2+."""
    from core.dashboard_links import base_url_publica
    from utils_text import fmt_brl

    def valor(r: dict) -> str:
        return fmt_brl(abs(float(r["amount"])))

    def dia(r: dict) -> str:
        return _dia(r).strftime("%d/%m")

    if len(rows) == 1:
        r = rows[0]
        receita = r["tipo"] == "receita"
        nome = _nome(r["alvo"])
        if receita:
            abertura = (f"🐷 A receita{' de ' + nome if nome else ''}, de {valor(r)}, "
                        f"que você marcou como recebida pelo banco em {dia(r)}, ")
            acoes = "vincular a receita à transação do banco, indicar que recebeu em dinheiro"
        else:
            abertura = (f"🐷 O gasto{' com ' + nome if nome else ''}, de {valor(r)}, "
                        f"que você marcou como pago pelo banco em {dia(r)}, ")
            acoes = "vincular o gasto à transação do banco, indicar que pagou em dinheiro"
        abertura += "ainda não apareceu no seu extrato."
    else:
        linhas = ["• {} {}, {}, {}".format("receita" if r["tipo"] == "receita" else "gasto",
                                          _nome(r["alvo"]) or "sem descrição", valor(r), dia(r))
                  for r in rows[:_MAX_LINHAS]]
        if len(rows) > _MAX_LINHAS:
            linhas.append(f"• e mais {len(rows) - _MAX_LINHAS}, no app.")
        abertura = (f"🐷 Estas {len(rows)} movimentações que você marcou como feitas pelo banco "
                    "ainda não apareceram no seu extrato:\n" + "\n".join(linhas))
        acoes = "vincular cada uma à transação do banco, indicar que foi em dinheiro"
    saidas = ("\nPara conferir, abra o app e vá em Visão geral → Patrimônio total → Movimentações não confirmadas. "
              f"Lá, você pode {acoes} ou excluir o lançamento. {base_url_publica()}/app?view=overview")
    return abertura + saidas + _RODAPE


def notify_declaracoes_whatsapp_once(now: datetime | None = None) -> int:
    """Passo horário do lembrete. `now` entra por parâmetro (a regra dos 7 dias, a janela e a hora saem dele; o
    `now()` do SQL só carimba `lembrete_em`). Retorna quantos destinos aceitaram."""
    now = (now or now_tz()).astimezone(_tz())  # data e hora no fuso do app, qualquer que seja o fuso de `now`
    if not bill_reminder_hour() <= now.hour < _HORA_LIMITE:
        return 0  # fora do horário: nem lê o banco

    from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
    from adapters.whatsapp.wa_client import send_text
    from core.observability import log_system_event_sync
    from core.reports.reports_daily import filtrar_por_acesso
    from db import get_whatsapp_updates_opt_out, is_account_scheduled_for_deletion, list_identities_by_user
    from db.bank_lembrete import (
        claim_declaracao_lembretes, list_declaracao_lembretes_devidos, release_declaracao_lembretes)

    # Meia-noite local do dia T-6: `declared_at` antes dela = o dia do gasto é T-7 ou antes.
    limite = datetime.combine(now.date() - timedelta(days=LEMBRETE_DIAS - 1), time.min, tzinfo=_tz())
    por_usuario: dict[int, list[int]] = {}
    for row in list_declaracao_lembretes_devidos(limite, now - _JANELA_WA):
        por_usuario.setdefault(int(row["user_id"]), []).append(int(row["launch_id"]))

    sent = 0
    for uid, ids in por_usuario.items():
        reservadas: list[int] = []
        aceitos = 0
        try:  # um usuário que falha (leitura, reserva, texto, envio, soltura) não derruba os outros
            ok = (bool(filtrar_por_acesso([uid])) and not get_whatsapp_updates_opt_out(uid)
                  and not is_account_scheduled_for_deletion(uid))  # 'scheduled' e 'processing' (D10)
            targets = _dedupe_whatsapp_targets(list_identities_by_user(uid)) if ok else []
            if not targets:
                continue  # barrado: não reserva (D4, D10); o lembrete sai quando voltar
            rows = claim_declaracao_lembretes(uid, ids, limite)
            reservadas = [r["launch_id"] for r in rows]
            if not rows:
                continue
            corpo = _mensagem_declaracoes(rows)
            # ponytail: mesmo bloco de envio do `notify_autopay_notices_whatsapp_once`, copiado de propósito: o portão
            # `test_portao_lacos_proativos` só enxerga laço que enumera e envia na MESMA função (cegueira 2c).
            for to in targets:
                try:
                    if send_text(to, corpo) is None:
                        # `None` é o 401 da Meta, que `send_text` não levanta.
                        print(f"[declaracao_wa] recusado user_id={uid} erro=token_invalido", file=sys.stderr)
                        continue
                    aceitos += 1
                except Exception as exc:
                    # Só o TIPO: `str(exc)` pode ecoar o número (PII).
                    print(f"[declaracao_wa] envio falhou user_id={uid} erro={type(exc).__name__}", file=sys.stderr)
            sent += aceitos
            if aceitos:
                log_system_event_sync(
                    "info", "whatsapp_declaracao_lembrete_sent",
                    "Lembrete de declaração bancária a conferir enviado via texto livre.",
                    source="declaracao_lembrete", user_id=uid,
                    details={"launch_ids": reservadas},  # sem alvo, valor nem telefone
                )
            else:
                release_declaracao_lembretes(uid, reservadas)
        except Exception as exc:
            print(f"[declaracao_wa] falhou user_id={uid} erro={type(exc).__name__}", file=sys.stderr)
            if reservadas and not aceitos:  # nada saiu: tenta devolver a reserva (best effort; se falhar, perde)
                try:
                    release_declaracao_lembretes(uid, reservadas)
                except Exception as exc2:
                    print(f"[declaracao_wa] soltura falhou user_id={uid} erro={type(exc2).__name__}", file=sys.stderr)
    return sent
