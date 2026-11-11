"""PR-9c2 da fatia D (#657, D4): as duas fontes de texto cru que a guarda não vê, porque o `str(exc)`
vai num dict antes do print.

1. `request_pluggy_refresh` põe a falha do PATCH em `failures`, que o tique do monólito imprime e
   grava em `details` de `pluggy_refresh_patch_failed` (painel admin). Passa a `_falha_da_pluggy`.
2. `excluir_conta_marcada` devolve a falha da exclusão em `error`, que o cron e o worker imprimem.
   Passa a `causa_segura`, menos na sobra depois da limpeza (`ExclusaoIncompleta`), cuja mensagem é
   nossa (uid e contagens) e é o diagnóstico do operador.

Pelo caminho real: o tique `_open_finance_refresh` e o cron `scripts/account_deletion_job.run`. A
sobra sai do `delete_user_data` de verdade, com um gatilho que impede o `delete` só da conta do caso.

CONTROLES (CLAUDE.md §3), medidos no PR, e obrigatórios aqui (a guarda não vê estas fontes):
`str(exc)[:200]` de volta em `pluggy_sync` → os 2 casos do tique vermelhos; `str(falha_local)` de
volta em `privacy` → a falha comum vermelha; `RuntimeError` no lugar de `ExclusaoIncompleta` no
`raise` de dentro da transação ou no da verificação pós-commit → o caso daquele `raise` vermelho
(sai `motivo=RuntimeError`).

Exceção de texto vazio (`TimeoutError()`) dava `"error": ""`, e o cron não a contava (`rc=0`). Com a
causa, que nunca é vazia, ela conta: `rc=1`, `errors == 1`. Mudança de desfecho aceita (A1, rodada 2).
CLASSE CEGA: a forma da linha no log do Railway só se vê em produção.
"""
from __future__ import annotations

import pytest

import core.services.pluggy_sync as ps
import db.privacy as privacy
from _log_dono_helpers import _ALHEIO, _erro_real, _vazou
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_account_deletion_pii_logs import _existe_usuario, _novos, _semeia, marca, tabelas_admin  # noqa: F401
from test_of_log_dono_coluna import _levanta
from test_of_log_stderr import _uma_volta_do_tick
from test_of_refresh_schedule import _conexao

CAUSA = "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"


# ── 1. failures do refresh periódico ────────────────────────────────────────

@pytest.mark.parametrize("erro, esperado", [
    (None, f"{CAUSA} code=None"),
    (PluggyApiError("PATCH /items recusado", status_code=503),
     "motivo=PluggyApiError sqlstate=None status=503 code=None texto=PATCH /items recusado"),
], ids=["banco", "pluggy"])
def test_failures_do_tick_levam_a_causa_e_o_evento_continua(user_id, monkeypatch, capsys, erro, esperado):
    erro = erro or _erro_real()
    item = f"i9c2-{user_id}"
    _conexao(user_id, item)
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    monkeypatch.setenv("OF_REFRESH_JITTER_PCT", "0")
    monkeypatch.setattr(ps, "update_pluggy_item", _levanta(erro))
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: {})
    real = ps.request_pluggy_refresh
    monkeypatch.setattr(ps, "request_pluggy_refresh", lambda **kw: real(**kw, user_id=user_id))
    eventos = []

    async def _grava(*a, **kw):
        eventos.append((a, kw))
    _uma_volta_do_tick(monkeypatch, tiques=2, log=_grava)

    saida = capsys.readouterr().out
    falha = {"item_id": item, "reason": type(erro).__name__, "error": esperado}
    assert f"[open_finance_refresh] triggered=0 total=1 origin=periodic failures={[falha]}" in saida, saida
    assert not (v := _vazou(saida, str(_ALHEIO))), f"{v!r} vazou: {saida}"
    gravados = [kw["details"] for a, kw in eventos if a[1] == "pluggy_refresh_patch_failed"]
    assert gravados == [falha], eventos


# ── 2. exclusão de conta ─────────────────────────────────────────────────────

