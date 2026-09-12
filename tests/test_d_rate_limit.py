"""Teto de requisição do magic link do bot (`GET /d/{code}`).

O #321 tirou o 500 e a linha em `system_event_logs` da rota, mas não o custo:
código bem formado e inexistente ainda atravessa a guarda e vira um `DELETE` no
Postgres. MEDIDO nesta árvore ANTES do `@limiter.limit`: 200 requisições
anônimas → `{401: 200}`, zero 429, a 589 req/s.

**Por que `shared_limit` e não `limit`.** O `Limiter` roda com o `key_style="url"`
default do slowapi: o balde de um `@limiter.limit` é (IP, **URL exata**), então
numa rota com path param cada código inventado abre um balde novo e o teto nunca
é alcançado. MEDIDO: com `@limiter.limit("30/minute")`, 35 GETs em `/d/x0..x34`
deram 0 × 429 e os mesmos 35 na MESMA URL deram 5 × 429. Por isso `_inunda` usa
um código DIFERENTE por requisição — é o que separa o teto que funciona do
teto decorativo, e é o que fica vermelho se alguém trocar de volta por `limit()`.

**Por que 30/minute.** É o número de `/auth/google/pending/{token}`, a irmã mais
próxima (GET anônimo, token de uso único no path, aberto uma vez por quem vem de
fora) — só o número: o teto DELA é `limit()` e, pelo parágrafo acima, é
decorativo. Janela de MINUTO e não de hora porque o `rate_limit_exceeded_handler`
devolve `Retry-After: 60` fixo: com teto por hora o 429 mentiria durante a hora
inteira. E o limitador é por IP (`get_remote_address`), então CGNAT de operadora
põe vários usuários na mesma chave — 30 cliques/min do mesmo IP fica muito acima
do tráfego real e quem esbarrar volta em 60 s.

CONTROLE NEGATIVO (§3): `test_negativo_*` desliga o limitador (`limiter.enabled
= False`) e exige que as 31 requisições passem. Sem ele, o teste do 429 passaria
num app que recusa /d/ por qualquer outro motivo.

CONTROLE POSITIVO (§3): `test_positivo_*` — o primeiro clique legítimo continua
logando E o link continua sendo de uso único. Sem ele, um teto de "0/minute"
(ou uma rota que só devolve 429) passaria no negativo e no do 429, e o magic
link do bot estaria morto.

ARMADILHA MEDIDA: `tests/test_pix_rota_registrada.py` faz `importlib.reload` do
monólito, e o `@limiter...` roda de novo — o slowapi faz `.extend()` na lista de
limites da rota, então a partir dali CADA requisição gasta DOIS slots e o teto
efetivo vira 15/min na suíte. Este arquivo roda antes (`d` < `p`) e não é
afetado; quem reordenar vai ver 429 no 16º. Artefato de teste, não de produção:
o módulo é importado uma vez no servidor.

O storage do limiter é EM MEMÓRIA e compartilhado pela sessão inteira de pytest:
a fixture zera antes e depois de cada teste daqui, senão as 31 requisições deste
arquivo derrubariam com 429 os testes de `/d/` dos outros arquivos
(`test_rotas_anonimas_venenosas.py`, `test_sessions.py`, `test_auth_cookie.py`),
que rodam depois na ordem alfabética.
"""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import core.observability as observability
import frontend.finance_bot_websocket_custom as dashboard
from db.reports import create_dashboard_session, get_conn

TETO = 30


@pytest.fixture(autouse=True)
def _limiter_limpo():
    dashboard.limiter._storage.reset()
    yield
    dashboard.limiter._storage.reset()


def _client() -> TestClient:
    return TestClient(dashboard.app, base_url="https://testserver",
                      raise_server_exceptions=False)


def _inunda(client: TestClient, n: int) -> list[int]:
    return [client.get(f"/d/naoexiste{i}", follow_redirects=False).status_code
            for i in range(n)]


