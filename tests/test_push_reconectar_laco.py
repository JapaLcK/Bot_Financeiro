"""PR 1 do push no app (servidor): o push "um banco precisa de atenção".

Caminho real: a conexão quebra pelo caminho de produção (fixtures de
`tests/test_of_aviso_mesma_regra.py`), o token é registrado pela ROTA com login de
app, e `open_finance_proactive.run_reconnect_push` roda de verdade. Só o APNs é
espião (`tests/_apoio_push.py`).

CONTROLES (medidos em 2026-10-18; remeça se mexer no código):
  Negativo trava: tirar o `and push_reconectar_em is null` de
      `reivindicar_push_reconectar` → uma_transicao_um_push vermelho (2ª varredura reenvia).
  Negativo limpeza: tirar `limpar_push_reconectar` do laço → volta_a_quebrar vermelho.
  Negativo soltar: tirar o `finally` que solta → falha_de_entrega e envio_que_levanta vermelhos.
  Negativo guardas: apagar `filtrar_por_acesso`, `push_bank_reconnect_bloqueado` ou
      `list_push_tokens` do laço → o caso de cada um vermelho (o último, por não reivindicar).
  Positivos: pagante envia, preferência religada envia, acesso religado envia.
  Negativo oscilação: fazer `limpar_push_reconectar` abrir a trava de toda conexão que
      não pede reconexão (o desenho antigo) → oscila_sem_reenviar vermelho; o positivo
      (saudavel_no_meio_manda_o_segundo) segue verde.
  Negativo isolamento: tirar `where user_id` de `set_push_bank_reconnect_opt_out` →
      opt_out_de_a_nao_muda_b vermelho; `limpar` de A abrindo a trava de B → a trava_de_b vermelho.
  Negativo re-registro: tirar `session_jti = excluded.session_jti` do `on conflict` →
      logout_e_novo_login_no_mesmo_aparelho vermelho. `push_bank_reconnect_bloqueado` com conta
      inexistente = False → conta_inexistente_e_bloqueada vermelho; sem agregação (fetchone
      sem ordem) → duas_linhas_de_auth_accounts vermelho.
"""
import pytest
from fastapi.testclient import TestClient

import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de, sql
from _apoio_push import apns, conta_paga, registra
from _billing_grants_helpers import garantir_system_event_logs
from core.services import open_finance_proactive as ofp
from test_of_aviso_mesma_regra import CASOS
from test_of_connection_state import (
    ITEM_SAUDAVEL, _conta_pluggy, _envelhece_autorizacao, _mock_pluggy, _tx_pluggy)
from test_of_webhook_observacao import _ambiente  # noqa: F401  (autouse)

TOK = "tok-reconectar-1"
CORPO = {"aps": {"alert": {"title": "PigBank", "body": "Um banco precisa de atenção. Toque para ver."},
                 "sound": "default"}, "rota": "conexoes"}


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


@pytest.fixture
def app_pronto(user_id, monkeypatch):
    """Conta paga, APNs de mentira e um aparelho registrado pela rota."""
    espiao = apns(monkeypatch)
    conta_paga(user_id)
    sessao = registra(user_id, TOK)
    return espiao, sessao


def _quebra(uid, mp, item="item-d", caso="d_login_error"):
    CASOS[caso][0](uid, mp, item)
    return db.get_connections_by_item_id(item)[0]["id"]


def _estado_sync(mp, item, **item_extra):
    _mock_pluggy(mp, item={**ITEM_SAUDAVEL, "id": item, **item_extra},
                 contas=[_conta_pluggy()], txs=[_tx_pluggy()])
    ps.sync_pluggy_item(item)


def _cura(mp, item="item-d"):
    _estado_sync(mp, item)


def _erro_temporario(mp, item="item-d"):
    _estado_sync(mp, item, status="ERROR", executionStatus="ERROR")


def _quebra_de_novo(mp, item="item-d"):
    _estado_sync(mp, item, status="LOGIN_ERROR", executionStatus="LOGIN_ERROR")


def _trava(conn_id):
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute("select push_reconectar_em from open_finance_connections where id = %s", (conn_id,))
        return cur.fetchone()["push_reconectar_em"]


# ── 4. uma transição, um push ────────────────────────────────────────────────

def test_uma_transicao_um_push(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)

    assert ofp.run_reconnect_push()["sent"] == 1
    assert [c["body"] for c in espiao.chamadas] == [CORPO]
    assert espiao.chamadas[0]["headers"]["apns-collapse-id"] == "of-reconectar"
    assert _trava(cid) is not None

    assert ofp.run_reconnect_push()["sent"] == 0 and len(espiao.chamadas) == 1