def _agendamento(uid: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("select deletion_status from auth_accounts where user_id = %s", (uid,)).fetchone()
        conn.commit()
    return row and row["deletion_status"]


@pytest.mark.parametrize("erro, causa", [
    (None, CAUSA),
    (TimeoutError(), "motivo=TimeoutError sqlstate=None status=None"),   # texto vazio: antes, `rc=0`
], ids=["banco", "texto_vazio"])
def test_exclusao_com_falha_comum_imprime_a_causa_e_reagenda(user_id, monkeypatch, capsys, marca, erro, causa):  # noqa: F811
    from scripts.account_deletion_job import run
    _semeia(user_id)
    monkeypatch.setattr(privacy, "delete_user_data", _levanta(erro or _erro_real()))
    assert run(limit=10) == 1
    saida = capsys.readouterr().out
    assert f"{{'user_id': {user_id}, 'error': '{causa}'}}" in saida, saida
    assert not (v := _vazou(saida, str(_ALHEIO))), f"{v!r} vazou: {saida}"
    assert _agendamento(user_id) == "scheduled"
    resumo = _novos(marca, "account_deletion_job")
    assert len(resumo) == 1 and resumo[0]["details"]["errors"] == 1, resumo


def test_exclusao_com_sobra_mantem_a_mensagem_do_operador(user_id, capsys, marca):  # noqa: F811
    """Gatilho `before delete on users` que devolve NULL só para o uid do caso: o `delete_user_data`
    real chega à conferência de dentro da transação e levanta `ExclusaoIncompleta`."""
    from scripts.account_deletion_job import run
    _semeia(user_id)
    fn = f"t9c2_sobra_{user_id}"
    with get_conn() as conn:
        conn.execute(f"""
            create function {fn}() returns trigger language plpgsql as $$
            begin if old.id = {int(user_id)} then return null; end if; return old; end $$;
            create trigger {fn} before delete on users for each row execute function {fn}()""")
        conn.commit()
    try:
        assert run(limit=10) == 1
    finally:
        with get_conn() as conn:
            conn.execute(f"drop function {fn}() cascade")
            conn.commit()
    saida = capsys.readouterr().out
    msg = f"Falha ao remover usuário {user_id}: registro ainda existe após a limpeza final."
    assert f"{{'user_id': {user_id}, 'error': '{msg}'}}" in saida, saida
    assert _existe_usuario(user_id), "a transação tinha de voltar"
    assert _agendamento(user_id) == "scheduled"


def test_exclusao_com_sobra_depois_do_commit_mantem_a_mensagem_do_operador(user_id, capsys, marca):  # noqa: F811
    """Gatilho `before delete on daily_report_prefs` que devolve NULL só para o uid do caso: `users`
    sai, o commit passa, e a verificação pós-commit acha a linha e levanta `ExclusaoIncompleta`."""
    from scripts.account_deletion_job import run
    _semeia(user_id)
    fn = f"t9c2_pos_{user_id}"
    with get_conn() as conn:
        conn.execute("insert into daily_report_prefs (user_id) values (%s)", (user_id,))
        conn.execute(f"""
            create function {fn}() returns trigger language plpgsql as $$
            begin if old.user_id = {int(user_id)} then return null; end if; return old; end $$;
            create trigger {fn} before delete on daily_report_prefs for each row execute function {fn}()""")
        conn.commit()
    try:
        assert run(limit=10) == 1
    finally:
        with get_conn() as conn:
            conn.execute(f"drop function {fn}() cascade")
            conn.execute("delete from daily_report_prefs where user_id = %s", (user_id,))
            conn.commit()
    saida = capsys.readouterr().out
    msg = (f"Falha ao confirmar exclusão do usuário {user_id}: "
           "user_exists=False; leftovers={'daily_report_prefs': 1}")
    assert f"{{'user_id': {user_id}, 'error': \"{msg}\"}}" in saida, saida
    assert not _existe_usuario(user_id), "PREMISSA: o commit passou, a conta saiu"
