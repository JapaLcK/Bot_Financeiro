"""G5c — as GUARDAS da adoção de item órfão pelo webhook da Pluggy.

O caminho feliz mora em `test_of_webhook_adopt.py`; aqui ficam os casos que o
Tester provou rodando contra a 1ª versão da adoção, e que ela perdia. Arquivo
separado porque os dois juntos passam do teto de 350 linhas
(`tests/test_max_lines_python.py`), não por assunto diferente.

O que cada bloco prende (todos já foram vermelhos):

  • RESSURREIÇÃO DE CONTA APAGADA — `save_pluggy_open_finance_item` chama
    `ensure_user_tx`, que INSERE em `users`. A FK de
    `open_finance_connections.user_id` nunca dispara, então "a constraint
    resolve" era falso: um webhook com `clientUserId` de conta apagada por LGPD
    (`db/privacy.py` faz `delete from users`, e o item na Pluggy só some em
    best-effort) recriava a linha de `users` e pendurava uma conexão nela.
  • RESSURREIÇÃO DE BANCO REMOVIDO — adotar em QUALQUER evento de
    `PLUGGY_SYNC_EVENTS` reabria pelo webhook o buraco que o
    `_disconnect_sob_lock` fechou pelo lado do disconnect.
  • 5xx PARA A PLUGGY — falha de escrita depois da conexão commitada devolvia
    500 (retentativa em laço) e deixava conexão sem rastro no registry.
  • Auditoria e a régua de posse: as duas portas (`POST /pluggy-item` e a
    adoção) têm de decidir igual sobre o MESMO `clientUserId`.

CONTROLE POSITIVO do grupo: `test_item_created_continua_adotando_o_dono_legitimo`
— sem ele, tudo aqui passaria num código que simplesmente não adota nada.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from test_of_item_ownership import SEGREDO, _auth, _item_remoto, _webhook, eventos  # noqa: F401


@pytest.fixture()
def webhook_pluggy(monkeypatch):
    """Webhook autorizado e sync inerte. Devolve a lista de ids agendados."""
    agendados: list[str] = []
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: agendados.append(i))
    return agendados


def _mock_item(monkeypatch, client_user_id):
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: _item_remoto(item_id, client_user_id))


def _limpa_item(item_id: str):
    """Apaga TUDO daquele item — rastro no registry E conexão —, para os 3 arquivos.

    Eram dois nomes para duas limpezas QUASE iguais (este e um `_limpa_registry`
    que não apagava a conexão): `handlers/credit.py` × `core/handlers/credit.py`
    em miniatura (CLAUDE.md §0.1). Apagar a conexão a mais é seguro no teardown.
    """
    with get_conn() as c:
        c.execute("delete from open_finance_item_registry where provider_item_id=%s", (item_id,))
        c.execute("delete from open_finance_connections where provider_item_id=%s", (item_id,))
        c.commit()


def _registry(item_id: str) -> list[dict]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select origin, user_id from open_finance_item_registry "
                        "where provider_item_id=%s order by id", (item_id,))
            return [dict(r) for r in (cur.fetchall() or [])]


def _existe_user(uid: int) -> bool:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select 1 from users where id=%s", (uid,))
            return cur.fetchone() is not None


# ── controle POSITIVO: a adoção legítima continua acontecendo ────────────────

def test_item_created_continua_adotando_o_dono_legitimo(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Sem este caso, todo o resto do arquivo passaria com a adoção deletada."""
    _mock_item(monkeypatch, user_id)
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "g-ok").status_code == 200
        linhas = db.get_connections_by_item_id("g-ok")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == user_id, linhas
        assert webhook_pluggy == ["g-ok"]
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-ok")


# ── conta apagada (LGPD) não pode voltar a existir ───────────────────────────

def test_usuario_inexistente_nao_e_criado_pelo_webhook(monkeypatch, eventos, webhook_pluggy):
    """`ensure_user_tx` CRIA a linha de `users`: a FK nunca chega a ser testada.

    Medido antes da guarda: 200, linha nova em `users` e conexão OF pendurada
    nela, com `origin='webhook_adopt'` no registry.
    """
    fantasma = 987654321987
    _mock_item(monkeypatch, fantasma)
    try:
        assert not _existe_user(fantasma), "pré-condição: a conta não existe"

        r = _webhook(TestClient(dashboard.app), "item/created", "g-fantasma")

        assert r.status_code == 200, r.text
        assert not _existe_user(fantasma), (
            "o webhook RECRIOU a linha de `users` de uma conta que não existe "
            "(é assim que uma conta apagada por LGPD ressuscita)")
        assert db.get_connections_by_item_id("g-fantasma") == []
        assert _registry("g-fantasma") == [{"origin": "webhook", "user_id": None}], \
            "sem dono adotável, o rastro é o mesmo de antes da adoção"
        # O MOTIVO, não só o desfecho: com a guarda desligada o desfecho continua
        # certo por acidente — a FK de `open_finance_item_registry.user_id` recusa
        # o rastro e a adoção morre ali. Duas defesas para o mesmo estrago é bom;
        # depender só da segunda é o que já falhou uma vez (a FK da conexão nunca
        # dispara — hoje ela dispara: a adoção grava com `criar_usuario=False`,
        # e esse é o conserto do P1 do Codex #313, não desta guarda). Este assert é o
        # que separa "recusamos por identidade" de "o banco recusou por acaso".
        motivos = [e["details"].get("motivo") for e in eventos
                   if e["event"] == "of_webhook_adopt_skipped"]
        assert motivos == ["usuario_inexistente"], motivos
    finally:
        _limpa_item("g-fantasma")
        with get_conn() as c:
            c.execute("delete from users where id=%s", (fantasma,))
            c.commit()


