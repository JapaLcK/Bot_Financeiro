"""Fontes do funil, 3ª rodada (ataque do Tester): validação do payload, TOCTOU entre a leitura
do cache e a marca de voo, relógio da cota, SDK REAL do Stripe contra um servidor local
(127.0.0.1, chave falsa, nada sai para a rede) e os sobreviventes de mutação.

Reaproveita os fixtures e helpers de `tests/test_funil_fontes.py`.
"""
import asyncio
import http.server
import json
import logging
import socketserver
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
import stripe

import core.admin_dashboard as admin_dashboard
from core import funil_fonte_stripe, funil_fontes, funil_fontes_cache
from core.funil_fontes import Fonte, FonteErro, obter
from db import get_conn
from tests.test_funil_fontes import (  # noqa: F401  (ambiente é autouse; stripe_falso é fixture)
    AGORA, JANELA, PII, RESUMO, SEGREDO, _ch, _ClienteFalso, _admin_client, _envelhece, _fake, _limpo,
    _run, _stripe_get, ambiente, stripe_falso)

_REQUEST_REAL = requests.sessions.Session.request  # capturado ANTES do bloqueio de rede do conftest


def _linha(nome):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select payload, buscado_em, falha_em, dia_utc, chamadas_dia "
                    "from funil_fontes_cache where fonte = %s", (nome,))
        return cur.fetchone()


# ── M1: validação do payload ──────────────────────────────────────────────

@pytest.mark.parametrize("ruim", [{"x": "a\u0000b"}, {"x": "\ud800"}, {"\u0000": 1}, {"x": ["ok", "\udfff"]}],
                         ids=["nul", "surrogate", "nul-na-chave", "surrogate-na-lista"])
def test_nul_e_surrogate_viram_resposta_invalida_com_backoff(monkeypatch, ruim):
    f, ch = _fake(monkeypatch, dados=ruim)
    r = _run(obter(f.nome))
    assert r["estado"] == "erro" and r["mensagem"] == funil_fontes.MENSAGENS["resposta_invalida"]
    assert _run(obter(f.nome))["estado"] == "erro" and len(ch) == 1  # backoff: não rechama
    assert _linha(f.nome)["falha_em"] is not None


def _aninha(n):
    d = {"fim": 1}
    for _ in range(n - 1):
        d = {"k": d}
    return d


def test_aninhamento_e_tamanho_tem_teto(monkeypatch):
    assert funil_fontes._validar(_aninha(20)) == _aninha(20)
    for ruim in (_aninha(21), _aninha(2000), {"x": "a" * (256 * 1024 + 1)}, {"x": list(range(30000))}):
        with pytest.raises(FonteErro) as e:
            funil_fontes._validar(ruim)
        assert e.value.codigo == "resposta_invalida"
    f, ch = _fake(monkeypatch, dados=_aninha(2000))
    assert _run(obter(f.nome))["estado"] == "erro"  # e não 500 por RecursionError


def test_validar_aceita_o_payload_legitimo_e_devolve_o_re_lido(monkeypatch):
    assert funil_fontes._validar({"x": (1, 2), "y": {"z": [1.5, None, True, "ç"]}}) == \
        {"x": [1, 2], "y": {"z": [1.5, None, True, "ç"]}}
    f, _ = _fake(monkeypatch, dados={"x": (1, 2)})
    a = _run(obter(f.nome))
    b = _run(obter(f.nome))
    assert a["dados"] == b["dados"] == {"x": [1, 2]}  # vivo e do cache, mesmo tipo


@pytest.mark.parametrize("chave", [1, None, (1, 2), 1.5], ids=str)
def test_chave_que_nao_e_texto_e_recusada(chave):
    with pytest.raises(FonteErro):
        funil_fontes._validar({chave: 1})


def test_gravar_ok_que_falha_por_qualquer_causa_devolve_ok_mas_marca_a_falha(monkeypatch, caplog):
    async def explode(*a, **k):
        raise RuntimeError(PII)

    f, ch = _fake(monkeypatch)
    monkeypatch.setattr(funil_fontes_cache, "gravar_ok", explode)
    with caplog.at_level(logging.DEBUG):
        a = _run(obter(f.nome))
    assert a["estado"] == "ok" and a["dados"] == {"n": 1}
    assert _linha(f.nome)["falha_em"] is not None
    assert _run(obter(f.nome))["estado"] == "erro" and len(ch) == 1  # não martela a cada GET
    _limpo(caplog.text)


# ── TOCTOU: leitura antiga do cache × marca de voo ────────────────────────

