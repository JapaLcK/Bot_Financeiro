"""
tests/test_corte_do_gratis_no_webhook.py — o CAMINHO DE VOLTA do corte do Grátis,
medido pelo webhook de verdade.

`has_app_access` passou a consultar `tem_direito_hoje`. Este arquivo prova que o
veredito **volta a ser True** pelos dois caminhos que devolvem direito em
produção: um `invoice.paid` da Stripe e um grant `admin` reprojetado. As
asserções são em `has_app_access`, **nunca nas colunas** — só ela passa pelo
caminho que o PR alterou; conferir `plan`/`plan_expires_at` mediria o webhook,
que é código de antes.

**Estes casos são o CONTROLE POSITIVO do PR e não têm negativo PRÓPRIO**, e a
ausência é deliberada: não há conserto a desligar aqui. Eles existem para provar
que o gate não recusa TODO MUNDO — a metade que o §3 exige quando um conserto
RESTRINGE algo. Cobrar mutação num caminho que só demonstra o legítimo é a
cerimônia que o mesmo §3 dispensa.

**Qual injeção derruba qual, medido** (2026-09-10; a instrução anterior mandava
aplicar o negativo nº 2 de `tests/test_access_gate.py` para ver os três
vermelhos, e isso era FALSO — a nº 2 só alcança um deles):

| injeção (de `tests/test_access_gate.py`) | vermelhos AQUI |
|---|---|
| nº 1 — `has_app_access` → `return True` | os QUATRO (as pré-condições `has_app_access is False` caem) |
| nº 2 — o status virando autoridade | só `test_carencia_aberta_mantem_o_acesso_ate_a_janela_fechar` |

A nº 2 não alcança os dois de restauração porque `_cortar` grava
`last_payment_status='canceled'` — fora de `PAST_DUE_PAYMENT_STATUSES` —, então
o termo injetado não muda o veredito daquelas contas.

**O que este arquivo NÃO cobre, e onde está**: o ramo TERMINAL do
`customer.subscription.deleted` (gravar `unpaid` + limpar o relógio
incondicionalmente) tem arquivo próprio,
`tests/test_dunning_encerramento_terminal.py`, com os controles dele. A decisão
de produto que ele dependia — se o admin pode liberar trial novo para quem a
Stripe encerrou por falta de pagamento — foi tomada pelo dono ("pode, libero
caso a caso") e está registrada em `docs/dunning_estados_eventos.md`, seção E4.

E a SAÍDA DE EMERGÊNCIA do corte mora no fim deste arquivo: `/settings` continua
alcançável para quem perdeu o direito, porque é a única tela com a UI de
exportar os dados e excluir a conta.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import garantir_system_event_logs
from core.services.plan_service import has_app_access
from db.connection import get_conn
from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub, _post, _setup
from token_utils import make_dashboard_token


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    """O corte é o default de produção; fixar as duas envs deixa o arquivo
    imune ao `PLANS_V2_ENABLED=0` que o `conftest.py` põe por `setdefault`."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


