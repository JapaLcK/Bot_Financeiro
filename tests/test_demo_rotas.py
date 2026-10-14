""""Testar o Piggy": as rotas /teste e /t/{code} e o funil, pelo app de verdade.

TestClient do app + Postgres real (padrão de tests/test_d_rate_limit.py).

Controles negativos (medidos no PR, ver o relato):
- [N] trocar `shared_limit(..., scope=)` por `limit()` em /t/{code} deixa vermelho
  `test_t_tem_teto_por_ip_e_nao_por_url` (31 códigos diferentes: zero 429, porque
  com o `key_style="url"` cada código vira um balde novo);
- [N] tirar `and checkout_at is null` de `marcar_checkout` deixa vermelho
  `test_t_carimba_so_o_primeiro_clique`;
- [N] `test_negativo_sem_limitador_as_31_passam` desliga o limitador e exige as 31.
Positivos: os 30 primeiros cliques passam; /wa continua com a mesma URL.
"""
import re
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _demo_whatsapp_helpers import GATILHO, _manda, _numero, _sessao, mundo  # noqa: F401  (mundo é fixture)
from db import demo_funnel as funil
from db.connection import get_conn
from test_funil_dashboard import _funil, configured_admin  # noqa: F401 (autouse: admin do painel)

NUMERO = "5511999999999"
PRECOS = "/precos?origem=teste"
TETO = 30


def _client() -> TestClient:
    return TestClient(dashboard.app, base_url="https://testserver", raise_server_exceptions=False)


