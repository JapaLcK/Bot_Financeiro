"""
tests/test_settings_saida_de_emergencia.py — as CINCO rotas de conta que o corte
do #380 não pode trancar, batidas pela URL real.

**Por que estas cinco e por que só elas** mora na docstring de
`shared.authorize_account_access` (§0.7). O que este arquivo acrescenta é a
MEDIÇÃO: o defeito é QUAL FUNÇÃO A ROTA CHAMA, então todo caso vai pela URL real.
`tests/test_gate_plan_selection.py` exercita `_enforce_subscription_gate` com um
`Request` falso e, por construção, nunca veria isso (§3, "rode a conversa").

O gate é DORMENTE na suíte (`conftest.py` põe `PLANS_V2_ENABLED=0` por
`setdefault`, e sem `PAYWALL_ENABLED` o `has_app_access` devolve True antes de
consultar qualquer coisa). Sem a fixture `_gate_ligado`, TODO caso de 402 deste
arquivo passa verde com e sem o conserto — medido, não deduzido.

CONTROLES DECLARADOS (`docs/controles_declarados.md`) — injeção -> VERMELHO
──────────────────────────────────────────────────────────────────────────
1. **A isenção existe?** Em `security_sessions_list_route`
   (`frontend/routes/settings.py`): `shared.authorize_account_access` ->
   `shared.authorize_dashboard_access`. -> `test_cortado_lista_sessoes`.
   Direção: falso NEGATIVO — a saída de emergência volta a trancar.
2. **A isenção vazou?** Em `shared.authorize_dashboard_access`, a linha
   `_enforce_subscription_gate(request, current_user_id)` -> `pass`. ->
   `test_cortado_nao_ve_atividade`, `test_cortado_nao_ve_notificacoes`,
   `test_cortado_nao_ve_open_finance`. Direção: falso POSITIVO — o #380 vira
   decoração nas rotas de dados.
3. **O dono é checado?** Em `shared.authorize_account_access`,
   `if current_user_id != int(user_id):` -> `if False:`. ->
   `test_cortado_nao_alcanca_sessoes_de_outro_usuario`. Direção: vazamento entre
   contas (`CLAUDE.md` §0) na função que nasceu neste PR.
4. **Vale para a perna da ESCOLHA?** Em `security_settings_route` (alvo
   DIFERENTE do nº 1): `shared.authorize_account_access` ->
   `shared.authorize_dashboard_access`. -> `test_cortado_le_a_secao_de_seguranca`
   (perna do CORTE) E `test_sem_escolha_de_plano_tem_a_mesma_saida` (perna da
   ESCOLHA). Direção: falso NEGATIVO nas duas pernas pela MESMA rota — prova que
   os dois casos medem motivos diferentes de 402.
5. **A sessão corrente é poupada?** Em `security_sessions_revoke_others_route`,
   `current_jti = _current_session_jti(request)` -> `current_jti = None`. ->
   `test_cortado_encerra_as_outras_sessoes`. Direção: "encerrar os OUTROS
   dispositivos" desloga quem clicou. Com o cliente sem `jti` (como era até a
   rodada 2) esta injeção é INVISÍVEL: a chamada já passava None.
6. **O guard da própria sessão existe?** Em `security_session_revoke_route`,
   `if current_jti and jti == current_jti:` -> `if False:`. ->
   `test_cortado_nao_encerra_a_propria_sessao_pelo_jti`.

**Positivos** (VERDES sob o nº 1, que é o que RESTRINGE):
  `test_pagante_continua_entrando_nas_cinco_rotas` — o legítimo não mudou; sem
  ele o grupo passaria num código que recusa todo mundo.
  `test_cortado_nao_ve_atividade` — pôr o gate de volta numa rota não afrouxa o
  das outras.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared
from core.sessions import create_session, get_active_session
from core.services.plan_service import has_app_access, needs_plan_selection
from db.connection import get_conn
from db.users import _hash_password
from tests._helpers_pii import insert_auth_account_pii

SENHA = "senha-forte-123"
# Hash de verdade, não a string "hash" do default do helper: o
# `schedule_account_deletion` do caso da conta em exclusão CONFERE a senha.
SENHA_HASH = _hash_password(SENHA)


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    """O corte é o default de produção; fixar as duas envs deixa o arquivo imune
    ao `PLANS_V2_ENABLED=0` que o `conftest.py` põe por `setdefault`. Sem ela o
    arquivo inteiro é tautológico."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