# ── banco removido pelo usuário não pode voltar sozinho ──────────────────────

def test_evento_posterior_nao_ressuscita_o_banco_que_o_usuario_removeu(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """`delete_pluggy_items_best_effort` engole a falha e apaga só o local.

    O item continua VIVO na Pluggy e continua mandando evento. Adotar em
    `transactions/created` reatava a conexão E agendava sync — que re-importa
    lançamento e compra de cartão na carteira que o usuário acabou de limpar.
    É o mesmo buraco que o `_disconnect_sob_lock` fecha pelo outro lado.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(of_routes, "delete_pluggy_item",
                        lambda i, api_key=None: (_ for _ in ()).throw(RuntimeError("Pluggy 503")))
    client = TestClient(dashboard.app)
    try:
        db.save_pluggy_open_finance_item(
            user_id, {"id": "g-zumbi", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert db.get_connections_by_item_id("g-zumbi"), "pré-condição: conectado"

        assert client.delete(f"/open-finance/{user_id}",
                             headers=_auth(client, user_id)).status_code == 200
        assert db.get_connections_by_item_id("g-zumbi") == [], "pré-condição: removeu o local"
        webhook_pluggy.clear()

        assert _webhook(client, "transactions/created", "g-zumbi").status_code == 200

        volta = db.get_connections_by_item_id("g-zumbi")
        assert volta == [], (
            f"o banco que o usuário REMOVEU voltou sozinho: {len(volta)} conexão(ões)")
        assert webhook_pluggy == [], "e com sync agendado, que re-importa o que ele apagou"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-zumbi")


@pytest.mark.parametrize("evento", ["item/updated", "transactions/created", "transactions/updated"])
def test_so_item_created_adota(user_id, monkeypatch, eventos, webhook_pluggy, evento):
    """A categoria inteira, não só o evento do repro: item sem conexão que recebe
    evento POSTERIOR é item que JÁ TEVE conexão e foi removida."""
    _mock_item(monkeypatch, user_id)
    try:
        assert _webhook(TestClient(dashboard.app), evento, "g-evento").status_code == 200
        assert db.get_connections_by_item_id("g-evento") == [], f"{evento} adotou"
        assert _registry("g-evento") == [{"origin": "webhook", "user_id": None}]
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-evento")


# ── nada pode virar 5xx para a Pluggy, nem estado parcial ────────────────────

def test_registry_fora_do_ar_nao_vaza_500_nem_grava_conexao_sem_rastro(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """`register_item` levantando dava 500 — a Pluggy retenta em laço — e, na
    ordem antiga (conexão primeiro), deixava conexão commitada com registry
    VAZIO: item adotado sem nenhum rastro para o script achar."""
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("registry fora do ar")))
    try:
        r = _webhook(TestClient(dashboard.app), "item/created", "g-reg")

        assert r.status_code == 200, f"{r.status_code}: a Pluggy retenta em laço"
        linhas = db.get_connections_by_item_id("g-reg")
        assert not linhas or _registry("g-reg"), (
            f"conexão gravada ({len(linhas)}) e NENHUM rastro no registry")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-reg")


def test_sync_falhando_nao_vaza_500_e_a_adocao_fica_de_pe(
        user_id, monkeypatch, eventos):
    """`_schedule_pluggy_sync` estava FORA do try: exceção dele = 500. E, como a
    conexão já está gravada quando ele roda, a adoção não pode ser desfeita."""
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync",
                        lambda i: (_ for _ in ()).throw(RuntimeError("loop fechado")))
    try:
        r = _webhook(TestClient(dashboard.app), "item/created", "g-sched")

        assert r.status_code == 200, f"{r.status_code}: a Pluggy retenta em laço"
        assert len(db.get_connections_by_item_id("g-sched")) == 1, "a adoção aconteceu"
        assert [x["origin"] for x in _registry("g-sched")] == ["webhook_adopt"]
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-sched")


# ── auditoria e régua de posse ───────────────────────────────────────────────

def test_adocao_grava_a_mesma_auditoria_do_pluggy_item(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Conexão de Open Finance nascendo sem `OPEN_FINANCE_CONNECTED` é conexão
    que não aparece no histórico de segurança do usuário."""
    from core.audit import AuditEvent

    auditados: list[tuple] = []
    monkeypatch.setattr(of_routes, "record_audit_event",
                        lambda uid, evento, **kw: auditados.append((uid, evento, kw)))
    _mock_item(monkeypatch, user_id)
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "g-audit").status_code == 200
        assert db.get_connections_by_item_id("g-audit"), "pré-condição: adotou"
        assert [(a[0], a[1]) for a in auditados] == \
            [(user_id, AuditEvent.OPEN_FINANCE_CONNECTED)], auditados
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-audit")


