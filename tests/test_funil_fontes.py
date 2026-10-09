"""Fontes externas do painel de funil: infraestrutura (`core/funil_fontes.py`), a rota
`GET /admin/api/funil/fonte/{nome}` e a fonte Stripe (`core/funil_fonte_stripe.py`).

HTTP/Stripe SEMPRE simulados: nenhum teste sai para a rede. Cobre: isolamento (uma fonte
que quebra, trava ou não tem credencial não derruba as outras nem o `/admin/api/funil`);
nenhum segredo no corpo nem no log; cache em TABELA com TTL, backoff e `stale`; cota diária
com reserva atômica (inclusive duas chamadas simultâneas); banco do cache fora do ar; auth
da rota; envelope e `dados` como LISTAS FECHADAS; e os cálculos do Stripe.
"""
import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
import stripe
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
from core import funil_fonte_stripe, funil_fontes, funil_fontes_cache
from core.funil_fontes import Fonte, FonteErro, obter
from db import get_conn
from tests.test_funil_dashboard import _PROIBIDAS_EXATAS, _PROIBIDAS_PARTE, _admin_client, _chaves
from token_utils import make_dashboard_token

SEGREDO = "sk_test_SEGREDO123"
ENVELOPE = {"fonte", "estado", "mensagem", "falta", "buscado_em", "janela", "dados"}
ESTADOS = {"ok", "nao_configurado", "erro", "stale"}
JANELA = {"rotulo": "teste", "fuso": "UTC"}


def _limpa():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from funil_fontes_cache where fonte = 'stripe' or fonte like 'tfx_%%' or fonte = 'ga4'")
        conn.commit()


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(admin_dashboard, "_billing_summary_cache", {"fetched_at": None, "data": None})
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    _limpa()
    yield
    _limpa()


# ── Fontes falsas ──────────────────────────────────────────────────────────

def _fake(monkeypatch, **kw):
    """Registra uma fonte falsa de nome único e devolve (fonte, chamadas)."""
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    chamadas = []
    dados = kw.pop("dados", {"n": 1})
    erro = kw.pop("erro", None)

    def buscar():
        chamadas.append(1)
        if erro:
            raise erro
        return dados

    f = Fonte(nome=nome, ttl_s=kw.pop("ttl_s", 300), backoff_s=kw.pop("backoff_s", 60), janela=JANELA,
              faltam=kw.pop("faltam", lambda: []), buscar=kw.pop("buscar", buscar), **kw)
    orig = funil_fontes.fonte
    monkeypatch.setattr(funil_fontes, "fonte", lambda n: f if n == nome else orig(n))
    return f, chamadas


