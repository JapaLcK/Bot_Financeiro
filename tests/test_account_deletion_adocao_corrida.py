"""P2 do Codex (Onda 4, PR-C): a exclusão de conta × a adoção de item pelo webhook.

ASSUNTO PRÓPRIO, e é por isso que o arquivo é novo: os dois irmãos
(`tests/test_account_deletion_pluggy.py`, "a exclusão deleta o item na Pluggy", e
`tests/test_account_deletion_pii_logs.py`, "o resíduo de PII nos logs") medem UM
processo cada; aqui os dois correm ao MESMO tempo, com barreira de thread, e o
defeito só existe no cruzamento. Os dois já passam do teto de 350 linhas
(`tests/test_max_lines_python.py`) e nenhum usa concorrência.

O DEFEITO (reproduzido pelo Tester antes do conserto): dentro da transação de
exclusão, DEPOIS do `delete from open_finance_connections ... returning` e ANTES
do `delete from users`, outra sessão ainda consegue commitar uma conexão nova — a
linha de `users` existe até ali, e `user_exists` (db/users.py) responde True
durante a exclusão AGENDADA de propósito. O `delete from users` levava essa
conexão pela CASCATA, e o `provider_item_id` dela nunca esteve no `RETURNING` que
alimenta o delete remoto: item vivo, e PAGO, na Pluggy depois de uma exclusão
LGPD. Porta única em produção: o webhook `item/created` → `_adota_item_orfao`
(o `POST /pluggy-item` já morria em 403).

O conserto tem duas metades, e cada teste aqui mede UMA:
  • T18 = a PORTA: a adoção por webhook recusa conta com exclusão agendada;
  • T17 = o CINTO: a exclusão reconsulta `open_finance_connections` antes do
    `delete from users` e soma ao `pluggy_items_swept`, então o 2º passe deleta o
    item mesmo que a conexão tenha entrado por outro caminho.

CONTROLES DO GRUPO (CLAUDE.md §3), cada mutação injetada em caso VERDE:
  • negativo — apagar o bloco da reconsulta (o `if _table_exists(cur,
    "open_finance_connections")` imediatamente ANTES do `delete from users`, em
    `db/privacy.delete_user_data`): T17 vermelho;
  • negativo — apagar o `if await asyncio.to_thread(is_account_scheduled_for_deletion,
    dono)` de `_adota_item_orfao` (`frontend/routes/open_finance.py`): T18 vermelho;
  • SEPARAÇÃO, medida nas duas mutações acima: com só a PORTA desligada T17 fica
    VERDE (é a prova de que o cinto mede sozinho — T17 escreve pelo
    `save_pluggy_open_finance_item`, a MESMA escrita da adoção, sem passar pelo
    webhook), e com só o CINTO desligado T18 fica VERDE. Com as DUAS desligadas:
    T17 e T18 vermelhos, T18b verde;
  • positivo — `test_t18b_conta_sem_exclusao_agendada_continua_sendo_adotada`: sem
    ele o grupo passaria num código que recusa TODA adoção. O positivo canônico da
    adoção mora em
    `tests/test_of_webhook_adopt_guards.py::test_item_created_continua_adotando_o_dono_legitimo`;
    o daqui é o par IMEDIATO da guarda nova (mesma conta, mesmo webhook, só o
    `deletion_status` muda) e é o que separa "recusa por exclusão agendada" de
    "recusa por qualquer motivo".

CLASSE CEGA declarada: a janela que SOBRA entre a reconsulta e o `delete from
users` não é medida aqui — é a mesma barreira de `_table_exists` apontada para o
statement seguinte, e o Tester a declarou raciocínio, não medição. Também não há
Pluggy de verdade em nenhum caso (o `delete_pluggy_item` é dublê).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import db.privacy as privacy
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from test_account_deletion_pluggy import _existe_usuario, _item_de, _mocka_pluggy, _semeia

SEGREDO = "test-webhook-secret-p2"


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` nasce preguiçosamente em `core/admin_dashboard.py`; sem
    ela o INSERT do log falha em silêncio e T18 não mediria o rastro."""
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


def _limpa_item(item_id: str):
    """Teardown por ITEM: a conta de T18 sobrevive ao teste (não foi excluída) e a
    de T17 já foi apagada, mas os logs vão com `user_id` NULL em parte dos casos e
    nenhuma cascata os leva."""
    with get_conn() as conn:
        conn.execute("delete from open_finance_item_registry where provider_item_id=%s", (item_id,))
        conn.execute("delete from open_finance_connections where provider_item_id=%s", (item_id,))
        conn.execute("delete from system_event_logs where details::text like %s", (f"%{item_id}%",))
        conn.commit()


def _conexoes_do_item(item_id: str) -> list[dict]:
    """SEM filtro de user_id de propósito: a pergunta é se a linha sobreviveu em
    QUALQUER dono (o item id é único por caso)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from open_finance_connections where provider_item_id=%s",
                (item_id,),
            )
            linhas = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return linhas


def _skips(item_id: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, details from system_event_logs
                where event_type = 'of_webhook_adopt_skipped'
                  and details::text like %s
                order by id
                """,
                (f"%{item_id}%",),
            )
            linhas = [dict(r) for r in cur.fetchall()]
        conn.commit()
    return linhas


# ── T17 (o CINTO) ───────────────────────────────────────────────────────────

