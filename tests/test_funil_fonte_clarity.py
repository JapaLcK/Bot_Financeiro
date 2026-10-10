"""Fonte Clarity do painel de funil (`core/funil_fonte_clarity.py`): parser tolerante, filtro da
página /precos, COTA diária (a Data Export API aceita 10 por dia), erros com mensagem fixa e,
acima de tudo, SEGREDO: o token não aparece em JSON, log, tabela de cache nem stderr, e a linha
de diagnóstico de formato leva SÓ nomes (nunca valor, URL ou número).

HERMÉTICO: nenhuma chamada ao Clarity. A API é um servidor HTTP local (127.0.0.1, porta
efêmera) com o host trocado SÓ no teste (a constante de produção é conferida à parte).
"""
import ast
import http.server
import ipaddress
import json
import logging
import re
import socketserver
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from core import funil_fonte_clarity as clarity
from core import funil_fontes, funil_fontes_cache
from tests.test_funil_dashboard import _PROIBIDAS_EXATAS, _PROIBIDAS_PARTE, _chaves
from tests.test_funil_fontes import (  # noqa: F401  (ambiente é autouse)
    ENVELOPE, _admin_client, _envelhece, _fake, _run, ambiente)
from tests.test_funil_fontes_r3 import _REQUEST_REAL, _linha
from tests.test_log_falha_traceback import _system_event_logs

_GET_REAL = requests.get  # capturado ANTES do bloqueio de rede do conftest
_KW: list[dict] = []  # os kwargs de cada requests.get da fonte (timeout, allow_redirects, params...)


def _get_so_local(url, **kw):
    if not url.startswith(("http://127.0.0.1:", "https://127.0.0.1:")):  # um host de verdade levanta aqui em vez de sair para a rede
        raise RuntimeError("rede real bloqueada no teste")
    _KW.append(kw)
    return _GET_REAL(url, **kw)


TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJGQUxTTyJ9.assinatura-FALSA_9f3a"
VAZA = "Invalid token: ana@exemplo.com cus_ABC123"  # texto "do Clarity" que nunca pode chegar ao painel
SEG_URL = "SEGREDO-FB-9911"
SEG_VALOR = "VALOR-STRING-SECRETO"
SEGREDOS = (TOKEN, *TOKEN.split("."), VAZA, "ana@exemplo.com", "cus_ABC123", "Bearer")
VALORES = (SEG_URL, "TOKEN-NA-URL-777", "pigbankai.com", "fbclid", "utm_source", SEG_VALOR, "7654321", "1234567",
           "frag-SEG", "localhost")
HOJE = datetime.now(timezone.utc).date()


def sem_segredo(*textos):
    for t in textos:
        for s in SEGREDOS:
            assert s not in t, s


# ── Respostas no formato da doc (amostra: metricName + information, números como texto) ──

def _t(url, total, bots, users):
    return {"totalSessionCount": str(total), "totalBotSessionCount": str(bots), "distantUserCount": str(users),
            "PagesPerSessionPercentage": 1.0931, "Url": url}


def _s(url, n, media):
    # ATENÇÃO: `sessionsCount` na rolagem é INVENÇÃO desta fixture (a doc pública só mostra a contagem no
    # Traffic): o caso REAL provável é `_s_sem` (sem contagem), coberto à parte (média simples).
    return {"sessionsCount": str(n), "averageScrollDepth": media, "Url": url}


def _s_sem(url, media):
    return {"averageScrollDepth": media, "Url": url}


def _c(url, n, pct):
    return {"sessionsCount": str(n), "sessionsWithMetricPercentage": pct,
            "sessionsWithoutMetricPercentage": 100 - pct, "Url": url}


A = f"https://pigbankai.com/precos?utm_source=FACE&fbclid=IwAR-{SEG_URL}&token=TOKEN-NA-URL-777#frag-SEG"
B = "https://pigbankai.com/precos/"
C = "http://localhost/precos"
FORA = ["https://pigbankai.com/precos-app.js", "https://pigbankai.com/continuar-compra", "https://pigbankai.com/",
        "/PRECOS", "https://pigbankai.com/precos/x"]


def _resp(extra=()):
    return [
        {"metricName": "Traffic", "information": [_t(A, 100, 10, 80), _t(B, 50, 0, 40), _t(C, 30, 5, 20)]
         + [_t(u, 999, 0, 999) for u in FORA]},
        {"metricName": "ScrollDepth", "information": [_s(A, 90, 50.0), _s(B, 50, 80.0), _s(C, 25, 20.0)]
         + [_s(u, 999, 10.0) for u in FORA]},
        {"metricName": "DeadClickCount", "information": [_c(A, 90, 10.0), _c(B, 50, 20.0), _c(C, 25, 4.0)]
         + [_c(u, 999, 90.0) for u in FORA]},
        {"metricName": "RageClickCount", "information": [_c(A, 90, 2.0), _c(B, 50, 0.0)] + [_c(u, 999, 90.0) for u in FORA]},
        {"metricName": "Popular Pages", "information": [{"Url": A, "pagesViews": 7654321}]},
        *extra,
    ]


# sessões humanas = (100-10) + 50 + (30-5); rolagem e % ponderadas pelas sessões da própria linha
ESPERADO = {
    "pagina": "/precos", "janela_dias": 3,
    "trafego": {"sessoes": 165, "usuarios": 140},
    "rolagem_media_pct": 54.55,  # (90*50 + 50*80 + 25*20) / 165
    "cliques_mortos": {"pct_sessoes": 12.12},  # (90*10 + 50*20 + 25*4) / 165; a contagem só pesa a média
    "cliques_raiva": {"pct_sessoes": 1.29},  # (90*2 + 50*0) / 140
    "truncado": False, "reconhecido": True,
}
NULOS = {"pagina": "/precos", "janela_dias": 3, "trafego": {"sessoes": None, "usuarios": None},
         "rolagem_media_pct": None, "cliques_mortos": {"pct_sessoes": None},
         "cliques_raiva": {"pct_sessoes": None}, "truncado": False, "reconhecido": False}
# formato reconhecido e nenhuma linha de /precos: sessões e usuários 0; as MÉDIAS (de nada) são null
ZEROS = {"pagina": "/precos", "janela_dias": 3, "trafego": {"sessoes": 0, "usuarios": 0}, "rolagem_media_pct": None,
         "cliques_mortos": {"pct_sessoes": None}, "cliques_raiva": {"pct_sessoes": None},
         "truncado": False, "reconhecido": True}


# ── Clarity falso ──────────────────────────────────────────────────────────