@pytest.fixture(autouse=True)
def _sem_rate_limit(monkeypatch):
    """3/minute no `/password-reset` — e este arquivo bate nele duas vezes."""
    monkeypatch.setattr(shared.limiter, "enabled", False)
    monkeypatch.setattr(dashboard.limiter, "enabled", False)


def _conta(user_id: int, *, com_senha: bool = False, plan: str = "free") -> str:
    """Conta web pelo helper PII: insert cru sem `email_hash` vira órfão invisível
    para `create_password_reset_token`. `com_senha=False` é a conta só-Google."""
    email = f"saida-{uuid.uuid4().hex[:10]}@test.local"
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(
            cur, user_id, email,
            password_hash=SENHA_HASH if com_senha else None,
            plan=plan,
        )
        conn.commit()
    return email


def _cortar(user_id: int) -> None:
    """Estado do corte: plano pago VENCIDO, sem carência, e a escolha de plano já
    feita — senão o 402 sairia `plan_selection_required` e o caso mediria a outra
    perna (a que `test_sem_escolha_de_plano_tem_a_mesma_saida` cobre).

    Delta ABSOLUTO (30 dias), nunca `DUNNING_GRACE_DAYS ± n`: escrito em função
    da constante, alargar a carência moveria o caso junto com o guard."""
    vencido = datetime.now(timezone.utc) - timedelta(days=30)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update auth_accounts set plan='pro', plan_expires_at=%s,"
            "       past_due_since=null, last_payment_status='canceled',"
            "       plan_selected_at=now() where user_id=%s",
            (vencido, user_id),
        )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    assert has_app_access(user_id) is False, "pré-condição: a conta tem de estar cortada"