def test_t17_conexao_commitada_entre_o_returning_e_o_delete_users(user_id, monkeypatch):
    """A conexão que entra na janela sai pela cascata, e o item TEM de ser
    deletado na Pluggy mesmo assim.

    Barreira REAL, zero sleep: a 1ª pergunta de `_table_exists` por
    "credit_transactions" é o statement imediatamente seguinte ao `delete from
    open_finance_connections ... returning`. Nela a sessão 2 é soltada e a
    transação de exclusão ESPERA o commit dela — determinístico, não temporizado.
    """
    _semeia(user_id)
    item_velho = _item_de(user_id)
    item_novo = f"{item_velho}-t17"
    deletados = _mocka_pluggy(monkeypatch)

    porta = threading.Event()
    concluiu = threading.Event()
    sessao2: dict = {}

    def _sessao2():
        if not porta.wait(30):
            sessao2["erro"] = "porta nunca abriu"
            concluiu.set()
            return
        try:
            # A MESMA escrita de `_adota_item_orfao` (`criar_usuario=False`).
            db.save_pluggy_open_finance_item(
                user_id,
                {"id": item_novo, "status": "UPDATED",
                 "connector": {"id": 613, "name": "Inter"}},
                criar_usuario=False,
            )
        except Exception as exc:  # noqa: BLE001 — é isso que o caso mede
            sessao2["erro"] = f"{type(exc).__name__}: {exc}"
        finally:
            concluiu.set()

    t = threading.Thread(target=_sessao2, name="sessao2-t17", daemon=True)
    t.start()

    real = privacy._table_exists
    acionada = {"ok": False}

    def _hook(cur, table):
        r = real(cur, table)
        if table == "credit_transactions" and not acionada["ok"]:
            acionada["ok"] = True
            porta.set()
            sessao2["commitou_antes_do_delete_users"] = concluiu.wait(30)
        return r

    monkeypatch.setattr(privacy, "_table_exists", _hook)
    try:
        db.process_due_account_deletions(limit=10)
        t.join(10)

        assert acionada["ok"], "a barreira não foi acionada — o caso não mediu nada"
        assert sessao2.get("erro") is None, \
            f"a sessão 2 tinha que ter commitado na janela: {sessao2}"
        assert sessao2.get("commitou_antes_do_delete_users") is True
        assert not _existe_usuario(user_id), "a conta tinha que ter sido excluída"
        assert _conexoes_do_item(item_novo) == [], \
            "a conexão da janela tinha que sair pela cascata do `delete from users`"
        assert item_velho in deletados, "o item enumerado tinha que sair no 1º passe"
        assert item_novo in deletados, (
            "ITEM ÓRFÃO NA PLUGGY: a conexão commitada na janela saiu pela cascata "
            "sem entrar no `pluggy_items_swept` — o 2º passe nunca deletou o item"
        )
    finally:
        _limpa_item(item_novo)
        _limpa_item(item_velho)


# ── T18 (a PORTA) ───────────────────────────────────────────────────────────

def _webhook_de_item_criado(monkeypatch, dono: int, item_id: str):
    """`item/created` de um item que a Pluggy diz ser do `dono`. Só a resposta
    remota é dublada e o sync fica inerte: as guardas da adoção rodam de verdade.
    """
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: None)
    monkeypatch.setattr(
        of_routes, "get_pluggy_item",
        lambda item, api_key=None: {
            "id": item, "clientUserId": str(dono), "status": "UPDATED",
            "connector": {"id": 613, "name": "Inter"},
        },
    )
    client = TestClient(dashboard.app)
    return client.post(
        f"/open-finance/pluggy/webhook?token={SEGREDO}",
        content=json.dumps({"event": "item/created", "itemId": item_id}).encode(),
        headers={"Content-Type": "application/json"},
    )


def test_t18_webhook_nao_adota_item_de_conta_com_exclusao_agendada(user_id, monkeypatch):
    """A porta que o P2 usava. `user_exists` responde True nessa janela, então é
    `is_account_scheduled_for_deletion` que recusa — e o rastro leva o dono na
    COLUNA `user_id` (a conta existe, e a cascata de `system_event_logs` a levará
    no dia da exclusão), nunca em `details`, que a cascata não alcança.
    """
    _semeia(user_id, item=None)  # conta agendada, ZERO conexões
    item_novo = f"{_item_de(user_id)}-t18"
    try:
        r = _webhook_de_item_criado(monkeypatch, user_id, item_novo)

        assert r.status_code == 200, r.text  # nunca 5xx: a Pluggy retentaria
        assert _conexoes_do_item(item_novo) == [], (
            "o webhook adotou um item para conta em exclusão agendada — a conexão "
            "sai pela cascata do `delete from users` e o item fica vivo na Pluggy"
        )
        skips = _skips(item_novo)
        assert len(skips) == 1, f"esperava 1 rastro de skip, veio {skips}"
        assert skips[0]["details"]["motivo"] == "exclusao_agendada", skips[0]
        assert skips[0]["user_id"] == user_id, \
            "o dono tinha que ir na COLUNA (a conta existe e a cascata a leva)"
        assert "user_id" not in skips[0]["details"], \
            "uid em `details` sobrevive à exclusão: a cascata não alcança o JSON"
    finally:
        _limpa_item(item_novo)


def test_t18b_conta_sem_exclusao_agendada_continua_sendo_adotada(user_id, monkeypatch):
    """CONTROLE POSITIVO, par imediato do T18: a MESMA conta e o MESMO webhook,
    só sem exclusão agendada, continuam adotando. Sem ele o grupo passaria num
    código que recusa tudo — que é pior que o bug.
    """
    _semeia(user_id, agendada=False, item=None)
    item_novo = f"{_item_de(user_id)}-t18b"
    try:
        r = _webhook_de_item_criado(monkeypatch, user_id, item_novo)

        assert r.status_code == 200, r.text
        assert _conexoes_do_item(item_novo) == [{"user_id": user_id}], \
            "a adoção legítima parou de funcionar"
        assert _skips(item_novo) == [], \
            f"a adoção legítima gravou skip: {_skips(item_novo)}"
    finally:
        _limpa_item(item_novo)