class Clarity:
    def __init__(self):
        self.hits, self.modo, self.resp, self.espera, self.alvo = [], "ok", _resp(), 0.0, None
        self.gota, self.n_gotas = 0.3, 20
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer._tratar(self)

        class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.srv = Srv(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def _tratar(self, h):
        u = urllib.parse.urlsplit(h.path)
        self.hits.append({"path": u.path, "query": dict(urllib.parse.parse_qsl(u.query)), "bruto": h.path,
                          "auth": h.headers.get("Authorization"), "cabecalhos": dict(h.headers)})
        modo = self.modo
        if modo == "lento":
            time.sleep(self.espera)
            modo = "ok"
        if modo == "ok":
            return self._raw(h, 200, "application/json", json.dumps(self.resp))
        if modo == "html":
            return self._raw(h, 200, "text/html", f"<html>{VAZA} {TOKEN}</html>")
        if modo == "dict":
            return self._raw(h, 200, "application/json", json.dumps({"metricName": "Traffic", "information": []}))
        if modo == "gigante":  # 200 de 8 MB: a fonte tem de parar no teto
            return self._grande(h, 200, b"[" + b" " * (12 * 1024 * 1024) + b"]")
        if modo == "erro_gigante":  # 500 de 20 MB: o corpo de erro nem pode ser lido
            return self._grande(h, 500, b"x" * (20 * 1024 * 1024))
        if modo == "profundo":
            return self._raw(h, 200, "application/json", "[" * 200000 + "]" * 200000)
        if modo in ("bomba_gzip", "bomba_brotli"):  # poucos KB comprimidos, dezenas de MB descomprimidos
            import brotli
            import gzip
            gigante = b"[" + b" " * (64 * 1024 * 1024) + b"]"
            corpo = gzip.compress(gigante) if modo == "bomba_gzip" else brotli.compress(gigante)
            h.send_response(200)
            h.send_header("Content-Encoding", "gzip" if modo == "bomba_gzip" else "br")
            h.send_header("Content-Length", str(len(corpo)))
            h.end_headers()
            try:
                return h.wfile.write(corpo)
            except OSError:
                return
        if modo in ("chunked", "gzip_ok", "brotli_ok"):  # respostas boas, só com outra codificação
            corpo = json.dumps(self.resp).encode()
            h.send_response(200)
            if modo == "chunked":
                h.send_header("Transfer-Encoding", "chunked")
                h.end_headers()
                for i in range(0, len(corpo), 700):
                    parte = corpo[i:i + 700]
                    h.wfile.write(f"{len(parte):x}\r\n".encode() + parte + b"\r\n")
                return h.wfile.write(b"0\r\n\r\n")
            import brotli
            import gzip
            corpo = gzip.compress(corpo) if modo == "gzip_ok" else brotli.compress(corpo)
            h.send_header("Content-Encoding", "gzip" if modo == "gzip_ok" else "br")
            h.send_header("Content-Length", str(len(corpo)))
            h.end_headers()
            return h.wfile.write(corpo)
        if modo == "gotejo":  # 1 byte a cada `gota` s e Content-Length enorme: a leitura nunca "termina"
            h.send_response(200)
            h.send_header("Content-Length", "100000")
            h.end_headers()
            try:
                for _ in range(self.n_gotas):
                    h.wfile.write(b" ")
                    h.wfile.flush()
                    time.sleep(self.gota)
            except OSError:
                pass
            return
        if modo == "gzip_ruim":
            h.send_response(200)
            h.send_header("Content-Encoding", "gzip")
            h.send_header("Content-Length", "10")
            h.end_headers()
            return h.wfile.write(b"nao e gzip")
        if modo == "chunk_ruim":
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            return h.wfile.write(b"zz\r\nabc\r\n")
        if modo == "html502":
            return self._raw(h, 502, "text/html", f"<html>{VAZA} {TOKEN}</html>")
        if modo.startswith("redir"):  # "redir301": 3xx para o host B (`alvo`)
            h.send_response(int(modo[5:]))
            h.send_header("Location", self.alvo + h.path)
            h.send_header("Content-Length", "0")
            return h.end_headers()
        if modo in ("204", "202"):
            h.send_response(int(modo))
            h.send_header("Content-Length", "0")
            return h.end_headers()
        self._raw(h, int(modo), "application/json", json.dumps({"error": {"message": f"{VAZA} {TOKEN}"}}))

    def _grande(self, h, code, corpo):
        h.send_response(code)
        h.send_header("Content-Length", str(len(corpo)))
        h.end_headers()
        try:
            for i in range(0, len(corpo), 65536):
                h.wfile.write(corpo[i:i + 65536])
        except OSError:  # o cliente fechou: era o esperado
            pass

    def _raw(self, h, code, tipo, texto):
        b = texto.encode()
        h.send_response(code)
        h.send_header("Content-Type", tipo)
        h.send_header("Content-Length", str(len(b)))
        h.end_headers()
        h.wfile.write(b)


@pytest.fixture()
def api(monkeypatch):
    c = Clarity()
    monkeypatch.setattr(requests.sessions.Session, "request", _REQUEST_REAL)  # só localhost (o conftest bloqueia)
    monkeypatch.setattr(requests, "get", _get_so_local)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(clarity, "_URL", c.base + "/export-data/api/v1/project-live-insights")
    monkeypatch.setenv("CLARITY_API_TOKEN", TOKEN)
    _KW.clear()
    yield c
    c.srv.shutdown()


_CLI: dict = {}


@pytest.fixture(autouse=True)
def _um_login_por_teste():  # o login do admin tem limite de 10 por minuto: um cliente por teste
    _CLI.clear()
    yield


def _get(esperado=200):
    cli = _CLI.get("c") or _CLI.setdefault("c", _admin_client())
    r = cli.get("/admin/api/funil/fonte/clarity")
    assert r.status_code == esperado and r.headers["cache-control"] == "no-store"
    return r


def _erro_fixo(j, codigo):
    assert j["estado"] == "erro" and j["dados"] is None
    assert j["mensagem"] == funil_fontes.MENSAGENS[codigo]


def _cham():
    linha = _linha("clarity")
    return (linha or {}).get("chamadas_dia") or 0


def _venceu():  # TTL de 3 h vencido (em vez de dormir)
    _envelhece("clarity", buscado_s=10801)


def _log_formato(caplog):
    return [x.getMessage() for x in caplog.records
            if x.name == clarity.__name__ and x.levelno == logging.WARNING and "formato:" in x.getMessage()]


# ── Constantes, envs, rota ─────────────────────────────────────────────────

def test_host_fixo_e_parametros_da_cota_real_do_clarity():
    assert clarity._URL == "https://www.clarity.ms/export-data/api/v1/project-live-insights"
    f = clarity.FONTE
    assert (f.nome, f.ttl_s, f.limite_dia) == ("clarity", 10800, 8)
    assert f.janela == {"rotulo": "últimos 3 dias (UTC)", "fuso": "UTC"}
    assert f.limite_dia <= 10 - 2  # a API aceita 10 por dia: 2 de folga
    assert 86400 // f.ttl_s <= f.limite_dia  # o TTL sozinho já cabe na cota
    assert f.backoff_s >= 3600  # cada falha depois da reserva queima cota: backoff de HORAS
    assert 0 < clarity._PRAZO_S <= 8


@pytest.mark.parametrize("definidas,faltam", [
    ((), ["CLARITY_API_TOKEN"]),
    (("CLARITY_PROJECT_ID",), ["CLARITY_API_TOKEN"]),  # o ID do projeto não é necessário para a API
    (("CLARITY_API_TOKEN",), []),
    (("CLARITY_API_TOKEN", "CLARITY_PROJECT_ID"), []),
])
def test_faltam_lista_so_o_token_lido_na_hora(monkeypatch, definidas, faltam):
    for e in ("CLARITY_API_TOKEN", "CLARITY_PROJECT_ID"):
        monkeypatch.delenv(e, raising=False)
    for e in definidas:
        monkeypatch.setenv(e, TOKEN)
    assert clarity.FONTE.faltam() == faltam
    monkeypatch.setenv("CLARITY_API_TOKEN", "   ")  # só espaço = ausente
    assert clarity.FONTE.faltam() == ["CLARITY_API_TOKEN"]


def test_rota_sem_sessao_401_e_sem_token_nao_configurado_sem_tocar_na_cota(monkeypatch):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    assert TestClient(dashboard.app).get("/admin/api/funil/fonte/clarity").status_code == 401
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    j = _get().json()
    assert j["estado"] == "nao_configurado" and j["dados"] is None and j["fonte"] == "clarity"
    assert j["falta"] == ["CLARITY_API_TOKEN"] and _linha("clarity") is None


# ── Caminho feliz: UMA chamada, cabeçalho, parser, cache ───────────────────

def test_ok_uma_chamada_com_bearer_so_no_cabecalho_dados_certos_e_cache(api):
    j = _get().json()
    assert set(j) == ENVELOPE and j["estado"] == "ok" and j["mensagem"] is None
    assert j["janela"] == {"rotulo": "últimos 3 dias (UTC)", "fuso": "UTC"}
    assert j["dados"] == ESPERADO
    [h] = api.hits  # EXATAMENTE uma requisição HTTP por busca
    assert h["path"] == "/export-data/api/v1/project-live-insights"
    assert h["query"] == {"numOfDays": "3", "dimension1": "URL"}
    assert h["auth"] == "Bearer " + TOKEN and TOKEN not in h["bruto"]  # nunca na URL
    [kw] = _KW
    assert kw["allow_redirects"] is False and 0 < kw["timeout"] <= 8
    assert kw["params"] == {"numOfDays": 3, "dimension1": "URL"} and "data" not in kw and "json" not in kw
    assert kw["headers"]["Authorization"] == "Bearer " + TOKEN
    assert _cham() == 1
    # dentro do TTL (3 h): nada de chamada nova, nem cota nova
    assert _get().json()["estado"] == "ok" and len(api.hits) == 1 and _cham() == 1
    _venceu()
    assert _get().json()["estado"] == "ok" and len(api.hits) == 2 and _cham() == 2


def test_dados_sao_lista_fechada_sem_url_pessoa_nem_texto_do_clarity(api):
    j = _get().json()
    permitidas = {"pagina", "janela_dias", "trafego", "sessoes", "usuarios", "rolagem_media_pct", "cliques_mortos",
                  "cliques_raiva", "pct_sessoes", "truncado", "reconhecido"}
    assert _chaves(j["dados"]) <= permitidas, sorted(_chaves(j["dados"]) - permitidas)
    assert set(j["dados"]) == set(ESPERADO)
    assert set(j["dados"]["cliques_mortos"]) == set(j["dados"]["cliques_raiva"]) == {"pct_sessoes"}  # sem contagem: é ambígua
    assert set(j["dados"]["trafego"]) == {"sessoes", "usuarios"}
    for ch in _chaves(j):
        assert ch not in _PROIBIDAS_EXATAS and not any(p in ch for p in _PROIBIDAS_PARTE), ch
    texto = json.dumps(j)
    for fora in VALORES + ("Popular Pages", "pagesViews", "PagesPerSession", "Url"):
        assert fora not in texto, fora
    sem_segredo(texto)


# ── Parser tolerante: nomes de métrica e de URL em variantes ───────────────

def _um(metrica, nome, linhas):
    return [{"metricName": nome, "information": linhas}] if metrica else []


def _completa(**troca):
    """Resposta mínima com as 4 métricas para a página /precos (1 linha cada); `troca` muda o nome de uma."""
    nomes = {"Traffic": "Traffic", "ScrollDepth": "ScrollDepth", "DeadClickCount": "DeadClickCount",
             "RageClickCount": "RageClickCount"} | troca
    u = "https://pigbankai.com/precos"
    return [{"metricName": nomes["Traffic"], "information": [_t(u, 10, 2, 7)]},
            {"metricName": nomes["ScrollDepth"], "information": [_s(u, 8, 40.0)]},
            {"metricName": nomes["DeadClickCount"], "information": [_c(u, 8, 25.0)]},
            {"metricName": nomes["RageClickCount"], "information": [_c(u, 8, 12.5)]}]


@pytest.mark.parametrize("metrica,variante", [
    *[("ScrollDepth", v) for v in ("ScrollDepth", "Scroll Depth", "scrolldepth", "SCROLL_DEPTH", "scroll-depth", "Scroll depth")],
    *[("DeadClickCount", v) for v in ("DeadClickCount", "Dead Click Count", "deadclickcount", "dead_click_count", "Dead Clicks")],
    *[("RageClickCount", v) for v in ("RageClickCount", "Rage Click Count", "rageclickcount", "rage_click_count", "Rage Clicks")],
    *[("Traffic", v) for v in ("Traffic", "traffic", "TRAFFIC", "Traffic ")],
])
def test_nome_da_metrica_em_variantes_e_reconhecido(metrica, variante):
    d = clarity._dados(_completa(**{metrica: variante}))
    assert d["reconhecido"] is True and d["trafego"] == {"sessoes": 8, "usuarios": 7}
    assert d["rolagem_media_pct"] == 40.0 and d["cliques_mortos"] == {"pct_sessoes": 25.0}
    assert d["cliques_raiva"] == {"pct_sessoes": 12.5}


@pytest.mark.parametrize("chave", ["Url", "URL", "url", "PageUrl", "pageUrl", "page_url", "Page URL", "pagUrl", "PageURL"])
def test_chave_da_url_em_variantes(chave):
    resp = _completa()
    for m in resp:
        for r in m["information"]:
            r[chave] = r.pop("Url")
    d = clarity._dados(resp)
    assert d["reconhecido"] is True and d["trafego"]["sessoes"] == 8


def test_chave_exata_url_vence_outra_que_contem_url():
    linha = _t("https://pigbankai.com/precos", 10, 0, 7) | {"referrerUrl": "https://x.test/outra"}
    assert clarity._dados([{"metricName": "Traffic", "information": [linha]}])["trafego"]["sessoes"] == 10
    linha2 = {"totalSessionCount": "10", "referrerUrl": "https://x.test/outra"} | {"Url": "https://pigbankai.com/precos"}
    assert clarity._pagina(linha2) is True


# ── Filtro da página ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "/precos", "/precos/", "https://pigbankai.com/precos", "https://pigbankai.com/precos/",
    "https://pigbankai.com/precos?utm=x&fbclid=y#z", "https://pigbankai.com/precos#ancora", "http://localhost/precos",
    "http://localhost:8000/precos/?a=1", "pigbankai.com/precos", "//cdn.test/precos", "https://PIGBANKAI.com/precos",
    "/precos?fbclid=IwAR0", "/precos//",
])
def test_url_que_e_a_pagina_precos_conta_ignorando_host_query_e_fragmento(url):
    assert clarity._pagina({"Url": url}) is True


