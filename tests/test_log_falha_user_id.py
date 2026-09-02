"""O `user_id` da falha vai na COLUNA, não só no texto.

`_log_falha` escrevia o identificador do titular apenas dentro da `message`
(`"… falha user_id=42 …"`). O `_DashboardHandler` chamava
`log_system_event_sync()` sem `user_id=`, então `system_event_logs.user_id`
ficava NULL — e `db/privacy.py` faz o select da exportação (`:366`) e o delete
da exclusão de conta (`:485`) EXCLUSIVAMENTE por `WHERE user_id = %s`. Efeito:
esses eventos ficavam de fora da exportação e SOBREVIVIAM à exclusão da conta,
carregando o identificador do titular no texto. Com a coluna preenchida o
`:485` apaga a linha inteira, e o texto vai junto.

Três camadas, porque texto de arquivo não mede comportamento (§3):

  A. 8 dos 24 call sites de `_log_falha`, escolhidos por PORTA DE ENTRADA e
     dirigidos de VERDADE (`handle_incoming`, runner do /ai/chat, cliente
     HTTP) — a coluna chega preenchida e nada do `str(e)` entra na `message`
     nem no `details`. São 24 call sites e 11 operações destrutivas distintas;
     estes 8 cobrem 5 delas (`delete_launch`, `undo_credit_transaction`,
     `delete_all_launches`, `delete_card`, `undo_installment_group`) e as três
     portas de entrada que existem.

     DIRIGIDOS (8) — medidos, não deduzidos: os 4 primeiros por instrumentação
     do `logging` durante a própria suíte, os 4 de `to_thread` por serem o
     único call site daquela `op` no arquivo (o do `delete_launch` por HTTP,
     que tem dois, pelo ramo `except Exception` — é o que devolve o 500 que
     `_http` asseria; o irmão é o ramo de erro de DOMÍNIO, que devolve 400):
     `core/handlers/pending.py:214`, `core/handlers/credit.py:1584`,
     `core/services/ai_chat/tools/launches.py:471` e `:537`,
     `frontend/finance_bot_websocket_custom.py:5519` e `:5610`,
     `frontend/routes/cards.py:353` e `:448`.

     SEM DIRIGIR (16), de propósito: `core/handlers/pending.py:183`, `:195`,
     `:206`, `:240`, `:245`, `:282`, `:285`, `:315`, `:318`;
     `core/handlers/credit.py:1560`;
     `core/services/ai_chat/tools/launches.py:443`, `:453`, `:463`, `:478`,
     `:492`; `frontend/finance_bot_websocket_custom.py:5501`.

     8 + 16 = 24, e a SOMA é o ponto, não a lista: uma lista de "o que falta"
     que não fecha com o total afirma exaustividade sem ter — foi assim que
     este parágrafo já reprovou uma vez, com 8 + 13 = 21 escrito como se fosse
     24. Quem reusar estes números, remeça: a enumeração exata é a varredura da
     camada C (`_varre_repo` + `_e_log_falha`), a única que enxerga também o
     `asyncio.to_thread(_log_falha, …)` das 5 chamadas de `frontend/`.

     8 bastam porque o MECANISMO é único: o `user_id` sai de `_log_falha` pelo
     `extra=` e chega na coluna pelo `getattr(record, "user_id", None)` do
     `_DashboardHandler` — dois pontos, iguais para os 24. O que varia entre
     call sites não é o mecanismo, é a FORMA do call site (qual expressão vai
     no 2º posicional), e disso quem cuida é a camada C, que varre os 24 sem
     precisar dirigir nenhum. Dirigir os 16 restantes repetiria a mesma medida
     por 16 caminhos de setup diferentes. O nível (`WARNING` × `ERROR`) não
     toca o `extra=`: é o mesmo `logger.log(nivel, …, extra={"user_id": …})`.
  B. ausência é `None`: um logger qualquer do processo continua sendo GRAVADO,
     com `user_id=None` (controle positivo — sem ele o grupo passaria numa
     versão que só grava quando há `user_id`);
  C. guarda `ast` da CLASSE: todo `_log_falha` do repositório passa como 2º
     posicional um `ast.Name` `user_id` ligado a um PARÂMETRO da função
     envolvente. É o que pega o call site futuro que escrever
     `_log_falha("x", display_id, e)` — e é CEGA a rebind do nome antes da
     chamada (`user_id = display_id`); o teto completo está no docstring de
     `_chamadas_sem_user_id`.

O identificador vem do atributo estruturado do `LogRecord` (`extra=`), nunca de
parsing de texto — por isso a isca `DETAIL: Key (user_id)=(999999) —
5511987654321` em toda exceção: se algum dia alguém extrair o id da mensagem,
o `999999` aparece no lugar do id real e o grupo fica vermelho.

FORA de escopo, de propósito (issues #220/#221): os outros call sites que não
usam `_log_falha`, o `admin_error_logging_middleware`
(`core/admin_dashboard.py:1348`), a FK da coluna e o backfill das linhas NULL
já existentes.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.observability as observability

# Reuso, não recriação (§0.1): as helpers da varredura moram no teste irmão.
from test_log_falha_traceback import _apelidos, _e_log_falha, _nome, _varre_repo

# A isca: `999999` é um id que NÃO é o do usuário e `5511987654321` é um dado de
# cliente. Se qualquer um dos dois chegar na coluna, na `message` ou no
# `details`, é porque saiu de `str(e)` — parsing de texto, exatamente o que a
# regra proíbe.
_ISCA = "DETAIL: Key (user_id)=(999999) — 5511987654321"
_ISCA_ID = "999999"
_ISCA_TELEFONE = "5511987654321"


def _uid() -> int:
    """Abaixo de 2 bilhões DE PROPÓSITO — `core.handle_incoming._normalize_user_id`
    comprime id maior que isso e o `handle_incoming` roteia para OUTRO usuário
    (mesma escolha de `tests/test_pending_rollback.py`). O
    `_auto_cleanup_orphan_users` do conftest apaga o user no fim do teste."""
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return uid


def _explode(*a, **k):
    raise RuntimeError(_ISCA)


@pytest.fixture
def coletor(monkeypatch):
    """O que chegaria em `system_event_logs`, pelo handler REAL no root logger.

    `_configure_root_logger()` é a mesma função que `get_logger()` chama em
    produção — nenhum handler falso é montado aqui."""
    gravados: list[dict] = []
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda level, **kw: gravados.append({"level": level, **kw}))
    observability._configure_root_logger()
    assert any(isinstance(h, observability._DashboardHandler)
               for h in logging.getLogger().handlers), \
        "sem o _DashboardHandler no root nada é gravado e o grupo não mede nada"
    return gravados


def _evento(coletor: list[dict], op: str) -> dict:
    achados = [g for g in coletor if g["message"].startswith(f"{op}: falha")]
    assert len(achados) == 1, \
        f"esperava 1 evento de {op!r}, veio {[g['message'][:60] for g in coletor]}"
    return achados[0]


# ── A. as 8 portas, dirigidas de verdade ─────────────────────────────────────
#
# Cada driver ARRUMA a falha, dirige a porta pelo caminho que o usuário
# percorre e devolve `(user_id, op)`. Ele também confere que a porta continua
# respondendo o que respondia — o logging não pode mascarar a falha.


def _diga(uid: int, texto: str) -> str:
    from core.types import IncomingMessage
    import core.handle_incoming as HI

    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id=f"m{uuid.uuid4().hex[:8]}", attachments=[],
                          external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return "\n".join(m.text for m in (saida or []) if getattr(m, "text", None))


def _porta_whatsapp_pending(monkeypatch):
    """`core/handlers/pending.py::resolve_delete` — o 'sim' do WhatsApp."""
    uid = _uid()
    monkeypatch.setattr(db, "delete_launch_and_rollback", _explode)
    db.set_pending_action(uid, "delete_launch",
                          {"launch_id": 4242, "display_id": 9}, minutes=20)

    resp = _diga(uid, "sim")

    assert "Tenta de novo" in resp, resp
    assert _ISCA_TELEFONE not in resp and "DETAIL" not in resp, resp
    return uid, "delete_launch"


def _porta_whatsapp_credito(monkeypatch):
    """`core/handlers/credit.py::handle` — 'apagar CC12' no WhatsApp."""
    from core.handlers import credit as h_credit

    uid = _uid()
    monkeypatch.setattr(h_credit, "undo_credit_transaction", _explode)

    resp = _diga(uid, "apagar cc12")

    assert "Tenta de novo" in resp, resp
    assert _ISCA_TELEFONE not in resp and "DETAIL" not in resp, resp
    return uid, "undo_credit_transaction"


def _porta_ai_chat_um(monkeypatch):
    """`tools/launches.py::_delete_launch_execute`, pelo runner do /ai/chat."""
    from core.services.ai_chat import chat

    uid = _uid()
    _lid, seq, _bal = db.add_launch_and_update_balance(
        uid, "despesa", 300, "aluguel", "paguei 300 aluguel")
    monkeypatch.setattr(db, "delete_launch_and_rollback", _explode)
    db.ai_set_pending_action(uid, "delete_launch", {"launch_id": str(seq)},
                             f"apagar o lançamento #{seq}")

    resp = chat(uid, "sim", monthly_limit=1000, platform="dashboard")

    # o logging não mascara a falha: o usuário vê o `_ERRO_APAGAR` de
    # `core/services/ai_chat/tools/launches.py:45`, não um "apaguei" em cima
    # de um delete que morreu
    assert "Não consegui apagar agora" in resp, resp
    assert _ISCA_TELEFONE not in resp and "DETAIL" not in resp, resp
    return uid, "delete_launch"


def _porta_ai_chat_tudo(monkeypatch):
    """`tools/launches.py::_delete_all_launches_execute`, pelo mesmo runner."""
    from core.services.ai_chat import chat

    uid = _uid()
    monkeypatch.setattr(db, "delete_all_launches_and_rollback", _explode)
    db.ai_set_pending_action(uid, "delete_all_launches", {}, "apagar tudo")

    resp = chat(uid, "sim", monthly_limit=1000, platform="dashboard")

    assert "Não consegui apagar agora" in resp, resp
    assert _ISCA_TELEFONE not in resp and "DETAIL" not in resp, resp
    return uid, "delete_all_launches"


def _client(uid: int):
    """Mesmo harness HTTP de `tests/test_delete_endpoints_nao_vazam.py`."""
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(uid, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return client, {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}


def _http(uid: int, caminho: str):
    client, headers = _client(uid)
    resp = client.delete(caminho, headers=headers)
    # o logging não mascara a falha: continua 500 com a frase de produto
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert _ISCA_TELEFONE not in detail and "DETAIL" not in detail, detail
    assert _ISCA_ID not in detail, detail
    return resp


def _porta_http_launch(monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard

    uid = _uid()
    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", _explode)
    _http(uid, f"/launches/{uid}/424242")
    return uid, "delete_launch"


def _porta_http_credito(monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard

    uid = _uid()
    monkeypatch.setattr(dashboard, "undo_credit_transaction", _explode)
    _http(uid, f"/credit-transactions/{uid}/9")
    return uid, "undo_credit_transaction"


def _porta_http_cartao(monkeypatch):
    import db.cards as cards_mod

    uid = _uid()
    monkeypatch.setattr(cards_mod, "get_card_by_id",
                        lambda u, c: {"id": c, "name": "Nubank"})
    monkeypatch.setattr(cards_mod, "delete_card", _explode)
    _http(uid, f"/cards/{uid}/9")
    return uid, "delete_card"


def _porta_http_parcelamento(monkeypatch):
    import db.cards as cards_mod

    uid = _uid()
    monkeypatch.setattr(cards_mod, "undo_installment_group", _explode)
    _http(uid, f"/installments/{uid}/PC12345678")
    return uid, "undo_installment_group"


PORTAS = [
    ("whatsapp/resolve_delete", _porta_whatsapp_pending),
    ("whatsapp/credit.handle", _porta_whatsapp_credito),
    ("ai_chat/delete_launch", _porta_ai_chat_um),
    ("ai_chat/delete_all_launches", _porta_ai_chat_tudo),
    ("http/delete_launch_route", _porta_http_launch),
    ("http/delete_credit_transaction_route", _porta_http_credito),
    ("http/delete_card_route", _porta_http_cartao),
    ("http/installment_delete_route", _porta_http_parcelamento),
]


@pytest.mark.parametrize("nome,driver", PORTAS, ids=[p[0] for p in PORTAS])
def test_porta_grava_o_user_id_na_coluna(nome, driver, monkeypatch, coletor):
    """O evento chega a `system_event_logs` com a COLUNA `user_id` preenchida —
    é o que a exportação (`db/privacy.py:366`) e a exclusão de conta (`:485`)
    enxergam. E nada da isca vaza pro texto: o id vem do atributo do
    `LogRecord`, não de `str(e)`."""
    uid, op = driver(monkeypatch)

    evento = _evento(coletor, op)
    assert evento["user_id"] == uid, (
        f"{nome}: coluna user_id={evento['user_id']!r} (esperado {uid}) — "
        "sem ela a linha some da exportação e SOBREVIVE à exclusão de conta")

    # O TRACEBACK é outra decisão, e já tem dono: as duas rotas de
    # `frontend/routes/cards.py` mandam `com_traceback=True` de propósito
    # (`tests/test_log_falha_traceback.py`), e ali o `str(e)` inteiro é o
    # rastro que o `admin_error_logging_middleware` já gravava. O que este
    # teste mede é outra coisa: nada foi EXTRAÍDO de `str(e)` pra virar
    # identificador. Então a isca é procurada no texto SEM o traceback.
    details = {k: v for k, v in evento["details"].items() if k != "traceback"}
    texto = (evento["message"].split("Traceback (most recent call last)")[0]
             + json.dumps(details, ensure_ascii=False))
    assert _ISCA_ID not in texto, texto
    assert _ISCA_TELEFONE not in texto, texto
    # o diagnóstico que já existia continua lá
    assert f"user_id={uid}" in evento["message"]
    assert "causa=RuntimeError" in evento["message"]


# ── B. ausência é None — e a linha continua sendo gravada ────────────────────

def test_outro_logger_grava_com_user_id_none(coletor):
    """Controle POSITIVO: `getattr(record, "user_id", None)` não pode virar um
    filtro. Um logger qualquer do processo (que nunca passa `extra=`) continua
    gravando em `system_event_logs`, só que com a coluna NULL — que é o que
    sempre foi."""
    logging.getLogger("qualquer.outro").error("boom")

    achados = [g for g in coletor if g["message"].endswith("boom")]
    assert len(achados) == 1, \
        f"a linha de outro logger sumiu: {[g['message'][:60] for g in coletor]}"
    # `.get`: o papel deste caso é o CONTROLE POSITIVO — a linha de outro
    # logger continua sendo gravada, e nenhum valor é inventado pra ela.
    # Provar que a coluna CHEGA a ser passada é papel dos 8 casos de cima.
    assert achados[0].get("user_id") is None, achados[0]
    assert achados[0]["source"] == "qualquer.outro", achados[0]


# ── C. a guarda `ast`: o 2º posicional é o `user_id` da função ───────────────
#
# Allowlist ESTRUTURAL (arquivo, função) no padrão do teste irmão. Está vazia:
# nenhum call site de hoje passa outra coisa. Entrada nova aqui é decisão
# consciente de gravar a coluna com um id que NÃO é `users.id`.
_ALLOWLIST_USER_ID: set[tuple[str, str]] = set()


def _parametros(fn: ast.AST) -> set[str]:
    a = fn.args
    nomes = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return nomes | {x.arg for x in (a.vararg, a.kwarg) if x}


def _chamadas_sem_user_id(fonte: str, rel: str) -> list[tuple[str, str]]:
    """(arquivo, função) de cada `_log_falha` cujo 2º POSICIONAL não é o
    `ast.Name` `user_id` ligado a um parâmetro da função envolvente.

    PEGA — 8 formas sintéticas, TODAS travadas em
    `test_porta_nova_com_id_errado_nao_passa_em_silencio` (nenhuma é afirmação
    solta: cada uma tem uma `assert` própria lá, e some se regredir): segundo
    posicional que seja OUTRO nome (`user_seq`), um atributo (`msg.user_id`),
    uma constante (`999999`), uma expressão (`int(user_id)`), um `user_id` de
    closure, um `user_id` global do módulo, um parâmetro renomeado (`uid`), e a
    chamada escrita no nível de MÓDULO (sem função envolvente).

    NÃO PEGA — e este é o teto, escrito aqui de propósito (§3, "que classe de
    bug esta verificação nunca pegaria?"): **rebind do nome antes da chamada**.

        def resolve(user_id, display_id):
            user_id = display_id          # <- passa em SILÊNCIO
            _log_falha("x", user_id, e)

    Ela compara o NOME do 2º posicional e a existência de um parâmetro
    homônimo, não a LIGAÇÃO — `user_id = display_id` e `user_id = str(user_id)`
    saem as duas com `[]`. Rastrear rebind exige análise de fluxo de dados;
    é desproporcional para uma guarda de estilo de call site, e a camada A
    (as 8 portas dirigidas de verdade) é quem mede o valor que chega na coluna.

    FALSO POSITIVO CONSERVADOR, por escolha: `_log_falha("x", user_id=user_id,
    e=e)` (keyword em vez de posicional) e o `user_id` que chega por
    `**kwargs` são ACUSADOS, porque a varredura só lê posicional e só conhece
    nome de parâmetro escrito. Nenhum call site de hoje usa essas formas; se
    algum passar a usar, a saída é uma entrada em `_ALLOWLIST_USER_ID` — não
    afrouxar a varredura.

    Herda também as cegueiras declaradas em `_chamadas_com_traceback`
    (`partial`, `run_in_executor`): a varredura só enxerga a chamada escrita
    no lugar."""
    arvore = ast.parse(fonte)
    apelidos = _apelidos(arvore)
    dono: dict[ast.AST, ast.AST] = {}
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for filho in ast.walk(no):
                dono[filho] = no       # BFS: a função mais INTERNA sobrescreve

    achados = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call) or not _e_log_falha(no, apelidos):
            continue
        # `to_thread(_log_falha, op, user_id, e)` empurra os posicionais em 1
        args = no.args[1:] if _nome(no.func) == "to_thread" else no.args
        fn = dono.get(no)
        segundo = args[1] if len(args) > 1 else None
        ok = (isinstance(segundo, ast.Name) and segundo.id == "user_id"
              and fn is not None and "user_id" in _parametros(fn))
        if not ok:
            achados.append((rel, getattr(fn, "name", "<módulo>")))
    return achados


def test_todo_log_falha_passa_o_user_id_da_funcao():
    """A CLASSE, não a instância: um call site futuro com `_log_falha("x",
    display_id, e)` grava na coluna um id que não é `users.id` — a linha deixa
    de sair na exportação e SOBREVIVE à exclusão da conta daquele titular."""
    achados = _varre_repo(_chamadas_sem_user_id)
    fora = sorted(set(achados) - _ALLOWLIST_USER_ID)
    assert not fora, (
        "`_log_falha` com 2º posicional que não é o `user_id` da função: o "
        "`system_event_logs.user_id` passa a guardar um id que `db/privacy.py` "
        "não enxerga (select da exportação em `:366`, delete da exclusão de "
        f"conta em `:485`). Fora da lista: {fora}"
    )
    orfas = sorted(_ALLOWLIST_USER_ID - set(achados))
    assert not orfas, f"allowlist com entrada que nenhum código produz: {orfas}"


def _conta_log_falha(fonte: str) -> int:
    """Quantas chamadas de `_log_falha` a MESMA varredura enxerga no arquivo."""
    arvore = ast.parse(fonte)
    apelidos = _apelidos(arvore)
    return sum(1 for no in ast.walk(arvore)
               if isinstance(no, ast.Call) and _e_log_falha(no, apelidos))


# Os arquivos que HOJE têm `_log_falha`. Sem contagem exata de propósito: a
# versão anterior fixava 10/2/7/3/2 e ficava VERMELHA quando alguém acrescentava
# um `_log_falha` CORRETO a qualquer um deles — alarme falso, medido: um call
# site novo e certo em `core/handlers/credit.py` derrubava o caso `[…credit.py-2]`
# sem nada estar errado. O que a asserção precisa provar é só que a varredura
# não é vácua (`>= 1`); quem reprova call site errado é
# `_chamadas_sem_user_id`, na linha seguinte. Não recoloque o número.
PORTAS_NO_REPO = [
    "core/handlers/pending.py",
    "core/handlers/credit.py",
    "core/services/ai_chat/tools/launches.py",
    "frontend/finance_bot_websocket_custom.py",
    "frontend/routes/cards.py",
]


@pytest.mark.parametrize("caminho", PORTAS_NO_REPO)
def test_a_varredura_enxerga_as_portas_de_verdade(caminho):
    """Sem isto, `_chamadas_sem_user_id` podia estar medindo ZERO chamada e o
    teste acima passaria verde por vacuidade. Aqui a varredura tem de enxergar
    pelo menos uma chamada em cada arquivo que tem `_log_falha`, e nenhuma
    delas pode estar na lista de reprovadas."""
    fonte = (Path(__file__).resolve().parent.parent / caminho).read_text(encoding="utf-8")
    assert _conta_log_falha(fonte) >= 1, \
        f"{caminho}: a varredura enxerga ZERO chamada de `_log_falha` — ou o "\
        "arquivo deixou de ter uma (tire-o da lista), ou a varredura quebrou e "\
        "o teste da classe acima está passando por vacuidade"
    assert _chamadas_sem_user_id(fonte, caminho) == []


def test_porta_nova_com_id_errado_nao_passa_em_silencio():
    """Negativo 3 — a porta fictícia que passa `user_seq` no lugar do
    `user_id`, sem editar o repositório (padrão do
    `test_porta_de_conversa_nova_nao_passa_em_silencio`)."""
    rel = "core/handlers/porta_nova.py"
    fonte = (
        "from core.observability import _log_falha\n"
        "def resolve_apagar_tudo(user_id, user_seq):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as e:\n"
        "        _log_falha('apagar_tudo', user_seq, e)\n"
    )
    assert _chamadas_sem_user_id(fonte, rel) == [(rel, "resolve_apagar_tudo")]

    # as outras formas plausíveis do mesmo erro
    for errado in ("msg.user_id", "int(user_id)", "999999"):
        assert _chamadas_sem_user_id(
            fonte.replace("user_seq, e", f"{errado}, e"), rel) == \
            [(rel, "resolve_apagar_tudo")], errado

    # `user_id` que NÃO é parâmetro (global do módulo) também é acusado
    global_do_modulo = fonte.replace(
        "def resolve_apagar_tudo(user_id, user_seq):",
        "user_id = 1\ndef resolve_apagar_tudo(user_seq):").replace("user_seq, e", "user_id, e")
    assert _chamadas_sem_user_id(global_do_modulo, rel) == [(rel, "resolve_apagar_tudo")]

    # parâmetro RENOMEADO: `uid` é o `users.id` certo, mas a varredura compara o
    # NOME — acusa, e a saída é allowlist, não afrouxar a regra (ver docstring).
    renomeado = fonte.replace("def resolve_apagar_tudo(user_id, user_seq):",
                              "def resolve_apagar_tudo(uid):").replace("user_seq, e", "uid, e")
    assert _chamadas_sem_user_id(renomeado, rel) == [(rel, "resolve_apagar_tudo")]

    # CLOSURE: o `user_id` é parâmetro da função de FORA; `dono` resolve para a
    # função mais interna, que não o tem. Sem isto, um `_log_falha` dentro de
    # callback aninhado passaria em silêncio.
    closure = (
        "from core.observability import _log_falha\n"
        "def resolve_apagar_tudo(user_id):\n"
        "    def depois(e):\n"
        "        _log_falha('apagar_tudo', user_id, e)\n"
        "    return depois\n"
    )
    assert _chamadas_sem_user_id(closure, rel) == [(rel, "depois")]

    # chamada no NÍVEL DE MÓDULO: não há função envolvente, então não há
    # parâmetro `user_id` — acusada com `<módulo>`.
    nivel_modulo = (
        "from core.observability import _log_falha\n"
        "user_id = 1\n"
        "_log_falha('apagar_tudo', user_id, Exception())\n"
    )
    assert _chamadas_sem_user_id(nivel_modulo, rel) == [(rel, "<módulo>")]

    # controle POSITIVO: a forma certa não é acusada — senão a guarda reprovaria
    # qualquer porta e não mediria nada.
    assert _chamadas_sem_user_id(fonte.replace("user_seq, e", "user_id, e"), rel) == []
    assert _chamadas_sem_user_id(
        "import asyncio\n"
        "from core import observability\n"
        "async def rota(user_id, card_id):\n"
        "    try:\n"
        "        apaga(user_id)\n"
        "    except Exception as exc:\n"
        "        await asyncio.to_thread(observability._log_falha, 'x', user_id, exc,\n"
        "                                card_id=card_id)\n", rel) == []


# ── D. o irmão do `_log_falha`: `logger.*` DIRETO ────────────────────────────
#
# `delete_all_launches_and_rollback` não usa `_log_falha` — loga por `logger.*`
# direto, então não herdava o `extra={"user_id": …}` e caía no MESMO bug por
# outra porta: id do titular na `message`, coluna NULL, linha fora da
# exportação (`db/privacy.py:366`) e SOBREVIVENDO à exclusão de conta (`:485`).
# Foi regressão DESTE PR, achada na revisão.
#
# RECORTE: só `db/accounts.py`. Medido, não estimado: o arquivo tem 1729 linhas
# e EXATAMENTE 3 chamadas de logging em qualquer nível — as 3 desta função —,
# então a guarda é apertada e não colide com código alheio. Os outros
# `logger.warning/error` de produção que interpolam identificador ficam FORA de
# propósito: são a issue #220, que o dono manteve fora deste PR; varrer o repo
# inteiro os reprovaria e arrastaria a #220 para dentro.
#
# A REGRA não é "todo `logger.*` precisa de `extra=`" — um log que não fala de
# usuário não deve inventar coluna. É: **`logger.*` que põe o IDENTIFICADOR no
# TEXTO tem de pôr também na COLUNA**. É essa a classe, e é ela que pega o site
# futuro.
_RECORTE_LOGGER = "db/accounts.py"


def _niveis_espelhados() -> set[str]:
    """Níveis que o `_DashboardHandler` espelha, MEDIDOS nele em vez de
    copiados: `emit` sai cedo abaixo do limiar, então quem não grava não tem
    coluna a preencher. Copiar o conjunto fazia o guarda mentir em silêncio se
    o limiar caísse para INFO (§0.7) — os nomes saem do próprio
    `record.levelname.lower()` do handler. `exception` não é nível: é o método
    que loga em ERROR, e por isso entra à mão."""
    gravou: list[str] = []
    handler, real = observability._DashboardHandler(), observability.log_system_event_sync
    observability.log_system_event_sync = lambda level, **kw: gravou.append(level)
    try:
        for nome in ("debug", "info", "warning", "error", "critical"):
            handler.emit(logging.LogRecord(
                "t", getattr(logging, nome.upper()), __file__, 1, "x", None, None))
    finally:
        observability.log_system_event_sync = real
    assert gravou, "handler não espelhou nível nenhum — a varredura não mediria nada"
    return set(gravou) | {"exception"}


_NIVEIS_ESPELHADOS = _niveis_espelhados()

# Allowlist ESTRUTURAL (arquivo, função), no padrão de `_ALLOWLIST_USER_ID`.
# Vazia: nenhum site de hoje precisa. Entrada nova aqui é decisão consciente de
# deixar o id do titular no texto com a coluna vazia.
_ALLOWLIST_LOGGER: set[tuple[str, str]] = set()


def _loggers_do_recorte(fonte: str, rel: str) -> list[tuple[str, str, int, bool]]:
    """(arquivo, função, linha, tem_extra_user_id) de cada `logger.<nível>` do
    recorte que FALA de `user_id` — seja no formato (`"… user_id=%s …"`) ou
    passando a variável como argumento.

    Só níveis espelhados em `system_event_logs`: o `_DashboardHandler` ignora
    abaixo de WARNING (`core/observability.py:55-56`), então `logger.info` não
    cria linha e não tem coluna a preencher.

    TETO: lê a chamada escrita no lugar, como as varreduras irmãs — `partial`,
    `to_thread` e logger com outro nome não são vistos. Aceitável no recorte de
    UM arquivo, cujas 3 chamadas o caso de vacuidade abaixo conta."""
    if rel.replace("\\", "/") != _RECORTE_LOGGER:
        return []
    arvore = ast.parse(fonte)
    dono: dict[ast.AST, ast.AST] = {}
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for filho in ast.walk(no):
                dono[filho] = no

    achados = []
    for no in ast.walk(arvore):
        f = getattr(no, "func", None)
        if not (isinstance(no, ast.Call) and isinstance(f, ast.Attribute)
                and f.attr in _NIVEIS_ESPELHADOS):
            continue
        fala_de_user = any(
            (isinstance(a, ast.Constant) and isinstance(a.value, str)
             and "user_id" in a.value)
            or (isinstance(a, ast.Name) and a.id == "user_id")
            for a in no.args
        )
        if not fala_de_user:
            continue
        extra = next((k.value for k in no.keywords if k.arg == "extra"), None)
        # `extra=` COM a chave `user_id` — e na MESMA chamada. Um `extra=` três
        # linhas abaixo é outra chamada e não conta.
        tem = isinstance(extra, ast.Dict) and any(
            isinstance(c, ast.Constant) and c.value == "user_id"
            for c in extra.keys)
        achados.append((rel, getattr(dono.get(no), "name", "<módulo>"),
                        no.lineno, tem))
    return achados


def test_logger_direto_com_id_no_texto_leva_o_id_na_coluna():
    """A CLASSE, não os 3 sites: um `logger.warning`/`error` NOVO em
    `db/accounts.py` que interpole o identificador no texto sem `extra=` grava
    linha com `user_id` NULL — que `db/privacy.py` não enxerga nem para exportar
    (`:366`) nem para apagar na exclusão de conta (`:485`)."""
    achados = _varre_repo(_loggers_do_recorte)
    fora = sorted({(r, fn) for r, fn, _ln, tem in achados if not tem}
                  - _ALLOWLIST_LOGGER)
    assert not fora, (
        f"`logger.*` com o id do titular no TEXTO e a coluna VAZIA: {fora}. "
        "A linha sobrevive à exclusão da conta carregando o id. Passe "
        "`extra={'user_id': user_id}` na MESMA chamada."
    )


def test_o_recorte_do_logger_fecha_a_conta():
    """Sem isto o teste acima passaria por VACUIDADE (varredura vendo zero).
    A soma é o controle: `com_extra + sem_extra == total`, e `total >= 1`.
    Sem número fixo de propósito (§2) — acrescentar um `logger.*` CORRETO ao
    arquivo não pode ficar vermelho."""
    achados = _varre_repo(_loggers_do_recorte)
    com = [a for a in achados if a[3]]
    sem = [a for a in achados if not a[3]]
    assert len(com) + len(sem) == len(achados)
    assert len(achados) >= 1, (
        f"a varredura enxerga ZERO `logger.*` em {_RECORTE_LOGGER} — ou o "
        "arquivo mudou de forma, ou a varredura quebrou e o teste da classe "
        "está verde à toa")
    assert not sem, f"sites sem a coluna: {[(a[0], a[2]) for a in sem]}"


def test_logger_novo_sem_extra_e_acusado():
    """Negativo da VARREDURA: o site futuro, sem editar o repositório."""
    rel = _RECORTE_LOGGER
    base = ("import logging\nlogger = logging.getLogger(__name__)\n"
            "def apaga_tudo(user_id):\n"
            "    logger.error('falhou user_id=%s', user_id{})\n")

    sem_extra = _loggers_do_recorte(base.format(""), rel)
    assert sem_extra == [(rel, "apaga_tudo", 4, False)], sem_extra

    com_extra = _loggers_do_recorte(
        base.format(", extra={'user_id': user_id}"), rel)
    assert com_extra == [(rel, "apaga_tudo", 4, True)], com_extra

    # `extra=` SEM a chave certa não vale
    outra_chave = _loggers_do_recorte(
        base.format(", extra={'launch_id': 1}"), rel)
    assert outra_chave == [(rel, "apaga_tudo", 4, False)], outra_chave

    # log que NÃO fala de usuário não é acusado (senão a guarda exigiria coluna
    # de todo log do arquivo, e passaria a mentir sobre o que mede)
    assert _loggers_do_recorte(
        "import logging\nlogger = logging.getLogger(__name__)\n"
        "def sobe():\n    logger.error('pool indisponível')\n", rel) == []

    # `logger.info` não é espelhado em `system_event_logs` — fora da classe
    assert _loggers_do_recorte(
        "import logging\nlogger = logging.getLogger(__name__)\n"
        "def f(user_id):\n    logger.info('oi user_id=%s', user_id)\n", rel) == []

    # fora do recorte, nada é acusado (é a issue #220, não este PR)
    assert _loggers_do_recorte(base.format(""), "core/handlers/pending.py") == []


# ── D2. comportamento: os 3 sites dirigidos, coluna conferida ────────────────
#
# Varredura `ast` não mede o que CHEGA na coluna — é a mesma divisão A × C.
# Aqui os 3 `logger.*` são alcançados de verdade e a coluna é lida.


def _evento_accounts(coletor: list[dict], trecho: str) -> dict:
    achados = [g for g in coletor if trecho in g["message"]]
    assert len(achados) == 1, \
        f"esperava 1 evento com {trecho!r}, veio {[g['message'][:70] for g in coletor]}"
    return achados[0]


def test_kept_unsafe_grava_o_user_id_na_coluna(coletor):
    """`db/accounts.py:1580` — o `logger.warning` do `kept_unsafe`, que é o
    caminho COMUM da recusa. Dirigido de verdade: um `efeitos` com chave fora
    de `_EFEITOS_REVERSIVEIS` faz `delete_launch_and_rollback` levantar
    `LaunchUnsafeRollback`, o lançamento é MANTIDO e o aviso é logado."""
    from test_delete_all_launches import _set_efeitos

    uid = _uid()
    lid, seq, _bal = db.add_launch_and_update_balance(
        uid, "despesa", 300, "aluguel", "paguei 300 aluguel")
    _set_efeitos(uid, lid, '{"delta_conta": -300.0, "chave_que_ninguem_reverte": 1}')

    resultado = db.delete_all_launches_and_rollback(uid)

    assert resultado["kept_unsafe"] == [seq], resultado
    assert resultado["deleted"] == 0, resultado
    evento = _evento_accounts(coletor, "mantido sem reverter")
    assert evento["user_id"] == uid, (
        f"coluna user_id={evento['user_id']!r} (esperado {uid}) — sem ela a "
        "linha some da exportação e SOBREVIVE à exclusão de conta")
    assert evento["level"] == "warning", evento
    assert f"user_id={uid}" in evento["message"]


def test_errors_grava_o_user_id_na_coluna(monkeypatch, coletor):
    """`db/accounts.py:1592` — o `logger.error` do `errors`, quando a exclusão
    de UM lançamento estoura por falha inesperada."""
    import db.accounts as accounts

    uid = _uid()
    _lid, seq, _bal = db.add_launch_and_update_balance(
        uid, "despesa", 40, "cafe", "gastei 40 cafe")
    monkeypatch.setattr(accounts, "delete_launch_and_rollback", _explode)

    resultado = db.delete_all_launches_and_rollback(uid)

    assert resultado["errors"] == [seq], resultado
    evento = _evento_accounts(coletor, "falha inesperada")
    assert evento["user_id"] == uid, evento
    # a isca não vaza pro texto: o id vem do `extra=`, não de `str(e)`
    assert _ISCA_ID not in evento["message"], evento["message"]
    assert _ISCA_TELEFONE not in evento["message"], evento["message"]


def test_recontagem_falha_grava_o_user_id_na_coluna(monkeypatch, coletor):
    """`db/accounts.py:1601` — o `logger.error` da recontagem."""
    import db.accounts as accounts

    uid = _uid()
    db.add_launch_and_update_balance(uid, "despesa", 40, "cafe", "gastei 40 cafe")
    monkeypatch.setattr(accounts, "count_launches", _explode)

    resultado = db.delete_all_launches_and_rollback(uid)

    assert resultado["remaining"] is None, resultado
    evento = _evento_accounts(coletor, "recontagem falhou")
    assert evento["user_id"] == uid, evento
