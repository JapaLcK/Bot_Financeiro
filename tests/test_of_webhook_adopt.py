"""G5b — o webhook ADOTA o item órfão que ninguém reivindicou.

Continuação de `test_of_item_ownership.py` (helpers de lá e de
`test_of_webhook_adopt_guards.py`; separado só porque o arquivo bateria no teto
de 350 linhas do `test_max_lines_python.py`).

O buraco que estes testes fecham está escrito em UM lugar (CLAUDE.md §0.7): a
docstring de `frontend/routes/open_finance._adota_item_orfao`, "Por que existe".

CONTROLE NEGATIVO do grupo:
  • `test_adocao_desligada_volta_a_deixar_o_dono_sem_linha` desliga
    `_adota_item_orfao` e o caso do teste positivo volta a `connections == []`;
  • `test_conta_apagada_no_meio_da_adocao_nao_ressuscita` tem o seu: tirar o
    `criar_usuario=False` da adoção e ele volta a achar a conta recriada.
CONTROLE POSITIVO (a adoção RESTRINGE nada, mas ADICIONA escrita num caminho de
posse): `test_item_sem_client_user_id_mantem_o_comportamento_de_hoje` prova que
o ramo antigo continua de pé quando não há dono a resolver.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
# Helpers de _guards, como o `_dupe.py` já fazia: eram cópias byte a byte aqui
# (CLAUDE.md §0.1). `_limpa_item` apaga registry E conexão — antes havia um
# `_limpa_registry` local que só apagava metade, com outro nome.
from test_of_webhook_adopt_guards import (  # noqa: F401
    _existe_user, _limpa_item, _mock_item, _registry, webhook_pluggy)


def test_webhook_adota_item_orfao_e_a_tela_passa_a_mostrar_o_banco(
        user_id, monkeypatch, eventos, webhook_pluggy):
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        resp = _webhook(client, "item/created", "item-orfao")
        assert resp.status_code == 200, resp.text

        linhas = db.get_connections_by_item_id("item-orfao")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert webhook_pluggy == ["item-orfao"], "o sync não precisa mais do navegador"
        assert [r["origin"] for r in _registry("item-orfao")] == ["webhook_adopt"]

        snap = client.get(f"/open-finance/{user_id}", headers=_auth(client, user_id)).json()
        assert [c["ui"]["state"] for c in snap["connections"]] == ["updating"], snap
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("item-orfao")


def test_adocao_desligada_volta_a_deixar_o_dono_sem_linha(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """CONTROLE NEGATIVO, injetado no caso que estava VERDE (o de cima)."""
    _mock_item(monkeypatch, user_id)

    async def _sem_adocao(item_id, last_event=None):
        return None
    monkeypatch.setattr(of_routes, "_adota_item_orfao", _sem_adocao)
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "item-orfao").status_code == 200
        assert db.get_connections_by_item_id("item-orfao") == [], "sem o fix, nenhuma linha nasce"
        assert webhook_pluggy == []
        assert any(e["event"] == "of_webhook_item_unknown" for e in eventos), eventos
    finally:
        _limpa_item("item-orfao")


def test_adocao_respeita_o_dono_remoto_e_nao_vaza_para_o_usuario_errado(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """O dono vem do `clientUserId` REMOTO — o corpo do webhook não decide posse."""
    dono = user_id + 1
    db.ensure_user(dono)
    _mock_item(monkeypatch, dono)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "item-do-outro").status_code == 200

        linhas = db.get_connections_by_item_id("item-do-outro")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == dono, linhas

        snap = client.get(f"/open-finance/{user_id}", headers=_auth(client, user_id)).json()
        assert snap["connections"] == [], "item de outra conta não pode aparecer aqui"
    finally:
        db.disconnect_open_finance_connection(dono)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (dono,))
            c.commit()
        _limpa_item("item-do-outro")


def test_item_sem_client_user_id_mantem_o_comportamento_de_hoje(
        monkeypatch, eventos, webhook_pluggy):
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: {"id": item_id, "status": "UPDATING"})
    try:
        r = _webhook(TestClient(dashboard.app), "item/created", "item-sem-dono")
        assert r.status_code == 200, r.text
        assert db.get_connections_by_item_id("item-sem-dono") == []
        assert webhook_pluggy == []
        assert any(e["event"] == "of_webhook_item_unknown" for e in eventos), eventos
        assert _registry("item-sem-dono") == [{"origin": "webhook", "user_id": None}]
    finally:
        _limpa_item("item-sem-dono")


def test_teto_de_bancos_estourado_nao_adota_e_o_webhook_responde_200(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """402 para a Pluggy viraria retentativa em laço: o webhook engole e registra."""
    from fastapi import HTTPException

    _mock_item(monkeypatch, user_id)

    async def _estoura(uid, new_item_id=None):
        raise HTTPException(status_code=402, detail={"code": "OF_BANK_LIMIT"})
    monkeypatch.setattr(of_routes, "_enforce_bank_limit", _estoura)
    try:
        r = _webhook(TestClient(dashboard.app), "item/created", "item-no-teto")
        assert r.status_code == 200, r.text
        assert db.get_connections_by_item_id("item-no-teto") == [], "nada podia ser gravado"
        assert webhook_pluggy == []
        pulado = [e for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert pulado, eventos
        # `motivo="HTTPException"` sozinho é o nome de TRÊS desfechos com ações
        # de operador diferentes — 402 (teto do plano), 409 (o estado sumiu) e
        # 503 (lock ocupado). Sem o status/detail, o log não separa nenhum deles.
        assert pulado[0]["details"]["error"].startswith("402"), pulado[0]["details"]
        assert "OF_BANK_LIMIT" in pulado[0]["details"]["error"], pulado[0]["details"]
        assert [r["origin"] for r in _registry("item-no-teto")] == ["webhook"]
    finally:
        _limpa_item("item-no-teto")


def test_adotado_o_delete_enumera_e_apaga_o_item_na_pluggy(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """É ESTE assert que prova que o dono consegue reconectar.

    Sem a linha local, `list_pluggy_item_ids` devolvia `[]`, o item continuava
    vivo na Pluggy e o `avoidDuplicates` do connect token recusava item novo.
    """
    _mock_item(monkeypatch, user_id)
    apagados: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key-fake")
    monkeypatch.setattr(of_routes, "delete_pluggy_item",
                        lambda item_id, api_key=None: apagados.append(item_id))
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "item-travado").status_code == 200

        resp = client.delete(f"/open-finance/{user_id}", headers=_auth(client, user_id))

        assert resp.status_code == 200, resp.text
        assert apagados == ["item-travado"], "o item ficaria órfão na Pluggy"
        assert db.get_connections_by_item_id("item-travado") == []
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("item-travado")


def test_pluggy_item_depois_da_adocao_nao_duplica_a_conexao(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """O webhook adota e DEPOIS o `onSuccess` do widget posta o mesmo item.

    As duas escritas passam por `_grava_reconexao` → `pluggy_item_lock` do MESMO
    item, e o upsert casa em `uq_of_conn_provider_item`. Uma linha só.

    NÃO é a corrida (isto é sequencial, num processo só) — o nome antigo dizia
    "corrida" e o assert final (`len == 1`) ficava VERDE com a adoção desligada,
    porque o POST sozinho também escreve uma linha. O assert do MEIO é o que
    discrimina: sem adoção, ali há zero linhas.
    """
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "item-corrida").status_code == 200
        assert len(db.get_connections_by_item_id("item-corrida")) == 1, \
            "pré-condição: a adoção gravou a linha ANTES do POST do navegador"

        resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": "item-corrida"}},
                           headers=_auth(client, user_id))

        assert resp.status_code == 200, resp.text
        assert len(db.get_connections_by_item_id("item-corrida")) == 1
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("item-corrida")


def test_adocao_nao_agenda_sync_de_item_que_a_pluggy_ainda_esta_montando(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Em `item/created` o status normal é `CREATED`/`UPDATING`: o item ainda não
    tem conta nem transação, e pode estar esperando MFA.

    Agendar sync ali é uma task esperando o item sair de UPDATING para achar
    zero conta. O `item/updated` seguinte sincroniza sozinho — e a essa altura a
    conexão JÁ existe, então ele entra pelo caminho comum (`len(conexoes) == 1`),
    que é o assert final aqui.
    """
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: {"id": item_id, "status": "UPDATING",
                                                       "clientUserId": str(user_id),
                                                       "connector": {"id": 612, "name": "Nubank"}})
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "item-updating").status_code == 200
        assert len(db.get_connections_by_item_id("item-updating")) == 1, "a adoção acontece"
        assert webhook_pluggy == [], "sync agendado num item que a Pluggy ainda está montando"

        assert _webhook(TestClient(dashboard.app), "item/updated", "item-updating").status_code == 200
        assert webhook_pluggy == ["item-updating"], (
            "o evento seguinte tinha de sincronizar pelo caminho comum")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("item-updating")