@pytest.mark.parametrize("url", [
    "/precos-app.js", "/continuar-compra", "/precos/x", "/PRECOS", "/Precos", "/", "", "https://pigbankai.com/",
    "https://pigbankai.com/x?next=/precos", "https://pigbankai.com/#/precos", "/precos.html", "/xprecos",
    "/precos%2Fx", "https://precos.com/", "/precos/../x", "//precos", "precos", "https://pigbankai.com/precos-app.js?v=1",
])
def test_url_que_nao_e_a_pagina_precos_nao_conta(url):
    assert clarity._pagina({"Url": url}) is False


@pytest.mark.parametrize("linha", [{}, {"Url": None}, {"Url": 5}, {"Url": ["/precos"]}, {"x": "/precos"},
                                    {"Url": "http://[::1"}])
def test_linha_sem_url_legivel_e_nenhuma_pagina(linha):
    assert clarity._pagina(linha) is None


def test_caixa_e_exata_precos_maiusculo_nao_conta_e_o_formato_segue_reconhecido():
    u = "https://pigbankai.com/PRECOS"
    d = clarity._dados([{"metricName": n, "information": [_t(u, 10, 0, 5) | _s(u, 4, 9.0) | _c(u, 4, 5.0)]}
                        for n in ("Traffic", "ScrollDepth", "DeadClickCount", "RageClickCount")])
    assert d == ZEROS


def test_soma_de_todas_as_variantes_e_media_ponderada_pelas_sessoes():
    d = clarity._dados(_resp())
    assert d == ESPERADO
    # pesos 1:99 -> a média acompanha o peso, não a média simples (50.0)
    u1, u2 = "/precos?a=1", "/precos?a=2"
    d = clarity._dados([{"metricName": "ScrollDepth", "information": [_s(u1, 1, 0.0), _s(u2, 99, 100.0)]}])
    assert d["rolagem_media_pct"] == 99.0


@pytest.mark.parametrize("total,bots,esperado", [(100, 30, 70), (100, 0, 100), (5, 9, 0), ("100", "30", 70), (100.0, 30.0, 70)])
def test_sessoes_humanas_sao_total_menos_bots(total, bots, esperado):
    r = _t("/precos", total, bots, 5)
    assert clarity._dados([{"metricName": "Traffic", "information": [r]}])["trafego"]["sessoes"] == esperado


def test_sem_campo_de_bots_vale_o_total_e_sem_usuarios_o_campo_e_null():
    r = {"totalSessionCount": "40", "Url": "/precos"}
    d = clarity._dados([{"metricName": "Traffic", "information": [r]}])
    assert d["trafego"] == {"sessoes": 40, "usuarios": None} and d["reconhecido"] is False


@pytest.mark.parametrize("campos", [{"sessionsCount": "9", "Url": "/precos"},  # sem % nem profundidade
                                     {"sessionsCount": "9", "averageScrollDepth": "x", "Url": "/precos"},
                                     {"sessionsCount": "9", "averageScrollDepth": 140, "Url": "/precos"},  # fora de 0..100
                                     {"sessionsCount": "9", "averageScrollDepth": -3, "Url": "/precos"},
                                     {"sessionsCount": "9", "averageScrollDepth": None, "Url": "/precos"}])
def test_rolagem_nao_encontrada_ou_implausivel_vira_null_nunca_zero(campos):
    d = clarity._dados(_completa()[:1] + [{"metricName": "ScrollDepth", "information": [campos]}] + _completa()[2:])
    assert d["rolagem_media_pct"] is None and d["reconhecido"] is False
    assert d["trafego"]["sessoes"] == 8  # o resto continua


def test_clique_so_com_contagem_nao_vira_percentual_e_a_contagem_nunca_e_exposta():
    so_pct = {"sessionsWithMetricPercentage": 12.5, "Url": "/precos"}
    so_n = {"sessionsCount": 7, "subTotal": 99, "Url": "/precos"}
    d = clarity._dados(_completa()[:2] + [{"metricName": "DeadClickCount", "information": [so_pct]},
                                          {"metricName": "RageClickCount", "information": [so_n]}])
    assert d["cliques_mortos"] == {"pct_sessoes": 12.5}  # sem contagem: peso 1, % reconhecido
    assert d["cliques_raiva"] == {"pct_sessoes": None} and d["reconhecido"] is False  # contagem não é %


def test_pct_de_sessoes_sem_evento_nao_e_confundido_com_o_pct_com_evento():
    r = {"sessionsCount": 10, "sessionsWithoutMetricPercentage": 88.0, "Url": "/precos"}
    assert clarity._clique(r)[0] is None
    assert clarity._clique(r | {"sessionsWithMetricPercentage": 12.0})[0] == 12.0


def test_sem_nenhuma_linha_da_pagina_mas_formato_reconhecido_sessoes_zero_e_medias_null():
    d = clarity._dados([{"metricName": n, "information": [_t("/outra", 10, 0, 5) | _s("/outra", 3, 9.0) | _c("/outra", 3, 5.0)]}
                        for n in ("Traffic", "ScrollDepth", "DeadClickCount", "RageClickCount")])
    assert d == ZEROS
    vazio = clarity._dados([{"metricName": n, "information": []} for n in ("Traffic", "ScrollDepth", "DeadClickCount", "RageClickCount")])
    assert vazio == ZEROS  # métricas presentes e sem linha nenhuma: sem visita (sessões 0), médias n/d


# ── Formato desconhecido: null, reconhecido=false e UMA linha de NOMES ─────

def test_metrica_ausente_fica_null_e_reconhecido_false(caplog):
    resp = _completa()[:2] + _completa()[3:]  # sem DeadClickCount
    with caplog.at_level(logging.DEBUG):
        d = clarity._dados(resp)
    assert d["cliques_mortos"] == {"pct_sessoes": None} and d["reconhecido"] is False
    assert d["trafego"]["sessoes"] == 8 and d["cliques_raiva"] == {"pct_sessoes": 12.5}
    [linha] = _log_formato(caplog)
    assert linha.startswith("[funil_clarity] formato: metricas=['Traffic', 'ScrollDepth', 'RageClickCount'] campos={")


@pytest.mark.parametrize("resp", [[], [{}], [1, "x", None], [{"metricName": "Traffic"}], [{"information": []}],
                                   [{"metricName": 5, "information": []}], [{"metricName": "Traffic", "information": "x"}]])
def test_lista_sem_metricas_reconhecidas_vira_nulos_sem_excecao(caplog, resp):
    with caplog.at_level(logging.DEBUG):
        d = clarity._dados(resp)
    assert d == NULOS and len(_log_formato(caplog)) == 1


def test_url_nao_encontrada_nas_linhas_nao_vira_zero(caplog):
    resp = [{"metricName": n, "information": [{k: v for k, v in r.items() if k != "Url"} | {"Pagina": "/precos"}]}
            for n, r in (("Traffic", _t("", 10, 0, 5)), ("ScrollDepth", _s("", 4, 9.0)),
                         ("DeadClickCount", _c("", 4, 5.0)), ("RageClickCount", _c("", 4, 5.0)))]
    with caplog.at_level(logging.DEBUG):
        assert clarity._dados(resp) == NULOS
    assert "'Pagina'" in _log_formato(caplog)[0]  # o nome da chave que faltou reconhecer vai ao log


