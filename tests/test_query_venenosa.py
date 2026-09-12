"""Issue #321 — NUL/surrogate em QUERY param (a fatia que o #369 e o path não pegam).

O #369 fechou o CORPO (modelo Pydantic + `recusa_veneno` → 422) e o commit
anterior deste branch fechou o PATH (guarda nas funções de `db/`). A query
continuava chegando crua ao `cur.execute`.

VARREDURA DA CATEGORIA — medida em 2026-09-10 nesta árvore, REMEÇA antes de
reusar o número (§2). Universo: `app.routes` (monólito + os `APIRouter` de
`frontend/routes/`), olhando `dependant.query_params` — **38** rotas têm query
param, e as com param `str`/`str | None` foram sondadas uma a uma com
`?campo=a%00b`, com sessão de admin e com sessão de usuário comum. Deu **8** em
500, não 4:

    admin  /admin/api/users?plan
    admin  /admin/api/pii-access?actor
    admin  /admin/api/pii-access?field
    admin  /admin/grant-pro?email
    user   /categories/{id}/launches?categoria
    user   /data/{id}?q
    user   /history/{id}/list?categoria
    user   /history/{id}/list?q

As 4 de usuário comum não estavam na issue e são as mais alcançáveis: qualquer
conta logada, sem privilégio nenhum, e cada 500 grava uma linha em
`system_event_logs` pelo `admin_error_logging_middleware`. Anônimo continua
fora: `_resolve_admin_username`/`_authorize_dashboard_access` levantam antes.

O resto dos params `str` não estava em 500 por motivo medido, não deduzido:
`?q` e `?status` de `/admin/api/users` filtram em Python ou por whitelist;
`from_`/`to`/`date`/`month` passam por parser de data; `sort`/`tipo`/`kind`/
`filter_type` são whitelist; `/unsubscribe?token` valida formato antes.

CONTROLE NEGATIVO (§3): `test_negativo_*` desliga a guarda no módulo em que ela
mora (troca `tem_veneno` por `lambda _: False` no monólito) e exige o 500 de
volta — num caso ADMIN e num caso de USUÁRIO comum, porque são caminhos
diferentes e um só não discrimina o outro.

CONTROLE POSITIVO (§3): `test_positivo_*`. São rotas de FILTRO — uma guarda que
recusasse demais deixaria o painel de admin e a busca do histórico sem
resultado, o que é pior que o 500. Por isso o positivo exige o dado certo de
volta, não só o 200: o `?plan=pro` do admin tem de trazer a conta Pro e NÃO
trazer a free, e o `?q=` do histórico tem de trazer o lançamento do dono e
**nenhum** do outro usuário (isolamento por usuário, CLAUDE.md §0).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro

NUL = "a%00b"

# (rótulo, template do path, param) — os 8 sites medidos em 500.
ADMIN_500 = [
    ("users_plan", "/admin/api/users", "plan"),
    ("pii_actor", "/admin/api/pii-access", "actor"),
    ("pii_field", "/admin/api/pii-access", "field"),
    ("grant_pro_email", "/admin/grant-pro", "email"),
]
USER_500 = [
    ("launches_categoria", "/categories/{uid}/launches", "categoria"),
    ("data_q", "/data/{uid}", "q"),
    ("history_categoria", "/history/{uid}/list", "categoria"),
    ("history_q", "/history/{uid}/list", "q"),
]


@pytest.fixture(autouse=True)
def _admin_configurado(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop)
    # Storage do slowapi é em memória e compartilhado entre testes: sem o reset
    # os logins deste arquivo somados aos dos vizinhos estouram o teto de
    # `/admin/auth/login` e o 429 passaria por "não é 500".
    dashboard.limiter._storage.reset()


def _client() -> TestClient:
    # raise_server_exceptions=False: sem isso o 500 vira exceção propagada e o
    # teste nunca chega a ler o status que quer medir.
    return TestClient(dashboard.app, base_url="https://testserver",
                      raise_server_exceptions=False)


def _admin_client() -> TestClient:
    client = _client()
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    login = client.post("/admin/auth/login", headers={dashboard.CSRF_HEADER_NAME: "t"},
                        json={"username": "admin", "password": "secret-admin"})
    assert login.status_code == 200, login.text
    return client


def _user_client(uid: int) -> TestClient:
    client = _client()
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(uid, hours=1))
    return client


@pytest.mark.parametrize("rotulo,path,param", ADMIN_500, ids=[r for r, _, _ in ADMIN_500])
def test_query_venenosa_de_admin_vira_422(rotulo, path, param):
    resp = _admin_client().get(f"{path}?{param}={NUL}", follow_redirects=False)
    assert resp.status_code == 422, f"{path}?{param} devolveu {resp.status_code}"
    assert "caractere inválido" in resp.json()["detail"]


@pytest.mark.parametrize("rotulo,path,param", USER_500, ids=[r for r, _, _ in USER_500])
def test_query_venenosa_de_usuario_comum_vira_422(rotulo, path, param, user_id):
    url = path.format(uid=user_id)
    resp = _user_client(user_id).get(f"{url}?{param}={NUL}", follow_redirects=False)
    assert resp.status_code == 422, f"{url}?{param} devolveu {resp.status_code}"


def test_422_nao_ecoa_o_valor_venenoso(user_id):
    """O 422 diz que o parâmetro é inválido; não devolve o que veio (§ do #369:
    o 422 do app não ecoa o que o cliente mandou)."""
    resp = _user_client(user_id).get(f"/data/{user_id}?q=SEGREDO{NUL}")
    assert "SEGREDO" not in resp.text, resp.text


def test_negativo_sem_a_guarda_o_admin_volta_a_500(monkeypatch):
    """Desliga o conserto no módulo em que ele mora: o 500 volta."""
    monkeypatch.setattr(dashboard, "tem_veneno", lambda _valor: False)
    resp = _admin_client().get(f"/admin/api/users?plan={NUL}", follow_redirects=False)
    assert resp.status_code == 500, f"sem a guarda deu {resp.status_code}, não 500"


def test_negativo_sem_a_guarda_o_usuario_comum_volta_a_500(monkeypatch, user_id):
    """O par do de cima: admin e usuário comum são caminhos diferentes, e um
    negativo só não discriminaria o outro."""
    monkeypatch.setattr(dashboard, "tem_veneno", lambda _valor: False)
    resp = _user_client(user_id).get(f"/history/{user_id}/list?q={NUL}", follow_redirects=False)
    assert resp.status_code == 500, f"sem a guarda deu {resp.status_code}, não 500"


def test_positivo_filtro_de_plano_do_admin_continua_filtrando(user_id):
    """`?plan=pro` tem de continuar trazendo a conta Pro e NÃO trazer a free —
    sem isto, uma guarda que recusasse tudo passaria nos testes de cima e
    deixaria o painel de usuários vazio."""
    pro_uid = promote_to_pro(user_id)

    resp = _admin_client().get("/admin/api/users?plan=pro&per_page=200")

    assert resp.status_code == 200, resp.text
    planos = {u["user_id"]: u["plan"] for u in resp.json()["users"]}
    assert planos.get(pro_uid) == "pro", f"a conta Pro sumiu do filtro: {pro_uid}"
    assert all(p == "pro" for p in planos.values()), f"veio conta não-pro: {planos}"


def test_positivo_busca_do_historico_filtra_e_nao_vaza_de_outro_usuario(user_id):
    """O `?q=` legítimo continua achando o lançamento do dono — e só o dele.
    O outro usuário tem um lançamento com a MESMA marca de propósito: é o que
    separa "o filtro funciona" de "o filtro devolve a tabela inteira"."""
    marca = f"marca{uuid.uuid4().hex[:8]}"
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    try:
        db.add_launch_and_update_balance(user_id, "despesa", 12.34, marca, "do dono")
        db.add_launch_and_update_balance(outro, "despesa", 99.99, marca, "do outro")

        resp = _user_client(user_id).get(f"/history/{user_id}/list?q={marca}")

        assert resp.status_code == 200, resp.text
        itens = resp.json()["items"]
        assert len(itens) == 1, f"esperado 1 item, veio {len(itens)}: {itens}"
        assert float(itens[0]["valor"]) == 12.34, itens[0]
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)
