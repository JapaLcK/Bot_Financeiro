"""G5d — entrega DUPLICADA e auditoria de RECONEXÃO.

Terceiro arquivo do mesmo assunto (caminho feliz em `test_of_webhook_adopt.py`,
guardas em `test_of_webhook_adopt_guards.py`), separado só pelo teto de 350
linhas do `tests/test_max_lines_python.py`. Helpers importados de lá — os dois
casos aqui usam exatamente o mesmo cenário, e uma segunda cópia deles seria
duplicação (CLAUDE.md §0.1).

O que cada bloco prende:

  • DUPLICATA DE `item/created` — webhook é entrega AT-LEAST-ONCE, e este
    endpoint devolve 401/400/503 (e pode dar 500 antes do ramo de adoção), o que
    faz a Pluggy retentar o MESMO evento. "Dispara uma vez" era premissa errada:
    a 2ª entrega, depois de o usuário remover o banco, readotava a conexão E
    agendava sync — re-importando lançamento e compra de cartão na carteira que
    ele acabou de limpar. Quem tiver o `PLUGGY_WEBHOOK_SECRET` (que trafega em
    `?token=`) disparava isso à vontade contra um item conhecido.
  • DUAS ENTREGAS AO MESMO TEMPO — a janela entre a leitura do rastro e a escrita
    dela (descrita no "LIMITE CONHECIDO" de `_adota_item_orfao`, fonte única):
    com a leitura antes do `GET /items`, ela era um round-trip HTTP inteiro.
  • RECONEXÃO AUDITADA — a guarda que deduplica a auditoria no
    `POST /pluggy-item` não pode engolir o re-consentimento (o widget reconecta
    banco existente e o `avoidDuplicates` devolve o MESMO itemId), senão
    re-autorizar o banco some de "Atividade da conta" (settings → Segurança).

CONTROLES do grupo:
  • negativo (duplicata): trocar a guarda de `_adota_item_orfao` por `origens =
    set()` → `test_duplicata_de_item_created_nao_ressuscita_banco_removido`
    vermelho, num caso que está VERDE hoje;
  • negativo (concorrência): mover a leitura de `item_registry_origins` de volta
    para ANTES do `get_pluggy_item` →
    `test_segunda_entrega_inteira_dentro_do_get_da_primeira_audita_uma_vez`
    vermelho (2 auditorias), num caso que está VERDE hoje;
  • negativo (auditoria): voltar o `if not conexao_recem_adotada:` para
    `if not tinha_conexao_propria:` → `test_reconexao_do_mesmo_banco_audita_toda
    _vez` vermelho, e o `test_webhook_antes_do_navegador_audita_uma_vez_por_
    conexao` (guards) continua verde — é ele que cobre os outros dois casos:
    conexão nova COM webhook antes, e conexão nova SEM webhook antes;
  • negativo (evidência durável): tirar o `if conexao_recem_adotada:` que exige a
    auditoria do webhook PERSISTIDA →
    `test_auditoria_engolida_no_webhook_nao_apaga_a_da_rota` e
    `test_reconexao_de_conexao_anterior_ao_registry_audita` vermelhos (0 eventos),
    nos dois casos que estão VERDES hoje;
  • negativo (`details` escalar): voltar o `isinstance(e.get("details"), dict)`
    para `(e.get("details") or {})` →
    `test_details_escalar_no_rastro_nao_derruba_o_post` vermelho
    (`AttributeError`), com os outros casos do arquivo verdes;
  • positivo: `test_rastro_sem_dono_nao_bloqueia_a_adocao_legitima` prova que a
    guarda nova recusa por DONO, não por "tem rastro" — senão ela mataria
    justamente o usuário que o PR veio destravar.
  • negativo (janela do handoff): tirar o `and e["created_at"] >= handoff_desde`
    → `test_reconexao_muito_depois_da_adocao_audita` vermelho (0 eventos da
    rota), num caso VERDE hoje; o positivo do par é o handoff LEGÍTIMO
    (`test_webhook_antes_do_navegador_audita_uma_vez_por_conexao`, guards);
"""