def test_formato_reconhecido_nao_gera_linha_de_diagnostico(caplog):
    with caplog.at_level(logging.DEBUG):
        clarity._dados(_resp())
    assert _log_formato(caplog) == []


def test_linha_de_diagnostico_so_tem_nomes_nunca_valor_url_ou_numero(api, caplog, monkeypatch, capfd):
    def hostil(nome):
        return {"metricName": nome, "information": [{
            "sessoes_humanas": 7654321, "totalUsuarios": "1234567", "Pagina": A, "extra": SEG_VALOR,
            "aninhado": {"x": SEG_VALOR}, "lista": [SEG_VALOR]}]}
    api.resp = [hostil(n) for n in ("Traffic", "ScrollDepth", "DeadClickCount", "RageClickCount", "Popular Pages")]
    with caplog.at_level(logging.DEBUG):
        r = _get()
    j = r.json()
    assert j["estado"] == "ok" and j["dados"] == NULOS
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    [linha] = _log_formato(caplog)
    assert re.fullmatch(r"\[funil_clarity\] formato: metricas=\[[A-Za-z0-9_ ./',-]*\] campos=\{[A-Za-z0-9_ ./',:\[\]-]*\}", linha), linha
    for s in VALORES + SEGREDOS:
        assert s not in linha, s
    assert all(not x.exc_info for x in warns)
    out, err = capfd.readouterr()
    gravados = repr(_system_event_logs(monkeypatch, warns))
    cache = json.dumps(_linha("clarity"), default=str)
    for s in VALORES + SEGREDOS:
        for onde in (r.text, caplog.text, out, err, gravados, cache):
            assert s not in onde, s
    assert "sessoes_humanas" in linha and "Pagina" in linha  # os NOMES, sim


def test_nomes_no_diagnostico_sao_sanitizados_e_limitados():
    resp = [{"metricName": "Traffic\n[evil]\x00" + "x" * 80, "information": [{f"k{i}<script>": 1 for i in range(40)}]}] + \
           [{"metricName": f"M{i}", "information": [{"a": 1}]} for i in range(30)] + [{"metricName": "\n\x00", "information": []}]
    logs = []

    class H(logging.Handler):
        def emit(self, r):
            logs.append(r.getMessage())

    clarity.log.addHandler(H())
    try:
        clarity._dados(resp)
    finally:
        clarity.log.handlers.clear()
    [linha] = logs
    assert "\n" not in linha and "\x00" not in linha and "<" not in linha
    metricas, campos = linha.split("formato: metricas=")[1].split(" campos=")
    nomes, mapa = ast.literal_eval(metricas), ast.literal_eval(campos)
    assert len(nomes) == 20 and len(mapa) <= 20
    for n in nomes + list(mapa) + [k for ks in mapa.values() for k in ks]:
        assert n == clarity._FORA or re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]{0,29}", n), n
    assert all(len(ks) <= 30 for ks in mapa.values())


def test_resposta_com_1000_linhas_e_truncada_e_999_nao():
    def resp(n):
        return [{"metricName": "Traffic", "information": [_t(f"/x/{i}", 1, 0, 1) for i in range(n)]}]
    assert clarity._dados(resp(1000))["truncado"] is True
    assert clarity._dados(resp(999))["truncado"] is False
    assert clarity._dados(resp(1500))["truncado"] is True
    # em qualquer métrica, inclusive uma que o painel não usa
    assert clarity._dados([{"metricName": "Popular Pages", "information": [{"Url": "/x"}] * 1000}])["truncado"] is True


def test_resposta_de_1000_linhas_por_metrica_cabe_porque_so_o_agregado_vai_ao_cache(api):
    longa = "https://pigbankai.com/precos?fbclid=" + "A" * 200
    api.resp = [{"metricName": n, "information": [{**r, "Url": f"{longa}{i}"} for i, r in zip(range(1000), [r0] * 1000)]}
                for n, r0 in (("Traffic", _t("", 3, 1, 2)), ("ScrollDepth", _s("", 3, 10.0)),
                              ("DeadClickCount", _c("", 3, 10.0)), ("RageClickCount", _c("", 3, 10.0)))]
    assert len(json.dumps(api.resp)) > 256 * 1024  # a resposta crua estouraria o `_validar`
    j = _get().json()
    assert j["estado"] == "ok" and j["dados"]["truncado"] is True and j["dados"]["trafego"] == {"sessoes": 2000, "usuarios": 2000}
    assert len(json.dumps(j["dados"])) < 1024


def test_sessoes_que_chegam_como_numero_texto_float_ou_lixo():
    r = {"totalSessionCount": 12.0, "totalBotSessionCount": "x", "distantUserCount": True, "Url": "/precos"}
    d = clarity._dados([{"metricName": "Traffic", "information": [r]}])
    assert d["trafego"] == {"sessoes": 12, "usuarios": None}  # bots ilegível = sem bots; bool não é número


# ── Erros: códigos fechados, mensagem fixa, nenhum texto do Clarity ────────

@pytest.mark.parametrize("modo,codigo", [
    ("400", "resposta_invalida"), ("401", "auth"), ("403", "permissao"), ("404", "indisponivel"),
    ("429", "cota"), ("500", "indisponivel"), ("503", "indisponivel"), ("418", "indisponivel"),
    ("html", "resposta_invalida"), ("html502", "indisponivel"), ("dict", "resposta_invalida"),
    ("204", "indisponivel"), ("202", "indisponivel"), ("redir301", "indisponivel"),
])
def test_erro_da_api_vira_codigo_fechado_sem_vazar(api, caplog, modo, codigo):
    api.modo, api.alvo = modo, "http://127.0.0.1:1"
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), codigo)
    assert len(api.hits) == 1
    sem_segredo(r.text, caplog.text)


def test_timeout_real_vira_timeout_e_recusa_de_conexao_indisponivel(api, monkeypatch):
    monkeypatch.setattr(clarity, "_PRAZO_S", 0.3)
    api.modo, api.espera = "lento", 1.5
    _erro_fixo(_get().json(), "timeout")
    _envelhece("clarity", falha_s=10801)
    monkeypatch.setattr(clarity, "_URL", "http://127.0.0.1:1/x")  # ninguém escutando
    _erro_fixo(_get().json(), "indisponivel")


def test_resposta_lenta_dentro_do_prazo_e_ok(api):
    api.modo, api.espera = "lento", 0.2
    assert _get().json()["estado"] == "ok"


# ── Token: formato, nunca em log/cache ─────────────────────────────────────

@pytest.mark.parametrize("ruim", ["ab cd", "ab\ncd", "ab\r\nX: y", "ya29.é", "a;b", 'a"b', "a,b", "a%20b", "a" * 4097,
                                  "a<b>", "a\tb", "Bearer abc def"])
def test_token_fora_do_formato_e_recusado_sem_chamada_sem_cache_e_sem_log(api, caplog, monkeypatch, ruim):
    monkeypatch.setenv("CLARITY_API_TOKEN", ruim)
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), "resposta_invalida")
    assert api.hits == [] and _KW == []
    linha = _linha("clarity")
    assert linha["payload"] is None
    for onde in (r.text, caplog.text, json.dumps(linha, default=str)):
        assert ruim.strip() not in onde or not ruim.strip()
    assert [x.getMessage() for x in caplog.records if x.name == clarity.__name__ and x.levelno == logging.WARNING] \
        == ["[funil_clarity] token: formato recusado"]


@pytest.mark.parametrize("bom", ["a" * 4096, "eyJ.a-b_c.d~e+f/g==", TOKEN, "  " + TOKEN + "\n"])
def test_token_no_formato_de_jwt_passa_e_espacos_nas_pontas_sao_aparados(api, monkeypatch, bom):
    monkeypatch.setenv("CLARITY_API_TOKEN", bom)
    assert _get().json()["estado"] == "ok"
    assert api.hits[0]["auth"] == "Bearer " + bom.strip()


# ── Redirecionamento: ponto único de saída ─────────────────────────────────

@pytest.mark.parametrize("codigo", [301, 302, 303, 307, 308])
def test_redirecionamento_nao_e_seguido_e_o_bearer_nao_vaza_para_o_outro_host(api, codigo):
    mau = Clarity()
    try:
        api.alvo, api.modo = mau.base, f"redir{codigo}"
        _erro_fixo(_get().json(), "indisponivel")
        assert mau.hits == [] and len(api.hits) == 1  # nem o Bearer chegou ao host B
        assert _KW and all(kw["allow_redirects"] is False for kw in _KW)
    finally:
        mau.srv.shutdown()


# ── COTA: o teste mais importante (infra real, limite_dia=8) ───────────────

def test_a_nona_busca_no_dia_nao_chama_a_api_e_o_dia_seguinte_volta(api, monkeypatch):
    for i in range(8):
        assert _get().json()["estado"] == "ok" and len(api.hits) == i + 1
        _venceu()
    assert _cham() == 8
    s = _get().json()  # a 9ª: TTL vencido, mas a cota do dia acabou
    assert len(api.hits) == 8
    assert s["estado"] == "stale" and s["dados"] == ESPERADO and s["mensagem"] == funil_fontes.MENSAGENS["cota"]
    assert _get().json()["mensagem"] == funil_fontes.MENSAGENS["cota"] and len(api.hits) == 8
    monkeypatch.setattr(funil_fontes_cache, "_HOJE_TESTE", HOJE + timedelta(days=2))  # virou o dia UTC
    j = _get().json()
    assert j["estado"] == "ok" and j["mensagem"] is None and len(api.hits) == 9 and _cham() == 1