def _pagante(user_id: int) -> None:
    """O par de `_cortar`: plano pago VIGENTE. `plan_expires_at` no futuro (e não
    NULL) porque o vitalício percorre outro ramo de `tem_direito_hoje`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update auth_accounts set plan='pro', plan_expires_at=%s,"
            "       last_payment_status='active', plan_selected_at=now()"
            " where user_id=%s",
            (datetime.now(timezone.utc) + timedelta(days=30), user_id),
        )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    assert has_app_access(user_id) is True, "pré-condição: a conta tem de estar pagando"


def _cliente(user_id: int, email: str, jti: str | None = None) -> tuple[TestClient, dict]:
    """TestClient com sessão do próprio usuário. O `jti` vai nos DOIS tokens: é
    o do cookie de DASHBOARD que `resolve_dashboard_user_id` valida contra
    `auth_sessions`, e o do `auth_token` que o `_current_session_jti` lê."""
    client = TestClient(dashboard.app)
    client.cookies.set(
        dashboard.AUTH_COOKIE_NAME,
        dashboard._make_jwt(user_id, email, jti=jti) if jti else dashboard._make_jwt(user_id, email),
    )
    client.cookies.set(
        dashboard.DASHBOARD_COOKIE_NAME,
        dashboard.make_dashboard_token(user_id, hours=1, jti=jti),
    )
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    return client, {dashboard.CSRF_HEADER_NAME: csrf}


def _cortado(user_id: int, *, com_senha: bool = False):
    email = _conta(user_id, com_senha=com_senha)
    _cortar(user_id)
    return _cliente(user_id, email) + (email,)


# ── 1-5: a saída de emergência abre para quem foi cortado ───────────────────

def test_cortado_le_a_secao_de_seguranca(user_id):
    """O PRIMEIRO passo da saída: com 402 aqui o front caía em
    `applySecuritySettings({})` e o botão de reset ficava `disabled`."""
    client, headers, email = _cortado(user_id)
    r = client.get(f"/settings/{user_id}/security", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email, "sem o e-mail o front não habilita o reset"


def test_cortado_lista_sessoes(user_id):
    create_session(user_id, ip="203.0.113.7", user_agent="pytest/1.0")
    client, headers, _ = _cortado(user_id)
    r = client.get(f"/settings/{user_id}/sessions", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_cortado_encerra_as_outras_sessoes(user_id):
    """O cliente vai com um `jti` REAL. Sem ele `_current_session_jti` devolve
    None, `revoke_other_sessions` cai no ramo "revoga TODAS" (`core/sessions.py`)
    e o `revoked == 1` sairia igual se a rota matasse a sessão corrente junto —
    o que discrimina é a corrente SOBREVIVER."""
    outra = create_session(user_id, ip="203.0.113.8")
    email = _conta(user_id)
    _cortar(user_id)
    atual = create_session(user_id, ip="203.0.113.80")
    client, headers = _cliente(user_id, email, jti=atual)
    r = client.delete(f"/settings/{user_id}/sessions", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 1
    assert get_active_session(outra) is None, "respondeu 200 sem revogar a outra"
    assert get_active_session(atual) is not None, "revogou a sessão CORRENTE junto"


def test_cortado_nao_encerra_a_propria_sessao_pelo_jti(user_id):
    """O guard `jti == current_jti -> 400` (`security_session_revoke_route`) não
    era exercitado: todo caso ia sem `jti`, e com `current_jti` None ele nunca
    entra."""
    email = _conta(user_id)
    _cortar(user_id)
    atual = create_session(user_id, ip="203.0.113.90")
    client, headers = _cliente(user_id, email, jti=atual)
    r = client.delete(f"/settings/{user_id}/sessions/{atual}", headers=headers)
    assert r.status_code == 400, r.text
    assert get_active_session(atual) is not None, "revogou a corrente mesmo com 400"


def test_cortado_encerra_uma_sessao_especifica(user_id):
    alvo = create_session(user_id, ip="203.0.113.9")
    client, headers, _ = _cortado(user_id)
    r = client.delete(f"/settings/{user_id}/sessions/{alvo}", headers=headers)
    assert r.status_code == 200, r.text
    assert get_active_session(alvo) is None, "respondeu 200 sem revogar de verdade"


def test_cortado_so_google_pede_o_link_de_definir_senha(user_id, monkeypatch):
    """O NÓ do fluxo só-Google: exportar e excluir exigem senha, a conta não tem
    nenhuma, e este é o único endereço que a cria."""
    import core.services.email_service as es
    enviados: list[tuple] = []
    monkeypatch.setattr(
        es, "send_password_reset_email",
        lambda *args, **kwargs: (enviados.append(args), True)[1],
    )
    client, headers, _ = _cortado(user_id, com_senha=False)
    r = client.post(f"/settings/{user_id}/password-reset", headers=headers)
    assert r.status_code == 200, r.text
    assert "definir" in r.json()["message"].lower(), r.json()
    assert enviados, "respondeu 200 sem mandar e-mail nenhum"


# ── 6-8: a isenção NÃO vazou para as rotas de dados ─────────────────────────

def _corpo_do_gate(resp) -> str:
    detail = resp.json().get("detail")
    return (detail or {}).get("error") if isinstance(detail, dict) else str(detail)


def test_cortado_nao_ve_atividade(user_id):
    """`/activity` fica de FORA da isenção por decisão do dono — é dado de uso,
    não é a saída."""
    client, headers, _ = _cortado(user_id)
    r = client.get(f"/settings/{user_id}/activity?limit=10", headers=headers)
    assert r.status_code == 402, r.text
    assert _corpo_do_gate(r) == "subscription_required", r.text


def test_cortado_nao_ve_notificacoes(user_id):
    client, headers, _ = _cortado(user_id)
    r = client.get(f"/settings/{user_id}/notifications", headers=headers)
    assert r.status_code == 402, r.text
    assert _corpo_do_gate(r) == "subscription_required", r.text


def test_cortado_nao_ve_open_finance(user_id):
    """Outro router: a isenção é das cinco rotas nominais, não de
    `authorize_dashboard_access` inteira."""
    client, headers, _ = _cortado(user_id)
    r = client.get(f"/open-finance/{user_id}", headers=headers)
    assert r.status_code == 402, r.text
    assert _corpo_do_gate(r) == "subscription_required", r.text


# ── 9-11: as guardas que NÃO caíram junto com o gate ────────────────────────

def test_cortado_nao_alcanca_sessoes_de_outro_usuario(user_id):
    """Isolamento (`CLAUDE.md` §0): tirar o gate não tira a guarda do DONO. A
    sessão é de A, o `{user_id}` do path é de B."""
    outro = user_id + 1
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into users (id) values (%s) on conflict do nothing", (outro,))
        conn.commit()
    _conta(outro, com_senha=True)
    create_session(outro, ip="203.0.113.10")
    client, headers, _ = _cortado(user_id)

    r = client.get(f"/settings/{outro}/sessions", headers=headers)
    assert r.status_code == 403, r.text
    assert "sessions" not in r.text, "vazou a lista do outro usuário no corpo do 403"


def test_sessao_revogada_continua_dando_401(user_id):
    """A guarda (a): o `jti` do token não está em `auth_sessions`, então nem chega
    ao gate de plano — 401, nunca 402."""
    email = _conta(user_id)
    _cortar(user_id)
    client, headers = _cliente(user_id, email, jti=f"jti-revogado-{uuid.uuid4().hex[:8]}")
    r = client.get(f"/settings/{user_id}/security", headers=headers)
    assert r.status_code == 401, r.text


def test_conta_agendada_para_exclusao_continua_dando_403(user_id):
    """A guarda (b): `raise_if_account_scheduled_for_deletion` ficou DENTRO da
    função nova."""
    from db import schedule_account_deletion
    email = _conta(user_id, com_senha=True)
    _cortar(user_id)
    schedule_account_deletion(user_id, SENHA)
    client, headers = _cliente(user_id, email)

    r = client.get(f"/settings/{user_id}/security", headers=headers)
    assert r.status_code == 403, r.text
    assert "exclusão" in r.json()["detail"], r.text


def test_sem_escolha_de_plano_tem_a_mesma_saida(user_id):
    """A OUTRA perna do gate: `_cortar` fixa `plan_selected_at=now()` de
    propósito, então todo caso acima mede só a perna do CORTE. Aqui ele é NULL
    (default dropado no schema) e o 402 sairia `plan_selection_required`. A
    decisão de derrubar as duas está na docstring de `authorize_account_access`.
    A rota de DADOS no fim é o par que separa "isentou a CONTA" de "isentou o
    gate inteiro"."""
    email = _conta(user_id)
    assert needs_plan_selection(user_id) is True, "pré-condição: a escolha não foi feita"
    create_session(user_id, ip="203.0.113.12")
    client, headers = _cliente(user_id, email)

    assert client.get(f"/settings/{user_id}/security", headers=headers).status_code == 200
    assert client.get(f"/settings/{user_id}/sessions", headers=headers).status_code == 200
    r = client.get(f"/settings/{user_id}/notifications", headers=headers)
    assert r.status_code == 402, r.text
    assert _corpo_do_gate(r) == "plan_selection_required", r.text


# ── 12: o POSITIVO — o legítimo não mudou ──────────────────────────────────

def test_pagante_continua_entrando_nas_cinco_rotas(user_id, monkeypatch):
    """Sem este caso o grupo passaria num código que recusa todo mundo (§3). As
    CINCO de uma vez: o pagante atravessa a função nova como atravessava a
    outra."""
    import core.services.email_service as es
    monkeypatch.setattr(es, "send_password_reset_email", lambda *a, **k: True)

    email = _conta(user_id, com_senha=True)
    _pagante(user_id)
    alvo = create_session(user_id, ip="203.0.113.11")
    client, headers = _cliente(user_id, email)

    assert client.get(f"/settings/{user_id}/security", headers=headers).status_code == 200
    assert client.get(f"/settings/{user_id}/sessions", headers=headers).status_code == 200
    assert client.post(f"/settings/{user_id}/password-reset", headers=headers).status_code == 200
    assert client.delete(f"/settings/{user_id}/sessions/{alvo}", headers=headers).status_code == 200
    assert client.delete(f"/settings/{user_id}/sessions", headers=headers).status_code == 200
