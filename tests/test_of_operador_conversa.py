"""PR-E — o diagnóstico do operador lido depois de CONVERSAS reais pelas rotas.

Rotas de verdade (POST /pluggy-item, DELETE /open-finance, webhook) com banco
REAL; a Pluggy é dublada. A pergunta: o estado que o fluxo deixou no banco é o
que o operador vê, e o `--apagar` fecha a marca que o fluxo abriu.
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from db import open_finance_diagnostico as diag
from db.connection import get_conn
from scripts import of_itens_operador as op
from test_of_diagnostico_estados import _linha
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import (  # noqa: F401 — `webhook_pluggy` é fixture
    _existe_user,
    _limpa_item,
    _mock_item,
    webhook_pluggy,
)


@pytest.fixture(autouse=True)
def tabelas_admin():
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


def _limpa(i: str) -> None:
    _limpa_item(i)
    with get_conn() as c:
        c.execute("delete from system_event_logs where details::text like %s", (f"%{i}%",))
        c.commit()


def test_rastro_perdido_desconectado_e_reentregue(user_id, monkeypatch, eventos,
                                                 webhook_pluggy):
    """POST com `register_item` quebrado → CONECTADO_SEM_RASTRO → DELETE →
    REMOVIDO → `item/created` reentregue não adota e o estado não muda."""
    promote_to_pro(user_id)   # no Grátis `of_banks_max=0` e o POST mede 402
    _mock_item(monkeypatch, user_id)
    item = "conv-sem-rastro"
    real = of_routes.register_item

    def _sem_pluggy_item(*a, **kw):
        if kw.get("origin") == "pluggy_item":
            raise psycopg.OperationalError("registry fora do ar")
        return real(*a, **kw)

    try:
        monkeypatch.setattr(of_routes, "register_item", _sem_pluggy_item)
        c1 = TestClient(dashboard.app)
        r = c1.post(f"/open-finance/{user_id}/pluggy-item",
                    json={"item": {"id": item}}, headers=_auth(c1, user_id))
        assert r.status_code == 200, r.text
        monkeypatch.setattr(of_routes, "register_item", real)
        assert diag.classifica_item(item) == diag.CONECTADO_SEM_RASTRO
        assert item in diag.conexoes_sem_rastro()

        monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")
        monkeypatch.setattr(of_routes, "delete_pluggy_item", lambda i, api_key=None: True)
        c2 = TestClient(dashboard.app)
        assert c2.delete(f"/open-finance/{user_id}",
                         headers=_auth(c2, user_id)).status_code == 200
        assert diag.classifica_item(item) == diag.REMOVIDO
        assert diag.listar_sem_conexao()[item] == diag.REMOVIDO

        webhook_pluggy.clear()   # o sync inicial do POST não é deste passo
        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200
        assert diag.classifica_item(item) == diag.REMOVIDO, "a reentrega readotou"
        assert webhook_pluggy == [], "sync agendado para banco removido"
    finally:
        _limpa(item)


def test_item_de_conta_inexistente_com_delete_falho_e_apagado_pelo_operador(
        monkeypatch, eventos, webhook_pluggy):
    """`item/created` de conta que não existe: o webhook tenta apagar, a Pluggy
    responde 503 → a marca aparece → `--apagar` → `operator_delete` → marca some."""
    fantasma, item = 987654321988, "conv-fantasma"
    _mock_item(monkeypatch, fantasma)
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")

    def _503(i, api_key=None):
        raise PluggyApiError("Pluggy 503", status_code=503)

    monkeypatch.setattr(of_routes, "delete_pluggy_item", _503)
    apagados: list[str] = []
    monkeypatch.setattr(op, "delete_pluggy_item", lambda i, api_key=None: apagados.append(i))
    try:
        assert not _existe_user(fantasma), "pré-condição"
        assert _webhook(TestClient(dashboard.app), "item/created", item).status_code == 200
        assert item in diag.itens_com_remocao_remota_falha()
        assert diag.classifica_item(item) == diag.NUNCA_ATRIBUIDO

        assert op.main(["--item", item, "--apagar", "--estado", diag.NUNCA_ATRIBUIDO,
                        "--apply"]) == 0
        assert apagados == [item]
        assert item not in diag.itens_com_remocao_remota_falha()
    finally:
        _limpa(item)


def _falha_de_delete(uid: int, item: str, monkeypatch) -> None:
    """O log de DELETE falho pelo caminho de produção (`delete_pluggy_items_best_effort`)."""
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")

    def _503(i, api_key=None):
        raise RuntimeError("pluggy 503")

    monkeypatch.setattr(of_routes, "delete_pluggy_item", _503)
    of_routes.delete_pluggy_items_best_effort(uid, [item])


def test_404_com_marca_prescreve_o_apagar_que_fecha_a_marca(user_id, monkeypatch, capsys):
    """Sem o comando, `--item` dizia "Nada a apagar" e a marca ficava eterna."""
    item = "conv-404-marca"
    try:
        _falha_de_delete(user_id, item, monkeypatch)

        def _404(i):
            raise PluggyApiError("x", status_code=404)

        monkeypatch.setattr(op, "get_pluggy_item", _404)
        assert op.main(["--item", item]) == 0
        comando = f"--item {item} --apagar --estado {diag.NUNCA_ATRIBUIDO} --apply"
        assert comando in capsys.readouterr().out
        monkeypatch.setattr(op, "delete_pluggy_item", lambda i, api_key=None: True)
        assert op.main(comando.split()) == 0
        assert item not in diag.itens_com_remocao_remota_falha()
    finally:
        _limpa(item)


def test_listar_agrupa_conta_apagados_e_nao_prescreve_id_recusado(
        user_id, monkeypatch, capsys):
    removido, apagado, ruim = "conv-lista-rem", "conv-lista-apag", "1234567"
    try:
        db.register_item(user_id, provider_item_id=removido, origin="removed")
        _falha_de_delete(user_id, removido, monkeypatch)
        db.register_item(user_id, provider_item_id=apagado, origin="pluggy_item")
        db.register_item(None, provider_item_id=apagado, origin="operator_delete")
        db.register_item(None, provider_item_id=ruim, origin="webhook")

        assert op.main([]) == 0
        linhas = capsys.readouterr().out.splitlines()
        assert str(user_id) not in "\n".join(linhas), "vazou user_id na lista"
        grupo = next(l for l in linhas if l.startswith("REMOVIDO ("))
        assert removido in grupo
        falha = next(l for l in linhas if l.startswith("remoção remota falhou ("))
        assert f"{removido} [REMOVIDO]" in falha
        assert any("apagado(s) pelo operador" in l for l in linhas)
        assert not any(apagado in l for l in linhas), "apagado pelo operador segue listado"
        recusa = next(l for l in linhas if "FORA da lista" in l)
        assert repr(ruim) in recusa
        assert not any(ruim in l for l in linhas if l.startswith("NUNCA_ATRIBUIDO")), \
            "id recusado pela régua foi contado e prescrito"
    finally:
        for i in (removido, apagado, ruim):
            _limpa(i)


# `ITEMS_SEM_CONEXAO` LITERAL da base (`git show ef472f8f:db/open_finance_state.py`),
# copiado de propósito: é a "coluna main" da comparação, não uma derivação do atual.
_ITEMS_SEM_CONEXAO_BASE = """
  from open_finance_item_registry r
 where r.provider_item_id is not null
   and not exists (
       select 1 from open_finance_connections c
        where c.provider = r.provider
          and c.provider_item_id = r.provider_item_id
   )