def test_cota_esgotada_sem_cache_anterior_e_erro_cota_sem_chamar(api):
    for _ in range(8):
        assert _run(funil_fontes_cache.reservar("clarity", 8)) is True
    _erro_fixo(_get().json(), "cota")
    assert api.hits == []


def test_um_429_esgota_o_dia_inteiro_e_a_proxima_busca_nem_chama(api, monkeypatch):
    assert _get().json()["estado"] == "ok" and _cham() == 1
    _venceu()
    api.modo = "429"
    s = _get().json()
    assert s["estado"] == "stale" and s["dados"] == ESPERADO and s["mensagem"] == funil_fontes.MENSAGENS["cota"]
    assert len(api.hits) == 2 and _cham() == 8 and _linha("clarity")["dia_utc"] == HOJE  # gastou o resto do dia
    api.modo = "ok"
    _envelhece("clarity", falha_s=10801)  # até o backoff de 3 h vencido: quem segura é a cota
    s = _get().json()
    assert s["estado"] == "stale" and s["mensagem"] == funil_fontes.MENSAGENS["cota"] and len(api.hits) == 2
    monkeypatch.setattr(funil_fontes_cache, "_HOJE_TESTE", HOJE + timedelta(days=2))
    assert _get().json()["estado"] == "ok" and len(api.hits) == 3


def test_429_sem_cache_anterior_e_erro_cota_e_tambem_esgota(api):
    api.modo = "429"
    _erro_fixo(_get().json(), "cota")
    assert _cham() == 8 and len(api.hits) == 1


def test_esgotar_nunca_diminui_o_contador_e_reinicia_em_dia_novo():
    hoje, amanha = HOJE + timedelta(days=3), HOJE + timedelta(days=4)
    _run(funil_fontes_cache.esgotar("clarity", 8, hoje))
    assert (_cham(), _linha("clarity")["dia_utc"]) == (8, hoje)
    assert _run(funil_fontes_cache.reservar("clarity", 8, hoje)) is False
    _run(funil_fontes_cache.esgotar("clarity", 5, hoje))  # nunca baixa
    assert _cham() == 8
    assert _run(funil_fontes_cache.reservar("clarity", 8, amanha)) is True and _cham() == 1  # dia novo
    _run(funil_fontes_cache.esgotar("clarity", 8, hoje))  # requisição atrasada de ontem não derruba o contador de hoje
    assert (_cham(), _linha("clarity")["dia_utc"]) == (1, amanha)


def test_falha_timeout_e_token_ruim_queimam_cota_sem_devolver(api, monkeypatch):
    assert _get().json()["estado"] == "ok" and _cham() == 1
    _venceu()
    api.modo = "500"
    assert _get().json()["estado"] == "stale" and _cham() == 2  # falha DEPOIS da reserva: queimou
    _envelhece("clarity", falha_s=10801)
    monkeypatch.setattr(clarity, "_PRAZO_S", 0.3)
    api.modo, api.espera = "lento", 1.5
    assert _get().json()["mensagem"] == funil_fontes.MENSAGENS["timeout"] and _cham() == 3
    _envelhece("clarity", falha_s=10801)
    monkeypatch.setenv("CLARITY_API_TOKEN", "token ruim")
    assert _get().json()["mensagem"] == funil_fontes.MENSAGENS["resposta_invalida"] and _cham() == 4


def test_ocupada_nao_queima_cota_nem_chama(api):
    assert _get().json()["estado"] == "ok"
    _venceu()
    funil_fontes._EM_VOO["clarity"] = {"t": time.monotonic()}
    try:
        o = _get().json()
    finally:
        funil_fontes._EM_VOO.pop("clarity", None)
    assert o["estado"] == "stale" and o["mensagem"] == funil_fontes.MENSAGENS["ocupada"]
    assert _cham() == 1 and len(api.hits) == 1


def test_sem_cache_anterior_e_ocupada_vira_erro_sem_cota(api):
    funil_fontes._EM_VOO["clarity"] = {"t": time.monotonic()}
    try:
        o = _get().json()
    finally:
        funil_fontes._EM_VOO.pop("clarity", None)
    _erro_fixo(o, "ocupada")
    assert api.hits == [] and _linha("clarity") is None


def test_backoff_de_horas_depois_de_falha_nao_martela_a_api(api):
    api.modo = "500"
    _erro_fixo(_get().json(), "indisponivel")
    for _ in range(3):
        _erro_fixo(_get().json(), "indisponivel")
    assert len(api.hits) == 1 and _cham() == 1  # com 60 s de backoff a cota acabaria em minutos
    _envelhece("clarity", falha_s=10799)  # ainda dentro das 3 h
    _erro_fixo(_get().json(), "indisponivel")
    assert len(api.hits) == 1
    _envelhece("clarity", falha_s=2)
    _erro_fixo(_get().json(), "indisponivel")
    assert len(api.hits) == 2 and _cham() == 2


def test_stale_vem_da_infra_com_dado_anterior(api):
    assert _get().json()["estado"] == "ok"
    _venceu()
    api.modo = "503"
    s = _get().json()
    assert s["estado"] == "stale" and s["dados"] == ESPERADO and s["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]
    assert s["buscado_em"]


# ── SEGREDO: nenhuma saída leva o token ────────────────────────────────────

def _excecoes():
    return [requests.exceptions.ConnectionError(TOKEN), requests.exceptions.SSLError("Bearer " + TOKEN),
            requests.exceptions.ReadTimeout(TOKEN), requests.exceptions.ConnectTimeout(TOKEN),
            requests.exceptions.InvalidHeader(TOKEN), requests.exceptions.TooManyRedirects(TOKEN),
            RuntimeError(TOKEN), ValueError("Authorization: Bearer " + TOKEN), KeyError(TOKEN), OSError(TOKEN),
            UnicodeError(TOKEN)]


def _saidas(r, caplog, monkeypatch, capfd):
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert all(not x.exc_info for x in warns)
    out, err = capfd.readouterr()
    sem_segredo(r.text, caplog.text, out, err, repr(_system_event_logs(monkeypatch, warns)))
    linha = _linha("clarity")
    sem_segredo(json.dumps(linha, default=str) if linha else "")
    mem = repr({k: v for k, v in vars(clarity).items() if k != "__doc__"})  # o docstring cita "Authorization"
    assert TOKEN not in mem and TOKEN not in repr(clarity.FONTE) and TOKEN not in repr(vars(clarity._buscar))


@pytest.mark.parametrize("erro", _excecoes(), ids=lambda e: type(e).__name__)
def test_excecao_que_carrega_o_token_nao_vaza_em_nenhuma_saida(api, caplog, monkeypatch, capfd, erro):
    def quebra(*a, **kw):
        raise erro

    monkeypatch.setattr(requests, "get", quebra)
    with caplog.at_level(logging.DEBUG):
        r = _get()
    assert r.json()["estado"] == "erro" and r.json()["dados"] is None
    assert r.json()["mensagem"] in funil_fontes.MENSAGENS.values()
    _saidas(r, caplog, monkeypatch, capfd)


@pytest.mark.parametrize("modo", ["ok", "401", "403", "429", "500", "html", "html502", "dict"])
def test_respostas_com_o_token_no_corpo_nao_vazam(api, caplog, monkeypatch, capfd, modo):
    api.modo = modo
    with caplog.at_level(logging.DEBUG):  # inclui urllib3/requests em DEBUG
        r = _get()
    if modo == "ok":
        assert any(x.name.startswith("urllib3") for x in caplog.records)  # o DEBUG de verdade foi capturado
    _saidas(r, caplog, monkeypatch, capfd)


@pytest.mark.parametrize("cenario", ["token_ruim", "http_500", "http_429", "html", "dict", "rede", "timeout"])
def test_erro_da_fonte_nao_carrega_cadeia_de_excecoes(api, monkeypatch, cenario):
    if cenario == "token_ruim":
        monkeypatch.setenv("CLARITY_API_TOKEN", "a b")
    elif cenario in ("http_500", "http_429"):
        api.modo = cenario[5:]
    elif cenario in ("html", "dict"):
        api.modo = cenario
    elif cenario == "rede":
        monkeypatch.setattr(clarity, "_URL", "http://127.0.0.1:1/x")
    else:
        monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout(TOKEN)))
    with pytest.raises(funil_fontes.FonteErro) as e:
        clarity._buscar()
    x = e.value
    assert x.__cause__ is None and (x.__suppress_context__ is True or x.__context__ is None)
    assert TOKEN not in repr(x) and TOKEN not in str(x)


def test_token_nao_fica_em_atributo_de_modulo_nem_em_cache_depois_de_uma_busca(api):
    assert _get().json()["estado"] == "ok"
    for k, v in vars(clarity).items():
        if k != "__doc__":
            assert TOKEN not in repr(v), k
    sem_segredo(json.dumps(_linha("clarity"), default=str))


# ── Integração pela rota ───────────────────────────────────────────────────

def test_modulo_registrado_e_a_rota_e_a_do_clarity(api):
    assert funil_fontes.MODULOS["clarity"] == "core.funil_fonte_clarity"
    assert funil_fontes.fonte("clarity") is clarity.FONTE
    j = _get().json()
    assert j["fonte"] == "clarity" and j["estado"] == "ok"


def test_faltam_que_levanta_com_o_token_vira_erro_generico_sem_vazar(monkeypatch, caplog, capfd):
    from dataclasses import replace
    quebrada = replace(clarity.FONTE, faltam=lambda: (_ for _ in ()).throw(RuntimeError(TOKEN)))
    monkeypatch.setattr(funil_fontes, "fonte", lambda n: quebrada)
    with caplog.at_level(logging.DEBUG):
        r = _get()
    out, err = capfd.readouterr()
    j = r.json()
    assert j["estado"] == "erro" and j["mensagem"] == funil_fontes.MENSAGENS["indisponivel"] and set(j) == ENVELOPE
    for texto in (r.text, caplog.text, out, err):
        assert TOKEN not in texto


