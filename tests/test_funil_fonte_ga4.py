"""Fonte GA4 do painel de funil (`core/funil_fonte_ga4.py`): parser da Data API, erros com
mensagem fixa, hosts fixos, validação do ID da propriedade e, acima de tudo, SEGREDO: o JSON
da conta de serviço, a `private_key` e o token OAuth não aparecem em JSON, log, tabela de
cache nem stderr.

HERMÉTICO: nenhuma chamada ao Google. A chave é uma RSA 2048 FALSA gerada aqui; o token
endpoint e a Data API são um servidor HTTP local (127.0.0.1, porta efêmera) com o host
trocado SÓ no teste (as constantes de produção são conferidas à parte).
"""
import http.server
import json
import logging
import re
import socketserver
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core import funil_fonte_ga4 as ga4
from core import funil_fontes
from core.funil_fontes import obter
from tests.test_funil_dashboard import _PROIBIDAS_EXATAS, _PROIBIDAS_PARTE, _chaves
from tests.test_funil_fontes import (  # noqa: F401  (ambiente é autouse)
    ENVELOPE, _admin_client, _envelhece, _run, ambiente)
from tests.test_funil_fontes_r3 import _REQUEST_REAL, _linha
from tests.test_log_falha_traceback import _system_event_logs

_POST_REAL = requests.post  # capturado ANTES do bloqueio de rede do conftest


def _post_so_local(url, **kw):
    # Hermético: só o servidor local passa. Um host de verdade (ex.: um `token_uri` que o código
    # passasse a obedecer) levanta aqui em vez de sair para a rede.
    if not url.startswith("http://127.0.0.1:"):
        raise RuntimeError("rede real bloqueada no teste")
    _KW.append(kw)
    return _POST_REAL(url, **kw)


_KW: list[dict] = []  # os kwargs de cada requests.post da fonte (timeout, allow_redirects...)


RAIZ = Path(__file__).resolve().parent.parent
PID = "123456789"
TOKEN = "ya29.TOKEN-OAUTH-FALSO-9f3a"
EMAIL_SA = "leitor@proj-falso.iam.gserviceaccount.com"
VAZA = "Invalid JWT: ana@exemplo.com cus_ABC123"  # texto "do Google" que nunca pode chegar ao painel


def _par():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PEM, PUB = _par()
CORPO_CHAVE = PEM.splitlines()[1]  # um pedaço do corpo base64 da chave


def _sa(pem=PEM, **extra):
    return json.dumps({"type": "service_account", "project_id": "proj-falso", "private_key_id": "k1",
                       "private_key": pem, "client_email": EMAIL_SA, "client_id": "1",
                       "token_uri": "https://oauth2.googleapis.com/token", **extra})


SA = _sa()
AUSENTE = object()  # `expires_in` fora da resposta do token
SEGREDOS = (CORPO_CHAVE, "BEGIN PRIVATE KEY", "private_key", TOKEN, EMAIL_SA, VAZA, "ana@exemplo.com", "cus_ABC123")


def sem_segredo(*textos):
    for t in textos:
        for s in SEGREDOS:
            assert s not in t, s


# ── Respostas realistas da Data API ────────────────────────────────────────

def _resp(dim, linhas, met=("eventCount", "totalUsers"), **extra):
    return {"dimensionHeaders": [{"name": dim}, {"name": "dateRange"}],
            "metricHeaders": [{"name": m, "type": "TYPE_INTEGER"} for m in met],
            "rows": [{"dimensionValues": [{"value": a}, {"value": j}],
                      "metricValues": [{"value": str(x)}, {"value": str(y)}]} for a, j, x, y in linhas],
            "rowCount": len(linhas), "metadata": {"currencyCode": "BRL", "timeZone": "America/Sao_Paulo"},
            "kind": "analyticsData#runReport", **extra}


EV = _resp("eventName", [
    ("page_view", "7d", 1234, 456), ("page_view", "30d", 5000, 1800),
    ("view_item_list", "7d", 300, 120), ("view_item_list", "30d", 900, 400),
    ("begin_checkout", "30d", 40, 30), ("sign_up", "7d", 25, 25), ("purchase", "30d", 3, 3),
    ("vsl_play", "7d", "70", "50"),
    ("scroll", "7d", 999, 9), ("user_engagement", "30d", 5, 5), ("first_visit", "7d", 7, 7),  # fora da lista
], propertyQuota={"tokensPerDay": {"consumed": 2, "remaining": 24998}})

_SCRIPT = "<script>alert(1)</script>"
OG = _resp("sessionDefaultChannelGroup", [
    ("Direct", "7d", 500, 400), ("Organic Search", "7d", 300, 250), ("Paid Social", "7d", 120, 100),
    (_SCRIPT, "7d", 10, 9), ("Direct", "30d", 2000, 1500), ("Organic Search", "30d", 1200, 900),
], met=("sessions", "totalUsers"))


def _zeros():
    return {e: {"eventos": 0, "usuarios": 0} for e in ga4.EVENTOS}