"""


def test_card_so_perde_o_item_cuja_ultima_linha_e_operator_delete(user_id):
    """Base × branch, no mesmo banco. ENTRE OS ITEMS SEMEADOS (um por forma de
    rastro), o branch conta exatamente o que a base conta menos o resolvido pelo
    operador. Não prova nada sobre formas de rastro que não estão semeadas."""
    from db.open_finance_state import ITEMS_SEM_CONEXAO

    item = "conv-card"
    sementes = {
        "a": [(user_id, "pluggy_item")],
        "b": [(user_id, "pluggy_item"), (user_id, "removed")],
        "c": [(None, "webhook"), (None, "webhook")],
        "d": [(None, "operator_delete"), (user_id, "pluggy_item")],   # reaberto
        "f": [(user_id, "pluggy_item"), (None, "operator_delete")],   # resolvido
    }
    try:
        for sufixo, linhas in sementes.items():
            for uid, origem in linhas:
                _linha(uid, f"{item}-{sufixo}", origem)

        def _itens(sql):
            with get_conn() as c:
                return {r["i"] for r in c.execute(
                    f"select distinct r.provider_item_id as i {sql}").fetchall()
                    if r["i"].startswith(item)}

        base, branch = _itens(_ITEMS_SEM_CONEXAO_BASE), _itens(ITEMS_SEM_CONEXAO)
        assert base == {f"{item}-{x}" for x in "abcdf"}
        assert branch == base - {f"{item}-f"}
    finally:
        for sufixo in sementes:
            _limpa(f"{item}-{sufixo}")


# ── detalhe (--item): o GET na Pluggy e o dono remoto ───────────────────────

def test_detalhe_mostra_dono_remoto_e_prescreve_o_estado(user_id, monkeypatch, capsys):
    item = "conv-detalhe"
    db.register_item(None, provider_item_id=item, origin="webhook")
    monkeypatch.setattr(op, "get_pluggy_item", lambda i: {
        "status": "UPDATED", "clientUserId": "987654321989", "connector": {"name": "Nubank"}})
    op.main(["--item", item])
    saida = capsys.readouterr().out
    assert "NUNCA_ATRIBUIDO" in saida and "NÃO existe" in saida
    assert f"--item {item} --apagar --estado NUNCA_ATRIBUIDO --apply" in saida

    def _404(i):
        raise PluggyApiError("x", status_code=404)

    monkeypatch.setattr(op, "get_pluggy_item", _404)
    op.main(["--item", item])
    assert "404" in capsys.readouterr().out


def test_lista_so_com_apagados_nao_diz_que_nao_ha_nada(monkeypatch, capsys):
    monkeypatch.setattr(op, "listar_sem_conexao", lambda: {})
    monkeypatch.setattr(op, "itens_com_remocao_remota_falha", lambda: [])
    monkeypatch.setattr(op, "conexoes_sem_rastro", lambda: [])
    monkeypatch.setattr(op, "apagados_pelo_operador", lambda: 3)
    assert op.main([]) == 0
    saida = capsys.readouterr().out
    assert "3 apagado(s) pelo operador" in saida
    assert "Nada no registry" not in saida



# ── rodada 4: REMOVIDO é só contagem, legado só da auditoria ───────────────

def _404(monkeypatch):
    def _get(i):
        raise PluggyApiError("x", status_code=404)

    monkeypatch.setattr(op, "get_pluggy_item", _get)


def test_removido_com_404_e_sem_marca_nao_prescreve_e_nao_e_listado(
        user_id, monkeypatch, capsys):
    """Desconexão normal não é pendência: sem comando no `--item` e, na lista,
    só na contagem."""
    item = "conv-removido-ok"
    try:
        db.register_item(user_id, provider_item_id=item, origin="pluggy_item")
        db.register_item(user_id, provider_item_id=item, origin="removed")
        _404(monkeypatch)
        assert op.main(["--item", item]) == 0
        assert "--apagar" not in capsys.readouterr().out
        assert op.main([]) == 0
        linhas = capsys.readouterr().out.splitlines()
        assert not any(item in l for l in linhas), "REMOVIDO sem falha listado um a um"
        assert any("removido(s) sem falha registrada" in l for l in linhas)
    finally:
        _limpa(item)


def test_removido_com_404_e_com_marca_prescreve_e_e_listado(user_id, monkeypatch, capsys):
    """Controle do anterior: com DELETE falho registrado, o REMOVIDO é pendência."""
    item = "conv-removido-falho"
    try:
        db.register_item(user_id, provider_item_id=item, origin="removed")
        _falha_de_delete(user_id, item, monkeypatch)
        _404(monkeypatch)
        assert op.main(["--item", item]) == 0
        assert f"--item {item} --apagar --estado REMOVIDO --apply" in capsys.readouterr().out
        assert op.main([]) == 0
        grupo = next(l for l in capsys.readouterr().out.splitlines()
                     if l.startswith("REMOVIDO ("))
        assert item in grupo
    finally:
        _limpa(item)


def test_legado_so_da_auditoria_aparece_na_lista_sem_user_id(user_id, capsys):
    from core.audit import AuditEvent, record_audit_event

    so_auditoria, com_linha = "conv-so-auditoria", "conv-auditoria-e-linha"
    try:
        for i in (so_auditoria, com_linha):
            record_audit_event(user_id, AuditEvent.OPEN_FINANCE_CONNECTED,
                               details={"provider": "pluggy", "item_id": i})
        db.register_item(None, provider_item_id=com_linha, origin="webhook")
        assert diag.listar_sem_conexao()[so_auditoria] == diag.LEGADO_AMBIGUO
        assert op.main([]) == 0
        saida = capsys.readouterr().out
        grupo = next(l for l in saida.splitlines() if l.startswith("LEGADO_AMBIGUO ("))
        assert so_auditoria in grupo and grupo.count(com_linha) == 1
        assert str(user_id) not in saida, "vazou o user_id da auditoria"
    finally:
        with get_conn() as c:
            c.execute("delete from audit_events where details->>'item_id' in (%s, %s)",
                      (so_auditoria, com_linha))
            c.commit()
        _limpa(so_auditoria)
        _limpa(com_linha)