from __future__ import annotations

import asyncio

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from fastapi.testclient import TestClient
from test_of_item_ownership import SEGREDO, _auth, _item_remoto, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, _registry, webhook_pluggy  # noqa: F401


def test_duplicata_de_item_created_nao_ressuscita_banco_removido(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """O repro do Tester: adota → o usuário remove → a MESMA entrega chega de novo."""
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "z-res").status_code == 200
        assert len(db.get_connections_by_item_id("z-res")) == 1, "pré-condição: adotou"

        db.disconnect_open_finance_connection(user_id)
        assert db.get_connections_by_item_id("z-res") == [], "pré-condição: o usuário removeu"
        webhook_pluggy.clear()

        r = _webhook(client, "item/created", "z-res")   # duplicata (at-least-once)

        assert r.status_code == 200, f"{r.status_code}: a Pluggy retenta em laço"
        volta = db.get_connections_by_item_id("z-res")
        assert volta == [], (
            f"banco REMOVIDO ressuscitou por uma duplicata de item/created: {len(volta)}")
        assert webhook_pluggy == [], "e com sync agendado, que re-importa o que ele apagou"
        motivos = [e["details"].get("motivo") for e in eventos
                   if e["event"] == "of_webhook_adopt_skipped"]
        assert motivos == ["rastro_com_dono"], motivos
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-res")


def test_rastro_sem_dono_nao_bloqueia_a_adocao_legitima(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """CONTROLE POSITIVO: o item do bug antigo tem rastro (`origin='webhook'`,
    `user_id IS NULL`) e continua adotável — a guarda pergunta por DONO."""
    _mock_item(monkeypatch, user_id)
    db.register_item(None, provider_item_id="z-null", origin="webhook", last_event="item/updated")
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "z-null").status_code == 200
        linhas = db.get_connections_by_item_id("z-null")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert [r["origin"] for r in _registry("z-null")] == ["webhook", "webhook_adopt"]
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-null")