# TODOS os cinco derivam do id do usuário que EXISTE no teste: é isso que os
# torna discriminantes. Com literais fixos (`'١٢'`, `'1_2'`), o `int()` dava 12,
# `user_exists(12)` era falso no banco de teste e o caso passava VERDE com a
# régua desligada — 2 dos 5 anunciavam discriminação e não mediam nada.
_FORJA = {
    "espacos": lambda u: f" {u} ",
    "mais": lambda u: f"+{u}",
    "zero_a_esquerda": lambda u: f"0{u}",
    "arabe_indico": lambda u: str(u).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")),
    "pep515": lambda u: "_".join(str(u)),   # 12345 → '1_2_3_4_5', que int() lê como 12345
}


@pytest.mark.parametrize("forja", list(_FORJA), ids=list(_FORJA))
def test_as_duas_portas_decidem_igual_sobre_o_mesmo_client_user_id(
        user_id, monkeypatch, eventos, webhook_pluggy, forja):
    """A docstring prometia "a mesma discriminação do POST /pluggy-item", e a
    rota compara STRING enquanto a adoção fazia `int()`.

    `int()` aceita mais: `' 5 '`, `'+5'`, `'007'`, dígitos árabe-índicos e o
    sublinhado da PEP 515 — e os cinco viram o id do usuário REAL do teste, que
    passa em `user_exists`. Nenhum é escolhido por atacante hoje (nós setamos o
    `clientUserId` em `core/services/pluggy.py`), mas duas portas divergindo no
    MESMO valor é a definição de dupla fonte de verdade.
    """
    valor = _FORJA[forja](user_id)
    assert valor != str(user_id) and int(valor) == user_id, (
        f"o caso {forja} não discrimina: {valor!r}")
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: {"id": item_id, "status": "UPDATED",
                                                       "clientUserId": valor,
                                                       "connector": {"id": 612, "name": "Nubank"}})
    client = TestClient(dashboard.app)
    try:
        rota = client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": "g-regua"}}, headers=_auth(client, user_id))
        rota_aceitou = rota.status_code == 200
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-regua")

        _webhook(client, "item/created", "g-regua")
        adocao_aceitou = bool(db.get_connections_by_item_id("g-regua"))

        assert rota_aceitou == adocao_aceitou, (
            f"clientUserId={valor!r}: /pluggy-item aceitou={rota_aceitou} "
            f"({rota.status_code}) e a adoção aceitou={adocao_aceitou}")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-regua")


def test_webhook_antes_do_navegador_audita_uma_vez_por_conexao(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """`item/created` dispara na CRIAÇÃO do item; o `onSuccess` do widget (que
    chama o `POST /pluggy-item`) só dispara em status final — a ordem NORMAL em
    produção é webhook primeiro. Então a adoção roda no fluxo comum, e as duas
    portas gravavam `OPEN_FINANCE_CONNECTED` para a MESMA conexão: o histórico
    de segurança do usuário mentia sobre quantas vezes ele conectou o banco.

    O espião GRAVA de verdade (ele chama o `record_audit_event` real) porque a
    guarda passou a exigir evidência DURÁVEL da auditoria do webhook — um stub que
    só anota numa lista deixava `audit_events` vazio, e a rota (com razão) auditava
    de novo. Com o stub mudo este caso ficaria verde num código sem dedup nenhuma.
    """
    from core.audit import AuditEvent

    auditados: list[int] = []
    real_audit = of_routes.record_audit_event

    def _espia(uid, evento, **kw):
        if evento == AuditEvent.OPEN_FINANCE_CONNECTED:
            auditados.append(uid)
        return real_audit(uid, evento, **kw)

    monkeypatch.setattr(of_routes, "record_audit_event", _espia)
    _mock_item(monkeypatch, user_id)
    client = TestClient(dashboard.app)
    try:
        assert _webhook(client, "item/created", "g-2x").status_code == 200
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "g-2x"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text
        assert auditados == [user_id], (
            f"{len(auditados)} OPEN_FINANCE_CONNECTED para UMA conexão: {auditados}")

        # CONTROLE POSITIVO: sem webhook antes, o POST continua auditando — senão
        # o assert acima passaria num código que parou de auditar a rota.
        auditados.clear()
        assert client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": "g-so-post"}},
                           headers=_auth(client, user_id)).status_code == 200
        assert auditados == [user_id], auditados
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("g-2x")
        _limpa_item("g-so-post")