def _cortar(uid: int):
    """Deixa a conta no estado do corte: plano vencido, sem relógio de carência.

    Delta ABSOLUTO (30 dias), nunca `DUNNING_GRACE_DAYS ± n`: escrito em função
    da constante, alargar a carência moveria o caso junto com o guard e o teste
    ficaria verde por outro motivo (`docs/controles_declarados.md`).
    """
    agora = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='pro', plan_expires_at=%s,"
                "       past_due_since=null, last_payment_status='canceled',"
                "       plan_selected_at=now() where user_id=%s",
                (agora - timedelta(days=30), uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def test_pagamento_posterior_restaura_o_acesso(user_id, monkeypatch):
    """Bloqueado paga → o acesso volta, pelo `invoice.paid` de verdade."""
    uid, client, fake = _setup(monkeypatch, f"volta-{user_id}")
    _cortar(uid)
    assert has_app_access(uid) is False, "pré-condição: a conta tem de estar cortada"

    r = _post(client, fake,
              {"type": "invoice.paid", "id": f"evt_paid_{uid}", "created": _T_LIFE,
               "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                   "subscription": f"sub_volta_{uid}"}}},
              subs={f"sub_volta_{uid}": _fake_sub("active")})
    assert r.status_code == 200, r.text
    assert has_app_access(uid) is True


def test_grant_admin_depois_do_bloqueio_devolve_o_acesso(user_id, monkeypatch):
    """Irmão do de cima: o grant `admin` começa DEPOIS do bloqueio e a
    reprojeção o devolve — sem webhook nenhum, pela ferramenta de PRODUÇÃO
    (`/admin/grant-pro` e o painel passam os dois por `set_account_plan`).

    A passada de 60 s do loop é `recompute_entitlement(origem="varredura")`;
    chamá-la aqui é o mesmo caminho, sem esperar o relógio."""
    uid, client, fake = _setup(monkeypatch, f"grant-{user_id}")
    _cortar(uid)
    assert has_app_access(uid) is False

    from core.admin_dashboard import set_account_plan
    set_account_plan("pro", 12, user_id=uid)
    from core.services.billing_access import recompute_entitlement
    recompute_entitlement(uid, origem="varredura")

    assert has_app_access(uid) is True


def test_carencia_aberta_mantem_o_acesso_ate_a_janela_fechar(user_id, monkeypatch):
    """O lado DIREITO do OR, ponta a ponta no banco real: o relógio CONCEDE.

    Duas idades absolutas em vez de uma: 2 dias entra, 21 dias não. Sem o par,
    um `carencia_aberta` que devolvesse True sempre passaria verde."""
    uid, client, fake = _setup(monkeypatch, f"carencia-{user_id}")
    agora = datetime.now(timezone.utc)
    for dias, esperado in [(2, True), (21, False)]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update auth_accounts set plan='pro', plan_expires_at=%s,"
                    "       past_due_since=%s, last_payment_status='past_due',"
                    "       plan_selected_at=now() where user_id=%s",
                    (agora - timedelta(days=1), agora - timedelta(days=dias), uid),
                )
            conn.commit()
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(uid)
        assert has_app_access(uid) is esperado, f"relógio de {dias} dia(s)"


# ── a saída de emergência: /settings sobrevive ao corte ─────────────────────

def _pagina(uid: int, caminho: str):
    """GET numa página autenticada, com o cookie de dashboard (12 h) — o mesmo
    que `_resolve_page_user_id` aceita. `follow_redirects=False` porque é o
    302 que estamos medindo."""
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", make_dashboard_token(uid, hours=1))
    return client.get(caminho, follow_redirects=False)


def test_cortado_ainda_alcanca_settings_e_a_secao_da_conta(user_id, monkeypatch):
    """O corte NÃO pode trancar a porta de sair do produto (decisão do dono).

    `/settings` é a única tela com a UI de EXPORTAR os dados e EXCLUIR a conta
    (medido 2026-09-11:
    `grep -rln "account/export" frontend/*.html frontend/*.js`). Os endpoints
    `/auth/*` continuam
    isentos por prefixo, mas sem a página não sobra porta para alcançá-los.

    Mede o HTML servido, não só o status: um 200 com a página errada passaria
    numa asserção de status sozinha.
    """
    uid, _client, _fake = _setup(monkeypatch, f"saida-{user_id}")
    _cortar(uid)
    assert has_app_access(uid) is False, "pré-condição: a conta tem de estar cortada"

    r = _pagina(uid, "/settings")
    assert r.status_code == 200, f"o corte trancou a saída de emergência: {r.status_code} {r.headers.get('location')}"
    assert "/auth/account" in r.text, "a página veio sem a UI de exportar/excluir a conta"

    # E o /app continua cortado — a isenção é de UMA rota, não do gate.
    r_app = _pagina(uid, "/app")
    assert r_app.status_code == 302
    assert r_app.headers["location"] == "/precos?escolha=1"


def test_quem_nunca_escolheu_plano_continua_barrado_no_settings(user_id, monkeypatch):
    """O PAR do teste acima, e o controle que impede a isenção de virar
    "settings aberto pra todo mundo": a perna da ESCOLHA sobrevive nela."""
    uid, _client, _fake = _setup(monkeypatch, f"escolha-{user_id}")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='free', plan_expires_at=null,"
                "       plan_selected_at=null where user_id=%s",
                (uid,),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)

    r = _pagina(uid, "/settings")
    assert r.status_code == 302, r.status_code
    assert r.headers["location"] == "/precos?escolha=1"


def test_pagante_recebe_settings_normalmente(user_id, monkeypatch):
    """POSITIVO: sem gate nenhum pendente, a página é servida como sempre."""
    uid, _client, _fake = _setup(monkeypatch, f"pag-settings-{user_id}")
    agora = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='pro', plan_expires_at=%s,"
                "       last_payment_status='active', plan_selected_at=now()"
                " where user_id=%s",
                (agora + timedelta(days=30), uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)

    assert _pagina(uid, "/settings").status_code == 200
