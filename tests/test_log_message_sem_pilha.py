"""#1331 (#657, grupo D): a `message` que o `_DashboardHandler` grava em `system_event_logs` é
`record.getMessage()`, sem a pilha. A pilha mora só em `details.traceback`: o admin a mostra e a
exportação LGPD a omite (A9). O stderr continua com ela. O `/auth/google/callback` grava só tipo e
sqlstate, sem pilha em lugar nenhum.

T1 ponta a ponta (Postgres e handler reais, `exc_info` e o caminho do `exc_text` do filtro);
T2 unidade (mensagem inteira, corte em 1000, stderr com a pilha); T3 a porta real com dono e pilha
(`DELETE /installments`); T4 o callback do Google.

CONTROLES (CLAUDE.md §3), medidos no PR: `message = self.format(record)` de volta → T1, T2c e T3
vermelhos; a chave `traceback` renomeada → T1 vermelho; a linha antiga do Google → T4 vermelho;
o `emit` zerando `exc_info`/`exc_text` do record → T2c `dashboard_stream` vermelho; o `emit`
anexando `record.exc_text` à `message` → T2c `stream_dashboard` vermelho. Positivos: T1 e T2a (a
mensagem inteira e formatada), T2b (o corte em 1000), T2c (o stderr com a pilha).
"""
from __future__ import annotations

import io
import logging
import zipfile

import pytest
from fastapi.testclient import TestClient

import core.observability as observability
import db
import db.cards as cards_mod
import db.privacy as privacy
import frontend.finance_bot_websocket_custom as dashboard
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    FONE, _exportados, _isola_e_apaga, _levanta, _linhas, _marca, _marcador, _uid, _violacao,
    _vazou, banco_real)

MSG = "teste_1331 falha controlada x"
PILHA = ("Traceback", 'File "')


def _sem_pilha(texto: str) -> None:
    for p in PILHA:
        assert p not in texto, f"{p!r} em {texto[:300]!r}"


def _zip_inteiro(uid: int) -> str:
    """JSON e CSV: a pilha não pode sair em nenhum dos dois."""
    with zipfile.ZipFile(io.BytesIO(privacy.build_user_export_zip(uid))) as zf:
        return "".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist())


def _levanta_forma(forma: str, m: str):
    if forma == "exc_info":
        raise _violacao(m)
    raise RuntimeError(f"new row violates check constraint\nDETAIL:  Failing row contains ({m}, 1e20).")


FORMAS = pytest.mark.parametrize("forma", ["exc_info", "failing_row"])


def _loga(forma: str, m: str, uid: int | None, lg: logging.Logger | None = None) -> None:
    try:
        _levanta_forma(forma, m)
    except Exception:  # noqa: BLE001
        (lg or logging.getLogger("teste.1331")).exception(
            "teste_1331 falha controlada %s", "x", extra={"user_id": uid} if uid else None)


# ── T1 ponta a ponta ─────────────────────────────────────────────────────────

@FORMAS
def test_t1_message_sem_pilha_no_banco_e_no_zip(forma, caplog):
    a, b, mark_a, mark_b = _uid(), _uid(), _marcador(), _marcador()
    marca = _marca()
    with caplog.at_level(logging.WARNING):
        _loga(forma, mark_a, a)
        _loga(forma, mark_b, b)
    if forma == "failing_row":  # premissa: o filtro zerou o exc_info e o emit foi pelo `elif exc_text`
        rec = [r for r in caplog.records if r.name == "teste.1331"][0]
        assert rec.exc_info is None and "Traceback" in rec.exc_text, rec.__dict__

    def _linha(uid):
        achadas = _linhas(marca, "user_id = %s and source = 'teste.1331'", (uid,))
        assert len(achadas) == 1, achadas
        return achadas[0]

    linha_a, linha_b = _linha(a), _linha(b)
    assert linha_a["message"] == MSG, linha_a       # positivo: inteira e formatada
    _sem_pilha(linha_a["message"])
    tb = linha_a["details"]["traceback"]
    assert all(p in tb for p in PILHA), linha_a["details"]  # a pilha ficou para o admin, na chave de sempre
    assert linha_a["id"] in _exportados(a)[1], "sem a linha no ZIP, a busca abaixo seria vazia"
    texto = _zip_inteiro(a)
    _sem_pilha(texto)
    assert not (p := _vazou(texto, mark_a)), f"{p!r} vazou no ZIP de A"
    _isola_e_apaga(a, b, {linha_a["id"]}, linha_b["id"], marca)


# ── T2 unidade ───────────────────────────────────────────────────────────────

@pytest.fixture
def gravados(monkeypatch):
    lista: list[dict] = []
    monkeypatch.setattr(observability, "log_system_event_sync", lambda level, **kw: lista.append(kw))
    return lista


def _logger_isolado(*handlers: logging.Handler) -> logging.Logger:
    lg = logging.Logger("t1331", logging.INFO)  # fora do manager: só os handlers abaixo
    for h in handlers:
        h.addFilter(observability._sem_failing_row)
        lg.addHandler(h)
    return lg