def test_conta_apagada_no_meio_da_adocao_nao_ressuscita(monkeypatch, eventos, webhook_pluggy):
    """A JANELA entre o `user_exists` e a escrita da conexão (Codex #313, P1).

    `user_exists` lê em transação própria, e `register_item`/`save_pluggy_open_
    finance_item` escrevem em outras duas. O interleaving que o Codex nomeou:
    rastro gravado → `delete_user_data` roda INTEIRO (a cascata leva o rastro
    junto) → a escrita da conexão. Aqui a exclusão é disparada de dentro do
    `register_item` justamente para cair nessa ordem, sem sleep.

    Com `ensure_user_tx` no caminho, essa última escrita RECRIAVA a linha de
    `users` que a LGPD acabou de apagar e pendurava a conexão nela — a guarda de
    identidade não alcança, porque no instante em que ela leu a conta existia.
    Quem alcança é a FK, e só com `criar_usuario=False`.
    """
    fantasma = 987654321988
    db.ensure_user(fantasma)
    _mock_item(monkeypatch, fantasma)

    real_register = of_routes.register_item

    def _apaga_a_conta_no_meio(*a, **k):
        rid = real_register(*a, **k)
        with get_conn() as c:      # a exclusão commita DEPOIS do user_exists
            c.execute("delete from users where id=%s", (fantasma,))
            c.commit()
        return rid

    monkeypatch.setattr(of_routes, "register_item", _apaga_a_conta_no_meio)
    try:
        r = _webhook(TestClient(dashboard.app), "item/created", "item-lgpd-corrida")

        assert r.status_code == 200, f"{r.status_code}: a Pluggy retenta em laço"
        assert not _existe_user(fantasma), (
            "a escrita da conexão RECRIOU a conta que a exclusão apagou no meio")
        assert db.get_connections_by_item_id("item-lgpd-corrida") == [], \
            "conexão pendurada numa conta que não existe mais"
        motivos = [e["details"].get("motivo") for e in eventos
                   if e["event"] == "of_webhook_adopt_skipped"]
        assert motivos == ["ForeignKeyViolation"], (
            f"a FK tinha de ser quem recusa nesta janela: {motivos}")
    finally:
        _limpa_item("item-lgpd-corrida")
        with get_conn() as c:
            c.execute("delete from users where id=%s", (fantasma,))
            c.commit()
