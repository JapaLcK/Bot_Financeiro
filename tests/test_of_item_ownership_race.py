"""G5f — o item que ganha DONO EM OUTRA CONTA na espera do `pluggy_item_lock`.

Arquivo separado de `test_of_item_ownership.py` pelo mesmo motivo que
`test_of_webhook_adopt_race.py:3-6` registra: os dois juntos passam do teto de
350 linhas (`tests/test_max_lines_python.py`) — não por assunto diferente.

O fato: o `POST /pluggy-item` lê os donos do item FORA do lock e devolve o 409
lá; entre aquela leitura e o `pluggy_item_lock` de `_salva_item_sob_lock` cabe
outro dono inteiro. O upsert de `save_pluggy_open_finance_item` tem `on conflict
(user_id, provider, provider_item_id)`, que para o NOSSO user_id não conflita:
INSERE. Daí os dois desfechos de hoje, que são os dois vermelhos deste arquivo:

  • com o índice `uq_of_conn_provider_item` de pé → `UniqueViolation`, que não é
    `psycopg.OperationalError` e sobe como 500;
  • sem ele (`db/schema.py` o cria dentro de um bloco que só emite warning,
    então em banco com duplicata ele NÃO existe) → 200 e o item com DOIS donos,
    estado em que `get_open_finance_connection_by_item_id` passa a levantar
    `AmbiguousItemError` para os dois e o `_refresh_items_report` cai em `{}`,
    pintando "Ainda não sincronizou" para sempre.

CONTROLES do grupo (rodados, não deduzidos):
  • negativo (a guarda): `if False and outros:` → `test_guarda_le_TODAS_...`
    vermelho;
  • negativo (o desfazimento): `pass` no `unregister_item` do aborto de DONO
    ALHEIO (`frontend/routes/open_finance.py:397`, NÃO o `:477` do rastro, cuja
    linha é idêntica) → `test_adocao_com_dono_novo_no_lock_...` vermelho;
  • negativo (leitura PLURAL): trocar `get_connections_by_item_id` pela singular
    `get_open_finance_connection_by_item_id` → `test_guarda_le_TODAS_...`
    vermelho — mas o MOTIVO envelheceu: era o desfecho (500 por
    `AmbiguousItemError`, porque uma linha alheia não é ambígua e duas são) e
    hoje vem antes dele, `TypeError`, porque a chamada passou a levar `budget_ms=`
    (prazo da etapa 4) e a singular não tem o parâmetro; mais a contagem em
    `test_a_rota_com_item_novo_le_conexoes_uma_vez`. Discrimina igual;
  • negativo (ponto de inserção): mover a guarda para dentro do
    `if tinha_conexao_propria:` → os três casos de dono alheio e o da adoção
    vermelhos, porque item novo e item órfão chegam ao lock com
    `tinha_conexao_propria=False`;
  • positivo: `test_reconexao_do_MESMO_dono_continua_200` — sem ele o grupo
    passaria num código que recusa toda reconexão, que é pior que o bug.
Sem CONTAR os vermelhos: o número envelhece a cada teste novo aqui
(CLAUDE.md §2).

LIMITE CONHECIDO 1 (a injeção): todos injetam a corrida num ponto
DETERMINÍSTICO, dentro de um processo só — nenhum mede intercalação real entre
dois processos. A justificativa de não precisar é o inventário, não a
conveniência: o `pluggy_item_lock` é chaveado por `item_id`
(`db/open_finance_state.py:589`) e há UM escritor de conexão pluggy em produção
— `frontend/routes/open_finance.py:515`, a chamada de
`save_pluggy_open_finance_item`, cujo INSERT é `db/open_finance.py:683` —, então
a serialização que estes testes presumem é a que o lock garante. O único outro
INSERT em `open_finance_connections` é `create_mock_open_finance_connection`
(`db/open_finance.py:125`), alcançável em produção sem lock por
`POST /open-finance/{user_id}/mock-connect` (`open_finance.py:1915`), mas ele
grava `provider='mock_pluggy'` (`db/open_finance.py:142`) e item id escopado por
usuário (`:82`), e a leitura da guarda filtra `provider='pluggy'` — então ele
nunca produz o estado guardado.

LIMITE CONHECIDO 2 (o enquadramento): NENHUMA corrida foi construída — nem pelo
Arquiteto, nem no código, nem pelo Tester, nem pelo Manager. Os dois pontos de
entrada de HOJE derivam o dono de `remote["clientUserId"]` — a rota devolve 403 se
ele não for o da sessão (`frontend/routes/open_finance.py:1489-1496`) e a adoção
faz `dono = int(...)` (`:1021`) —, então duas escritas concorrentes do MESMO item
com donos DIFERENTES exigiriam que o `clientUserId` mudasse entre duas chamadas de
`get_pluggy_item`. Os 7 testes daqui FORÇAM o estado, semeando a linha alheia com
`db.save_pluggy_open_finance_item` direto (`_semeia`, abaixo) e contornando essa
checagem: o que eles provam é o COMPORTAMENTO DA GUARDA DADO O ESTADO, não que o
estado nasça de corrida.

E o estado não precisa de corrida nenhuma. (a) deixou de ser hipótese: é ORIGEM
HISTÓRICA identificada, determinística. Em `bad3a22^` (antes do #161, Onda 1) o
`POST /pluggy-item` era três linhas — `authorize_dashboard_access`,
`_enforce_bank_limit` e `save_pluggy_open_finance_item(user_id, payload.item)` —
sem `get_pluggy_item`, sem checagem de `clientUserId`, sem checagem de dono
alheio, com o `item` vindo DO NAVEGADOR: quem postasse o item id de outra conta
ganhava uma conexão daquele item na sua. E `db/schema.py:520-522` JÁ assume a
duplicata em produção ("se já houver duplicata em produção, a criação falha") —
no mesmo bloco só-warning que cria o `uq_of_conn_provider_item`, ou seja, onde a
duplicata existe o índice NÃO existe, que é exatamente o 2º desfecho da lista
acima. (b) segue HIPÓTESE, não medida, e pediria teste diferente destes:
`avoidDuplicates` devolvendo o mesmo itemId para um segundo `clientUserId` (login
de banco compartilhado entre duas contas).

O NOME do arquivo FICA. "race" é o que os 7 injetam e medem (o dono alheio
nascido na espera do lock, num ponto determinístico); quem não é corrida é a
ORIGEM do estado, e dizer isso é trabalho deste parágrafo, não do nome. Custo de
renomear medido: zero referências externas (`grep -rn test_of_item_ownership_race`
em `*.py`/`*.md` volta vazio) — não foi o custo que decidiu.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from test_of_item_ownership import _auth, _item_remoto, eventos, sem_indice_unico  # noqa: F401
from test_of_webhook_adopt_guards import (_limpa_item, _mock_item,  # noqa: F401
                                          _registry, webhook_pluggy)


def _logs(monkeypatch) -> list[dict]:
    """Coletor do diagnóstico async, emitido depois de liberar o lock."""
    capturados: list[dict] = []

    anterior = of_routes.log_system_event

    async def captura(level, event_type, message, **kw):
        capturados.append({"level": level, "event": event_type, **kw})
        await anterior(level, event_type, message, **kw)

    monkeypatch.setattr(of_routes, "log_system_event", captura)
    return capturados


def _semeia(uid: int, item_id: str) -> None:
    """Conexão daquele item na conta `uid` — o dono que nasce durante a corrida."""
    db.ensure_user(uid)
    db.save_pluggy_open_finance_item(uid, _item_remoto(item_id, uid))


def _donos(item_id: str) -> list[int]:
    return [int(c["user_id"]) for c in db.get_connections_by_item_id(item_id)]


def _post(client: TestClient, user_id: int, item_id: str):
    return client.post(f"/open-finance/{user_id}/pluggy-item",
                       json={"item": {"id": item_id}}, headers=_auth(client, user_id))


def _apaga_users(*uids: int) -> None:
    """Só os ids EXTRAS que o teste criou, pelo nome exato — nunca `like`."""
    with get_conn() as c:
        for uid in uids:
            c.execute("delete from users where id=%s", (uid,))
        c.commit()


def _dono_novo_no_lock(monkeypatch, item_id: str, *outros: int) -> None:
    """Injeta a corrida no ÚLTIMO await antes do `_grava_reconexao` da rota.

    `_enforce_bank_limit` roda DEPOIS do `outros`/`tinha_conexao_propria`
    pré-lock e imediatamente antes da escrita — tudo entre as duas é leitura.
    Substituí-lo não perde cobertura: os tetos de plano estão dormentes na suíte
    (`tests/conftest.py` põe `PLANS_V2_ENABLED=0`). Sem sleep e sem thread: o
    ponto é determinístico.
    """
    async def _semeia_e_segue(uid, novo_item_id=None):
        for outro in outros:
            _semeia(outro, item_id)

    monkeypatch.setattr(of_routes, "_enforce_bank_limit", _semeia_e_segue)


# ── POST /pluggy-item: dono alheio nascido na espera do lock ─────────────────

def test_dono_novo_entre_a_leitura_e_o_lock_devolve_409(user_id, monkeypatch, eventos):
    """Índice de pé. Hoje: `UniqueViolation` na escrita → 500 (por isso o
    `raise_server_exceptions=False`; sem ele o vermelho é a exceção crua)."""
    item, outro = "oflock-t1", user_id + 1
    _mock_item(monkeypatch, user_id)
    logs = _logs(monkeypatch)
    _dono_novo_no_lock(monkeypatch, item, outro)
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    try:
        r = _post(client, user_id, item)

        assert r.status_code == 409, f"{r.status_code}: {r.text[:200]}"
        assert _donos(item) == [outro], "o dono que chegou primeiro tinha que ficar intacto"
        conflitos = [e for e in logs if e["event"] == "of_item_owner_conflict"]
        assert [e["details"]["origin"] for e in conflitos] == ["salva_item_sob_lock"], logs
    finally:
        _limpa_item(item)
        _apaga_users(outro)


def test_dono_novo_no_lock_sem_indice_nao_cria_segundo_dono(
        user_id, monkeypatch, eventos, sem_indice_unico):
    """Sem o índice não há `UniqueViolation`: hoje isto devolve 200 e deixa o
    item com DOIS donos — o estado em que a leitura singular passa a levantar
    `AmbiguousItemError` para os dois usuários, para sempre."""
    item, outro = "oflock-t2", user_id + 1
    _mock_item(monkeypatch, user_id)
    logs = _logs(monkeypatch)
    _dono_novo_no_lock(monkeypatch, item, outro)
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    try:
        r = _post(client, user_id, item)

        assert r.status_code == 409, f"{r.status_code}: {r.text[:200]}"
        assert _donos(item) == [outro], "segundo dono gravado sem o índice para barrar"
        # Não levanta `AmbiguousItemError` — é o que separa "recusamos" de
        # "gravamos e o item virou ambíguo".
        assert int(db.get_open_finance_connection_by_item_id(item)["user_id"]) == outro
        assert any(e["event"] == "of_item_owner_conflict" for e in logs), logs
    finally:
        _limpa_item(item)
        _apaga_users(outro)


def test_guarda_le_TODAS_as_conexoes_e_nao_a_singular(
        user_id, monkeypatch, eventos, sem_indice_unico):
    """DUAS conexões alheias — o único caso que discrimina a leitura plural.

    Com `get_open_finance_connection_by_item_id` no lugar dela, os dois casos
    acima ficam VERDES (uma linha alheia não é ambígua) e este fica vermelho: a
    singular levanta `AmbiguousItemError`, que não é `OperationalError` e vira
    500 exatamente no estado que a guarda existe para conter.
    """
    item, o1, o2 = "oflock-t3", user_id + 1, user_id + 2
    _mock_item(monkeypatch, user_id)
    _logs(monkeypatch)
    _dono_novo_no_lock(monkeypatch, item, o1, o2)
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    try:
        r = _post(client, user_id, item)

        assert r.status_code == 409, f"{r.status_code} (500 = leitura singular): {r.text[:200]}"
        assert sorted(_donos(item)) == sorted([o1, o2]), "as duas linhas alheias ficaram intactas"
        assert user_id not in _donos(item), "gravou uma TERCEIRA conexão para o mesmo item"
    finally:
        _limpa_item(item)
        _apaga_users(o1, o2)


# ── regressão da 2ª revalidação, que passou a consumir a MESMA leitura ───────

def test_estado_proprio_que_sumiu_na_espera_do_lock_continua_409(
        user_id, monkeypatch, eventos):
    """VERDE antes e depois: é guarda de regressão da revalidação de estado, que
    trocou a leitura própria pela releitura compartilhada e não tinha NENHUM
    teste antes deste arquivo."""
    item = "oflock-t4"
    _mock_item(monkeypatch, user_id)
    logs = _logs(monkeypatch)
    _semeia(user_id, item)

    async def _desconecta(uid, novo_item_id=None):
        db.disconnect_open_finance_connection(user_id)

    monkeypatch.setattr(of_routes, "_enforce_bank_limit", _desconecta)
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    try:
        r = _post(client, user_id, item)

        assert r.status_code == 409, f"{r.status_code}: {r.text[:200]}"
        assert "reiniciada ou o banco foi desconectado" in r.json()["detail"], r.text
        assert db.get_connections_by_item_id(item) == [], \
            "a conexão que o usuário mandou apagar ressuscitou"
        assert any(e["event"] == "of_reconnect_aborted_state_gone" for e in logs), logs
    finally:
        _limpa_item(item)


# ── controle POSITIVO ────────────────────────────────────────────────────────

def test_reconexao_do_MESMO_dono_continua_200(user_id, monkeypatch, eventos):
    """Sem injeção nenhuma: o dono reconectando o PRÓPRIO item continua
    passando. Sem este caso o grupo passaria num código que recusa tudo."""
    item = "oflock-t5"
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: None)
    logs = _logs(monkeypatch)
    _semeia(user_id, item)
    client = TestClient(dashboard.app)
    try:
        r = _post(client, user_id, item)

        assert r.status_code == 200, r.text
        assert _donos(item) == [user_id], "a reconexão legítima não gravou"
        assert not [e for e in logs + eventos if e["event"] == "of_item_owner_conflict"], \
            "a guarda recusou o próprio dono"
    finally:
        _limpa_item(item)


# ── adoção pelo webhook: o 409 tem de desfazer a reivindicação ───────────────

def test_adocao_com_dono_novo_no_lock_desfaz_a_reivindicacao(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A adoção grava o rastro COM dono antes da conexão; se o 409 não apagar a
    própria linha, sobra rastro reivindicado e ZERO conexão nossa — o estado
    terminal do P0, do qual nem a retentativa (1ª guarda) nem o script one-shot
    (que filtra fora rastro com dono) tiram o usuário.

    `HTTPException` não é `psycopg.OperationalError`: ela atravessa o `except`
    de infra de `_grava_reconexao` e o desfazimento do 503 nunca roda.
    """
    item, outro = "oflock-t6", user_id + 1
    _mock_item(monkeypatch, user_id)
    _logs(monkeypatch)
    real = of_routes.register_item

    def _registra_e_semeia(*a, **kw):
        registro = real(*a, **kw)
        _semeia(outro, item)      # o outro dono nasce entre o rastro e o lock
        return registro

    monkeypatch.setattr(of_routes, "register_item", _registra_e_semeia)
    try:
        assert asyncio.run(of_routes._adota_item_orfao(item, "item/created")) is None

        assert _registry(item) == [], \
            f"reivindicação abandonada (rastro com dono, 0 conexões): {_registry(item)}"
        assert _donos(item) == [outro], "a adoção gravou por cima do dono que chegou antes"
        assert webhook_pluggy == [], "sync agendado para a carteira do outro dono"
        skips = [e["details"] for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert len(skips) == 1 and "vinculado a outra conta" in skips[0]["error"], skips
    finally:
        _limpa_item(item)
        _apaga_users(outro)


# ── estrutural: a rota com item novo lê as conexões uma vez sob o lock ───────

def test_a_rota_com_item_novo_le_conexoes_uma_vez(user_id, monkeypatch, eventos):
    """DUAS leituras de `get_connections_by_item_id` no POST de item NOVO: a da
    rota, fora do lock, e UMA sob o lock, compartilhada pelas duas revalidações
    (o tempo dela sai do orçamento da escrita, cujo `resto` tem piso de 1 ms).

    NÃO COBRE o caminho da ADOÇÃO (`adocao_registro_id is None` aqui), que é
    justamente onde a contagem sob o lock é DOIS: esta leitura mais o
    `item_registry_origins` da revalidação da adoção. E a espia daqui embrulha só
    `get_connections_by_item_id`, sendo estruturalmente cega ao segundo leitor —
    o nome antigo (`..._a_releitura_sob_o_lock_e_UMA`) prometia um requisito que
    esta asserção não pode falhar por."""
    item = "oflock-t7"
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: None)
    real, lidos = of_routes.get_connections_by_item_id, []

    def _espia(provider_item_id, *a, **kw):
        lidos.append(provider_item_id)
        return real(provider_item_id, *a, **kw)

    monkeypatch.setattr(of_routes, "get_connections_by_item_id", _espia)
    client = TestClient(dashboard.app)
    try:
        assert _post(client, user_id, item).status_code == 200
        assert lidos == [item, item], \
            f"{len(lidos)} leituras do item no POST (a da rota + UMA sob o lock)"
    finally:
        _limpa_item(item)