def _envelhece(nome, *, buscado_s=0, falha_s=0):
    """Empurra o relógio da linha para o passado (em vez de dormir)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update funil_fontes_cache set buscado_em = buscado_em - make_interval(secs => %s), "
                "falha_em = falha_em - make_interval(secs => %s) where fonte = %s",
                (buscado_s, falha_s, nome))
        conn.commit()


def _run(c):
    return asyncio.run(c)


# ── Estados, cache, TTL, backoff ───────────────────────────────────────────

def test_sem_credencial_nao_toca_no_banco_nem_na_fonte(monkeypatch):
    f, ch = _fake(monkeypatch, faltam=lambda: ["MINHA_ENV"])

    async def banco(*a, **k):
        raise AssertionError("não devia tocar no banco")

    monkeypatch.setattr(funil_fontes_cache, "db_connect", banco)
    r = _run(obter(f.nome))
    assert r["estado"] == "nao_configurado" and r["falta"] == ["MINHA_ENV"] and r["dados"] is None
    assert r["mensagem"] is None and ch == []


def test_ttl_dentro_nao_chama_depois_chama(monkeypatch):
    f, ch = _fake(monkeypatch)
    a = _run(obter(f.nome))
    b = _run(obter(f.nome))
    assert (a["estado"], b["estado"], len(ch)) == ("ok", "ok", 1)
    assert a["dados"] == b["dados"] == {"n": 1} and b["buscado_em"]
    _envelhece(f.nome, buscado_s=301)
    assert _run(obter(f.nome))["estado"] == "ok" and len(ch) == 2


def test_falha_com_payload_anterior_e_stale_sem_payload_e_erro_e_backoff(monkeypatch):
    f, ch = _fake(monkeypatch, erro=FonteErro("limite"))
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and r["dados"] is None and r["mensagem"] == funil_fontes.MENSAGENS["limite"]
    assert _run(obter(f.nome))["estado"] == "erro" and len(ch) == 1  # backoff: não martela

    g, ch2 = _fake(monkeypatch, dados={"n": 7})
    assert _run(obter(g.nome))["dados"] == {"n": 7}
    _envelhece(g.nome, buscado_s=301)  # TTL vencido (e, com isso, "reinício": nada em memória)
    quebrada = Fonte(**{**g.__dict__, "buscar": lambda: (_ for _ in ()).throw(FonteErro("auth"))})
    monkeypatch.setattr(funil_fontes, "fonte", lambda n: quebrada)
    s = _run(obter(g.nome))
    assert s["estado"] == "stale" and s["dados"] == {"n": 7} and s["buscado_em"]
    assert s["mensagem"] == funil_fontes.MENSAGENS["auth"]
    # dentro do backoff: stale sem chamar a fonte; vencido o backoff, chama de novo
    n = []
    quebrada2 = Fonte(**{**g.__dict__, "buscar": lambda: n.append(1) or {"n": 8}})
    monkeypatch.setattr(funil_fontes, "fonte", lambda _n: quebrada2)
    assert _run(obter(g.nome))["estado"] == "stale" and n == []
    _envelhece(g.nome, falha_s=61)
    assert _run(obter(g.nome))["dados"] == {"n": 8} and n == [1]


def test_codigo_fora_da_tabela_vira_indisponivel(monkeypatch):
    f, _ = _fake(monkeypatch, erro=FonteErro("texto livre da fonte"))
    assert _run(obter(f.nome))["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]


def test_fonte_lenta_vira_timeout(monkeypatch):
    async def lenta():
        await asyncio.sleep(5)

    f, _ = _fake(monkeypatch, buscar=lenta)
    monkeypatch.setattr(funil_fontes, "TIMEOUT_S", 0.05)
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and r["mensagem"] == funil_fontes.MENSAGENS["timeout"]


def test_excecao_inesperada_nao_vaza_segredo_nem_no_log(monkeypatch, caplog):
    f, _ = _fake(monkeypatch, erro=RuntimeError(f"GET https://api.x/v1?key={SEGREDO} falhou"))
    with caplog.at_level(logging.DEBUG):
        r = _run(obter(f.nome))
    assert r["estado"] == "erro" and r["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]
    assert SEGREDO not in json.dumps(r) and SEGREDO not in caplog.text
    assert "RuntimeError" in caplog.text
    assert all(not rec.exc_info for rec in caplog.records)


# ── Cota diária: reserva atômica ───────────────────────────────────────────

def test_cota_enesima_chamada_nao_chama_e_dia_novo_volta(monkeypatch):
    f, ch = _fake(monkeypatch, ttl_s=0, limite_dia=3, backoff_s=0)
    assert [_run(obter(f.nome))["estado"] for _ in range(3)] == ["ok"] * 3 and len(ch) == 3
    r = _run(obter(f.nome))
    assert len(ch) == 3  # a 4ª não chamou
    assert r["estado"] == "stale" and r["dados"] == {"n": 1} and r["mensagem"] == funil_fontes.MENSAGENS["cota"]
    amanha = datetime.now(timezone.utc).date() + timedelta(days=2)
    monkeypatch.setattr(funil_fontes_cache, "_HOJE_TESTE", amanha)
    assert _run(obter(f.nome))["estado"] == "ok" and len(ch) == 4


def test_cota_sem_payload_anterior_e_erro_cota(monkeypatch):
    f, ch = _fake(monkeypatch, ttl_s=0, limite_dia=1, backoff_s=0, erro=FonteErro("limite"))
    _run(obter(f.nome))  # gasta a única; falha
    r = _run(obter(f.nome))
    assert len(ch) == 1 and r["estado"] == "erro" and r["mensagem"] == funil_fontes.MENSAGENS["cota"]


def test_cota_reservas_simultaneas_nao_passam_do_limite():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"

    async def rajada():
        return await asyncio.gather(*(funil_fontes_cache.reservar(nome, 5, date(2026, 10, 8)) for _ in range(14)))

    assert sorted(_run(rajada())).count(True) == 5


def test_reservar_direto_para_na_cota_e_reinicia_no_dia_seguinte():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    hoje = date(2026, 10, 8)
    assert [_run(funil_fontes_cache.reservar(nome, 2, hoje)) for _ in range(3)] == [True, True, False]
    assert _run(funil_fontes_cache.reservar(nome, 2, hoje + timedelta(days=1))) is True


# ── Banco do cache fora do ar ──────────────────────────────────────────────

def test_banco_do_cache_fora_fonte_sem_cota_segue_ao_vivo(monkeypatch):
    f, ch = _fake(monkeypatch)

    async def banco(*a, **k):
        raise OSError("sem banco")

    monkeypatch.setattr(funil_fontes_cache, "db_connect", banco)
    r = _run(obter(f.nome))
    assert r["estado"] == "ok" and r["dados"] == {"n": 1} and len(ch) == 1


def test_banco_do_cache_fora_fonte_com_cota_recusa(monkeypatch):
    f, ch = _fake(monkeypatch, limite_dia=10)

    async def banco(*a, **k):
        raise OSError("sem banco")

    monkeypatch.setattr(funil_fontes_cache, "db_connect", banco)
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and ch == []


# ── Rota: auth, nomes, isolamento ──────────────────────────────────────────

def test_rota_sem_auth_401_usuario_comum_401_e_so_get():
    assert TestClient(dashboard.app).get("/admin/api/funil/fonte/stripe").status_code == 401
    assert TestClient(dashboard.app).get("/admin/api/funil/fonte/inexistente").status_code == 401
    c = TestClient(dashboard.app, base_url="https://testserver")
    token = make_dashboard_token(987654321)
    c.cookies.set("auth_token", token)
    c.cookies.set("admin_auth_token", token)
    assert c.get("/admin/api/funil/fonte/stripe").status_code == 401
    assert c.get("/admin/api/funil/fonte/stripe", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    adm = _admin_client()
    for m in ("post", "put", "patch", "delete"):
        r = getattr(adm, m)("/admin/api/funil/fonte/stripe", headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"})
        assert r.status_code == 405, m


def test_rota_nome_fora_do_dicionario_404_inclusive_os_dos_proximos_prs():
    c = _admin_client()
    for nome in ("inexistente", "ga4", "clarity", "meta", "STRIPE", "stripe%2F..", "x" * 300):
        r = c.get(f"/admin/api/funil/fonte/{nome}")
        assert r.status_code == 404, nome
        if nome.isalnum():  # os com "/" ou ".." nem chegam à rota (404 do roteador)
            assert r.headers["cache-control"] == "no-store"


def test_rota_isolamento_fonte_quebrada_nao_afeta_stripe_nem_o_funil(monkeypatch):
    f, _ = _fake(monkeypatch, erro=RuntimeError(f"boom {SEGREDO}"))
    monkeypatch.setitem(funil_fontes.MODULOS, f.nome, "nao.importado")
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "")
    c = _admin_client()
    ruim = c.get(f"/admin/api/funil/fonte/{f.nome}")
    assert ruim.status_code == 200 and ruim.json()["estado"] == "erro" and SEGREDO not in ruim.text
    assert c.get("/admin/api/funil/fonte/stripe").json()["estado"] == "nao_configurado"
    assert c.get("/admin/api/funil").status_code == 200


# ── Stripe ─────────────────────────────────────────────────────────────────

AGORA = 1_800_000_000


def _ch(status="succeeded", amount=1000, refunded=0, currency="brl", dias=1, code=None, **extra):
    return SimpleNamespace(status=status, amount=amount, amount_refunded=refunded, currency=currency,
                           created=AGORA - int(dias * 86400), failure_code=code, **extra)


def test_agregar_lista_vazia_e_zeros():
    r = funil_fonte_stripe.agregar_cobrancas([], AGORA)
    vazio = {"aprovadas": 0, "recusadas": 0, "receita_liquida": 0.0, "motivos_recusa": []}
    assert r == {"7d": vazio, "30d": vazio, "truncado": False}


def test_agregar_receita_liquida_reembolso_parcial_moeda_e_janelas():
    r = funil_fonte_stripe.agregar_cobrancas([
        _ch(amount=10000, refunded=2500, dias=1),   # 75,00 nas duas janelas
        _ch(amount=5000, dias=20),                   # só 30d
        _ch(amount=9999, currency="usd", dias=1),    # aprovada, mas fora da receita BRL
        _ch(status="pending", amount=7000),          # ignorada
        _ch(status="failed", amount=3000, code="card_declined", dias=2),
    ], AGORA)
    assert r["7d"]["receita_liquida"] == 75.0 and r["7d"]["aprovadas"] == 2 and r["7d"]["recusadas"] == 1
    assert r["30d"]["receita_liquida"] == 125.0 and r["30d"]["aprovadas"] == 3


def test_agregar_motivos_so_no_formato_top5_e_resto_vira_outros():
    cobr = [_ch(status="failed", code="card_declined")] * 4
    cobr += [_ch(status="failed", code=f"codigo_{chr(97 + i)}") for i in range(6)]   # 6 distintos, n=1
    cobr += [_ch(status="failed", code="<img src=x>"), _ch(status="failed", code=None), _ch(status="failed", code="A" * 50)]
    m = funil_fonte_stripe.agregar_cobrancas(cobr, AGORA)["30d"]["motivos_recusa"]
    assert m[0] == {"codigo": "card_declined", "n": 4}
    assert len(m) == 6 and m[-1] == {"codigo": "outros", "n": 2 + 3}  # 2 além do top5 + 3 inválidos
    assert all(x["codigo"] == "outros" or funil_fonte_stripe._CODIGO.fullmatch(x["codigo"]) for x in m)


def test_agregar_trunca_a_partir_de_1001():
    assert funil_fonte_stripe.agregar_cobrancas([_ch()] * 1000, AGORA)["truncado"] is False
    r = funil_fonte_stripe.agregar_cobrancas([_ch()] * 1001, AGORA)
    assert r["truncado"] is True and r["30d"]["aprovadas"] == 1000


class _ClienteFalso:
    """Substitui `stripe.StripeClient`: guarda como foi construído e o que listou."""
    instancias = []

    def __init__(self, key, **kw):
        self.key, self.kw, self.params = key, kw, None
        self.cobrancas, self.erro = _ClienteFalso.cobrancas, _ClienteFalso.erro
        _ClienteFalso.instancias.append(self)
        self.v1 = SimpleNamespace(charges=SimpleNamespace(list=self._list))

    def _list(self, params):
        self.params = params
        if self.erro:
            raise self.erro
        return SimpleNamespace(auto_paging_iter=lambda: iter(self.cobrancas))


RESUMO = {"available": True, "subscriptions": {"active": 3, "trialing": 2, "past_due": 1, "canceled": 9, "other": 4},
          "mrr": 59.7, "ticket_medio": 19.9, "trial_mrr_potencial": 39.8, "fetched_at": "x"}


@pytest.fixture()
def stripe_falso(monkeypatch):
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", SEGREDO)
    monkeypatch.setattr(admin_dashboard, "_fetch_stripe_billing_sync", lambda: dict(RESUMO))
    _ClienteFalso.instancias, _ClienteFalso.cobrancas, _ClienteFalso.erro = [], [], None
    monkeypatch.setattr(stripe, "StripeClient", _ClienteFalso)
    return _ClienteFalso


def _stripe_get():
    r = _admin_client().get("/admin/api/funil/fonte/stripe")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r


def test_stripe_sem_chave_nao_configurado():
    j = _stripe_get().json()
    assert j["estado"] == "nao_configurado" and j["falta"] == ["STRIPE_SECRET_KEY"]
    assert j["dados"] is None and j["fonte"] == "stripe"


def test_stripe_ok_numeros_e_cliente_com_timeout_curto(stripe_falso):
    stripe_falso.cobrancas = [
        _ch(amount=2990, dias=1, receipt_email="ana@exemplo.com", customer="cus_ABC123",
            billing_details=SimpleNamespace(email="ana@exemplo.com", name="Ana"), description="Ana pagou"),
        _ch(status="failed", dias=3, code="insufficient_funds", customer="cus_ZZZ", receipt_email="bia@exemplo.com"),
    ]
    j = _stripe_get().json()
    assert j["estado"] == "ok" and j["mensagem"] is None and j["falta"] is None
    d = j["dados"]
    assert d["assinaturas"] == {"ativas": 3, "em_trial": 2, "em_atraso": 1, "canceladas": 9, "outras": 4}
    assert (d["mrr"], d["ticket_medio"], d["mrr_trial_potencial"]) == (59.7, 19.9, 39.8)
    assert d["cobrancas"]["7d"] == {"aprovadas": 1, "recusadas": 1, "receita_liquida": 29.9,
                                    "motivos_recusa": [{"codigo": "insufficient_funds", "n": 1}]}
    assert d["cobrancas"]["truncado"] is False
    [cli] = stripe_falso.instancias
    assert cli.key == SEGREDO and cli.kw["max_network_retries"] == 0
    assert cli.kw["http_client"]._timeout == 8
    assert cli.params["limit"] == 100 and "gte" in cli.params["created"]
    # sem PII: nada do que o Stripe devolveu de pessoa chega ao JSON, nem a chave
    texto = j_texto(j)
    for proibido in ("@", "cus_", "exemplo.com", "Ana", SEGREDO):
        assert proibido not in texto, proibido
    # segunda chamada: dentro do TTL, não consulta o Stripe de novo
    assert _stripe_get().json()["estado"] == "ok" and len(stripe_falso.instancias) == 1


def j_texto(j):
    return json.dumps(j, ensure_ascii=False)


def test_stripe_envelope_e_dados_sao_listas_fechadas(stripe_falso):
    stripe_falso.cobrancas = [_ch(status="failed", code="card_declined", email="x@y.com"), _ch()]
    j = _stripe_get().json()
    assert set(j) == ENVELOPE and j["estado"] in ESTADOS and set(j["janela"]) == {"rotulo", "fuso"}
    permitidas = {"assinaturas", "ativas", "em_trial", "em_atraso", "canceladas", "outras", "mrr", "ticket_medio",
                  "mrr_trial_potencial", "cobrancas", "7d", "30d", "truncado", "aprovadas", "recusadas",
                  "receita_liquida", "motivos_recusa", "codigo", "n"}
    chaves = _chaves(j["dados"])
    assert chaves <= permitidas, sorted(chaves - permitidas)
    for ch in _chaves(j):
        assert ch not in _PROIBIDAS_EXATAS and not any(p in ch for p in _PROIBIDAS_PARTE), ch
    assert set(j["dados"]) == {"assinaturas", "mrr", "ticket_medio", "mrr_trial_potencial", "cobrancas"}
    assert set(j["dados"]["cobrancas"]["7d"]) == {"aprovadas", "recusadas", "receita_liquida", "motivos_recusa"}


def _com_causa(e, causa):
    e.__cause__ = causa
    return e


@pytest.mark.parametrize("erro,codigo", [
    (stripe.AuthenticationError(f"Invalid API Key provided: {SEGREDO}"), "auth"),
    (stripe.PermissionError(f"restricted key {SEGREDO}"), "permissao"),
    (stripe.RateLimitError(f"too many {SEGREDO}"), "limite"),
    (stripe.APIConnectionError(f"https://api.stripe.com?k={SEGREDO}"), "indisponivel"),
    (_com_causa(stripe.APIConnectionError(f"timeout {SEGREDO}"), requests.exceptions.ReadTimeout()), "timeout"),
    (_com_causa(stripe.APIConnectionError(f"timeout {SEGREDO}"), requests.exceptions.ConnectTimeout()), "timeout"),
    (_com_causa(stripe.APIConnectionError(f"dns {SEGREDO}"), requests.exceptions.ConnectionError()), "indisponivel"),
    (stripe.APIError(f"500 {SEGREDO}"), "indisponivel"),
])
def test_stripe_falha_vira_erro_com_mensagem_fixa_e_sem_segredo(stripe_falso, caplog, erro, codigo):
    stripe_falso.erro = erro
    with caplog.at_level(logging.DEBUG):
        r = _stripe_get()
    j = r.json()
    assert j["estado"] == "erro" and j["mensagem"] == funil_fontes.MENSAGENS[codigo] and j["dados"] is None
    assert SEGREDO not in r.text and SEGREDO not in caplog.text
    assert all(not rec.exc_info for rec in caplog.records)


def test_stripe_falha_depois_de_sucesso_e_stale(stripe_falso):
    stripe_falso.cobrancas = [_ch(amount=1000)]
    assert _stripe_get().json()["estado"] == "ok"
    _envelhece("stripe", buscado_s=301)
    stripe_falso.erro = stripe.APIConnectionError(SEGREDO)
    j = _stripe_get().json()
    assert j["estado"] == "stale" and j["dados"]["cobrancas"]["30d"]["receita_liquida"] == 10.0
    assert j["buscado_em"] and SEGREDO not in j_texto(j)


def test_stripe_resumo_indisponivel_nao_repassa_reason(stripe_falso, monkeypatch):
    monkeypatch.setattr(admin_dashboard, "_fetch_stripe_billing_sync",
                        lambda: {"available": False, "reason": f"Invalid API Key {SEGREDO}"})
    r = _stripe_get()
    assert r.json()["estado"] == "erro" and r.json()["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]
    assert SEGREDO not in r.text and stripe_falso.instancias == []  # nem chegou a listar cobranças


def test_stripe_resumo_stale_em_memoria_vira_falha(stripe_falso, monkeypatch):
    monkeypatch.setattr(admin_dashboard, "_billing_summary_cache", {
        "fetched_at": datetime.now(timezone.utc), "data": {**RESUMO, "stale": True}})
    j = _stripe_get().json()
    assert j["estado"] == "erro" and stripe_falso.instancias == []


# ══ Rodada 2 (ataque do Tester) ════════════════════════════════════════════
import threading
import time
from functools import partial

from tests.test_log_falha_traceback import _system_event_logs

PII = f"{SEGREDO} ana@exemplo.com cus_ABC123"


# ── A1: segredo no caminho reaproveitado (resumo do admin) e na listagem ───

_EXCECOES = [
    stripe.AuthenticationError(f"Invalid API Key provided: {PII}"),
    stripe.APIConnectionError(f"net {PII}"),
    stripe.PermissionError(PII), stripe.RateLimitError(PII), stripe.APIError(PII),
    RuntimeError(PII), ValueError(PII), KeyError(PII), OSError(PII),
    requests.exceptions.ConnectionError(PII),
]


def _limpo(texto):
    for proibido in (SEGREDO, "ana@exemplo.com", "cus_ABC123"):
        assert proibido not in texto, proibido


@pytest.mark.parametrize("erro", _EXCECOES, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("onde", ["inicio", "meio"])
def test_resumo_do_admin_nao_vaza_nem_no_log_nem_no_cache_nem_no_json(monkeypatch, caplog, erro, onde):
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", SEGREDO)

    def itera():
        yield SimpleNamespace(status="active", items=None)
        raise erro

    def lista(**kw):
        if onde == "inicio":
            raise erro
        return SimpleNamespace(auto_paging_iter=itera)

    monkeypatch.setattr(stripe.Subscription, "list", lista)
    with caplog.at_level(logging.DEBUG):
        r = asyncio.run(admin_dashboard.fetch_billing_summary(force=True))
    assert r["available"] is False and r["reason"] == type(erro).__name__
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert warns and all(not x.exc_info for x in warns)
    _limpo(caplog.text)
    _limpo(json.dumps(admin_dashboard._billing_summary_cache, default=str))
    _limpo(json.dumps(r))
    _limpo(repr(_system_event_logs(monkeypatch, warns)))


@pytest.mark.parametrize("erro", _EXCECOES, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("onde", ["inicio", "meio"])
def test_listagem_de_cobrancas_nao_vaza_em_nenhum_ponto(stripe_falso, monkeypatch, caplog, erro, onde):
    def itera():
        yield _ch()
        raise erro

    class Cli(_ClienteFalso):
        def _list(self, params):
            if onde == "inicio":
                raise erro
            return SimpleNamespace(auto_paging_iter=itera)

        def __init__(self, key, **kw):
            super().__init__(key, **kw)
            self.v1 = SimpleNamespace(charges=SimpleNamespace(list=self._list))

    monkeypatch.setattr(stripe, "StripeClient", Cli)
    with caplog.at_level(logging.DEBUG):
        r = _stripe_get()
    assert r.json()["estado"] == "erro"
    _limpo(r.text)
    _limpo(caplog.text)
    _limpo(repr(_system_event_logs(monkeypatch, [x for x in caplog.records if x.levelno >= logging.WARNING])))


# ── A2: executor dedicado e voo único por fonte ───────────────────────────

def _get(c, url):
    # `send`, não `.get`: o conftest bloqueia o verbo (rede externa), não o transporte ASGI.
    return c.send(c.build_request("GET", url))


def test_voo_unico_por_fonte_e_painel_responde_com_fonte_travada(monkeypatch):
    import httpx

    chamadas = []

    def travada():
        chamadas.append(threading.current_thread().name)
        time.sleep(2)
        return {"n": 1}

    f, _ = _fake(monkeypatch, buscar=travada)
    monkeypatch.setitem(funil_fontes.MODULOS, f.nome, "x")
    token = _admin_client().cookies.get("admin_auth_token")
    assert token

    async def cenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app),
                                     base_url="https://testserver",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            alvos = [asyncio.create_task(_get(c, f"/admin/api/funil/fonte/{f.nome}")) for _ in range(30)]
            await asyncio.sleep(0.3)
            t0 = time.monotonic()
            await asyncio.to_thread(lambda: 1)  # executor PADRÃO do loop
            padrao = time.monotonic() - t0
            t0 = time.monotonic()
            funil = await _get(c, "/admin/api/funil")
            painel = time.monotonic() - t0
            rs = await asyncio.gather(*alvos)
            return padrao, funil.status_code, painel, rs

    padrao, funil_status, painel, rs = _run(cenario())
    assert len(chamadas) == 1 and chamadas[0].startswith("funil-fonte"), chamadas
    assert padrao < 1 and funil_status == 200 and painel < 1.5, (padrao, painel)
    estados = [r.json()["estado"] for r in rs]
    assert estados.count("ok") == 1 and estados.count("erro") == 29, estados
    assert all(r.json()["mensagem"] in (None, funil_fontes.MENSAGENS["ocupada"]) for r in rs)


def test_stripe_travado_usa_uma_so_thread_do_executor_padrao(stripe_falso, monkeypatch):
    import httpx

    chamadas = []

    def lento():
        chamadas.append(1)
        time.sleep(1.5)
        return dict(RESUMO)

    monkeypatch.setattr(admin_dashboard, "_fetch_stripe_billing_sync", lento)
    token = _admin_client().cookies.get("admin_auth_token")

    async def cenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app),
                                     base_url="https://testserver",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            alvos = [asyncio.create_task(_get(c, "/admin/api/funil/fonte/stripe")) for _ in range(30)]
            await asyncio.sleep(0.3)
            t0 = time.monotonic()
            await asyncio.to_thread(lambda: 1)
            padrao = time.monotonic() - t0
            await asyncio.gather(*alvos)
            return padrao

    assert _run(cenario()) < 1 and len(chamadas) == 1


def test_marca_de_voo_solta_em_erro_timeout_e_quando_a_thread_termina(monkeypatch):
    # erro comum: solta na hora
    f, _ = _fake(monkeypatch, erro=FonteErro("auth"))
    _run(obter(f.nome))
    assert f.nome not in funil_fontes._EM_VOO
    # timeout com thread viva: marca fica; solta quando a thread termina
    liberar = threading.Event()
    g, _ = _fake(monkeypatch, buscar=lambda: liberar.wait(5) and {"n": 1})
    monkeypatch.setattr(funil_fontes, "TIMEOUT_S", 0.1)
    r = _run(obter(g.nome))
    assert r["mensagem"] == funil_fontes.MENSAGENS["timeout"] and g.nome in funil_fontes._EM_VOO
    _envelhece(g.nome, falha_s=61)  # backoff vencido: nova requisição bate na marca, não na fonte
    assert _run(obter(g.nome))["mensagem"] == funil_fontes.MENSAGENS["ocupada"]
    liberar.set()
    for _ in range(50):
        if g.nome not in funil_fontes._EM_VOO:
            break
        time.sleep(0.05)
    assert g.nome not in funil_fontes._EM_VOO


def test_thread_presa_nao_trava_a_fonte_para_sempre(monkeypatch):
    liberar = threading.Event()
    f, ch = _fake(monkeypatch, buscar=lambda: liberar.wait(5) and {"n": 1}, backoff_s=0)
    monkeypatch.setattr(funil_fontes, "TIMEOUT_S", 0.1)
    _run(obter(f.nome))
    funil_fontes._EM_VOO[f.nome]["t"] -= 10  # passou TIMEOUT_S + backoff_s
    assert not funil_fontes._ocupada(f)
    liberar.set()


def test_cancelar_a_requisicao_propaga_e_solta_a_marca(monkeypatch):
    f, _ = _fake(monkeypatch, buscar=lambda: time.sleep(0.5) or {"n": 1})

    async def cenario():
        t = asyncio.create_task(obter(f.nome))
        await asyncio.sleep(0.2)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        await asyncio.sleep(0.6)

    _run(cenario())
    assert f.nome not in funil_fontes._EM_VOO


# ── M1: dados não serializáveis ───────────────────────────────────────────

@pytest.mark.parametrize("ruim", [{"x": float("nan")}, {"x": float("inf")}, {"x": {1, 2}},
                                  {"x": b"bytes"}, {"x": object()}, ["lista"], None],
                         ids=["nan", "inf", "set", "bytes", "object", "lista", "none"])
def test_dados_nao_serializaveis_viram_erro_com_backoff(monkeypatch, ruim):
    f, ch = _fake(monkeypatch, dados=ruim)
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and r["mensagem"] == funil_fontes.MENSAGENS["resposta_invalida"]
    assert r["dados"] is None
    assert _run(obter(f.nome))["estado"] == "erro" and len(ch) == 1  # falha_em gravada: backoff
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select payload, falha_em from funil_fontes_cache where fonte = %s", (f.nome,))
        linha = cur.fetchone()
    assert linha["payload"] is None and linha["falha_em"] is not None


# ── M2: formas de `buscar` ────────────────────────────────────────────────

async def _async_fn():
    return {"n": 2}


class _AsyncCall:
    async def __call__(self):
        return {"n": 2}


@pytest.mark.parametrize("buscar", [lambda: {"n": 2}, _async_fn, _AsyncCall(), lambda: _async_fn(),
                                    partial(_async_fn)], ids=["def", "async def", "async __call__", "lambda->coro", "partial"])
def test_todas_as_formas_de_buscar_dao_dados_e_nao_coroutine(monkeypatch, buscar):
    f, _ = _fake(monkeypatch, buscar=buscar)
    r = _run(obter(f.nome))
    assert r["estado"] == "ok" and r["dados"] == {"n": 2}


# ── M3: cota entre dias e instâncias ──────────────────────────────────────

def test_cota_alternar_dias_entre_instancias_nao_burla_o_limite():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    d, d1 = date(2026, 10, 8), date(2026, 10, 9)
    res = [_run(funil_fontes_cache.reservar(nome, 3, d1 if i % 2 == 0 else d)) for i in range(20)]
    assert res.count(True) == 3, res


def test_cota_dia_novo_reinicia_conta_a_partir_da_segunda_e_nao_volta_atras():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    d, d1 = date(2026, 10, 8), date(2026, 10, 9)
    r = lambda dia: _run(funil_fontes_cache.reservar(nome, 2, dia))  # noqa: E731
    assert [r(d), r(d), r(d)] == [True, True, False]
    assert [r(d1), r(d1), r(d1)] == [True, True, False]   # reiniciou e incrementa a 2ª
    assert r(d) is False                                  # requisição atrasada: conta no dia novo
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select dia_utc, chamadas_dia from funil_fontes_cache where fonte = %s", (nome,))
        assert cur.fetchone() == {"dia_utc": d1, "chamadas_dia": 2}


def test_cota_linha_criada_pelo_cache_sem_dia_conta_como_dia_novo():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    _run(funil_fontes_cache.gravar_ok(nome, {"n": 1}, datetime.now(timezone.utc)))  # dia_utc NULL
    assert _run(funil_fontes_cache.reservar(nome, 1, date(2026, 10, 8))) is True
    assert _run(funil_fontes_cache.reservar(nome, 1, date(2026, 10, 8))) is False


# ── Sobreviventes: relógio, logs, fail-closed, loop livre, constantes ─────

def test_sem_override_o_dia_e_o_do_relogio_do_banco():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    assert _run(funil_fontes_cache.reservar(nome, 5)) is True
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select dia_utc, (now() at time zone 'utc')::date as hoje "
                    "from funil_fontes_cache where fonte = %s", (nome,))
        linha = cur.fetchone()
    assert linha["dia_utc"] == linha["hoje"]


@pytest.mark.parametrize("ponto", ["ler", "reservar", "gravar_ok", "gravar_falha"])
def test_logs_do_cache_so_levam_o_nome_do_tipo(monkeypatch, caplog, ponto):
    async def explode(*a, **k):
        raise RuntimeError(PII)

    kw = {"limite_dia": 5} if ponto == "reservar" else {}
    if ponto == "gravar_falha":
        kw["erro"] = FonteErro("auth")
    f, _ = _fake(monkeypatch, **kw)
    monkeypatch.setattr(funil_fontes_cache, ponto, explode)
    with caplog.at_level(logging.DEBUG):
        _run(obter(f.nome))
    assert "RuntimeError" in caplog.text
    _limpo(caplog.text)
    assert all(not x.exc_info for x in caplog.records)


def test_reserva_que_falha_nao_chama_a_fonte(monkeypatch):
    async def explode(*a, **k):
        raise OSError("x")

    f, ch = _fake(monkeypatch, limite_dia=5)
    monkeypatch.setattr(funil_fontes_cache, "reservar", explode)
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and ch == []


def _loop_livre(coro_fonte):
    """Roda `coro_fonte` e mede o maior intervalo entre batidas de um relógio no MESMO loop."""
    async def cenario():
        gaps, parar = [], False

        async def batida():
            ult = time.monotonic()
            while not parar:
                await asyncio.sleep(0.02)
                agora = time.monotonic()
                gaps.append(agora - ult)
                ult = agora

        t = asyncio.create_task(batida())
        await asyncio.sleep(0.05)
        r = await coro_fonte()
        parar = True
        await t
        return r, max(gaps)

    return _run(cenario())


def test_fonte_sincrona_nao_bloqueia_o_loop(monkeypatch):
    f, _ = _fake(monkeypatch, buscar=lambda: time.sleep(0.6) or {"n": 1})
    r, gap = _loop_livre(lambda: obter(f.nome))
    assert r["estado"] == "ok" and gap < 0.3, gap


def test_listagem_do_stripe_nao_bloqueia_o_loop(stripe_falso):
    def lenta():
        for c in [_ch(), _ch()]:
            time.sleep(0.3)
            yield c

    class Cli(_ClienteFalso):
        def __init__(self, key, **kw):
            super().__init__(key, **kw)
            self.v1 = SimpleNamespace(charges=SimpleNamespace(
                list=lambda params: SimpleNamespace(auto_paging_iter=lenta)))

    stripe.StripeClient = Cli  # restaurado pelo monkeypatch da fixture (mesmo atributo)
    r, gap = _loop_livre(lambda: obter("stripe"))
    assert r["estado"] == "ok" and gap < 0.25, gap


def test_timeout_da_consulta_e_12s(monkeypatch):
    visto = []
    orig = asyncio.wait_for

    async def espia(aw, timeout):
        visto.append(timeout)
        return await orig(aw, timeout)

    f, _ = _fake(monkeypatch)
    monkeypatch.setattr(funil_fontes.asyncio, "wait_for", espia)
    _run(obter(f.nome))
    assert visto == [12]


def test_constantes_da_fonte_stripe():
    F = funil_fonte_stripe.FONTE
    assert (F.nome, F.ttl_s, F.backoff_s, F.limite_dia) == ("stripe", 300, 60, None)
    assert F.janela["fuso"] == "UTC"


def test_stripe_lista_exatamente_os_ultimos_30_dias(stripe_falso, monkeypatch):
    monkeypatch.setattr(funil_fonte_stripe.time, "time", lambda: AGORA)
    _stripe_get()
    assert stripe_falso.instancias[0].params["created"] == {"gte": AGORA - 30 * 86400}


def test_stripe_backoff_de_60s_nao_martela(stripe_falso):
    stripe_falso.erro = stripe.APIError("x")
    _stripe_get()
    _stripe_get()
    assert len(stripe_falso.instancias) == 1


# ── B5, B11: prazo da paginação e só-autorização ──────────────────────────

def test_paginacao_estoura_o_prazo_e_devolve_o_que_tem_truncado():
    def lento():
        for _ in range(20):
            time.sleep(0.03)
            yield _ch()

    r = funil_fonte_stripe.agregar_cobrancas(lento(), AGORA, time.monotonic() + 0.1)
    assert r["truncado"] is True and 0 < r["30d"]["aprovadas"] < 20


def test_listagem_usa_o_prazo_total(stripe_falso, monkeypatch):
    def lento():
        for _ in range(50):
            time.sleep(0.03)
            yield _ch()

    class Cli(_ClienteFalso):
        def __init__(self, key, **kw):
            super().__init__(key, **kw)
            self.v1 = SimpleNamespace(charges=SimpleNamespace(
                list=lambda params: SimpleNamespace(auto_paging_iter=lento)))

    monkeypatch.setattr(stripe, "StripeClient", Cli)
    monkeypatch.setattr(funil_fonte_stripe, "_PRAZO_LISTAGEM_S", 0.1)
    d = _stripe_get().json()["dados"]["cobrancas"]
    assert d["truncado"] is True and d["30d"]["aprovadas"] < 50
    assert funil_fonte_stripe._PRAZO_LISTAGEM_S != 0  # (restaurado pelo monkeypatch)


def test_so_autorizacao_nao_e_receita_nem_aprovada():
    r = funil_fonte_stripe.agregar_cobrancas([
        _ch(amount=5000, captured=False), _ch(amount=1000, captured=True), _ch(amount=700)], AGORA)
    assert r["30d"]["aprovadas"] == 2 and r["30d"]["receita_liquida"] == 17.0
    assert "captured" in stripe.Charge.__annotations__  # o campo existe no SDK instalado