# ══ 1ª passada do Tester ═══════════════════════════════════════════════════

# A1: nome de campo no log = ALLOWLIST (o Clarity pode mandar DADO como nome de campo)
DADO_COMO_NOME = ["https://x/precos?token=SEGREDO123", "ana@exemplo.com", "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJGQUxTTyJ9.abc", TOKEN,
                  "4111111111111111", "5511999998888", "12345", "a" * 41, "abçd", "ab123", "a.b", "a/b", "a:b", "a?b", "a=b", "a&b",
                  "a@b", "a#b", "a-b", " abc", "1abc", "abc\n", "", "é", "SEGREDO123"]
LEGITIMOS = ["Scroll Depth", "sessionsCount", "Url", "ScrollDepth", "totalBotSessionCount", "PagesPerSessionPercentage",
             "dimension1", "a" * 40, "A1", "Traffic", "page_url", "sessionsWithoutMetricPercentage", "ab12", "ab 1 2"]


@pytest.mark.parametrize("nome", DADO_COMO_NOME)
def test_nome_fora_do_padrao_vira_constante_sem_nenhum_caractere_do_original(nome):
    assert clarity._nome(nome) == "(nome fora do padrão)"


@pytest.mark.parametrize("nome", LEGITIMOS)
def test_nome_legitimo_passa_inteiro(nome):
    assert clarity._nome(nome) == nome


@pytest.mark.parametrize("nao_texto", [None, 5, ["a"], {"a": 1}, b"abc"])
def test_nome_que_nao_e_texto_vira_constante(nao_texto):
    assert clarity._nome(nao_texto) == "(nome fora do padrão)"


def test_dado_como_nome_de_campo_nao_vaza_nem_no_system_event_logs(caplog, monkeypatch, capfd):
    linha = {n: 1 for n in DADO_COMO_NOME} | {n: 2 for n in LEGITIMOS}
    resp = [{"metricName": n, "information": [linha]} for n in DADO_COMO_NOME[:6] + ["Traffic", "Scroll Depth"]]
    with caplog.at_level(logging.DEBUG):
        clarity._dados(resp)
    [msg] = _log_formato(caplog)
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    gravados = repr(_system_event_logs(monkeypatch, warns))
    out, err = capfd.readouterr()
    metricas, campos = msg.split("formato: metricas=")[1].split(" campos=")
    nomes = ast.literal_eval(metricas) + [k for ks in ast.literal_eval(campos).values() for k in ks]
    assert all(n == clarity._FORA or re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]{0,29}", n) for n in nomes)  # nenhum ":?=&@#./-"
    for pedaco in ("SEGREDO", "token", "ana", "exemplo", "eyJ", "4111", "5511", "x/precos", "@", "?", "&", "#", "https", "//"):
        for onde in (msg, caplog.text, gravados, out, err):
            assert pedaco not in onde, pedaco
    for ok in ("Scroll Depth", "sessionsCount", "Url", "Traffic", "(nome fora do padrão)"):
        assert ok in msg, ok
    assert len(_log_formato(caplog)) == 1


# A3: nunca soma parcial
def _trafego(*linhas):
    return clarity._dados([{"metricName": "Traffic", "information": list(linhas)}])["trafego"]


def test_variante_com_sessoes_ilegiveis_zera_o_campo_em_vez_de_somar_so_a_outra():
    assert _trafego(_t("/precos?a=1", 100, 0, 5), _t("/precos?a=2", "n/a", 0, 7)) == {"sessoes": None, "usuarios": 12}
    assert _trafego(_t("/precos?a=1", 100, 0, 5), _t("/precos?a=2", 40, 0, "x")) == {"sessoes": 140, "usuarios": None}
    assert _trafego(_t("/precos?a=1", 100, 0, 5), _t("/precos?a=2", 40, 0, 7)) == {"sessoes": 140, "usuarios": 12}
    # linha ilegível de OUTRA página não contamina /precos
    assert _trafego(_t("/precos", 100, 0, 5), _t("/outra", "n/a", 0, "x")) == {"sessoes": 100, "usuarios": 5}


def test_variante_ilegivel_zera_tambem_a_media_e_o_pct_mas_so_do_campo_afetado():
    d = clarity._dados([{"metricName": "ScrollDepth", "information": [_s("/precos?a=1", 10, 50.0), _s("/precos?a=2", 10, "n/a")]},
                        {"metricName": "DeadClickCount", "information": [_c("/precos?a=1", 10, 10.0), _c("/precos?a=2", 10, 20.0)]}])
    assert d["rolagem_media_pct"] is None and d["cliques_mortos"] == {"pct_sessoes": 15.0}


def test_peso_so_em_algumas_linhas_deixa_a_media_ambigua_e_sem_peso_em_nenhuma_e_media_simples():
    a, b = {"averageScrollDepth": 20.0, "Url": "/precos?a=1"}, {"averageScrollDepth": 60.0, "Url": "/precos?a=2"}
    assert clarity._media([(20.0, None), (60.0, None)]) == 40.0
    assert clarity._media([(20.0, 3.0), (60.0, None)]) is None
    d = clarity._dados([{"metricName": "ScrollDepth", "information": [a, b]}])
    assert d["rolagem_media_pct"] == 40.0
    d = clarity._dados([{"metricName": "ScrollDepth", "information": [a, b | {"sessionsCount": 5}]}])
    assert d["rolagem_media_pct"] is None


def test_soma_e_media_unitarias_null_nao_vira_zero():
    assert clarity._soma([(1, None), (None, None)]) is None and clarity._soma([]) == 0
    assert clarity._soma([(1.4, None), (2.4, None)]) == 4
    assert clarity._media([(None, 1.0)]) is None and clarity._media([]) is None


# C25/C26: pesos zero
def test_peso_zero_conta_zero_e_pesos_todos_zero_viram_media_simples_sem_dividir_por_zero():
    assert clarity._media([(10.0, 0.0), (50.0, 10.0)]) == 50.0  # peso 0 não vira 1
    assert clarity._media([(40.0, 0.0), (60.0, 0.0)]) == 50.0  # tot == 0: média simples


# C66: ramos da rolagem
@pytest.mark.parametrize("campos,esperado", [
    ({"averageScrollDepth": 30.0}, 30.0), ({"scrollPercentage": 31.0}, 31.0), ({"averageDepth": 32.0}, 32.0),
    ({"scrollPercentage": 40.0, "averageScrollDepth": 41.0}, 41.0),  # scroll+depth tem prioridade
    ({"depthPercentage": 33.0}, 33.0), ({"sessionsWithMetricPercentage": 34.0}, None),  # % solto não é profundidade
])
def test_ramos_de_reconhecimento_da_rolagem(campos, esperado):
    assert clarity._roll({"Url": "/precos"} | campos)[0] == esperado


# C20: a query não decide, o path sim
@pytest.mark.parametrize("url,conta", [("/precos?x", True), ("/precos?", True), ("/precosx", False), ("/precos2?x", False)])
def test_so_o_path_decide_com_ou_sem_query(url, conta):
    assert clarity._pagina({"Url": url}) is conta


# A4: sem visita × não sabemos
def test_sem_linha_de_precos_sessoes_zero_e_medias_null_e_com_truncado_sessoes_null():
    outra = [_t(f"/x/{i}", 1, 0, 1) for i in range(1000)]
    d = clarity._dados([{"metricName": "Traffic", "information": outra[:5]}])
    assert d["trafego"] == {"sessoes": 0, "usuarios": 0} and d["truncado"] is False
    d = clarity._dados([{"metricName": "Traffic", "information": outra}])  # 1.000 linhas: a de /precos pode estar no corte
    assert d["trafego"] == {"sessoes": None, "usuarios": None} and d["truncado"] is True
    d = clarity._dados([{"metricName": "Traffic", "information": outra[:-1] + [_t("/precos", 7, 0, 3)]}])
    assert d["trafego"] == {"sessoes": 7, "usuarios": 3}  # truncado, mas a linha de /precos veio


def test_truncado_por_outra_metrica_tambem_nao_afirma_sem_visita():
    d = clarity._dados([{"metricName": "Traffic", "information": [_t("/x", 1, 0, 1)]},
                        {"metricName": "Popular Pages", "information": [{"Url": "/y"}] * 1000}])
    assert d["trafego"] == {"sessoes": None, "usuarios": None}


# A6: nunca soma métricas diferentes
def _res(*pares):
    return [{"metricName": n, "information": [_t("/precos", v, 0, 1)]} for n, v in pares]


def test_traffic_exato_tem_prioridade_e_trafficsources_nao_dobra():
    d = clarity._dados(_res(("Traffic", 10), ("TrafficSources", 100)))
    assert d["trafego"]["sessoes"] == 10
    d = clarity._dados(_res(("TrafficSources", 100), ("Traffic", 10)))
    assert d["trafego"]["sessoes"] == 10


def test_sem_exata_vale_a_unica_com_o_fragmento():
    assert clarity._dados(_res(("Traffic Overview", 10)))["trafego"]["sessoes"] == 10


def test_duas_candidatas_por_fragmento_sem_exata_nao_reconhece_nem_soma(caplog):
    with caplog.at_level(logging.DEBUG):
        d = clarity._dados(_res(("TrafficA", 10), ("TrafficB", 100)))
    assert d["trafego"] == {"sessoes": None, "usuarios": None} and d["reconhecido"] is False
    assert len(_log_formato(caplog)) == 1


