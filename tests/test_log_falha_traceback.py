"""Quem leva traceback pro `system_event_logs` — e quem não pode levar.

O `_DashboardHandler` (`core/observability.py`) PERSISTE duas coisas de cada
`warning()`/`error()`: `self.format(record)`, que anexa o traceback à
`message`, e `details["traceback"]`. Um erro do psycopg carrega
`DETAIL: Key (…)=(…)` com valor e descrição da linha — então mandar `exc_info`
numa porta de conversa grava dado financeiro do usuário no log, mesmo com a
mensagem formatada omitindo `str(e)`.

Por isso o traceback é decisão do CALL SITE (`com_traceback=`, default False),
não do nível — e o critério NÃO é "é rota HTTP", é o que a `main` gravava
NAQUELA rota. O `admin_error_logging_middleware` faz `except HTTPException:
raise`, então só via as rotas que deixavam a exceção subir crua. Medido nas
duas formas da `main`:

    frontend/routes/cards.py       (main: SEM try/except)     -> gravava
                                   http_unhandled_exception + traceback
    .../finance_bot_websocket_custom.py
                                   (main 77d3d4e:5450,5536: HTTPException(500))
                                                              -> gravava NADA

  - PORTA DE CONVERSA (WhatsApp, /ai/chat, crédito) → sem. Nenhum middleware
    gravava pilha ali; ligar não restaura nada, cria persistência nova.
  - `/launches` e `/credit-transactions` do dashboard → sem, pelo MESMO motivo:
    a `main` já levantava `HTTPException` e o middleware nunca as viu.
  - As duas rotas de `frontend/routes/cards.py`, ramo técnico → com. Ali, e só
    ali, o middleware gravava a mesma pilha no mesmo campo, e o
    `HTTPException(500)` desta versão a esconderia.

O `LogRecord` sozinho não prova nada: o teste roda o `_DashboardHandler` de
verdade e olha o que chegaria à tabela. O controle POSITIVO é a rota HTTP no
fim — sem ele, uma verificação que nunca encontra traceback passaria verde num
handler quebrado.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.observability as observability
from core.handlers import pending as h_pending
from core.services.ai_chat.tools import get_tool

_SEGREDO = 'DETAIL: Key (id)=(42) mercadinho segredo'


def _linhas(caplog, prefixo: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith(prefixo)]


def _system_event_logs(monkeypatch, records: list[logging.LogRecord]) -> list[dict]:
    """O que essas linhas gravariam em `system_event_logs`, pelo handler real."""
    gravados: list[dict] = []
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda level, **kw: gravados.append({"level": level, **kw}))
    handler = observability._DashboardHandler()
    for r in records:
        handler.emit(r)
    return gravados


def test_whatsapp_nao_persiste_o_detail_do_psycopg(user_id, monkeypatch, caplog):
    """Porta de conversa: 'apaga #9' com falha técnica. Nem `message` nem
    `details.traceback` podem carregar o `DETAIL` do driver."""
    def explode(uid, launch_id):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(db, "delete_launch_and_rollback", explode)
    db.set_pending_action(user_id, "delete_launch",
                          {"launch_id": 4242, "display_id": 9})

    with caplog.at_level(logging.WARNING):
        resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "segredo" not in resp and "DETAIL" not in resp, resp

    linhas = _linhas(caplog, "delete_launch: falha")
    assert len(linhas) == 1 and linhas[0].levelname == "ERROR", \
        [(r.levelname, r.getMessage()) for r in caplog.records]
    assert linhas[0].exc_info is None, "porta de conversa não manda exc_info"

    gravados = _system_event_logs(monkeypatch, linhas)
    assert len(gravados) == 1, gravados
    assert "traceback" not in gravados[0]["details"], gravados[0]["details"]
    assert "segredo" not in json.dumps(gravados, ensure_ascii=False), gravados
    # o que SOBRA é o diagnóstico que a main já tinha:
    assert f"user_id={user_id}" in gravados[0]["message"]
    assert "causa=RuntimeError" in gravados[0]["message"]


def test_ai_chat_apagar_tudo_nao_persiste_o_detail_do_psycopg(user_id, monkeypatch, caplog):
    """A mesma prova no delete em MASSA do /ai/chat
    (`core/services/ai_chat/tools/launches.py:537`)."""
    def explode(uid):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(db, "delete_all_launches_and_rollback", explode)

    with caplog.at_level(logging.WARNING):
        resp = get_tool("delete_all_launches").execute(user_id, {})

    assert "segredo" not in resp and "DETAIL" not in resp, resp

    linhas = _linhas(caplog, "delete_all_launches: falha")
    assert len(linhas) == 1 and linhas[0].exc_info is None, linhas

    gravados = _system_event_logs(monkeypatch, linhas)
    assert "traceback" not in gravados[0]["details"], gravados[0]["details"]
    assert "segredo" not in json.dumps(gravados, ensure_ascii=False), gravados


# ── a guarda: quem liga o traceback, por `ast` ───────────────────────────────
#
# Allowlist ESTRUTURAL (arquivo, função que contém a chamada), no padrão do
# `tests/test_pending_registry.py`. A versão anterior era allowlist por NOME DE
# ARQUIVO — listava as três portas de conversa conhecidas e checava
# `"com_traceback" not in texto`. Uma porta NOVA (`core/handlers/qualquer.py`)
# com `com_traceback=True` passava verde, porque não estava na lista.
_ALLOWLIST_TRACEBACK = {
    ("frontend/routes/cards.py", "delete_card_route"),
    ("frontend/routes/cards.py", "installment_delete_route"),
}
_RAIZ = Path(__file__).resolve().parent.parent
_IGNORADOS = {".venv", ".claude", ".git", "node_modules", "tests"}
# `tests/` fora: este arquivo cita `com_traceback` no texto da guarda, e os
# controles negativos ligam o parâmetro de propósito.


def _nome(no: ast.AST) -> str | None:
    """Último identificador de `x` ou `a.b.x` — `_log_falha` e
    `observability._log_falha` dão o mesmo nome."""
    return no.attr if isinstance(no, ast.Attribute) else getattr(no, "id", None)


def _apelidos(arvore: ast.Module) -> set[str]:
    """`_log_falha`, os `from … import _log_falha as _lf` E os rebinds por
    ATRIBUIÇÃO (`_lf = _log_falha`, `log = observability._log_falha`).

    A atribuição é a MESMA CLASSE do apelido de import — colher só o `asname`
    do `ImportFrom` fechava a instância e deixava a irmã aberta. Ponto fixo,
    porque `ast.walk` é BFS e não ordem de código: `a = _log_falha` seguido de
    `b = a` só fecha na segunda passada."""
    nomes = {"_log_falha"} | {
        a.asname for no in ast.walk(arvore) if isinstance(no, ast.ImportFrom)
        for a in no.names if a.name == "_log_falha" and a.asname
    }
    atribuicoes = [
        (alvo.id, no.value) for no in ast.walk(arvore) if isinstance(no, ast.Assign)
        for alvo in no.targets if isinstance(alvo, ast.Name)
    ]
    while True:
        novos = {n for n, valor in atribuicoes if _nome(valor) in nomes} - nomes
        if not novos:
            return nomes
        nomes |= novos


def _e_log_falha(no: ast.Call, apelidos: set[str]) -> bool:
    """`_log_falha(...)` direto OU `asyncio.to_thread(_log_falha, ...)`.

    As 4 rotas destrutivas são async e fazem o offload pra thread; sem este
    segundo caso a varredura mediria ZERO e a guarda ficaria tautológica.
    Nas duas formas vale o nome qualificado (`observability._log_falha`) e o
    apelido do import.
    """
    if _nome(no.func) in apelidos:
        return True
    return (_nome(no.func) == "to_thread" and bool(no.args)
            and _nome(no.args[0]) in apelidos)


def _chamadas_com_traceback(fonte: str, rel: str) -> list[tuple[str, str]]:
    """(arquivo, função envolvente) de cada `_log_falha(..., com_traceback=…)`
    que NÃO é literal `False`.

    Não-literal (`com_traceback=flag`) também entra: se a guarda não consegue
    conferir o valor, ela reprova em vez de deixar passar.

    CEGUEIRAS CONHECIDAS — a lista NÃO é prova de completude, é o que foi
    medido. A varredura só enxerga a chamada escrita no lugar; o parâmetro
    escondido em estrutura de dados, ou a função passada como VALOR para outro
    wrapper que não o `to_thread`, passam. As medidas até aqui:
      - `_log_falha(**kwargs)` com o parâmetro dentro do dict;
      - `loop.run_in_executor(None, _log_falha, …)`;
      - `partial(_log_falha, com_traceback=True)`, dentro ou fora do
        `to_thread`.
    Nenhum call site faz nenhuma delas (os 5 arquivos passam `**extra` só de
    campos nomeados e usam `asyncio.to_thread` direto); cobrir exigiria seguir
    o valor pelo fluxo. Formas que a guarda PEGA, também medidas: chamada
    direta, `to_thread(_log_falha, …)`, `to_thread(observability._log_falha,
    …)`, `to_thread(lambda: _log_falha(…))`, `observability._log_falha(…)`,
    apelido de import, rebind por atribuição (`_lf = _log_falha`,
    `log = observability._log_falha`, e o encadeado), `com_traceback=<expr>`,
    `com_traceback=1`, `com_traceback=None` e a chamada aninhada em
    `gather(...)`.
    """
    arvore = ast.parse(fonte)
    apelidos = _apelidos(arvore)
    dono: dict[ast.AST, str] = {}
    for no in ast.walk(arvore):
        nome = getattr(no, "name", None) if isinstance(
            no, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        for filho in ast.walk(no) if nome else ():
            dono[filho] = nome      # BFS: a função mais INTERNA sobrescreve

    achados = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call) or not _e_log_falha(no, apelidos):
            continue
        kw = next((k for k in no.keywords if k.arg == "com_traceback"), None)
        if kw is None or (isinstance(kw.value, ast.Constant) and kw.value.value is False):
            continue
        achados.append((rel, dono.get(no, "<módulo>")))
    return achados


def _varre_repo() -> list[tuple[str, str]]:
    achados = []
    for py in _RAIZ.rglob("*.py"):
        rel = py.relative_to(_RAIZ)
        if _IGNORADOS & set(rel.parts):
            continue
        try:
            achados += _chamadas_com_traceback(py.read_text(encoding="utf-8"), str(rel))
        except SyntaxError:                                    # pragma: no cover
            continue
    return achados


def test_so_a_allowlist_liga_o_traceback():
    """A CLASSE, não a instância: qualquer `com_traceback=True` no repositório
    fora da allowlist reprova — inclusive numa porta que este arquivo não
    simula, e inclusive num arquivo que nunca existiu antes."""
    achados = _varre_repo()
    fora = sorted(set(achados) - _ALLOWLIST_TRACEBACK)
    assert not fora, (
        "`com_traceback=True` fora da allowlist: o `system_event_logs` passa a "
        "guardar o `DETAIL: Key (…)` do psycopg. Só pode ligar quem a `main` "
        "deixava a exceção SUBIR CRUA (o middleware gravava a pilha) e o "
        "`except` novo passou a engolir — não basta ser rota sem `try/except`, "
        "que é a maioria do repositório. Critério em `core/observability.py`. "
        f"Fora da lista: {fora}"
    )
    orfas = sorted(_ALLOWLIST_TRACEBACK - set(achados))
    assert not orfas, f"allowlist com entrada que nenhum código liga: {orfas}"
    # e as portas continuam usando o helper (senão a varredura mede zero)
    for caminho in ("core/handlers/pending.py", "core/handlers/credit.py",
                    "core/services/ai_chat/tools/launches.py",
                    "frontend/finance_bot_websocket_custom.py"):
        assert "_log_falha" in Path(caminho).read_text(encoding="utf-8"),             f"{caminho} deixou de usar o helper"


def test_porta_de_conversa_nova_nao_passa_em_silencio():
    """O que a allowlist por NOME DE ARQUIVO deixava passar: um handler novo,
    num arquivo que a lista antiga não citava."""
    fonte = (
        "from core.observability import _log_falha\n"
        "def resolve_apagar_tudo(user_id):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as e:\n"
        "        _log_falha('apagar_tudo', user_id, e, com_traceback=True)\n"
    )
    rel = "core/handlers/porta_nova.py"
    achados = _chamadas_com_traceback(fonte, rel)
    assert achados == [(rel, "resolve_apagar_tudo")], achados
    assert set(achados) - _ALLOWLIST_TRACEBACK, "a guarda deixaria passar"

    # controle POSITIVO: o mesmo arquivo SEM o parâmetro (o default fail-safe)
    # não é acusado — senão a guarda reprovaria qualquer porta e não mediria nada.
    assert _chamadas_com_traceback(fonte.replace(", com_traceback=True", ""), rel) == []


def test_guarda_pega_modulo_qualificado_e_apelido():
    """As formas plausíveis que a guarda deixava passar antes: rota nova
    que importa o MÓDULO (`observability._log_falha` dentro do `to_thread`),
    import com APELIDO e rebind por ATRIBUIÇÃO. As cegueiras que SOBRAM estão
    declaradas no docstring de `_chamadas_com_traceback` — declaradas, não
    esgotadas: a lista é o que foi medido, não uma prova de completude."""
    rel = "frontend/routes/nova.py"
    modulo = (
        "import asyncio\n"
        "from core import observability\n"
        "async def rota_nova(user_id):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as e:\n"
        "        await asyncio.to_thread(observability._log_falha, 'x', user_id, e,\n"
        "                                com_traceback=True)\n"
    )
    apelido = (
        "from core.observability import _log_falha as _lf\n"
        "def rota_apelidada(user_id):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as e:\n"
        "        _lf('x', user_id, e, com_traceback=True)\n"
    )
    # A IRMÃ do apelido de import, mesma classe: rebind por ATRIBUIÇÃO. O
    # `_apelidos()` colhia só `asname` de `ImportFrom`, então as duas formas
    # abaixo passavam. Controle negativo medido: sem o ponto fixo do
    # `_apelidos`, as duas devolvem `[]` e a guarda deixa passar.
    atribuicao = (
        "from core.observability import _log_falha\n"
        "from core import observability\n"
        "_lf = _log_falha\n"
        "log = observability._log_falha\n"
        "encadeado = _lf\n"
        "def rota_rebind(user_id):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as e:\n"
        "        _lf('x', user_id, e, com_traceback=True)\n"
        "        log('x', user_id, e, com_traceback=True)\n"
        "        encadeado('x', user_id, e, com_traceback=True)\n"
    )
    assert _chamadas_com_traceback(modulo, rel) == [(rel, "rota_nova")]
    assert _chamadas_com_traceback(apelido, rel) == [(rel, "rota_apelidada")]
    assert _chamadas_com_traceback(atribuicao, rel) == [(rel, "rota_rebind")] * 3
    # controle POSITIVO: sem o parâmetro, nenhuma das três é acusada.
    for fonte in (modulo, apelido, atribuicao):
        assert _chamadas_com_traceback(
            fonte.replace("com_traceback=True", "com_traceback=False"), rel) == []


def test_rota_http_continua_persistindo_o_traceback(user_id, monkeypatch, caplog):
    """Controle POSITIVO, e a metade que a rodada 4 consertou: na rota HTTP o
    traceback CHEGA em `system_event_logs.details` — é o mesmo campo que o
    `admin_error_logging_middleware` preenchia antes do `HTTPException`."""
    from fastapi.testclient import TestClient

    import db.cards as cards_mod
    import frontend.finance_bot_websocket_custom as dashboard

    def explode(uid, gid):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(cards_mod, "undo_installment_group", explode)

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")

    with caplog.at_level(logging.WARNING):
        resp = client.delete(f"/installments/{user_id}/PC12345678",
                             headers={dashboard.CSRF_HEADER_NAME: "test-csrf-token"})

    assert resp.status_code == 500, resp.text
    assert "segredo" not in resp.text, resp.text

    linhas = _linhas(caplog, "undo_installment_group: falha")
    assert len(linhas) == 1 and linhas[0].exc_info is not None, linhas

    gravados = _system_event_logs(monkeypatch, linhas)
    assert gravados[0]["details"]["traceback"], gravados[0]["details"]
    assert "segredo" in "".join(gravados[0]["details"]["traceback"]), \
        "sem o traceback aqui o rastro fica MENOR que o da main"


def test_todo_connect_do_observability_tem_timeout():
    """A CLASSE, não a instância: as duas funções de `core/observability.py`
    abrem `psycopg.connect()` de forma bloqueante, e o `_DashboardHandler` é
    root logger — sem `connect_timeout` o caller fica preso enquanto o banco
    não responde (medido: >30s). O timeout do libpq limita o ESTABELECIMENTO da
    conexão, não a query (medido: `connect_timeout=2` + `pg_sleep(5)` devolveu
    em 5,00s): consulta lenta continua sendo esperada. A única diferença
    observável é connect que demore >2s (banco vivo, porém lento) — vira
    `False`, o mesmo que o `except` já devolvia.

    Guarda por `ast` para o connect NOVO que este módulo ganhar."""
    arvore = ast.parse(Path(observability.__file__).read_text(encoding="utf-8"))
    sem_timeout = [
        no.lineno for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and getattr(no.func, "attr", None) == "connect"
        and getattr(getattr(no.func, "value", None), "id", None) == "psycopg"
        and "connect_timeout" not in {k.arg for k in no.keywords}
    ]
    assert not sem_timeout, (
        "psycopg.connect() sem connect_timeout em core/observability.py, "
        f"linha(s) {sem_timeout} — trava o caller com banco inalcançável"
    )


def test_traceback_persistido_tem_o_mesmo_teto_da_main():
    """Paridade com o `admin_error_logging_middleware`, o caminho que este PR
    diz restaurar: ele grava `tb_str[-2000:]`
    (`core/admin_dashboard.py:1378`). Sem teto, um traceback fundo (recursão,
    stack de framework) cresce sem limite dentro do JSONB.

    Uma `str`, não a lista do `format_exception`: o
    `esc(row.details.traceback)` do `admin-dashboard.html:1308` renderizava
    `"['Traceback (most recent call last):\\n', …]"`."""
    gravados: list[dict] = []
    handler = observability._DashboardHandler()
    original = observability.log_system_event_sync
    observability.log_system_event_sync = lambda level, **kw: gravados.append(kw)
    try:
        # Cadeia de `__cause__`: cada camada acrescenta um bloco próprio ao
        # `format_exception`. Recursão simples não serve — o Python colapsa
        # frames repetidos em "[Previous line repeated N more times]" e o
        # traceback fica em ~1 KB, abaixo do teto que se quer provar.
        e: Exception = RuntimeError(_SEGREDO)
        for i in range(60):
            try:
                raise RuntimeError(f"camada {i} " + "x" * 40) from e
            except RuntimeError as novo:
                e = novo
        handler.emit(logging.LogRecord(
            "t", logging.ERROR, __file__, 0, "falha", (), (type(e), e, e.__traceback__)))
    finally:
        observability.log_system_event_sync = original

    tb = gravados[0]["details"]["traceback"]
    assert isinstance(tb, str), type(tb)
    assert len(tb) == 2000, len(tb)          # o cru passa de 15 mil
    assert "camada 59" in tb, tb[-200:]      # cortou o TOPO, guardou o fim
    assert "camada 0 " not in tb, tb[:200]


def _client(user_id):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return client


def test_rotas_do_dashboard_nao_persistem_o_traceback(user_id, monkeypatch, caplog):
    """As duas rotas que o commit anterior ligava por engano. Na `main` elas já
    eram `HTTPException(500, f"Erro ao apagar…")`, que o
    `admin_error_logging_middleware` deixa passar: ele NUNCA gravou pilha aqui.
    Medido nas duas colunas:

        [/launches 5488]  MAIN gravava em system_event_logs: NADA
                          BRANCH details.traceback tinha DETAIL: True

    Então `com_traceback=True` aqui não restaura rastro — cria persistência
    nova de dado do cliente."""
    import frontend.finance_bot_websocket_custom as dashboard

    def explode_launch(uid, lid):
        raise RuntimeError(_SEGREDO)

    def explode_credito(uid, tx_id):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", explode_launch)
    monkeypatch.setattr(dashboard, "undo_credit_transaction", explode_credito)

    cabecalho = {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}
    with caplog.at_level(logging.WARNING):
        r1 = _client(user_id).delete(f"/launches/{user_id}/424242", headers=cabecalho)
        r2 = _client(user_id).delete(f"/credit-transactions/{user_id}/9", headers=cabecalho)

    for r in (r1, r2):
        assert r.status_code == 500, r.text
        assert "segredo" not in r.text and "DETAIL" not in r.text, r.text

    linhas = (_linhas(caplog, "delete_launch: falha")
              + _linhas(caplog, "undo_credit_transaction: falha"))
    assert len(linhas) == 2, [r.getMessage() for r in caplog.records]
    assert all(l.exc_info is None for l in linhas), linhas

    gravados = _system_event_logs(monkeypatch, linhas)
    assert len(gravados) == 2, gravados
    assert all("traceback" not in g["details"] for g in gravados), gravados
    despejo = json.dumps(gravados, ensure_ascii=False)
    assert "DETAIL" not in despejo and "segredo" not in despejo, despejo
    # e o que SOBRA continua sendo diagnóstico útil:
    assert "causa=RuntimeError" in gravados[0]["message"], gravados[0]["message"]
    assert f"launch_id=424242" in gravados[0]["message"], gravados[0]["message"]


class _MarcaLoop(logging.Handler):
    """Registra, por linha de log, se ela foi emitida DENTRO do event loop."""

    def __init__(self):
        super().__init__()
        self.dentro_do_loop: list[tuple[str, bool]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            asyncio.get_running_loop()
            dentro = True
        except RuntimeError:
            dentro = False
        self.dentro_do_loop.append((record.getMessage(), dentro))


def test_rota_async_nao_loga_no_event_loop(user_id, monkeypatch):
    """O `_DashboardHandler` grava com `psycopg.connect()` + INSERT
    BLOQUEANTES, e as 4 rotas destrutivas são `async`: chamar `_log_falha`
    direto trava o event loop inteiro enquanto o banco não responde (medido com
    banco inalcançável: >30s antes do `connect_timeout`). Por isso o
    `asyncio.to_thread`.

    `asyncio.get_running_loop()` só devolve loop na thread do loop — é a prova
    barata de que o log saiu de lá."""
    import db.cards as cards_mod
    import frontend.finance_bot_websocket_custom as dashboard
    from db.accounts import LaunchUnsafeRollback

    def explode(uid, lid):
        # id 400400 = ramo de DOMÍNIO da `/launches` (WARNING + HTTP 400). O
        # nível não muda quem paga a conta: o `_DashboardHandler` espelha
        # WARNING em `system_event_logs` com o mesmo `psycopg.connect()`.
        if lid == 400400:
            raise LaunchUnsafeRollback("efeitos desconhecidos", "chave_desconhecida")
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", explode)
    monkeypatch.setattr(dashboard, "undo_credit_transaction", explode)
    monkeypatch.setattr(cards_mod, "get_card_by_id",
                        lambda uid, cid: {"id": cid, "name": "Nubank"})
    monkeypatch.setattr(cards_mod, "delete_card", explode)
    monkeypatch.setattr(cards_mod, "undo_installment_group", explode)

    marca = _MarcaLoop()
    logger = logging.getLogger("core.observability")
    logger.addHandler(marca)
    try:
        cabecalho = {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}
        cli = _client(user_id)
        for url, esperado in ((f"/launches/{user_id}/424242", 500),
                              (f"/launches/{user_id}/400400", 400),
                              (f"/credit-transactions/{user_id}/9", 500),
                              (f"/cards/{user_id}/9", 500),
                              (f"/installments/{user_id}/PC12345678", 500)):
            assert cli.delete(url, headers=cabecalho).status_code == esperado, url
    finally:
        logger.removeHandler(marca)

    falhas = [m for m in marca.dentro_do_loop if ": falha " in m[0]]
    assert len(falhas) == 5, marca.dentro_do_loop
    no_loop = [m for m, dentro in falhas if dentro]
    assert not no_loop, (
        "rota async logou DENTRO do event loop: o INSERT bloqueante do "
        f"_DashboardHandler trava todas as conexões do processo — {no_loop}"
    )