ESPERADO = {
    "eventos": {
        "7d": {**_zeros(), "page_view": {"eventos": 1234, "usuarios": 456},
               "view_item_list": {"eventos": 300, "usuarios": 120}, "sign_up": {"eventos": 25, "usuarios": 25},
               "vsl_play": {"eventos": 70, "usuarios": 50}},
        "30d": {**_zeros(), "page_view": {"eventos": 5000, "usuarios": 1800},
                "view_item_list": {"eventos": 900, "usuarios": 400},
                "begin_checkout": {"eventos": 40, "usuarios": 30}, "purchase": {"eventos": 3, "usuarios": 3}},
    },
    "origens": {
        "7d": [{"canal": "Direct", "sessoes": 500, "usuarios": 400},
               {"canal": "Organic Search", "sessoes": 300, "usuarios": 250},
               {"canal": "Paid Social", "sessoes": 120, "usuarios": 100},
               {"canal": _SCRIPT, "sessoes": 10, "usuarios": 9}],
        "30d": [{"canal": "Direct", "sessoes": 2000, "usuarios": 1500},
                {"canal": "Organic Search", "sessoes": 1200, "usuarios": 900}],
    },
}


# ── Google falso (token endpoint + Data API) ───────────────────────────────

class Google:
    def __init__(self):
        self.hits, self.token, self.dados, self.ev, self.og, self.espera = [], "ok", "ok", EV, OG, 0.0
        self.expires_in, self.token_valor, self.alvo = 3599, TOKEN, None  # alvo: Location dos 3xx
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer._tratar(self)

        class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.srv = Srv(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def de(self, quem):
        return [h for h in self.hits if h["quem"] == quem]

    def _tratar(self, h):
        bruto = h.rfile.read(int(h.headers.get("Content-Length") or 0)).decode()
        token = h.path == "/token"
        corpo = dict(urllib.parse.parse_qsl(bruto)) if token else json.loads(bruto)
        self.hits.append({"quem": "token" if token else "dados", "path": h.path,
                          "auth": h.headers.get("Authorization"), "corpo": corpo})
        modo = self.token if token else self.dados
        if modo == "lento":
            time.sleep(self.espera)
            modo = "ok"
        if modo == "ok":
            if token:
                t = {"access_token": self.token_valor, "token_type": "Bearer"}
                if self.expires_in is not AUSENTE:
                    t["expires_in"] = self.expires_in
                return self._j(h, 200, t)
            r = dict(self.ev if corpo["dimensions"][0]["name"] == "eventName" else self.og)
            r["rows"] = r["rows"][:corpo["limit"]]  # como a API: o `limit` vale para as linhas de TODOS os ranges
            return self._j(h, 200, r)
        if modo == "html":
            return self._raw(h, 200, "text/html", f"<html>{VAZA} {TOKEN}</html>")
        if modo == "html502":
            return self._raw(h, 502, "text/html", f"<html>{VAZA} {CORPO_CHAVE}</html>")
        if modo == "sem_token":
            return self._j(h, 200, {"token_type": "Bearer"})
        if modo == "lista":
            return self._j(h, 200, [1, 2])
        if modo.startswith("redir"):  # "redir301": 3xx para o host B (`alvo`)
            h.send_response(int(modo[5:]))
            h.send_header("Location", self.alvo + h.path)
            h.send_header("Content-Length", "0")
            return h.end_headers()
        if modo in ("204", "202"):
            h.send_response(int(modo))
            h.send_header("Content-Length", "0")
            return h.end_headers()
        self._j(h, int(modo), {"error": {"code": int(modo), "message": f"{VAZA} {CORPO_CHAVE} {TOKEN}", "status": "X"}})

    def _j(self, h, code, body):
        self._raw(h, code, "application/json", json.dumps(body))

    def _raw(self, h, code, tipo, texto):
        b = texto.encode()
        h.send_response(code)
        h.send_header("Content-Type", tipo)
        h.send_header("Content-Length", str(len(b)))
        h.end_headers()
        h.wfile.write(b)


@pytest.fixture()
def google(monkeypatch):
    g = Google()
    monkeypatch.setattr(requests.sessions.Session, "request", _REQUEST_REAL)  # só localhost (o conftest bloqueia)
    monkeypatch.setattr(requests, "post", _post_so_local)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(ga4, "_URL_TOKEN", g.base + "/token")
    monkeypatch.setattr(ga4, "_URL_DADOS", g.base + "/v1beta/properties/{}:runReport")
    monkeypatch.setenv("GA4_PROPERTY_ID", PID)
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", SA)
    ga4._TOKEN.clear()
    _KW.clear()
    yield g
    ga4._TOKEN.clear()
    g.srv.shutdown()


def _get(esperado=200):
    r = _admin_client().get("/admin/api/funil/fonte/ga4")
    assert r.status_code == esperado and r.headers["cache-control"] == "no-store"
    return r


# ── Constantes, envs, rota ─────────────────────────────────────────────────

def test_hosts_e_escopo_fixos_sao_os_do_google():
    assert ga4._URL_TOKEN == "https://oauth2.googleapis.com/token"
    assert ga4._URL_DADOS.format("1") == "https://analyticsdata.googleapis.com/v1beta/properties/1:runReport"
    assert ga4._ESCOPO == "https://www.googleapis.com/auth/analytics.readonly"
    f = ga4.FONTE
    assert (f.nome, f.ttl_s, f.backoff_s, f.limite_dia) == ("ga4", 1800, 300, None)


@pytest.mark.parametrize("definidas,faltam", [
    ((), ["GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"]),
    (("GA4_PROPERTY_ID",), ["GA4_SERVICE_ACCOUNT_JSON"]),
    (("GA4_SERVICE_ACCOUNT_JSON",), ["GA4_PROPERTY_ID"]),
    (("GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"), []),
])
def test_faltam_lista_so_os_nomes_lidos_na_hora(monkeypatch, definidas, faltam):
    for e in ("GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"):
        monkeypatch.delenv(e, raising=False)
    for e in definidas:
        monkeypatch.setenv(e, SA)  # o valor nunca aparece na lista
    assert ga4.FONTE.faltam() == faltam
    monkeypatch.setenv("GA4_PROPERTY_ID", "   ")  # só espaço = ausente
    assert "GA4_PROPERTY_ID" in ga4.FONTE.faltam()


def test_rota_sem_sessao_401_e_com_sessao_200_sem_credencial_nao_configurado(monkeypatch):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    assert TestClient(dashboard.app).get("/admin/api/funil/fonte/ga4").status_code == 401
    monkeypatch.delenv("GA4_PROPERTY_ID", raising=False)
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_JSON", raising=False)
    j = _get().json()
    assert j["estado"] == "nao_configurado" and j["dados"] is None and j["fonte"] == "ga4"
    assert j["falta"] == ["GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"]
    monkeypatch.setenv("GA4_PROPERTY_ID", PID)
    assert _get().json()["falta"] == ["GA4_SERVICE_ACCOUNT_JSON"]  # o cache não "lembra" a falta


# ── Caminho feliz: pedido, parser, cache ───────────────────────────────────

def test_ok_pedidos_exatos_dados_certos_e_cache(google):
    j = _get().json()
    assert set(j) == ENVELOPE and j["estado"] == "ok" and j["mensagem"] is None
    assert j["dados"] == ESPERADO
    [t] = google.de("token")
    assert t["corpo"]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    c = jwt.decode(t["corpo"]["assertion"], PUB, algorithms=["RS256"], audience=ga4._URL_TOKEN)  # assinada de verdade
    assert (c["iss"], c["scope"]) == (EMAIL_SA, ga4._ESCOPO) and c["exp"] - c["iat"] == 3600
    ev, og = google.de("dados")
    assert len(google.hits) == 3 and ev["path"] == og["path"] == f"/v1beta/properties/{PID}:runReport"
    assert ev["auth"] == og["auth"] == "Bearer " + TOKEN
    ranges = [{"name": "7d", "startDate": "7daysAgo", "endDate": "today"},
              {"name": "30d", "startDate": "30daysAgo", "endDate": "today"}]
    assert ev["corpo"]["dateRanges"] == og["corpo"]["dateRanges"] == ranges
    assert ev["corpo"]["dimensions"] == [{"name": "eventName"}]
    assert ev["corpo"]["metrics"] == [{"name": "eventCount"}, {"name": "totalUsers"}]
    assert ev["corpo"]["dimensionFilter"] == {"filter": {"fieldName": "eventName",
                                                         "inListFilter": {"values": list(ga4.EVENTOS)}}}
    assert og["corpo"]["dimensions"] == [{"name": "sessionDefaultChannelGroup"}]
    assert og["corpo"]["metrics"] == [{"name": "sessions"}, {"name": "totalUsers"}]
    # dentro do TTL (30 min): nada de chamada nova
    assert _get().json()["estado"] == "ok" and len(google.hits) == 3
    # TTL vencido: refaz as 2 consultas, mas REAPROVEITA o token de ~1 h
    _envelhece("ga4", buscado_s=1801)
    assert _get().json()["estado"] == "ok"
    assert len(google.de("token")) == 1 and len(google.de("dados")) == 4


def test_dados_sao_lista_fechada_sem_pessoa_nem_metadado_do_google(google):
    j = _get().json()
    permitidas = set(ga4.EVENTOS) | {"eventos", "origens", "7d", "30d", "usuarios", "canal", "sessoes"}
    assert _chaves(j["dados"]) <= permitidas, sorted(_chaves(j["dados"]) - permitidas)
    assert set(j["dados"]) == {"eventos", "origens"}
    for ch in _chaves(j):
        assert ch not in _PROIBIDAS_EXATAS and not any(p in ch for p in _PROIBIDAS_PARTE), ch
    texto = json.dumps(j)
    for fora in ("scroll", "user_engagement", "first_visit", "propertyQuota", "metadata", "tokensPerDay",
                 "currencyCode", "America/Sao_Paulo", "analyticsData", PID):
        assert fora not in texto, fora
    sem_segredo(texto)


def test_stale_backoff_e_ocupada_vem_da_infra(google):
    assert _get().json()["estado"] == "ok"
    _envelhece("ga4", buscado_s=1801)
    google.dados = "500"
    s = _get().json()
    assert s["estado"] == "stale" and s["dados"] == ESPERADO and s["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]
    n = len(google.hits)
    assert _get().json()["estado"] == "stale" and len(google.hits) == n  # backoff de 300 s: não martela
    _envelhece("ga4", falha_s=301)  # backoff vencido: agora quem segura é a marca de voo
    funil_fontes._EM_VOO["ga4"] = {"t": time.monotonic()}
    try:
        o = _get().json()
    finally:
        funil_fontes._EM_VOO.pop("ga4", None)
    assert o["estado"] == "stale" and o["mensagem"] == funil_fontes.MENSAGENS["ocupada"] and len(google.hits) == n


def test_sem_cache_anterior_e_ocupada_vira_erro(google):
    funil_fontes._EM_VOO["ga4"] = {"t": time.monotonic()}
    try:
        o = _get().json()
    finally:
        funil_fontes._EM_VOO.pop("ga4", None)
    assert o["estado"] == "erro" and o["mensagem"] == funil_fontes.MENSAGENS["ocupada"] and google.hits == []


# ── Parser tolerante ───────────────────────────────────────────────────────

def test_numeros_como_texto_viram_int_e_lixo_vira_zero():
    r = ga4._eventos(_resp("eventName", [("page_view", "7d", "123", "45"), ("sign_up", "7d", "abc", None),
                                         ("purchase", "7d", "-5", "1e999"), ("vsl_play", "7d", "12.0", "nan")]))
    p = r["7d"]
    assert p["page_view"] == {"eventos": 123, "usuarios": 45} and isinstance(p["page_view"]["eventos"], int)
    assert p["sign_up"] == {"eventos": 0, "usuarios": 0} and p["purchase"] == {"eventos": 0, "usuarios": 0}
    assert p["vsl_play"] == {"eventos": 12, "usuarios": 0}


@pytest.mark.parametrize("resp", [
    {}, {"rows": None}, {"rows": "x"}, {"rows": {}}, {"rows": [None, 1, "x", {}, {"dimensionValues": 1}]},
    {"rows": [{"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "1"}]}]},  # sem dateRange
    {"rows": [{"dimensionValues": [{"value": {"x": 1}}, {"value": ["7d"]}], "metricValues": [{}]}]},
    {"dimensionHeaders": "x", "metricHeaders": [1, None], "rows": []},
    {"rows": [{"dimensionValues": [{"value": "page_view"}, {"value": "date_range_0"}], "metricValues": []}]},
], ids=range(9))
def test_resposta_incompleta_ou_estranha_vira_zeros_sem_excecao(resp):
    assert ga4._eventos(resp) == {j: _zeros() for j in ("7d", "30d")}
    assert ga4._origens(resp) == {"7d": [], "30d": []}


def test_colunas_por_nome_e_sem_cabecalho_pela_ordem_pedida():
    invertida = {"dimensionHeaders": [{"name": "dateRange"}, {"name": "eventName"}],
                 "metricHeaders": [{"name": "totalUsers"}, {"name": "eventCount"}],
                 "rows": [{"dimensionValues": [{"value": "7d"}, {"value": "page_view"}],
                           "metricValues": [{"value": "4"}, {"value": "9"}]}]}
    assert ga4._eventos(invertida)["7d"]["page_view"] == {"eventos": 9, "usuarios": 4}
    sem_cab = {"rows": [{"dimensionValues": [{"value": "page_view"}, {"value": "30d"}],
                         "metricValues": [{"value": "9"}, {"value": "4"}]}]}
    assert ga4._eventos(sem_cab)["30d"]["page_view"] == {"eventos": 9, "usuarios": 4}


def test_um_range_so_com_dateRange_ausente_nao_inventa_janela():
    resp = {"dimensionHeaders": [{"name": "eventName"}], "metricHeaders": [{"name": "eventCount"}, {"name": "totalUsers"}],
            "rows": [{"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "1"}, {"value": "1"}]}]}
    assert ga4._eventos(resp) == {j: _zeros() for j in ("7d", "30d")}


def test_canal_sanitizado_ordenado_e_cortado_em_8_por_janela():
    sujos = ["a\x00b", "x\ud800y", "z" * 300, "\x07\x1b[31m", "   ", None, 5, "ok quebra​"]
    linhas = [(c, "7d", 100 - i, 1) for i, c in enumerate(sujos)] + [(f"c{i}", "30d", i, 1) for i in range(12)]
    out = ga4._origens(_resp("sessionDefaultChannelGroup", linhas, met=("sessions", "totalUsers")))
    assert len(out["7d"]) == 8 and len(out["30d"]) == 8
    for x in out["7d"] + out["30d"]:
        assert len(x["canal"]) <= 40 and x["canal"].isprintable() and x["canal"] == x["canal"].strip()
        x["canal"].encode("utf-8")  # sem surrogate
        assert "\x00" not in x["canal"]
    assert [x["sessoes"] for x in out["30d"]] == [11, 10, 9, 8, 7, 6, 5, 4]  # os 8 maiores, em ordem
    assert out["7d"][0]["canal"] == "ab" and out["7d"][2]["canal"] == "z" * 40


# ── Erros: códigos fechados, mensagem fixa, nenhum texto do Google ─────────

def _erro_fixo(j, codigo):
    assert j["estado"] == "erro" and j["dados"] is None
    assert j["mensagem"] == funil_fontes.MENSAGENS[codigo]


@pytest.mark.parametrize("modo,codigo", [
    ("400", "resposta_invalida"), ("401", "auth"), ("403", "permissao_ga4"), ("404", "nao_encontrado"),
    ("429", "limite"), ("500", "indisponivel"), ("503", "indisponivel"), ("418", "indisponivel"),
    ("html", "resposta_invalida"), ("html502", "indisponivel"), ("lista", "resposta_invalida"),
])
def test_erro_da_data_api_vira_codigo_fechado_sem_vazar(google, caplog, modo, codigo):
    google.dados = modo
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), codigo)
    assert len(google.de("dados")) == 1  # o 1º relatório falhou: o 2º nem sai
    sem_segredo(r.text, caplog.text)


@pytest.mark.parametrize("modo,codigo", [
    ("400", "auth"), ("401", "auth"), ("403", "auth"), ("429", "limite"), ("500", "indisponivel"),
    ("html", "resposta_invalida"), ("sem_token", "resposta_invalida"), ("lista", "resposta_invalida"),
])
def test_erro_do_token_endpoint_vira_codigo_fechado_sem_vazar(google, caplog, modo, codigo):
    google.token = modo
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), codigo)
    assert google.de("dados") == []
    sem_segredo(r.text, caplog.text)


def test_mensagem_de_permissao_orienta_as_tres_causas_em_uma_frase():
    m = funil_fontes.MENSAGENS["permissao_ga4"]
    assert "Google Analytics Data API" in m and "Leitor" in m and "GA4_PROPERTY_ID" in m and "só o número" in m
    assert m.count(".") == 1 and m.endswith(".") and len(m) < 220


def test_timeout_real_vira_timeout_e_recusa_de_conexao_nao(google, monkeypatch):
    monkeypatch.setattr(ga4, "_PRAZO_S", 0.3)
    google.token, google.espera = "lento", 1.5
    _erro_fixo(_get().json(), "timeout")
    ga4._TOKEN.clear()
    _apaga_cache()
    monkeypatch.setattr(ga4, "_URL_TOKEN", "http://127.0.0.1:1/token")  # ninguém escutando
    _erro_fixo(_get().json(), "indisponivel")


def _apaga_cache():
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from funil_fontes_cache where fonte = 'ga4'")
        conn.commit()


def test_data_api_lenta_tambem_vira_timeout(google, monkeypatch):
    monkeypatch.setattr(ga4, "_PRAZO_S", 0.3)
    google.dados, google.espera = "lento", 1.5
    _erro_fixo(_get().json(), "timeout")


# ── Credencial ilegível ────────────────────────────────────────────────────

_RUINS = {
    "nao_json": "isto não é json " + CORPO_CHAVE,
    "json_truncado": '{"client_email": "' + EMAIL_SA + '", "private_key": "' + PEM[:120],
    "lista": "[1, 2]", "vazio_objeto": "{}",
    "sem_private_key": json.dumps({"client_email": EMAIL_SA}),
    "sem_client_email": json.dumps({"private_key": PEM}),
    "chave_lixo": _sa("-----BEGIN PRIVATE KEY-----\nlixo-lixo\n-----END PRIVATE KEY-----\n"),
    "chave_numero": json.dumps({"client_email": EMAIL_SA, "private_key": 123}),
    "email_objeto": json.dumps({"client_email": {"x": 1}, "private_key": PEM}),
}


@pytest.mark.parametrize("nome", sorted(_RUINS))
def test_credencial_ilegivel_vira_credencial_invalida_sem_ecoar_nada(google, caplog, monkeypatch, nome):
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _RUINS[nome])
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), "credencial_invalida")
    assert google.hits == []  # nem token endpoint nem Data API
    sem_segredo(r.text, caplog.text)
    assert "lixo-lixo" not in r.text + caplog.text


def test_json_com_quebra_de_linha_real_na_chave_colada_ainda_funciona(google, monkeypatch):
    colado = SA.replace("\\n", "\n")  # quebras de linha REAIS dentro da string, como ao colar no Railway
    with pytest.raises(json.JSONDecodeError):
        json.loads(colado)
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", colado)
    assert _get().json()["estado"] == "ok"


# ── ID da propriedade e host fixo ──────────────────────────────────────────

@pytest.mark.parametrize("pid", ["12a4", "123/../x", "１２３", "123?x=1", "12 3", "-1", "9" * 21, "123\n4",
                                 "123:runReport", "abc", "123#", "../123"])
def test_id_com_nao_digito_e_recusado_sem_nenhuma_chamada(google, monkeypatch, pid):
    monkeypatch.setenv("GA4_PROPERTY_ID", pid)
    _erro_fixo(_get().json(), "nao_encontrado")
    assert google.hits == []


def test_id_com_espacos_nas_pontas_e_aparado(google, monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", f"  {PID}\n")
    assert _get().json()["estado"] == "ok" and google.de("dados")[0]["path"] == f"/v1beta/properties/{PID}:runReport"


def test_token_uri_do_json_nunca_e_chamado(google, monkeypatch):
    mau = Google()
    try:
        monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _sa(token_uri=mau.base + "/token"))
        assert _get().json()["estado"] == "ok"
        assert mau.hits == [] and len(google.de("token")) == 1
    finally:
        mau.srv.shutdown()


# ── Token: reaproveitado, invalidado, thread-safe ──────────────────────────

def test_trocar_o_json_invalida_o_token_em_cache(google, monkeypatch):
    assert _get().json()["estado"] == "ok" and len(google.de("token")) == 1
    _envelhece("ga4", buscado_s=1801)
    priv2, pub2 = _par()
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _sa(priv2))
    assert _get().json()["estado"] == "ok"
    t1, t2 = google.de("token")
    assert jwt.decode(t2["corpo"]["assertion"], pub2, algorithms=["RS256"], audience=ga4._URL_TOKEN)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(t2["corpo"]["assertion"], PUB, algorithms=["RS256"], audience=ga4._URL_TOKEN)