def test_volta_a_ficar_saudavel_e_quebra_de_novo_manda_mais_um(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()

    _cura(monkeypatch)
    ofp.run_reconnect_push()
    assert _trava(cid) is None and len(espiao.chamadas) == 1

    _quebra_de_novo(monkeypatch)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 2


def test_qr_dentro_da_janela_nao_manda_e_vencida_manda(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch, "item-tela-caixa", "g_caixa_health_55")
    ofp.run_reconnect_push()
    assert espiao.chamadas == [] and _trava(cid) is None

    _envelhece_autorizacao(cid, 61)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


def test_dois_bancos_na_mesma_varredura_sao_um_push(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    a = _quebra(user_id, monkeypatch, "item-d", "d_login_error")
    b = _quebra(user_id, monkeypatch, "item-e", "e_item_missing")
    assert ofp.run_reconnect_push()["sent"] == 1
    assert len(espiao.chamadas) == 1 and _trava(a) is not None and _trava(b) is not None


def test_dormente_sem_envs_nao_toca_a_trava(user_id, monkeypatch):
    for k in ("APNS_KEY_ID", "APNS_TEAM_ID", "APNS_AUTH_KEY", "APNS_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    conta_paga(user_id)
    cid = _quebra(user_id, monkeypatch)
    assert ofp.run_reconnect_push() == {"dormant": True} and _trava(cid) is None


def test_oscila_login_error_erro_login_error_e_um_episodio_so(user_id, monkeypatch, app_pronto):
    """LOGIN_ERROR → ERROR (error_recoverable, fora de `avisa_reconectar`) → LOGIN_ERROR:
    sair de "pede reconexão" sem ficar saudável não abre a trava."""
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()

    _erro_temporario(monkeypatch)
    ofp.run_reconnect_push()
    assert _trava(cid) is not None

    _quebra_de_novo(monkeypatch)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


def test_saudavel_no_meio_manda_o_segundo(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()
    _erro_temporario(monkeypatch)
    ofp.run_reconnect_push()
    _cura(monkeypatch)
    ofp.run_reconnect_push()
    assert _trava(cid) is None

    _quebra_de_novo(monkeypatch)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 2


def test_limpar_de_a_nao_abre_a_trava_de_b(user_id, monkeypatch, app_pronto):
    """A está saudável; B quebrado e já avisado: a limpeza de A não mexe na trava de B."""
    a_cid = _quebra(user_id, monkeypatch, "item-a")
    b = user_id + 1
    db.ensure_user(b)
    conta_paga(b)
    registra(b, "tok-de-b")
    b_cid = _quebra(b, monkeypatch, "item-b")
    ofp.run_reconnect_push()
    assert _trava(a_cid) is not None and _trava(b_cid) is not None
    _cura(monkeypatch, "item-a")   # A fica saudável; ninguém varre antes do `limpar` abaixo

    db.limpar_push_reconectar(user_id)
    assert _trava(a_cid) is None and _trava(b_cid) is not None


# ── 5. elegibilidade ─────────────────────────────────────────────────────────

def test_sem_acesso_nao_envia_nem_gasta_a_trava_e_ao_voltar_envia(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    sql("update auth_accounts set plan = 'free', plan_expires_at = null where user_id = %s", user_id)
    ofp.run_reconnect_push()
    assert espiao.chamadas == [] and _trava(cid) is None

    conta_paga(user_id)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


def test_preferencia_desligada_nao_envia_nem_gasta_a_trava_e_religada_envia(
        user_id, monkeypatch, app_pronto):
    espiao, sessao = app_pronto
    cid = _quebra(user_id, monkeypatch)
    _patch(user_id, sessao, False)
    ofp.run_reconnect_push()
    assert espiao.chamadas == [] and _trava(cid) is None

    _patch(user_id, sessao, True)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


def test_sem_aparelho_nao_gasta_a_trava_e_depois_que_registra_envia(user_id, monkeypatch):
    espiao = apns(monkeypatch)
    conta_paga(user_id)
    cid = _quebra(user_id, monkeypatch)
    reivindicacoes = []
    real = ofp.reivindicar_push_reconectar
    monkeypatch.setattr(ofp, "reivindicar_push_reconectar",
                        lambda *a: reivindicacoes.append(a) or real(*a))
    ofp.run_reconnect_push()
    # Barra ANTES de reivindicar: nem escrita (e pg_notify) para quem não tem aparelho.
    assert espiao.chamadas == [] and _trava(cid) is None and reivindicacoes == []

    registra(user_id, TOK)
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


# ── 6. isolamento ────────────────────────────────────────────────────────────

def test_so_o_aparelho_do_dono_da_conexao_e_chamado(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    b = user_id + 1
    db.ensure_user(b)
    conta_paga(b)
    registra(b, "tok-de-b")
    _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()
    assert espiao.tokens() == [TOK]


def test_token_de_a_registrado_por_b_nao_e_alcancado_pelo_banco_de_a(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    b = user_id + 1
    db.ensure_user(b)
    conta_paga(b)
    registra(b, TOK)   # o mesmo aparelho entra na conta de B
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()
    assert espiao.chamadas == [] and _trava(cid) is None


# ── 7. falha de entrega ──────────────────────────────────────────────────────

def test_falha_de_entrega_solta_a_trava_e_a_proxima_varredura_envia(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    espiao.respostas[TOK] = (500, "")
    assert ofp.run_reconnect_push()["sent"] == 0
    assert len(espiao.chamadas) == 1 and _trava(cid) is None

    espiao.respostas.clear()
    assert ofp.run_reconnect_push()["sent"] == 1


def test_envio_que_levanta_tambem_solta_a_trava(user_id, monkeypatch, app_pronto):
    cid = _quebra(user_id, monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(ofp.push_service, "send_push_to_user", _boom)
    ofp.run_reconnect_push()
    assert _trava(cid) is None


# ── 8. Configurações ─────────────────────────────────────────────────────────

def _notif(uid, sessao, metodo="get", **json):
    url = f"/settings/{uid}/notifications"
    h = {"Authorization": f"Bearer {sessao['access']}"}
    c = TestClient(dashboard.app)
    r = c.get(url, headers=h) if metodo == "get" else c.patch(url, headers=h, json=json)
    assert r.status_code == 200, r.text
    return r.json()


def _patch(uid, sessao, ligado):
    return _notif(uid, sessao, "patch", push_bank_reconnect_enabled=ligado)


def test_configuracao_padrao_ligada_e_patch_desliga_e_religa(user_id, app_pronto):
    _, sessao = app_pronto
    assert _notif(user_id, sessao)["push_bank_reconnect_enabled"] is True
    assert _patch(user_id, sessao, False)["push_bank_reconnect_enabled"] is False
    assert _notif(user_id, sessao)["push_bank_reconnect_enabled"] is False
    assert _patch(user_id, sessao, True)["push_bank_reconnect_enabled"] is True


def test_opt_out_de_a_nao_muda_b(user_id):
    b = user_id + 1
    db.ensure_user(b)
    conta_paga(user_id)
    conta_paga(b)
    db.set_push_bank_reconnect_opt_out(user_id, True)
    assert db.push_bank_reconnect_bloqueado(user_id) is True
    assert db.push_bank_reconnect_bloqueado(b) is False


def test_conta_inexistente_e_bloqueada(user_id):
    """Sem linha em `auth_accounts` (a fixture só cria `users`): sem informação não autoriza."""
    assert db.push_bank_reconnect_bloqueado(user_id) is True


def test_duas_linhas_de_auth_accounts_bloqueia_se_qualquer_tem_opt_out(user_id):
    """`user_id` não é unique em `auth_accounts`: o resultado não depende da ordem física."""
    conta_paga(user_id)
    sql("insert into auth_accounts(user_id, email, password_hash, plan, push_bank_reconnect_opt_out)"
        " values (%s, %s, 'x', 'free', true)", user_id, f"segunda-{user_id}@example.com")
    assert db.push_bank_reconnect_bloqueado(user_id) is True       # opt-out na 2ª
    sql("update auth_accounts set push_bank_reconnect_opt_out = (email like 'segunda-%%')"
        " where user_id = %s", user_id)
    sql("update auth_accounts set push_bank_reconnect_opt_out = not push_bank_reconnect_opt_out"
        " where user_id = %s", user_id)                            # agora o opt-out está na 1ª
    assert db.push_bank_reconnect_bloqueado(user_id) is True
    sql("update auth_accounts set push_bank_reconnect_opt_out = false where user_id = %s", user_id)
    assert db.push_bank_reconnect_bloqueado(user_id) is False      # positivo: nenhuma com opt-out
