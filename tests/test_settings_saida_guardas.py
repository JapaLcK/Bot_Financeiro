"""
tests/test_settings_saida_guardas.py — o que a saída de emergência do #380 NÃO
derrubou: o dono, a sessão revogada, a conta em exclusão, a perna da ESCOLHA e o
teto da conta sem e-mail.

Arquivo próprio porque o assunto é outro e porque o irmão
(`tests/test_settings_saida_de_emergencia.py`, que mede a ISENÇÃO) bateu no teto
de 350 linhas (`tests/test_max_lines_python.py`, arquivo novo não é isento). Os
helpers e as duas fixtures autouse vêm por IMPORT do irmão — uma fonte só (§0.7),
mesmo padrão de `tests/test_gate_saida_de_emergencia.py`.

**A decisão que este arquivo fixa** (dono, #380): a isenção derruba SÓ a perna do
DIREITO. Quem NUNCA escolheu plano continua recebendo 402
`plan_selection_required` nas cinco rotas — acabou de se cadastrar e não tem dado
financeiro para exportar; a saída existe para quem USOU o produto e perdeu o
direito. O texto longo está em `shared.authorize_account_access`, e o par de
navegação em `frontend/routes/static_pages.serve_settings`.

CONTROLES DECLARADOS (`docs/controles_declarados.md`) — injeção -> VERMELHO
──────────────────────────────────────────────────────────────────────────
Vermelhos nomeados dentro dos DOIS arquivos deste par.
1. **O dono é checado?** Em `shared.authorize_account_access`,
   `if current_user_id != int(user_id):` -> `if False:`. ->
   `test_cortado_nao_alcanca_sessoes_de_outro_usuario`. Direção: vazamento entre
   contas (`CLAUDE.md` §0) na função que nasceu neste PR.
2. **A perna da ESCOLHA continua valendo nas cinco rotas?** Em
   `shared._enforce_subscription_gate`, `if needs_plan_selection(user_id):` ->
   `if False:`. -> `test_sem_escolha_de_plano_nao_abre_a_saida`. Direção: falso
   POSITIVO de acesso — cadastro que nunca passou pela /precos entra pela saída
   de emergência, que é exatamente a decisão que o dono INVERTEU.
   Positivo do par, VERDE sob esta injeção: os `test_cortado_*` do irmão (eles
   fixam `plan_selected_at=now()`, então não dependem desta perna).
3. **O teto da conta sem e-mail responde 400?** Em
   `security_password_reset_route` (`frontend/routes/settings.py`),
   `if not email:` -> `if False:`. ->
   `test_conta_sem_email_sai_por_400_e_nao_por_500`. Direção: o teto declarado
   ("sai por suporte") vira 500 e a pessoa vê "erro inesperado" em vez da
   instrução.
"""
from __future__ import annotations

import uuid

from db.connection import get_conn
from core.sessions import create_session
from core.services.plan_service import has_app_access, needs_plan_selection

# Helpers e as DUAS fixtures autouse (`_gate_ligado`, `_sem_rate_limit`) vêm do
# irmão: importá-las liga o nome neste módulo e o pytest as registra aqui também.
from test_settings_saida_de_emergencia import (  # noqa: F401
    SENHA, _cliente, _conta, _cortado, _cortar, _corpo_do_gate,
    _gate_ligado, _sem_rate_limit,
)


def test_cortado_nao_alcanca_sessoes_de_outro_usuario(user_id):
    """Isolamento (`CLAUDE.md` §0): tirar o gate não tira a guarda do DONO. A
    sessão é de A, o `{user_id}` do path é de B.

    O `outro` não precisa de limpeza à mão: o `_auto_cleanup_orphan_users` do
    `tests/conftest.py` é autouse, fotografa `users` antes do teste e apaga todo
    id novo depois (medido nesta árvore com um par cria/confere: 0 linhas
    sobrevivem). `auth_accounts.user_id` cascateia junto (`db/schema.py`)."""
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
    função nova, e ANTES do 402 da escolha."""
    from db import schedule_account_deletion
    email = _conta(user_id, com_senha=True)
    _cortar(user_id)
    schedule_account_deletion(user_id, SENHA)
    client, headers = _cliente(user_id, email)

    r = client.get(f"/settings/{user_id}/security", headers=headers)
    assert r.status_code == 403, r.text
    assert "exclusão" in r.json()["detail"], r.text


def test_sem_escolha_de_plano_nao_abre_a_saida(user_id):
    """A perna da ESCOLHA continua valendo nas CINCO rotas (decisão do dono).

    `_cortar` fixa `plan_selected_at=now()` de propósito, então todo caso do
    irmão mede só a perna do CORTE. Aqui ele é NULL (default dropado no schema) e
    as cinco respondem 402 `plan_selection_required` — cadastro novo vai pra
    /precos como sempre foi, e no navegador nem chega aqui (`initSettings`
    redireciona antes de qualquer fetch)."""
    email = _conta(user_id)
    assert needs_plan_selection(user_id) is True, "pré-condição: a escolha não foi feita"
    alvo = create_session(user_id, ip="203.0.113.12")
    client, headers = _cliente(user_id, email)

    respostas = [
        client.get(f"/settings/{user_id}/security", headers=headers),
        client.get(f"/settings/{user_id}/sessions", headers=headers),
        client.post(f"/settings/{user_id}/password-reset", headers=headers),
        client.delete(f"/settings/{user_id}/sessions/{alvo}", headers=headers),
        client.delete(f"/settings/{user_id}/sessions", headers=headers),
    ]
    for r in respostas:
        assert r.status_code == 402, (r.request.url, r.text)
        assert _corpo_do_gate(r) == "plan_selection_required", r.text


def test_conta_sem_email_sai_por_400_e_nao_por_500(user_id):
    """TETO DECLARADO (dono, #380): conta sem e-mail e sem senha sai por suporte.

    Sem e-mail é SEM LINHA em `auth_accounts` — a coluna é `not null`
    (`db/schema.py`), então esse estado é a conta que nasceu no WhatsApp e nunca
    passou pelo cadastro web. O teto precisa de caso porque o dia em que alguém
    "consertar" o `/contact` ninguém percebe que ele existia: o que se fixa aqui é
    que a pessoa vê a INSTRUÇÃO (400), não "erro inesperado" (500)."""
    assert has_app_access(user_id) is False, "pré-condição: sem cadastro web não há direito"
    assert needs_plan_selection(user_id) is False, \
        "pré-condição: sem linha em auth_accounts a perna da ESCOLHA não barra"
    create_session(user_id, ip="203.0.113.13")
    client, headers = _cliente(user_id, "sem-cadastro-web@test.local")

    r = client.post(f"/settings/{user_id}/password-reset", headers=headers)
    assert r.status_code == 400, r.text
    assert "e-mail" in r.json()["detail"], r.text