def test_metrica_exata_repetida_nao_soma_nem_sobrescreve(caplog):
    with caplog.at_level(logging.DEBUG):
        d = clarity._dados(_res(("Traffic", 10), ("Traffic", 100)))
    assert d["trafego"] == {"sessoes": None, "usuarios": None} and d["reconhecido"] is False
    assert len(_log_formato(caplog)) == 1


@pytest.mark.parametrize("a,b", [("ScrollDepth", "ScrollDepthByDevice"), ("DeadClickCount", "DeadClickCountByDevice"),
                                  ("RageClickCount", "RageClickCountByDevice")])
def test_as_outras_metricas_tambem_preferem_a_exata(a, b):
    resp = _completa()
    resp.append({"metricName": b, "information": [_s("/precos", 8, 99.0) | _c("/precos", 8, 99.0)]})
    d = clarity._dados(resp)
    assert d["reconhecido"] is True and d["rolagem_media_pct"] == 40.0 and d["cliques_mortos"] == {"pct_sessoes": 25.0}


# B4: gravar_falha e esgotar são independentes
def _quebra(*a, **k):
    raise OSError("sem banco " + TOKEN)


def _async_quebra(*a, **k):
    async def f():
        raise OSError("sem banco " + TOKEN)
    return f()


def test_gravar_falha_quebrada_nao_impede_esgotar_e_o_log_diz_so_o_tipo(api, caplog, monkeypatch):
    api.modo = "429"
    monkeypatch.setattr(funil_fontes_cache, "gravar_falha", _async_quebra)
    with caplog.at_level(logging.DEBUG):
        _erro_fixo(_get().json(), "cota")
    assert _cham() == 8  # o dia foi esgotado mesmo sem gravar a falha
    msgs = [x.getMessage() for x in caplog.records if x.name == funil_fontes.__name__]
    assert "[funil_fontes] clarity: gravar falha falhou (OSError)" in msgs
    assert not any("esgotar" in m for m in msgs)
    sem_segredo(caplog.text)


def test_esgotar_quebrado_nao_impede_a_falha_e_o_log_e_o_dele(api, caplog, monkeypatch):
    api.modo = "429"
    monkeypatch.setattr(funil_fontes_cache, "esgotar", _async_quebra)
    with caplog.at_level(logging.DEBUG):
        _erro_fixo(_get().json(), "cota")
    assert _linha("clarity")["falha_em"] is not None
    msgs = [x.getMessage() for x in caplog.records if x.name == funil_fontes.__name__]
    assert "[funil_fontes] clarity: esgotar cota falhou (OSError)" in msgs
    assert not any("gravar falha" in m for m in msgs)
    sem_segredo(caplog.text)


# I04: contrato de esgotar sobre linha de dia mais VELHO
def test_esgotar_sobre_dia_mais_velho_atualiza_o_dia_e_poe_o_contador_no_limite():
    ontem, hoje = HOJE + timedelta(days=5), HOJE + timedelta(days=6)
    assert _run(funil_fontes_cache.reservar("clarity", 8, ontem)) is True and _cham() == 1
    _run(funil_fontes_cache.esgotar("clarity", 8, hoje))
    assert (_cham(), _linha("clarity")["dia_utc"]) == (8, hoje)


# C2: corpo em stream, com teto; corpo de erro não é lido
@pytest.fixture()
def espia(monkeypatch):
    """Vigia o corpo: `read1` do urllib3 é o ÚNICO jeito permitido de ler (e só em 200); qualquer outro
    caminho (`read`, `stream`, `iter_content`, `content`, `text`, `json`) falha o teste."""
    import urllib3.response as ur
    lido = {"n": 0, "chamadas": 0}
    orig = ur.HTTPResponse.read1

    def read1(self, *a, **kw):
        lido["chamadas"] += 1
        d = orig(self, *a, **kw)
        lido["n"] += len(d or b"")
        return d

    def proibido(self, *a, **kw):
        raise AssertionError("corpo lido por outro caminho")

    monkeypatch.setattr(ur.HTTPResponse, "read1", read1)
    for nome in ("read", "stream", "read_chunked", "data"):
        monkeypatch.setattr(ur.HTTPResponse, nome, proibido if nome != "data" else property(proibido))
    for nome in ("iter_content", "iter_lines", "json"):
        monkeypatch.setattr(requests.models.Response, nome, proibido)
    for nome in ("content", "text"):
        monkeypatch.setattr(requests.models.Response, nome, property(proibido))
    return lido


def test_pede_em_stream_e_le_o_ok_so_por_read1(api, espia):
    assert _get().json()["estado"] == "ok"
    assert _KW[0]["stream"] is True and espia["chamadas"] >= 2 and espia["n"] > 0  # termina no EOF (read1 vazio)


@pytest.mark.parametrize("modo,codigo", [("500", "indisponivel"), ("401", "auth"), ("429", "cota"), ("erro_gigante", "indisponivel")])
def test_corpo_de_erro_nem_e_lido_so_o_status(api, espia, modo, codigo):
    api.modo = modo
    _erro_fixo(_get().json(), codigo)
    assert espia["chamadas"] == 0 and espia["n"] == 0


def test_corpo_200_acima_do_teto_e_recusado_lendo_no_maximo_o_teto_mais_um_pedaco(api, espia):
    api.modo = "gigante"
    _erro_fixo(_get().json(), "resposta_invalida")
    assert clarity._MAX_BYTES < espia["n"] <= clarity._MAX_BYTES + 65536  # parou ao estourar: não baixou os 12 MB
    assert len(api.hits) == 1


def test_corpo_exatamente_no_teto_passa_e_um_byte_a_mais_e_recusado(api, monkeypatch):
    tam = len(json.dumps(api.resp))
    monkeypatch.setattr(clarity, "_MAX_BYTES", tam)
    assert _get().json()["estado"] == "ok"
    monkeypatch.setattr(clarity, "_MAX_BYTES", tam - 1)
    _venceu()
    s = _get().json()
    assert s["estado"] == "stale" and s["mensagem"] == funil_fontes.MENSAGENS["resposta_invalida"]


def test_json_profundo_demais_e_resposta_invalida_e_nao_excecao(api):
    api.modo = "profundo"
    _erro_fixo(_get().json(), "resposta_invalida")


# C46: exceções REAIS do requests carregam `.request.headers['Authorization']`
def _cert(tmp_path):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    agora = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(days=1))
         .not_valid_after(agora + datetime.timedelta(days=1))
         .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
         .sign(k, hashes.SHA256()))
    crt, key = tmp_path / "c.pem", tmp_path / "k.pem"
    crt.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(crt), str(key)


def _tls(tmp_path):
    import ssl

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")

    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

        def handle_error(self, *a):  # o cliente desiste do handshake: ruído esperado
            pass

    srv = Srv(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*_cert(tmp_path))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return srv


def test_as_excecoes_reais_do_requests_carregam_o_token_no_pedido(api, tmp_path):
    """Pré-condição do teste abaixo: sem isso um log com `exc.request.headers` não vazaria nada."""
    cab = {"Authorization": "Bearer " + TOKEN}
    with pytest.raises(requests.exceptions.ConnectionError) as e:
        _GET_REAL("http://127.0.0.1:1/x", headers=cab, timeout=1)
    assert TOKEN in str(e.value.request.headers)
    tls = _tls(tmp_path)
    try:
        with pytest.raises(requests.exceptions.SSLError) as e:
            _GET_REAL(f"https://127.0.0.1:{tls.server_port}/x", headers=cab, timeout=2)
        assert TOKEN in str(e.value.request.headers)
    finally:
        tls.shutdown()


@pytest.mark.parametrize("cenario", ["conexao_recusada", "tls_autoassinado", "gzip_ruim", "chunk_ruim", "read_timeout"])
def test_excecao_real_do_requests_com_o_token_nos_cabecalhos_nao_vaza(api, caplog, monkeypatch, capfd, tmp_path, cenario):
    tls = None
    try:
        if cenario == "conexao_recusada":
            monkeypatch.setattr(clarity, "_URL", "http://127.0.0.1:1/x")
        elif cenario == "tls_autoassinado":
            tls = _tls(tmp_path)
            monkeypatch.setattr(clarity, "_URL", f"https://127.0.0.1:{tls.server_port}/x")
        elif cenario == "read_timeout":
            monkeypatch.setattr(clarity, "_PRAZO_S", 0.3)
            api.modo, api.espera = "lento", 1.5
        else:
            api.modo = cenario
        with caplog.at_level(logging.DEBUG):
            r = _get()
        j = r.json()
        assert j["estado"] == "erro" and j["mensagem"] in funil_fontes.MENSAGENS.values()
        _saidas(r, caplog, monkeypatch, capfd)
    finally:
        if tls:
            tls.shutdown()


# ══ 2ª passada do Tester ═══════════════════════════════════════════════════

# 1) inteiro gigante: `null` só do campo, não o fetch inteiro
@pytest.mark.parametrize("v", [10 ** 400, -(10 ** 400), "9" * 400, "1e999", float("inf")])
def test_numero_gigante_vira_null_do_campo_sem_excecao(v):
    assert clarity._num(v) is None


def test_inteiro_de_400_digitos_num_campo_nao_derruba_o_fetch(api):
    api.resp = _completa()
    api.resp[0]["information"][0]["distantUserCount"] = 10 ** 400
    j = _get().json()
    assert j["estado"] == "ok" and j["dados"]["trafego"] == {"sessoes": 8, "usuarios": None}
    assert j["dados"]["reconhecido"] is False and _cham() == 1