def _q(sql, args=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _um(sql, args=()):
    rows = _q(sql, args)
    return rows[0] if rows else None


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("WHATSAPP_NUMBER", NUMERO)
    monkeypatch.setenv("DEMO_DAILY_MAX", "1000000")
    dashboard.limiter._storage.reset()
    t0 = _um("select clock_timestamp() as t")["t"]
    yield
    _q("delete from demo_sessions where created_at >= %s", (t0,))
    dashboard.limiter._storage.reset()


def _linhas() -> int:
    return _um("select count(*) as n from demo_sessions")["n"]


# ── /teste ───────────────────────────────────────────────────────────────────

def test_teste_abre_o_whatsapp_com_o_codigo_e_grava_o_clique():
    r = _client().get("/teste?utm_source=ig&utm_campaign=x&next=https://evil.example",
                      follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(f"https://api.whatsapp.com/send?phone={NUMERO}&text="), loc
    assert "evil" not in loc
    assert r.headers["cache-control"] == "no-store"
    texto = urllib.parse.unquote(loc.split("&text=", 1)[1])
    m = re.search(r"\(teste ([2-9A-HJKMNP-TV-Z]{6})\)", texto)
    assert m, texto
    linha = _um("select * from demo_sessions where code = %s", (m.group(1),))
    assert linha["clicked_at"] is not None and linha["opened_at"] is None
    assert (linha["utm_source"], linha["utm_campaign"]) == ("ig", "x")


def test_frase_do_botao_e_o_gatilho_do_bot_sao_a_mesma(mundo):
    """§0.7: a frase da rota e o regex do bot são duas fontes; aqui o texto EXATO que
    o botão põe no WhatsApp vai ao bot e tem de abrir a sessão com aquele código."""
    loc = _client().get("/teste", follow_redirects=False).headers["location"]
    texto = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)["text"][0]
    code = re.search(r"\(teste ([2-9A-HJKMNP-TV-Z]{6})\)", texto).group(1)
    n = _numero()
    _manda(n, texto)
    s = _sessao(n)
    assert s is not None, f"o bot não reconheceu a frase do botão: {texto!r}"
    assert s["code"] == code and s["opened_at"] is not None
    assert mundo.demo()[-1][1] == "lista"


def test_teste_limpa_e_corta_os_utms():
    longo = "a" * 100
    r = _client().get(f"/teste?utm_source=ig%27%3Bdrop%20table&utm_campaign={longo}",
                      follow_redirects=False)
    code = re.search(r"teste ([2-9A-HJKMNP-TV-Z]{6})", urllib.parse.unquote(r.headers["location"])).group(1)
    linha = _um("select utm_source, utm_campaign from demo_sessions where code = %s", (code,))
    assert linha["utm_source"] == "igdroptable" and linha["utm_campaign"] == "a" * 64


@pytest.mark.parametrize("env,valor", [("DEMO_DAILY_MAX", "0"), ("WHATSAPP_NUMBER", "")])
def test_teste_desligado_volta_para_os_precos_sem_gravar(monkeypatch, env, valor):
    monkeypatch.setenv(env, valor)
    antes = _linhas()
    r = _client().get("/teste?utm_source=ig", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == PRECOS
    assert r.headers["cache-control"] == "no-store"
    assert _linhas() == antes


def test_padrao_sem_a_env_e_desligado(mundo, monkeypatch):
    """Sem DEMO_DAILY_MAX o demo vem DESLIGADO (o dono liga na Railway): /teste cai
    nos preços sem gravar e o bot não atende o gatilho."""
    monkeypatch.delenv("DEMO_DAILY_MAX", raising=False)
    antes = _linhas()
    r = _client().get("/teste?utm_source=ig", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == PRECOS
    n = _numero()
    _manda(n, GATILHO)
    assert mundo.demo() == [] and _sessao(n) is None
    assert any("Ainda não tenho uma conta" in c for _, _, c in mundo.normal()), mundo.saida
    assert _linhas() == antes


def test_wa_continua_com_a_mesma_url():
    r = _client().get("/wa", follow_redirects=False)
    texto = urllib.parse.quote("Oi Piggy! Quero acessar minha conta PigBank 🐷")
    assert r.status_code == 302
    assert r.headers["location"] == f"https://api.whatsapp.com/send?phone={NUMERO}&text={texto}"


# ── /t/{code} ────────────────────────────────────────────────────────────────

def test_t_carimba_so_o_primeiro_clique():
    code = funil.criar_clique("ig", None)
    r = _client().get(f"/t/{code}?next=https://evil.example", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == PRECOS
    assert r.headers["cache-control"] == "no-store"
    primeiro = _um("select checkout_at from demo_sessions where code = %s", (code,))["checkout_at"]
    assert primeiro is not None

    _client().get(f"/t/{code}", follow_redirects=False)
    assert _um("select checkout_at from demo_sessions where code = %s", (code,))["checkout_at"] == primeiro


@pytest.mark.parametrize("code", [
    "ABC", "abcdef", "IIIIII", "3KM7Q", "3KM7QXX", "3KM7Q%00", "A%ED%A0%80B", "NAOEXISTE", "22222A",
])
def test_t_invalido_ou_desconhecido_e_o_mesmo_302_sem_carimbo(code):
    antes = _um("select count(*) as n from demo_sessions where checkout_at is not null")["n"]
    r = _client().get(f"/t/{code}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == PRECOS
    assert _um("select count(*) as n from demo_sessions where checkout_at is not null")["n"] == antes


def _inunda(client: TestClient, n: int) -> list[int]:
    # um código DIFERENTE por requisição: é o que separa teto de verdade de teto decorativo
    codigos = [funil.novo_codigo() for _ in range(n)]
    return [client.get(f"/t/{c}", follow_redirects=False).status_code for c in codigos]


def test_t_tem_teto_por_ip_e_nao_por_url():
    status = _inunda(_client(), TETO + 1)
    assert status[:TETO] == [302] * TETO, f"os {TETO} primeiros são legítimos: {status[:TETO]}"
    assert status[TETO] == 429, f"a {TETO + 1}ª deu {status[TETO]}, não 429"


def test_negativo_sem_limitador_as_31_passam(monkeypatch):
    monkeypatch.setattr(dashboard.limiter, "enabled", False)
    assert _inunda(_client(), TETO + 1) == [302] * (TETO + 1)


# ── funil ────────────────────────────────────────────────────────────────────

def test_funil_conta_exato_o_que_o_demo_gravou():
    ler = lambda: _funil()["janelas"]["7d"]["teste"]  # noqa: E731
    antes = ler()

    funil.criar_clique("ig", None)                         # clicou e não abriu
    c2 = funil.criar_clique("ig", None)                    # clicou e abriu
    assert funil.abrir_sessao(c2, funil.wa_hash("5511900000001"), 10**6) == c2
    assert funil.abrir_sessao(None, funil.wa_hash("5511900000002"), 10**6)  # orgânica
    assert funil.reservar_mensagem(c2) == 1
    funil.marcar_resposta(c2, 1)
    funil.marcar_resposta(c2, funil.LIMITE_MSGS)           # bateu o limite
    funil.marcar_checkout(c2)

    depois = ler()
    assert {k: depois[k] - antes[k] for k in antes} == {
        "clicaram": 2, "abriram": 1, "organicos": 1,
        "responderam": 1, "no_limite": 1, "clicaram_checkout": 1,
    }
