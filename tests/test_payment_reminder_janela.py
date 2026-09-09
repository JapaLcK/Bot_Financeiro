"""
tests/test_payment_reminder_janela.py — a LARGURA da janela do lembrete de
pagamento, e o teto que ela tem.

`db.dunning.list_payment_reminder_candidates` aceita a conta enquanto a idade de
`past_due_since` estiver em `[6d, 6d + PAYMENT_REMINDER_WINDOW_DAYS)`. A largura
era de 1 dia exato, e a cadência real do tick é MAIOR que 24 h (o `asyncio.sleep`
de `run_engagement_loop` só começa depois de todo o trabalho de engajamento,
trial, nudge e lembrete), sem contar restart e falha operacional: um deploy no
meio do tick sumia com o único lembrete do ciclo. Os dois lados do conserto:

  • largura suficiente para sobreviver a um tick INTEIRO perdido;
  • largura menor que a dedupe, senão o mesmo ciclo recebe DOIS lembretes.

As fronteiras (nos dois lados) estão em `test_janela` de
`tests/test_payment_reminder.py`, e a relação entre os dois números em
`test_invariante_janela_menor_que_dedupe` de `tests/test_billing_dunning.py`.
Arquivo próprio porque o `test_payment_reminder.py` está perto do teto de 350
linhas de `tests/test_max_lines_python.py`; os helpers vêm por IMPORT dele —
uma fonte só (§0.7).

CONTROLES NEGATIVOS DECLARADOS:

Regra dos controles: predicado citado, só os VERMELHOS nomeados, sem `N
passed` — ver `docs/controles_declarados.md`.

  • largura — troque `inicio + PAYMENT_REMINDER_WINDOW_DAYS` por `inicio + 1`
    em `db/dunning.py::list_payment_reminder_candidates`:
      VERMELHO: test_tick_inteiro_perdido_ainda_entrega (e o caso [8.9] do
                `test_janela` do arquivo irmão).
  • dedupe — `PAYMENT_REMINDER_DEDUPE_DAYS = 2.0` (menor que os 2,8 dias de
    travessia da janela deste teste):
      VERMELHO: test_dois_ticks_na_janela_larga_mandam_um_so e
                test_invariante_janela_menor_que_dedupe.

As duas injeções são DISJUNTAS — a da largura não reprova o teste da dedupe e
vice-versa —, e é isso que prova que os dois números são independentes.

CONTROLE POSITIVO: `test_tick_inteiro_perdido_ainda_entrega` é ele mesmo o
positivo do par — a largura maior tem de fazer o e-mail SAIR, não só deixar de
sair duas vezes. Sem ele, `PAYMENT_REMINDER_WINDOW_DAYS = 0` (janela vazia,
ninguém recebe nada) passaria no teste da dedupe.
"""
from __future__ import annotations

import pytest

from db.connection import get_conn
from test_payment_reminder import (
    _espia,
    _inadimplente,
    _limpar_eventos,
    _tick,
    garantir_system_event_logs,
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Mesmo ambiente do arquivo irmão: a flag nasce DESLIGADA em produção e é
    ligada aqui para que o funil rode; o template de WhatsApp fica opt-in."""
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    garantir_system_event_logs()


def _envelhecer_evento(uid: int, dias: float) -> None:
    """Recua o `created_at` do `payment_reminder_sent` — o tempo de CALENDÁRIO
    que passou entre dois ticks. Sem isto a dedupe é medida contra `now()`, e
    qualquer valor dela suprime: foi assim que a 1ª versão do teste abaixo ficou
    verde com `PAYMENT_REMINDER_DEDUPE_DAYS = 1.0` (medido)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update system_event_logs"
                "   set created_at = created_at - make_interval(secs => %s)"
                " where event_type = 'payment_reminder_sent' and user_id = %s",
                (dias * 86400, uid))
        conn.commit()


def test_tick_inteiro_perdido_ainda_entrega(user_id, monkeypatch):
    """Um tick INTEIRO perdido (deploy, restart, falha operacional) não come o
    único lembrete do ciclo.

    A conta só é OBSERVADA no 8,5º dia de atraso: o tick que devia tê-la visto
    no 6º não rodou. Com a janela de 24 h de antes ela já tinha saído, e o
    lembrete daquele ciclo não saía nunca.
    """
    _inadimplente(user_id, dias=8.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert len(enviados) == 1, enviados


def test_dois_ticks_na_janela_larga_mandam_um_so(user_id, monkeypatch):
    """A janela larga não manda DOIS lembretes no mesmo ciclo: quem cala o
    segundo é a dedupe, e é o que a invariante
    `PAYMENT_REMINDER_WINDOW_DAYS < PAYMENT_REMINDER_DEDUPE_DAYS` garante.

    Dois ticks em DIAS diferentes da mesma janela (6,1 → 8,9), com o evento de
    dedupe envelhecido os mesmos 2,8 dias. O
    `test_dedupe_nao_reenvia_no_mesmo_ciclo` do arquivo irmão roda os dois ticks
    com a MESMA idade e por isso não mede a largura.
    """
    _inadimplente(user_id, dias=6.1)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert len(enviados) == 1, enviados

    _inadimplente(user_id, dias=8.9)
    _envelhecer_evento(user_id, dias=2.8)
    _tick()
    assert len(enviados) == 1, ("dois lembretes no mesmo ciclo", enviados)