def test_token_vencido_ou_recusado_pelo_google_e_assinado_de_novo(google):
    assert _get().json()["estado"] == "ok"
    ga4._TOKEN["expira"] = time.monotonic() + 30  # falta menos de 60 s
    _envelhece("ga4", buscado_s=1801)
    assert _get().json()["estado"] == "ok" and len(google.de("token")) == 2
    google.dados = "401"
    _envelhece("ga4", buscado_s=1801)
    assert _get().json()["estado"] == "stale" and ga4._TOKEN == {}
    google.dados = "ok"
    _envelhece("ga4", falha_s=301)
    assert _get().json()["estado"] == "ok" and len(google.de("token")) == 3


def test_threads_simultaneas_assinam_e_pedem_o_token_uma_vez(google):
    with ThreadPoolExecutor(6) as ex:
        tokens = list(ex.map(lambda _: ga4._token(SA), range(6)))
    assert set(tokens) == {TOKEN} and len(google.de("token")) == 1


# ── SEGREDO: nenhuma saída leva o JSON, a chave nem o token ────────────────

def _excecoes():
    return [requests.exceptions.ConnectionError(SA), requests.exceptions.SSLError(SA),
            requests.exceptions.ReadTimeout(SA), requests.exceptions.ConnectTimeout(SA),
            RuntimeError(SA), ValueError(SA), KeyError(SA), OSError(SA), json.JSONDecodeError(SA, SA, 3),
            UnicodeError(SA)]