def test_flood_anonimo_para_no_teto():
    """As primeiras 30 seguem no 401 de link inválido; a 31ª vira 429."""
    status = _inunda(_client(), TETO + 1)

    assert status[:TETO] == [401] * TETO, f"antes do teto: {status[:TETO]}"
    assert status[TETO] == 429, f"a {TETO + 1}ª deu {status[TETO]}, não 429"


def test_negativo_sem_limitador_as_31_passam(monkeypatch):
    """Desliga o conserto: sem o limitador nenhuma das 31 é barrada."""
    monkeypatch.setattr(dashboard.limiter, "enabled", False)

    status = _inunda(_client(), TETO + 1)

    assert 429 not in status, f"429 sem o limitador ligado: {status}"
    assert status == [401] * (TETO + 1)


def test_positivo_primeiro_clique_loga_e_o_link_continua_de_uso_unico(user_id):
    """O caminho legítimo não pode ter sido fechado junto: o código válido loga
    (302) e o segundo uso do MESMO código cai no 401 de "já usado"."""
    client = _client()
    code = create_dashboard_session(user_id)

    primeiro = client.get(f"/d/{code}", follow_redirects=False)
    segundo = client.get(f"/d/{code}", follow_redirects=False)

    assert primeiro.status_code == 302, f"o clique legítimo deu {primeiro.status_code}"
    assert segundo.status_code == 401, f"o link foi reusado: {segundo.status_code}"


def test_positivo_clique_legitimo_sobrevive_ao_flood_de_outro_minuto(user_id):
    """29 tentativas inválidas não podem consumir o clique bom do usuário —
    é o caso do link clicado depois de alguém errar o código no mesmo IP."""
    client = _client()
    _inunda(client, TETO - 1)
    code = create_dashboard_session(user_id)

    assert client.get(f"/d/{code}", follow_redirects=False).status_code == 302


# ─── O custo do 429: o slowapi loga por requisição barrada ───────────────────
# O `_DashboardHandler` (core/observability.py) espelha todo WARNING do root em
# `system_event_logs` — um `psycopg.connect()` + INSERT bloqueante POR RECORD.
# O slowapi loga um WARNING por requisição BARRADA, então sem o filtro o teto
# trocava 200 `DELETE` baratos por uma inundação de log pior que a do #321.

def _conta_eventos() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from system_event_logs")
        return int(cur.fetchone()["n"])


@pytest.fixture()
def handler_do_dashboard():
    """Instala o handler que grava no banco e o remove no fim — sem o remove,
    TODO warning dos testes seguintes viraria INSERT."""
    asyncio.run(admin_dashboard.ensure_admin_tables())
    handler = observability._DashboardHandler()
    handler.setLevel(logging.WARNING)
    handler.addFilter(observability._sem_ratelimit_no_banco)
    logging.getLogger().addHandler(handler)
    try:
        yield handler
    finally:
        logging.getLogger().removeHandler(handler)


def test_429_nao_grava_linha_em_system_event_logs(handler_do_dashboard):
    antes = _conta_eventos()
    status = _inunda(_client(), TETO + 10)

    assert status.count(429) == 10, f"esperava 10 barradas, veio {status.count(429)}"
    assert _conta_eventos() == antes, (
        f"{_conta_eventos() - antes} linha(s) novas — o 429 virou inundação de log"
    )


def test_negativo_sem_o_filtro_cada_429_vira_uma_linha(handler_do_dashboard):
    """Desliga o conserto (tira o filtro do handler): volta uma linha por 429."""
    handler_do_dashboard.removeFilter(observability._sem_ratelimit_no_banco)
    antes = _conta_eventos()

    _inunda(_client(), TETO + 10)

    assert _conta_eventos() - antes == 10, (
        f"sem o filtro deu {_conta_eventos() - antes} linha(s), esperado 10"
    )