def test_t2a_mensagem_inteira_sem_excecao(gravados):
    lg = _logger_isolado(observability._DashboardHandler())
    lg.warning("olá %s", 7)
    lg.warning("100%")
    lg.warning("a\nb")  # várias linhas: um corte na 1ª quebra também tiraria a pilha
    assert [g["message"] for g in gravados] == ["olá 7", "100%", "a\nb"], gravados
    assert gravados[0]["details"] == {"logger": "t1331"}, gravados[0]


@pytest.mark.parametrize("com_exc", [False, True])
def test_t2b_corte_em_1000(gravados, com_exc):
    texto = "".join(f"{i:04d}" for i in range(375))  # 1500 caracteres, nenhum repetido em posição
    lg = _logger_isolado(observability._DashboardHandler())
    try:
        raise ValueError("boom")
    except ValueError:
        lg.error(texto, exc_info=com_exc)
    assert gravados[0]["message"] == texto[:1000], len(gravados[0]["message"])


@FORMAS
@pytest.mark.parametrize("stream_primeiro", [False, True], ids=["dashboard_stream", "stream_dashboard"])
def test_t2c_stderr_continua_com_a_pilha(gravados, forma, stream_primeiro):
    """As duas ordens medem coisas diferentes. Dashboard primeiro: um `emit` que limpe
    `exc_info`/`exc_text` do record tira a pilha do stderr. Stream primeiro, a ordem de produção:
    o `Formatter` do stderr já deixou o `exc_text` no record, e um `emit` que o use põe a pilha na
    `message`."""
    stream = io.StringIO()
    stderr = logging.StreamHandler(stream)
    stderr.setFormatter(logging.Formatter(observability._LOG_FORMAT))
    dash = observability._DashboardHandler()
    _loga(forma, _marcador(), None, _logger_isolado(*((stderr, dash) if stream_primeiro else (dash, stderr))))
    saida = stream.getvalue()
    assert all(p in saida for p in PILHA), saida
    assert gravados[0]["message"] == MSG, gravados[0]
    assert all(p in gravados[0]["details"]["traceback"] for p in PILHA), gravados[0]


# ── T3 a porta real com dono e pilha ─────────────────────────────────────────

def test_t3_delete_installment_grava_a_pilha_so_em_details(monkeypatch):
    a, m = _uid(), _marcador()
    monkeypatch.setattr(cards_mod, "undo_installment_group", _levanta(m))
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(a, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(a, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    marca = _marca()
    resp = client.delete(f"/installments/{a}/PC12345678",
                         headers={dashboard.CSRF_HEADER_NAME: "test-csrf-token"})
    assert resp.status_code == 500 and "Não consegui excluir o parcelamento" in resp.text, resp.text

    achadas = _linhas(marca, "user_id = %s and message like 'undo_installment_group: falha%%'", (a,))
    assert len(achadas) == 1, achadas
    linha = achadas[0]
    assert linha["message"].endswith("causa=UniqueViolation sqlstate=23505"), linha
    _sem_pilha(linha["message"])
    assert 'File "' in linha["details"]["traceback"], linha["details"]
    texto = _zip_inteiro(a)
    _sem_pilha(texto)
    assert not (p := _vazou(texto, m)), f"{p!r} vazou no ZIP"
    assert FONE not in texto


# ── T4 /auth/google/callback ─────────────────────────────────────────────────

def test_t4_google_callback_grava_so_tipo_e_sqlstate(monkeypatch, caplog):
    import core.services.google_oauth as google_oauth

    async def exchange(_code):
        return {"id_token": "token-test"}

    m = _marcador()
    monkeypatch.setattr(google_oauth, "exchange_code_for_tokens", exchange)
    monkeypatch.setattr(google_oauth, "verify_id_token", lambda _t: {
        "sub": "google-sub-1331", "email": "g1331@test.local", "email_verified": True})
    monkeypatch.setattr(db, "find_user_by_google_sub", _levanta(m))
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.GOOGLE_OAUTH_STATE_COOKIE, "state-valido")
    marca = _marca()
    with caplog.at_level(logging.WARNING):
        resp = client.get("/auth/google/callback?code=c&state=state-valido", follow_redirects=False)

    assert resp.status_code == 302, resp.text           # positivo: o desfecho de hoje
    assert "google_error=" in resp.headers["location"] and "UniqueViolation" in resp.headers["location"]

    recs = [r for r in caplog.records if r.name == "auth.google"]
    assert len(recs) == 1, recs
    assert recs[0].getMessage() == \
        "Falha inesperada no /auth/google/callback: causa=UniqueViolation sqlstate=23505", recs[0].getMessage()
    assert recs[0].exc_info is None

    achadas = _linhas(marca, "source = 'auth.google' and user_id is null")
    assert len(achadas) == 1, achadas
    _sem_pilha(achadas[0]["message"])
    assert not _vazou(achadas[0]["message"], m), achadas[0]
    assert "traceback" not in achadas[0]["details"], achadas[0]