def _saidas(r, caplog, monkeypatch, capfd):
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert all(not x.exc_info for x in warns)
    out, err = capfd.readouterr()
    sem_segredo(r.text, caplog.text, out, err, repr(_system_event_logs(monkeypatch, warns)))
    linha = _linha("ga4")
    sem_segredo(json.dumps(linha, default=str) if linha else "")
    assert PEM not in repr(vars(ga4)) and SA not in repr(vars(ga4)) and CORPO_CHAVE not in repr(ga4.FONTE)


@pytest.mark.parametrize("erro", _excecoes(), ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("onde", ["post", "assinar"])
def test_excecao_que_carrega_o_json_nao_vaza_em_nenhuma_saida(google, caplog, monkeypatch, capfd, erro, onde):
    def quebra(*a, **kw):
        raise erro

    if onde == "post":
        monkeypatch.setattr(requests, "post", quebra)
    else:  # só a referência do módulo da fonte: o jwt global também assina o login do admin
        monkeypatch.setattr(ga4, "jwt", SimpleNamespace(encode=quebra))
    with caplog.at_level(logging.DEBUG):
        r = _get()
    assert r.json()["estado"] == "erro" and r.json()["dados"] is None
    assert r.json()["mensagem"] in funil_fontes.MENSAGENS.values()
    _saidas(r, caplog, monkeypatch, capfd)


@pytest.mark.parametrize("token,dados", [("ok", "ok"), ("ok", "403"), ("ok", "html"), ("401", "ok"), ("html", "ok")])
def test_respostas_do_google_com_segredo_no_corpo_nao_vazam(google, caplog, monkeypatch, capfd, token, dados):
    google.token, google.dados = token, dados
    with caplog.at_level(logging.DEBUG):  # inclui urllib3/requests em DEBUG
        r = _get()
    if token == dados == "ok":
        assert any(x.name.startswith("urllib3") for x in caplog.records)  # o DEBUG de verdade foi capturado
    _saidas(r, caplog, monkeypatch, capfd)


# ── Lista de eventos == o que o app dispara ────────────────────────────────

def _disparados():
    achados = set()
    arquivos = [p for ext in ("*.html", "*.js") for p in (RAIZ / "frontend").rglob(ext) if "node_modules" not in p.parts]
    for p in arquivos:
        t = p.read_text(encoding="utf-8", errors="ignore")
        achados |= set(re.findall(r"""gtag\(\s*['"]event['"]\s*,\s*['"]([a-z_]+)['"]""", t))
        achados |= set(re.findall(r"""pbTrack\(\s*['"]([a-z_]+)['"]""", t))
    achados |= set(re.findall(r"""send_event\(\s*name="([a-z_]+)\"""", (RAIZ / "core/services/ga4_mp.py").read_text(encoding="utf-8")))
    if "gtag('config'" in (RAIZ / "frontend/routes/shared.py").read_text(encoding="utf-8"):
        achados.add("page_view")  # o `config` dispara o page_view sozinho
    return achados


def test_lista_de_eventos_e_exatamente_a_que_o_codigo_dispara():
    achados = _disparados()
    assert len(achados) >= 8
    assert set(ga4.EVENTOS) == achados, (sorted(set(ga4.EVENTOS) - achados), sorted(achados - set(ga4.EVENTOS)))
    assert len(set(ga4.EVENTOS)) == len(ga4.EVENTOS)


# ── 2ª passada do Tester ───────────────────────────────────────────────────

@pytest.mark.parametrize("codigo", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("onde", ["token", "dados"])
def test_redirecionamento_nao_e_seguido_e_nada_vaza_para_o_outro_host(google, onde, codigo):
    mau = Google()
    try:
        google.alvo = mau.base
        setattr(google, onde, f"redir{codigo}")
        _erro_fixo(_get().json(), "indisponivel")
        assert mau.hits == []  # nem assertion nem Bearer chegaram ao host B
        assert len(google.de("dados")) == (1 if onde == "dados" else 0)
        assert _KW and all(kw["allow_redirects"] is False for kw in _KW)
    finally:
        mau.srv.shutdown()


@pytest.mark.parametrize("modo", ["204", "202", "redir301"])
@pytest.mark.parametrize("onde", ["token", "dados"])
def test_status_2xx_ou_3xx_diferente_de_200_e_erro_indisponivel(google, onde, modo):
    google.alvo = "http://127.0.0.1:1"
    setattr(google, onde, modo)
    _erro_fixo(_get().json(), "indisponivel")


def test_timeout_de_cada_chamada_tem_valor_curto(google):
    assert _get().json()["estado"] == "ok"
    assert len(_KW) == 3 and all(0 < kw["timeout"] <= 8 for kw in _KW)
    assert ga4._PRAZO_S == 5


def test_limit_cobre_todas_as_linhas_e_o_servidor_que_o_respeita_nao_corta_nada(google):
    canais = [f"Canal {i:02d}" for i in range(18)]
    google.ev = _resp("eventName", [(e, j, 10 + i, 5) for i, e in enumerate(ga4.EVENTOS) for j in ("7d", "30d")])
    google.og = _resp("sessionDefaultChannelGroup", [(c, j, 100 - i, 5) for i, c in enumerate(canais) for j in ("7d", "30d")],
                      met=("sessions", "totalUsers"))
    d = _get().json()["dados"]
    ev, og = google.de("dados")
    assert 18 <= ev["corpo"]["limit"] <= 1000 and 36 <= og["corpo"]["limit"] <= 1000
    for j in ("7d", "30d"):
        assert all(d["eventos"][j][e]["eventos"] > 0 for e in ga4.EVENTOS), j  # nenhum evento zerado por corte
        assert len(d["origens"][j]) == 8 and d["origens"][j][0]["canal"] == "Canal 00"


def test_token_em_memoria_guarda_so_o_hash_do_json_e_o_access_token(google):
    import hashlib
    assert _get().json()["estado"] == "ok"
    assert set(ga4._TOKEN) == {"chave", "valor", "expira"}
    assert ga4._TOKEN["chave"] == hashlib.sha256(SA.encode()).hexdigest() and ga4._TOKEN["valor"] == TOKEN
    mem = repr(ga4._TOKEN) + repr({k: v for k, v in vars(ga4).items() if k != "__doc__"})  # o docstring cita "private_key"
    for s in (CORPO_CHAVE, "BEGIN PRIVATE KEY", "private_key", EMAIL_SA, SA, "proj-falso"):
        assert s not in mem, s


@pytest.mark.parametrize("expires_in,reaproveita,teto", [
    (AUSENTE, False, None), (0, False, None), (-5, False, None), (59, False, None), (60, False, None), (None, False, None),
    ([1], False, None), ("abc", False, None), ("1e400", False, None), (True, False, None),
    (3599, True, 3599), ("3599", True, 3599), (3601, True, 3600), (10 ** 12, True, 3600), ("99999", True, 3600),
])
def test_expires_in_tem_piso_de_60s_teto_de_1h_e_margem(google, expires_in, reaproveita, teto):
    google.expires_in = expires_in
    assert ga4._token(SA) == TOKEN
    restante = ga4._TOKEN["expira"] - time.monotonic()
    if teto:
        assert teto - 1 < restante <= teto + 0.5
    else:
        assert 59 < restante <= 60.5  # piso: nunca menos que a margem, nunca "eterno"
    ga4._token(SA)
    assert len(google.de("token")) == (1 if reaproveita else 2)


def test_margem_de_60s_do_token_em_cache(google):
    ga4._token(SA)
    ga4._TOKEN["expira"] = time.monotonic() + 120
    ga4._token(SA)
    assert len(google.de("token")) == 1
    ga4._TOKEN["expira"] = time.monotonic() + 55
    ga4._token(SA)
    assert len(google.de("token")) == 2


@pytest.mark.parametrize("ruim", [123, None, ["x"], "", "ab\ncd", "ya29.é", "com espaço", "a" * 4097, "ab\r\nX: y"])
def test_access_token_fora_do_formato_nao_entra_no_cache(google, ruim):
    google.token_valor = ruim
    _erro_fixo(_get().json(), "resposta_invalida")
    assert ga4._TOKEN == {} and google.de("dados") == []


def test_access_token_no_formato_de_bearer_passa(google):
    google.token_valor = "ya29.a0Af_-~+/=" + "b" * 100
    assert _get().json()["estado"] == "ok"
    assert google.de("dados")[0]["auth"] == "Bearer " + google.token_valor


def test_origens_empatadas_saem_por_nome_e_o_corte_em_8_vale_com_empate():
    nomes = ["m", "c", "x", "a", "q", "b", "z", "d", "k", "e", "y", "f"]
    r = ga4._origens(_resp("sessionDefaultChannelGroup", [(n, "7d", 10, 1) for n in nomes] + [("topo", "7d", 11, 1)],
                           met=("sessions", "totalUsers")))
    assert [x["canal"] for x in r["7d"]] == ["topo", "a", "b", "c", "d", "e", "f", "k"]


def test_metrica_ausente_ou_nula_vira_zero():
    resp = {"rows": [{"dimensionValues": [{"value": "page_view"}, {"value": "7d"}], "metricValues": [{"value": None}]},
                     {"dimensionValues": [{"value": "sign_up"}, {"value": "7d"}], "metricValues": []}]}
    p = ga4._eventos(resp)["7d"]
    assert p["page_view"] == {"eventos": 0, "usuarios": 0} and p["sign_up"] == {"eventos": 0, "usuarios": 0}
    assert ga4._origens({"rows": [{"dimensionValues": [{"value": "x"}, {"value": "7d"}], "metricValues": [{}]}]})["7d"] == \
        [{"canal": "x", "sessoes": 0, "usuarios": 0}]


@pytest.mark.parametrize("cenario", ["json_ruim", "chave_lixo", "dados_html", "token_html", "http_500", "rede"])
def test_erro_da_fonte_nao_carrega_o_json_na_cadeia_de_excecoes(google, monkeypatch, cenario):
    if cenario == "json_ruim":
        monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _RUINS["json_truncado"])
    elif cenario == "chave_lixo":
        monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _RUINS["chave_lixo"])
    elif cenario == "dados_html":
        google.dados = "html"
    elif cenario == "token_html":
        google.token = "html"
    elif cenario == "http_500":
        google.dados = "500"
    else:
        monkeypatch.setattr(ga4, "_URL_TOKEN", "http://127.0.0.1:1/token")
    with pytest.raises(funil_fontes.FonteErro) as e:
        ga4._buscar()
    x = e.value
    assert x.__cause__ is None and (x.__suppress_context__ is True or x.__context__ is None)