def test_leitura_velha_nao_chama_a_fonte_dentro_do_backoff(monkeypatch):
    f, ch = _fake(monkeypatch)
    _run(funil_fontes_cache.gravar_falha(f.nome, datetime.now(timezone.utc)))  # a outra requisição já falhou
    real = funil_fontes_cache.ler
    chamadas = []

    async def ler_velha(nome):
        chamadas.append(1)
        return None if len(chamadas) == 1 else await real(nome)  # a 1ª leitura é anterior à gravação

    monkeypatch.setattr(funil_fontes_cache, "ler", ler_velha)
    r = _run(obter(f.nome))
    assert ch == [] and r["estado"] == "erro" and len(chamadas) == 2


def test_leitura_velha_nao_chama_a_fonte_dentro_do_ttl(monkeypatch):
    f, ch = _fake(monkeypatch)
    _run(funil_fontes_cache.gravar_ok(f.nome, {"n": 9}, datetime.now(timezone.utc)))
    real = funil_fontes_cache.ler
    n = []

    async def ler_velha(nome):
        n.append(1)
        return None if len(n) == 1 else await real(nome)

    monkeypatch.setattr(funil_fontes_cache, "ler", ler_velha)
    r = _run(obter(f.nome))
    assert ch == [] and r["estado"] == "ok" and r["dados"] == {"n": 9}


def test_rajada_de_60_gets_com_fonte_que_falha_chama_exatamente_uma_vez(monkeypatch):
    for _ in range(8):
        n = []

        def falha():
            n.append(1)
            time.sleep(0.15)
            raise FonteErro("auth")

        f, _ = _fake(monkeypatch, buscar=falha, backoff_s=60, limite_dia=100)

        async def rajada():
            tarefas = []
            for _ in range(60):
                tarefas.append(asyncio.create_task(obter(f.nome)))
                await asyncio.sleep(0.005)
            return await asyncio.gather(*tarefas)

        _run(rajada())
        assert len(n) == 1, len(n)


# ── Cota: relógio do banco, limite_dia validado ───────────────────────────

def test_obter_com_cota_grava_o_dia_do_banco(monkeypatch):
    f, _ = _fake(monkeypatch, limite_dia=3)
    _run(obter(f.nome))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select (now() at time zone 'utc')::date as hoje")
        hoje = cur.fetchone()["hoje"]
    assert _linha(f.nome)["dia_utc"] == hoje and funil_fontes_cache._HOJE_TESTE is None


@pytest.mark.parametrize("ruim", [0, -1, "3", 1.5])
def test_limite_dia_invalido_e_erro_de_configuracao(ruim):
    with pytest.raises(ValueError):
        Fonte(nome="x", ttl_s=1, backoff_s=1, janela=JANELA, faltam=lambda: [], buscar=lambda: {}, limite_dia=ruim)
    Fonte(nome="x", ttl_s=1, backoff_s=1, janela=JANELA, faltam=lambda: [], buscar=lambda: {}, limite_dia=1)
    Fonte(nome="x", ttl_s=1, backoff_s=1, janela=JANELA, faltam=lambda: [], buscar=lambda: {})


def test_reservar_com_limite_zero_recusa_sem_gastar():
    nome = f"tfx_{uuid.uuid4().hex[:8]}"
    with pytest.raises(ValueError):
        _run(funil_fontes_cache.reservar(nome, 0, date(2026, 10, 8)))
    assert _linha(nome) is None


# ── SDK REAL do Stripe contra servidor local ──────────────────────────────