def test_reconexao_do_mesmo_banco_audita_toda_vez(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Re-consentimento / conserto de `LOGIN_ERROR`: o widget reconecta o banco
    existente e o `avoidDuplicates` devolve o MESMO itemId — dois
    `POST /pluggy-item` do mesmo item, e a conexão já está de pé no segundo.

    Medido com a guarda antiga (`if not tinha_conexao_propria:`): 1 evento para
    2 reconexões. O 1º POST aqui é também o caso "conexão nova, sem webhook
    antes" (1 evento), então este teste sozinho não passa num código que parou
    de auditar a rota.
    """
    from core.audit import AuditEvent

    auditados: list[int] = []
    monkeypatch.setattr(of_routes, "record_audit_event",
                        lambda uid, evento, **kw: auditados.append(uid)
                        if evento == AuditEvent.OPEN_FINANCE_CONNECTED else None)
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        for tentativa in (1, 2):
            r = client.post(f"/open-finance/{user_id}/pluggy-item",
                            json={"item": {"id": "z-recon"}}, headers=_auth(client, user_id))
            assert r.status_code == 200, f"POST {tentativa}: {r.text}"
            assert auditados == [user_id] * tentativa, (
                f"reconexão {tentativa}: {len(auditados)} OPEN_FINANCE_CONNECTED — "
                "re-autorizar o banco sumiu de 'Atividade da conta'")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-recon")


def test_segunda_entrega_inteira_dentro_do_get_da_primeira_audita_uma_vez(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """UM interleaving, e o nome diz qual: a 2ª entrega roda INTEIRA enquanto a
    1ª está parada dentro do `GET /items` (at-least-once + retentativa da
    Pluggy). O nome antigo ("entrega concorrente") prometia a classe toda; os
    outros entrelaçamentos — os dois presos DEPOIS da leitura do rastro, que é a
    janela do "LIMITE CONHECIDO" — continuam sem cobertura, de propósito.

    Sem sleep: o mock da Pluggy PRENDE a 1ª entrega num `threading.Event` (ela
    roda em `asyncio.to_thread`, o loop segue livre), a 2ª roda inteira, e só
    então a 1ª volta. Determinístico — o que se mede é a ORDEM das operações, não
    o relógio.

    A conexão é uma só nas duas ordens (upsert em `uq_of_conn_provider_item`); o
    que discrimina é a AUDITORIA, que fica fora do `pluggy_item_lock`.
    """
    import threading

    from core.audit import AuditEvent

    auditados: list[int] = []
    monkeypatch.setattr(of_routes, "record_audit_event",
                        lambda uid, evento, **kw: auditados.append(uid)
                        if evento == AuditEvent.OPEN_FINANCE_CONNECTED else None)

    liberar = threading.Event()
    chamadas: list[str] = []

    def get_preso(item_id, api_key=None):
        chamadas.append(item_id)
        if len(chamadas) == 1:                      # a 1ª entrega fica presa no HTTP
            assert liberar.wait(10), "a 2ª entrega nunca terminou"
        return _item_remoto(item_id, user_id)

    monkeypatch.setattr(of_routes, "get_pluggy_item", get_preso)

    async def cenario():
        primeira = asyncio.create_task(of_routes._adota_item_orfao("z-corrida", "item/created"))
        for _ in range(1000):                       # espera a 1ª ENTRAR no GET
            if chamadas:
                break
            await asyncio.sleep(0.01)
        assert chamadas, "a 1ª entrega não chegou ao GET /items"
        await of_routes._adota_item_orfao("z-corrida", "item/created")   # 2ª, inteira
        liberar.set()
        await primeira

    try:
        asyncio.run(cenario())

        assert auditados == [user_id], (
            f"{len(auditados)} OPEN_FINANCE_CONNECTED para UMA conexão: "
            "as duas entregas leram o rastro vazio")
        assert [r["origin"] for r in _registry("z-corrida")] == ["webhook_adopt"], \
            _registry("z-corrida")
        assert len(db.get_connections_by_item_id("z-corrida")) == 1
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-corrida")


def test_auditoria_engolida_no_webhook_nao_apaga_a_da_rota(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """`record_audit_event` ENGOLE falha de banco (core/audit.py:152).

    Se o insert dele falhar DENTRO da adoção, o rastro `webhook_adopt` fica lá
    dizendo "o webhook auditou" — e a guarda do `POST /pluggy-item` suprimia a
    auditoria da rota por causa dele. Desfecho: conexão viva e NENHUM
    `OPEN_FINANCE_CONNECTED` em "Atividade da conta" (Codex #313, P2).
    """
    from core.audit import AuditEvent, list_audit_events

    real_audit = of_routes.record_audit_event

    def _engole_a_do_webhook(uid, evento, **kw):
        if (kw.get("details") or {}).get("origin") == "webhook_adopt":
            return                      # o insert falhou e ninguém ficou sabendo
        return real_audit(uid, evento, **kw)

    monkeypatch.setattr(of_routes, "record_audit_event", _engole_a_do_webhook)
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "z-sem-audit").status_code == 200
        assert len(db.get_connections_by_item_id("z-sem-audit")) == 1, "pré-condição: adotou"

        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "z-sem-audit"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text

        gravados = [e for e in list_audit_events(user_id, limit=50)
                    if e["event"] == AuditEvent.OPEN_FINANCE_CONNECTED
                    and (e.get("details") or {}).get("item_id") == "z-sem-audit"]
        assert len(gravados) == 1, (
            f"{len(gravados)} eventos: a conexão nasceu e não aparece em "
            "'Atividade da conta' — duplicata é ruído, buraco é perda")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-sem-audit")


def test_reconexao_de_conexao_anterior_ao_registry_audita(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Conexão que existe SEM rastro nenhum (nasceu antes do registry): a guarda
    lia "sem `pluggy_item`" como "o webhook auditou", e o webhook nunca existiu.

    É a 2ª metade do mesmo achado do Codex, e não estava no `ponytail:` que a
    rodada anterior declarou: ali o teto era só o item ADOTADO cujo navegador
    nunca postou.
    """
    from core.audit import AuditEvent, list_audit_events

    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        db.save_pluggy_open_finance_item(
            user_id, {"id": "z-legado", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert _registry("z-legado") == [], "pré-condição: conexão sem rastro"

        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "z-legado"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text

        gravados = [e for e in list_audit_events(user_id, limit=50)
                    if e["event"] == AuditEvent.OPEN_FINANCE_CONNECTED
                    and (e.get("details") or {}).get("item_id") == "z-legado"]
        assert len(gravados) == 1, f"a reconexão sumiu de 'Atividade da conta': {gravados}"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-legado")


def test_details_escalar_no_rastro_nao_derruba_o_post(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Uma linha de `audit_events` com `details` ESCALAR (`'"texto"'::jsonb`)
    fazia o `.get` da guarda estourar `AttributeError` dentro do laço — 500
    PERMANENTE no `POST /pluggy-item` daquele usuário, para sempre, por causa de
    um dado. Nenhum escritor de hoje grava assim; é fronteira de leitura do banco.
    """
    from core.audit import AuditEvent

    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "z-escalar").status_code == 200
        with get_conn() as c:                       # mais NOVA que a do webhook: o
            c.execute(                              # laço bate nela primeiro
                "insert into audit_events (user_id, event, details) values (%s,%s,%s::jsonb)",
                (user_id, AuditEvent.OPEN_FINANCE_CONNECTED, '"texto"'))
            c.commit()

        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "z-escalar"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text
    finally:
        with get_conn() as c:
            c.execute("delete from audit_events where user_id=%s and details=%s::jsonb",
                      (user_id, '"texto"'))
            c.commit()
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-escalar")


def test_reconexao_muito_depois_da_adocao_audita(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Adotado pelo webhook, navegador NUNCA postou: o registry fica sem
    `pluggy_item` PARA SEMPRE, e toda reconexão futura cai no predicado. Sem
    correlação de TEMPO, a de dias depois consumia a auditoria histórica como
    duplicata da adoção de agora: a 1ª reconexão real sumia do log (Codex #313).

    O relógio não é tocado: quem anda é o `created_at` do evento, 2 dias para
    trás contra uma janela de 1h — sem borda, sem flake. O evento velho continua
    DENTRO dos 50 últimos: o que discrimina é a janela, não o limite da consulta.
    """
    from core.audit import AuditEvent, list_audit_events

    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "z-tarde").status_code == 200
        assert [r["origin"] for r in _registry("z-tarde")] == ["webhook_adopt"], \
            "pré-condição: adotou e o navegador nunca postou (sem `pluggy_item`)"
        with get_conn() as c:
            c.execute("update audit_events set created_at = now() - interval '2 days' "
                      "where user_id = %s and event = %s",
                      (user_id, AuditEvent.OPEN_FINANCE_CONNECTED))
            c.commit()

        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "z-tarde"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text

        da_rota = [e for e in list_audit_events(user_id, limit=50)
                   if e["event"] == AuditEvent.OPEN_FINANCE_CONNECTED
                   and isinstance(e.get("details"), dict)
                   and e["details"].get("item_id") == "z-tarde"
                   and e["details"].get("origin") != "webhook_adopt"]
        assert len(da_rota) == 1, (
            f"{len(da_rota)} eventos da rota: a 1ª reconexão real sumiu de "
            "'Atividade da conta' — a auditoria de 2 dias atrás foi lida como "
            "duplicata DESTE POST")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-tarde")