def test_env_do_json_com_byte_invalido_e_credencial_invalida_e_nao_levanta(google, monkeypatch):
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", "{\udc80\udcff" + SA)  # surrogateescape, como um byte inválido no ambiente
    _erro_fixo(_get().json(), "credencial_invalida")
    assert google.hits == []


# ── Infra: faltam()/montagem quebrados viram `erro` genérico (latente) ─────

def test_faltam_que_levanta_com_a_chave_na_mensagem_vira_erro_generico_sem_vazar(monkeypatch, caplog, capfd):
    from dataclasses import replace
    segredo = "sk_live_FALSA_do_faltam"
    quebrada = replace(ga4.FONTE, faltam=lambda: (_ for _ in ()).throw(RuntimeError(segredo)))
    monkeypatch.setattr(funil_fontes, "fonte", lambda n: quebrada)
    with caplog.at_level(logging.DEBUG):
        r = _get()
    out, err = capfd.readouterr()
    j = r.json()
    assert j["estado"] == "erro" and j["mensagem"] == funil_fontes.MENSAGENS["indisponivel"] and j["dados"] is None
    assert set(j) == ENVELOPE and set(j["janela"]) == {"rotulo", "fuso"}
    assert all(isinstance(v, str) for v in j["janela"].values())
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert any("RuntimeError" in x.getMessage() for x in warns) and all(not x.exc_info for x in warns)
    for texto in (r.text, caplog.text, out, err, repr(_system_event_logs(monkeypatch, warns))):
        assert segredo not in texto


def test_modulo_que_nao_importa_vira_erro_generico_e_cancelamento_propaga(monkeypatch):
    import asyncio
    monkeypatch.setitem(funil_fontes.MODULOS, "ga4", "nao.importado")
    assert _run(obter("ga4"))["estado"] == "erro"

    async def cancela(nome):
        raise asyncio.CancelledError

    monkeypatch.setattr(funil_fontes, "_obter", cancela)
    with pytest.raises(asyncio.CancelledError):
        _run(obter("ga4"))


# ── 3ª rodada: teto e classe do regex do access_token, cadeia no timeout ───

def _bearer(n):  # token realista: prefixo do Google + base64url com `-`, `_`, `.` e `=`
    corpo = "".join("Ab0-_.=Zx9"[(i * 7 + i // 3) % 10] for i in range(n - len("ya29.c.b0Aaek")))
    return ("ya29.c.b0Aaek" + corpo)[:n]


@pytest.mark.parametrize("n", [1100, 2100, 4096])
def test_token_longo_e_realista_passa_ate_4096_caracteres(google, n):
    google.token_valor = _bearer(n)
    assert len(google.token_valor) == n and {"-", "_", ".", "="} <= set(google.token_valor)
    assert _get().json()["estado"] == "ok"
    assert google.de("dados")[0]["auth"] == "Bearer " + google.token_valor


def test_token_de_4097_caracteres_e_recusado(google):
    google.token_valor = _bearer(4097)
    _erro_fixo(_get().json(), "resposta_invalida")
    assert ga4._TOKEN == {} and google.de("dados") == []


@pytest.mark.parametrize("c", list("%:*!@;,\"'<>\\|{}"))
def test_caractere_fora_da_classe_do_bearer_e_recusado_mesmo_dentro_de_um_token_valido(google, c):
    google.token_valor = f"ya29.a0Af_-~+/={c}b0Aaek"
    _erro_fixo(_get().json(), "resposta_invalida")
    assert ga4._TOKEN == {} and google.de("dados") == []


@pytest.mark.parametrize("erro", [requests.exceptions.ConnectTimeout(SA), requests.exceptions.ReadTimeout(SA)],
                         ids=lambda e: type(e).__name__)
def test_timeout_real_tambem_nao_carrega_cadeia_de_excecao(google, monkeypatch, erro):
    def estoura(*a, **kw):
        raise erro

    monkeypatch.setattr(requests, "post", estoura)
    with pytest.raises(funil_fontes.FonteErro) as e:
        ga4._buscar()
    assert e.value.codigo == "timeout" and e.value.__cause__ is None and e.value.__suppress_context__ is True


# ── Logs de diagnóstico da 1ª carga em produção: só a etapa, nunca o conteúdo ──

def test_log_de_formato_de_token_recusado_so_com_a_etapa(google, caplog, monkeypatch):
    google.token_valor = "ya29.com espaço e " + TOKEN + "\n"
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), "resposta_invalida")
    linhas = [x for x in caplog.records if x.name == ga4.__name__ and x.levelno == logging.WARNING]
    assert [x.getMessage() for x in linhas] == ["[funil_ga4] token: formato recusado"] and not linhas[0].exc_info
    sem_segredo(caplog.text, repr(_system_event_logs(monkeypatch, linhas)), r.text)
    assert "com espaço" not in caplog.text


@pytest.mark.parametrize("nome", ["nao_json", "json_truncado", "sem_private_key", "chave_lixo"])
def test_log_de_credencial_invalida_so_com_a_etapa(google, caplog, monkeypatch, nome):
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", _RUINS[nome])
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), "credencial_invalida")
    linhas = [x for x in caplog.records if x.name == ga4.__name__ and x.levelno == logging.WARNING]
    assert [x.getMessage() for x in linhas] == ["[funil_ga4] credencial: JSON inválido"] and not linhas[0].exc_info
    sem_segredo(caplog.text, repr(_system_event_logs(monkeypatch, linhas)), r.text)
    assert "lixo-lixo" not in caplog.text