class _H(http.server.BaseHTTPRequestHandler):
    modo = "ok"
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        _H.hits += 1
        msg = f"Invalid API Key provided: {PII}"
        rotas = {"401": (401, {"error": {"type": "invalid_request_error", "message": msg}}),
                 "403": (403, {"error": {"type": "invalid_request_error", "message": msg, "code": "x"}}),
                 "429": (429, {"error": {"type": "rate_limit_error", "message": msg}}),
                 "500": (500, {"error": {"message": msg}})}
        if _H.modo in rotas:
            return self._j(*rotas[_H.modo])
        if _H.modo == "html":
            corpo = f"<html>{PII}</html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        now = int(time.time())
        self._j(200, {"object": "list", "has_more": False, "url": "/v1/charges", "data": [
            {"id": "ch_1", "object": "charge", "status": "succeeded", "amount": 1000, "amount_refunded": 0,
             "currency": "brl", "created": now - 3600, "captured": True,
             "billing_details": {"email": "ana@exemplo.com"}, "customer": "cus_ABC123"}]})

    def _j(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture()
def sdk_real(monkeypatch):
    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    srv = Srv(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _H.modo, _H.hits = "ok", 0
    real = stripe.StripeClient
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(stripe, "StripeClient", lambda key, **kw: real(key, base_addresses={"api": base}, **kw))
    monkeypatch.setattr(requests.sessions.Session, "request", _REQUEST_REAL)  # só localhost
    # Hermético com proxy no ambiente (CI, máquina do dev): localhost nunca passa por proxy.
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", SEGREDO)
    monkeypatch.setattr(admin_dashboard, "_fetch_stripe_billing_sync", lambda: dict(RESUMO))
    yield srv
    srv.shutdown()


@pytest.mark.parametrize("modo,codigo", [("401", "auth"), ("403", "permissao"), ("429", "limite"),
                                         ("500", "indisponivel"), ("html", "indisponivel")])
def test_sdk_real_nao_loga_chave_nem_pii_em_nenhum_modo_de_erro(sdk_real, caplog, modo, codigo):
    _H.modo = modo
    with caplog.at_level(logging.DEBUG):
        r = _stripe_get()
    j = r.json()
    assert j["estado"] == "erro" and j["mensagem"] == funil_fontes.MENSAGENS[codigo] and _H.hits == 1
    _limpo(r.text)
    _limpo(caplog.text)


def test_sdk_real_caminho_feliz_e_sem_pii(sdk_real, caplog):
    with caplog.at_level(logging.DEBUG):
        r = _stripe_get()
    d = r.json()["dados"]["cobrancas"]["30d"]
    assert r.json()["estado"] == "ok" and d["aprovadas"] == 1 and d["receita_liquida"] == 10.0
    _limpo(r.text)
    _limpo(caplog.text)
    assert logging.getLogger("stripe").level == logging.WARNING


# ── Stripe: pequenos ──────────────────────────────────────────────────────

def test_codigo_de_recusa_so_com_fullmatch_e_ate_40_caracteres():
    m = lambda c: funil_fonte_stripe.agregar_cobrancas(  # noqa: E731
        [_ch(status="failed", code=c)], AGORA)["30d"]["motivos_recusa"]
    assert m("card_declined\n") == [{"codigo": "outros", "n": 1}]
    assert m("a" * 40) == [{"codigo": "a" * 40, "n": 1}]
    assert m("a" * 41) == [{"codigo": "outros", "n": 1}]


def test_int_tolera_infinito_e_lixo():
    for v in (float("inf"), float("nan"), None, "x", object()):
        assert funil_fonte_stripe._int(v) == 0
    assert funil_fonte_stripe._int("12") == 12
    r = funil_fonte_stripe.agregar_cobrancas([_ch(amount=float("inf"))], AGORA)
    assert r["30d"]["receita_liquida"] == 0.0


def test_bordas_exatas_de_7_e_30_dias():
    ts = lambda s: SimpleNamespace(status="succeeded", amount=1000, amount_refunded=0, currency="brl",  # noqa: E731
                                   created=AGORA - s, failure_code=None)
    r = funil_fonte_stripe.agregar_cobrancas([ts(7 * 86400), ts(7 * 86400 + 1), ts(30 * 86400), ts(30 * 86400 + 1)], AGORA)
    assert r["7d"]["aprovadas"] == 1 and r["30d"]["aprovadas"] == 3  # o segundo exato ENTRA; 1 s além, não


# ── Sobreviventes de mutação ──────────────────────────────────────────────

def test_thread_velha_nao_solta_a_marca_nova():
    velha, nova = {"t": 0}, {"t": 1}
    funil_fontes._EM_VOO["tfx_marca"] = nova
    try:
        funil_fontes._soltar("tfx_marca", velha)
        assert funil_fontes._EM_VOO["tfx_marca"] is nova
        funil_fontes._soltar("tfx_marca", nova)
        assert "tfx_marca" not in funil_fontes._EM_VOO
    finally:
        funil_fontes._EM_VOO.pop("tfx_marca", None)


def test_listagem_do_stripe_roda_numa_thread_do_executor_dedicado(stripe_falso, monkeypatch):
    nomes = []

    def itera():
        nomes.append(threading_name())
        yield _ch()

    def threading_name():
        return threading.current_thread().name

    class Cli(_ClienteFalso):
        def __init__(self, key, **kw):
            super().__init__(key, **kw)
            self.v1 = SimpleNamespace(charges=SimpleNamespace(list=self._lista))

        def _lista(self, params):
            nomes.append(threading_name())
            return SimpleNamespace(auto_paging_iter=itera)

    monkeypatch.setattr(stripe, "StripeClient", Cli)
    assert _stripe_get().json()["estado"] == "ok"
    assert nomes and all(n.startswith("funil-fonte") for n in nomes), nomes


def test_quatro_fontes_diferentes_correm_em_paralelo(monkeypatch):
    barreira = threading.Barrier(4)

    def espera():
        barreira.wait(timeout=5)  # só passa se as 4 threads estiverem vivas ao mesmo tempo
        return {"n": 1}

    fontes = [_fake(monkeypatch, buscar=espera)[0] for _ in range(4)]

    async def todas():
        return await asyncio.gather(*(obter(f.nome) for f in fontes))

    assert [r["estado"] for r in _run(todas())] == ["ok"] * 4


# ══ Rodada 4 (Manager) ═════════════════════════════════════════════════════
import importlib
from pathlib import Path


def _semeia(nome, *, buscado=None, falha=None, payload='{"velho": 1}'):
    """Linha do cache com timestamps relativos ao `now()` do banco, em segundos (negativo = passado)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into funil_fontes_cache (fonte, payload, buscado_em, falha_em) values (%s, %s::jsonb, "
            "now() + make_interval(secs => %s::float8), now() + make_interval(secs => %s::float8))",
            (nome, payload, buscado, falha))
        conn.commit()


def test_buscado_em_no_futuro_vale_como_vencido(monkeypatch):
    f, ch = _fake(monkeypatch, dados={"novo": 1})
    _semeia(f.nome, buscado=365 * 86400)
    r = _run(obter(f.nome))
    assert len(ch) == 1 and r["estado"] == "ok" and r["dados"] == {"novo": 1}
    antes = _linha(f.nome)
    assert antes["buscado_em"] <= datetime.now(timezone.utc)  # o futuro foi sobrescrito
    assert _run(obter(f.nome))["dados"] == {"novo": 1} and len(ch) == 1  # e agora respeita o TTL


def test_falha_em_no_futuro_vale_como_vencido_e_respeita_a_cota(monkeypatch):
    f, ch = _fake(monkeypatch, limite_dia=1)
    _semeia(f.nome, falha=365 * 86400, payload="null")
    assert _run(obter(f.nome))["estado"] == "ok" and len(ch) == 1   # rechamou
    with get_conn() as conn, conn.cursor() as cur:  # de novo um futuro, com a cota já gasta
        cur.execute("update funil_fontes_cache set falha_em = now() + interval '365 days', "
                    "buscado_em = now() - interval '1 day' where fonte = %s", (f.nome,))
        conn.commit()
    r = _run(obter(f.nome))
    assert len(ch) == 1 and r["mensagem"] == funil_fontes.MENSAGENS["cota"]


def test_timestamps_passados_normais_continuam_respeitando_ttl_e_backoff(monkeypatch):
    f, ch = _fake(monkeypatch)
    _semeia(f.nome, buscado=-10)             # 10 s atrás, TTL de 300 s
    assert _run(obter(f.nome))["dados"] == {"velho": 1} and ch == []
    g, ch2 = _fake(monkeypatch)
    _semeia(g.nome, buscado=-1000, falha=-10, payload="null")  # TTL vencido, backoff de 60 s em curso
    assert _run(obter(g.nome))["estado"] == "erro" and ch2 == []


# ── MODULOS: erro de digitação nos PRs B/C/D vira vermelho aqui, não 500 em produção ──

@pytest.mark.parametrize("nome", sorted(funil_fontes.MODULOS))
def test_cada_entrada_de_modulos_importa_e_exporta_a_fonte_certa(nome):
    mod = importlib.import_module(funil_fontes.MODULOS[nome])
    assert isinstance(mod.FONTE, Fonte) and mod.FONTE.nome == nome
    assert funil_fontes.fonte(nome) is mod.FONTE


def test_texto_de_ocupada_do_front_e_igual_ao_do_backend():
    html = (Path(__file__).resolve().parent.parent / "frontend" / "funil.html").read_text(encoding="utf-8")
    assert json.dumps(funil_fontes.MENSAGENS["ocupada"], ensure_ascii=False) in html


def test_falha_com_falha_em_futuro_sobrescreve_com_o_agora_e_respeita_o_backoff(monkeypatch):
    f, ch = _fake(monkeypatch, erro=FonteErro("auth"), backoff_s=60)
    _semeia(f.nome, buscado=30 * 86400, falha=30 * 86400, payload="null")
    r = _run(obter(f.nome))
    assert len(ch) == 1 and r["estado"] == "erro"
    assert _linha(f.nome)["falha_em"] <= datetime.now(timezone.utc)  # o futuro foi sobrescrito
    assert _run(obter(f.nome))["estado"] == "erro" and len(ch) == 1   # 2ª dentro do backoff: não martela
