"""PR-9b da fatia D (#657, D4): `print` sem `str(exc)` nos helpers de banco e infraestrutura fora do
monólito (sessões, refresh, auditoria, `crypto`, painel admin, `_desfecho`, LISTEN e DDL de boot).

Cada caso confere três coisas: a causa (`motivo=… sqlstate=… status=…`) no fluxo certo (`out` ou
`err`), o texto do erro AUSENTE, e o desfecho de hoje (retorno, `raise`, backoff). Um conserto que
calasse o fluxo passaria só pela ausência. `_erro_real` é um 22P02 de verdade: um erro da classe 23
já chega redigido por `db/connection.py`, e o teste passaria com ou sem o conserto.

`banco_real` NÃO é importado nos casos de print: ele liga o `_DashboardHandler`, que reentra no
`log_system_event_sync` dublado. Só o caso da conversa o usa, porque é ele que se mede ali.

CONTROLES (CLAUDE.md §3): volte o `{exc}` de um ponto e o caso daquele ponto fica vermelho. O
positivo é a coluna do desfecho e o rótulo `teto`.

CLASSE CEGA: a forma da linha no log do Railway só se vê em produção.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import psycopg
import pytest

import core.admin_dashboard as admin
import core.audit as audit
import core.crypto as crypto
import core.refresh_tokens as refresh
import core.sessions as sessions
import core.system_event_log as sel
import db.bank_conferencia
import db.connection
import db.schema as schema
import core.observability as observability
from _log_dono_helpers import _ALHEIO, _erro_real, _marcador
from core.pg_text import causa_segura
from test_api_v2_eventos_escrita import _relogio_falso
from test_of_log_dono_coluna import _levanta
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import lancamentos, liga_flag
from tests._pendencia_credito_helpers import diga

CAUSA = "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"


def _alevanta(exc):
    async def _f(*a, **k):
        raise exc
    return _f


def _sessions(mp, erro):
    mp.setattr(sessions, "get_conn", _levanta(erro))
    assert sessions.get_active_session("jti657") is None
    assert sessions.touch_session("jti657") is None
    assert sessions.list_user_sessions(1) == []
    assert sessions.revoke_session(1, "jti657") is False
    assert sessions.revoke_other_sessions(1, "jti657") == 0


def _refresh(mp, erro):
    mp.setattr(refresh, "get_conn", _levanta(erro))
    assert refresh.revoke_refresh_token(f"{refresh.REFRESH_TOKEN_PREFIX}x657") is False
    assert refresh.revoke_session_refresh_tokens("jti657") == 0
    assert refresh.revoke_user_refresh_tokens(1) == 0


def _audit(mp, erro):
    mp.setattr(audit, "get_conn", _levanta(erro))
    assert audit.list_audit_events(1) == []
    assert audit.record_audit_event(1, audit.AuditEvent.LOGIN_FROM_NEW_IP) is None
    assert audit.is_known_login_ip(1, "203.0.113.7") is True
    mp.setattr(audit, "_count_login_from_new_ip", _levanta(erro))
    assert audit._dispatch_new_login_email(1, "203.0.113.7", "ua") is None


_CTX = crypto.PiiAccessContext(purpose="t657", actor="system", subject_user_id=1, field="email")


def _crypto(mp, erro):
    mp.delenv("PII_AUDIT_DISABLED", raising=False)
    mp.setattr(db.connection, "get_conn", _levanta(erro))  # o import é tardio, dentro do `try`
    assert crypto._record_access(_CTX) is None
    lote = crypto.pii_audit_batch()
    lote.__enter__()
    crypto._record_access(_CTX)
    assert lote.__exit__(None, None, None) is False
    assert crypto._audit_buffer.entries is None, "o buffer tem de zerar mesmo com o flush falhando"


def _admin(mp, erro):
    mp.setattr(admin, "db_connect", _alevanta(erro))
    mp.setattr(admin.psycopg.AsyncConnection, "connect", _alevanta(erro))
    mp.delenv("WA_TOKEN", raising=False)
    mp.delenv("WA_ACCESS_TOKEN", raising=False)  # para haver aviso a gravar
    assert asyncio.run(admin.log_auth_login_event(email="a@b.c", success=True, user_id=1)) is None
    assert asyncio.run(admin.log_system_event("info", "t657", "m")) is None
    assert asyncio.run(admin.log_admin_startup_warnings()) is None


def _desfecho(mp, erro):
    mp.setattr(sel, "psycopg", SimpleNamespace(connect=_levanta(erro), errors=psycopg.errors))
    assert sel.log_system_event_sync("info", "t657", "m") is None
    assert sel.recent_event_exists("t657", 1) is False


@pytest.mark.parametrize("roda, prefixos, fluxo", [
    (_sessions, ["[sessions] get_active_session failed: ", "[sessions] touch_session failed: ",
                 "[sessions] list_user_sessions failed: ", "[sessions] revoke_session failed: ",
                 "[sessions] revoke_other_sessions failed: "], "err"),
    (_refresh, ["[refresh] revoke falhou: ", "[refresh] revoke_session falhou: ",
                "[refresh] revoke_user falhou: "], "err"),
    (_audit, ["[audit] list_audit_events failed for user 1: ",
              "[audit] failed to record login_from_new_ip for user 1: ",
              "[audit] is_known_login_ip query failed: ",
              "[audit] new-login email dispatch failed for user 1: "], "err"),
    (_admin, ["[admin] failed to record auth login event: ", "[admin] failed to record system event: ",
              "[admin] failed to record startup warnings: "], "err"),
    (_desfecho, ["[observability] failed to record t657: defeito ",
                 "[observability] failed to check t657: defeito "], "err"),
], ids=["sessions", "refresh", "audit", "admin", "desfecho"])
def test_print_de_falha_leva_a_causa_e_nao_o_texto(monkeypatch, capsys, roda, prefixos, fluxo):
    erro = _erro_real()  # antes de dublar o `get_conn`: ele usa o banco
    assert str(_ALHEIO) in str(erro), "pré-condição: o texto cru traz o dado do usuário"
    roda(monkeypatch, erro)
    saida = capsys.readouterr()
    texto = getattr(saida, fluxo)
    for prefixo in prefixos:
        assert f"{prefixo}{CAUSA}" in texto, (prefixo, texto)
    assert str(_ALHEIO) not in saida.out + saida.err, saida


def test_crypto_imprime_a_mesma_causa_sem_import(monkeypatch, capsys):
    """`core/crypto.py` está no teto de 350 linhas e escreve a causa à mão. §0.7: a linha tem de ser
    a de `causa_segura` sem o `status` (o `try` só alcança o banco)."""
    erro = _erro_real()
    _crypto(monkeypatch, erro)
    saida = capsys.readouterr()
    sem_status = causa_segura(erro).rsplit(" status=", 1)[0]
    assert f"[crypto] failed to record pii access: {sem_status}\n" in saida.err, saida.err
    assert f"[crypto] failed to batch record pii access: {sem_status}\n" in saida.err, saida.err
    assert str(_ALHEIO) not in saida.out + saida.err, saida


def test_desfecho_na_conversa_passa_pelo_handler_real(uid_pro, monkeypatch, capsys):
    """Duas mensagens pelo `handle_incoming` com a IA-primeiro levantando: o `logger.warning` da
    5a passa pelo `_DashboardHandler` real e o `wa_ia_primeiro_fallback` é gravado direto, os dois
    pelo `log_system_event_sync`, que falha e imprime pelo `_desfecho`. O roteador atende."""
    import core.services.ai_chat as ai_chat
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler) for h in observability.logging.getLogger().handlers)
    marcador, erro = _marcador(), _erro_real()
    liga_flag(monkeypatch)
    chamadas = []

    def _ia(user_id, text, **kw):
        chamadas.append(text)
        raise RuntimeError(f"{marcador} {_ALHEIO}")

    monkeypatch.setattr(ai_chat, "chat", _ia)
    monkeypatch.setattr(sel, "psycopg", SimpleNamespace(connect=_levanta(erro), errors=psycopg.errors))

    assert diga(uid_pro, "gastei 50 no mercado")
    assert diga(uid_pro, f"qto sobrou esse mes {marcador}")
    assert len(chamadas) == 2, chamadas
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]
    saida = capsys.readouterr()
    for evento in ("logger.warning", "wa_ia_primeiro_fallback"):
        assert saida.err.count(f"[observability] failed to record {evento}: defeito {CAUSA}\n") == 2, saida.err
    assert str(_ALHEIO) not in saida.out + saida.err and marcador not in saida.out + saida.err


def test_desfecho_mantem_o_rotulo_do_teto():
    assert sel._desfecho(psycopg.errors.QueryCanceled("x")) == "teto motivo=QueryCanceled sqlstate=57014 status=None"
    assert sel._desfecho(ValueError("x")) == "defeito motivo=ValueError sqlstate=None status=None"


def test_listen_caido_imprime_a_causa_e_mantem_o_backoff(monkeypatch, capsys):
    from api.v2 import eventos
    erro = _erro_real()
    esperas = []
    _relogio_falso(monkeypatch, esperas, ate=250)
    monkeypatch.setattr(eventos.psycopg.AsyncConnection, "connect", _alevanta(erro))
    monkeypatch.setattr(eventos, "RELOG_CAIDO_S", 100.0)
    with pytest.raises(asyncio.CancelledError):
        eventos.escutar_banco().send(None)
    assert esperas == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
    saida = capsys.readouterr()
    assert saida.err.splitlines() == [
        f"[eventos] LISTEN pb_escrita caiu: {CAUSA}",
        f"[eventos] LISTEN pb_escrita segue caído há 123 s: {CAUSA}",
        f"[eventos] LISTEN pb_escrita segue caído há 243 s: {CAUSA}",
    ]
    assert str(_ALHEIO) not in saida.out + saida.err


def _lock_solto(conn) -> bool:
    """O lock é de SESSÃO e reentrante: tentar de novo na mesma conexão passaria preso. Conta em
    `pg_locks` pelo pid, sem corrida com outro worker."""
    return conn.execute("select count(*) from pg_locks where locktype = 'advisory'"
                        " and pid = pg_backend_pid()").fetchone()[0] == 0


def test_ddl_que_falha_imprime_a_causa_e_relanca(capsys):
    stmt = "select current_setting('application_name')::int"
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, application_name=f"x{_ALHEIO}") as conn:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation) as caught:
            schema._run_ddl(conn, [stmt])
        assert str(_ALHEIO) in str(caught.value), "pré-condição: o texto cru traz o valor"
        assert _lock_solto(conn), "o `finally` tem de soltar o lock"
    saida = capsys.readouterr()
    assert saida.out.splitlines()[:2] == [f"[init_db] erro no statement #1: {CAUSA}", stmt], saida.out
    assert str(_ALHEIO) not in saida.out + saida.err


def test_reparo_que_falha_imprime_a_causa_e_relanca(monkeypatch, capsys):
    erro = _erro_real()
    monkeypatch.setattr(db.bank_conferencia, "migrate_legacy_bank_movements", lambda cur: None)
    monkeypatch.setattr(schema, "ensure_plan_trials_user_fk", _levanta(erro))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            schema._run_ddl(conn, [])
        assert _lock_solto(conn), "o `finally` tem de soltar o lock"
    saida = capsys.readouterr()
    assert f"[init_db] schema_repairs falhou: {CAUSA}\n" in saida.out, saida.out
    assert str(_ALHEIO) not in saida.out + saida.err