# 2) truncamento em QUALQUER métrica (1ª, 2ª, 3ª, última, uma que o painel não usa)
@pytest.mark.parametrize("onde", [0, 1, 2, 3, 4])
def test_truncamento_em_qualquer_metrica_marca_truncado(onde):
    resp = _completa() + [{"metricName": "Popular Pages", "information": [{"Url": "/x"}]}]
    resp[onde]["information"] = resp[onde]["information"] * 1000  # 1.000 linhas só nesta métrica
    assert clarity._dados(resp)["truncado"] is True
    resp[onde]["information"] = resp[onde]["information"][:999]
    assert clarity._dados(resp)["truncado"] is False


# 3) E05/E07-09: rolagem exige scroll+depth; aliases coexistentes = duplicidade (NÃO reconhecido)
def test_scroll_sozinho_e_excessive_scroll_nao_sao_rolagem():
    base = _completa()[:1] + _completa()[2:]
    linha = [_s("/precos", 8, 77.0)]
    d = clarity._dados(base + [{"metricName": "Scroll", "information": linha},
                               {"metricName": "ExcessiveScroll", "information": linha},
                               {"metricName": "ScrollDepth", "information": [_s("/precos", 8, 40.0)]}])
    assert d["rolagem_media_pct"] == 40.0 and d["reconhecido"] is True
    for so in ("Scroll", "ExcessiveScroll"):
        d = clarity._dados(base + [{"metricName": so, "information": linha}])
        assert d["rolagem_media_pct"] is None and d["reconhecido"] is False


@pytest.mark.parametrize("alias,canon,campo", [("DeadClicks", "DeadClickCount", "cliques_mortos"),
                                                 ("RageClicks", "RageClickCount", "cliques_raiva")])
def test_alias_sozinho_vale_e_alias_com_o_nome_canonico_e_duplicidade(caplog, alias, canon, campo):
    u = "/precos"
    com = lambda nome, pct: {"metricName": nome, "information": [_c(u, 8, pct)]}
    base = [m for m in _completa() if m["metricName"] != canon]
    d = clarity._dados(base + [com(alias, 30.0)])
    assert d[campo] == {"pct_sessoes": 30.0} and d["reconhecido"] is True
    d = clarity._dados(base + [com(canon, 20.0)])
    assert d[campo] == {"pct_sessoes": 20.0} and d["reconhecido"] is True
    with caplog.at_level(logging.DEBUG):  # os dois ao mesmo tempo: não adivinha qual vale
        d = clarity._dados(base + [com(alias, 30.0), com(canon, 20.0)])
        d2 = clarity._dados(base + [com(canon, 20.0), com(alias, 30.0)])
    assert d[campo] == d2[campo] == {"pct_sessoes": None} and d["reconhecido"] is d2["reconhecido"] is False
    assert len(_log_formato(caplog)) == 2


# 4) PRAZO TOTAL de leitura: goteio não prende a thread do executor compartilhado
def test_prazo_total_e_10s_e_menor_que_o_da_infra():
    assert clarity._PRAZO_TOTAL_S == 10 < funil_fontes.TIMEOUT_S


def test_goteio_termina_no_prazo_total_com_timeout_sem_prender_a_thread_e_outra_fonte_responde(api, monkeypatch):
    monkeypatch.setattr(clarity, "_PRAZO_TOTAL_S", 1.5)
    api.modo, api.gota, api.n_gotas = "gotejo", 0.3, 20  # sem prazo, o corpo só acabaria em ~6 s e como `indisponivel`
    out = []
    t0 = time.monotonic()
    th = threading.Thread(target=lambda: out.append(_get().json()))
    th.start()
    time.sleep(0.6)
    f, _ = _fake(monkeypatch)  # enquanto o Clarity goteja, outra fonte do mesmo executor responde
    assert _run(funil_fontes.obter(f.nome))["estado"] == "ok"
    th.join(10)
    dt = time.monotonic() - t0
    _erro_fixo(out[0], "timeout")
    assert 1.4 <= dt < 4, dt
    # as 4 threads do EXECUTOR voltaram: 4 trabalhos simultâneos só passam se nenhuma ficou presa
    barreira = threading.Barrier(4)
    for fut in [funil_fontes.EXECUTOR.submit(barreira.wait, 5) for _ in range(4)]:
        fut.result(timeout=6)
    assert "clarity" not in funil_fontes._EM_VOO


@pytest.mark.parametrize("modo", ["ok", "chunked", "gzip_ok", "brotli_ok", "gotejo_curto"])
def test_leitura_por_read1_termina_em_respostas_normais(api, modo):
    if modo == "ok":
        pass
    elif modo == "gotejo_curto":  # goteja, mas dentro do prazo e termina de verdade
        api.resp = _completa()
        api.modo = "lento"
        api.espera = 0.2
    else:
        api.modo = modo
    j = _get().json()
    assert j["estado"] == "ok" and j["dados"] == (ESPERADO if modo != "gotejo_curto" else j["dados"])


# S08/S09/S10: a resposta é SEMPRE fechada
@pytest.mark.parametrize("modo", ["ok", "401", "403", "429", "500", "gigante", "redir301", "redir302", "202", "204",
                                  "gzip_ruim", "chunk_ruim", "html", "dict"])
def test_a_resposta_e_sempre_fechada(api, monkeypatch, modo):
    fechadas, orig = [], requests.models.Response.close
    monkeypatch.setattr(requests.models.Response, "close", lambda self: (fechadas.append(self), orig(self))[1])
    api.modo, api.alvo = modo, "http://127.0.0.1:1"
    _get()
    # o requests fecha sozinho o corpo de um 3xx (lê e fecha): ali só dá para exigir ao menos 1
    assert len(api.hits) == 1 and (len(fechadas) >= 1 if modo.startswith("redir") else len(fechadas) == 1)


def test_resposta_fechada_tambem_quando_a_leitura_levanta_no_meio(api, monkeypatch):
    import urllib3.response as ur
    fechadas, orig = [], requests.models.Response.close
    monkeypatch.setattr(requests.models.Response, "close", lambda self: (fechadas.append(self), orig(self))[1])

    def quebra(self, *a, **k):
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(ur.HTTPResponse, "read1", quebra)
    _erro_fixo(_get().json(), "indisponivel")
    assert len(fechadas) == 1


@pytest.mark.parametrize("modo", ["bomba_gzip", "bomba_brotli"])
def test_bomba_de_descompressao_e_recusada_pelo_teto_do_corpo_descomprimido(api, espia, modo):
    api.modo = modo
    _erro_fixo(_get().json(), "resposta_invalida")
    assert clarity._MAX_BYTES < espia["n"] <= clarity._MAX_BYTES + 65536  # parou no teto: não expandiu os 64 MB


# ══ Manager ═════════════════════════════════════════════════════════════════

def test_rolagem_sem_contagem_de_sessoes_e_media_simples_e_com_contagem_e_ponderada():
    u1, u2 = "/precos?fbclid=1", "/precos?fbclid=2"
    sem = clarity._dados([{"metricName": "ScrollDepth", "information": [_s_sem(u1, 10.0), _s_sem(u2, 60.0)]}])
    assert sem["rolagem_media_pct"] == 35.0  # simples: a contagem não existe na linha (1 sessão a 10% e 99 a 60%)
    com = clarity._dados([{"metricName": "ScrollDepth", "information": [_s(u1, 1, 10.0), _s(u2, 99, 60.0)]}])
    assert com["rolagem_media_pct"] == 59.5  # ponderada, só porque a linha traz a contagem
    # a contagem do Traffic da mesma URL NÃO pesa a rolagem (melhoria futura declarada)
    d = clarity._dados([{"metricName": "Traffic", "information": [_t(u1, 1, 0, 1), _t(u2, 99, 0, 1)]},
                        {"metricName": "ScrollDepth", "information": [_s_sem(u1, 10.0), _s_sem(u2, 60.0)]}])
    assert d["rolagem_media_pct"] == 35.0


def test_escape_do_backoff_nao_usa_delete_e_o_update_mantem_cota_e_dado(api):
    """O escape seguro: `update ... set falha_em = null` libera a tentativa SEM devolver a cota nem apagar o dado."""
    from db import get_conn
    assert _get().json()["estado"] == "ok"
    _venceu()
    api.modo = "500"
    assert _get().json()["estado"] == "stale" and _cham() == 2
    api.modo = "ok"
    assert _get().json()["estado"] == "stale" and len(api.hits) == 2  # dentro do backoff: não tenta
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update funil_fontes_cache set falha_em = null where fonte = 'clarity'")
        conn.commit()
    linha = _linha("clarity")
    assert linha["payload"] == ESPERADO and linha["chamadas_dia"] == 2  # dado e contador intactos
    assert _get().json()["estado"] == "ok" and len(api.hits) == 3 and _cham() == 3  # tentou, e a cota CONTOU


def test_nenhum_texto_do_repo_recomenda_delete_na_tabela_de_cache_do_clarity():
    import subprocess
    raiz = Path(__file__).resolve().parent.parent
    for arq in ("docs/CLAUDE.md", ".env.example", "core/funil_fonte_clarity.py", "core/funil_fontes.py",
                "core/funil_fontes_cache.py", "frontend/funil.html"):
        for linha in (raiz / arq).read_text(encoding="utf-8").splitlines():
            if "delete from funil_fontes_cache" in linha.lower():
                assert "NÃO use delete" in linha or "NAO use delete" in linha, (arq, linha)
