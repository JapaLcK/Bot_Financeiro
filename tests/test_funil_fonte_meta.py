"""Fonte Meta do painel de funil (`core/funil_fonte_meta.py`): disparos do pixel em 7 dias pela
Graph API. Parser tolerante (o formato REAL da resposta não foi visto), lista fechada de eventos,
mapa de códigos de erro da Graph, corpo de erro lido com teto e SÓ o inteiro `error.code`, e,
acima de tudo, SEGREDO: o token não aparece em URL, params, JSON, log, tabela de cache nem stderr.

HERMÉTICO: nenhuma chamada à Meta. A Graph é um servidor HTTP local (127.0.0.1, porta efêmera) com o
host trocado SÓ no teste (a constante de produção é conferida à parte). O parser também é testado
direto, sem HTTP.
"""
import gzip
import http.server
import json
import logging
import re
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path

import pytest
import requests

from core import funil_fonte_meta as meta
from core import funil_fontes
from tests.test_funil_dashboard import _PROIBIDAS_EXATAS, _PROIBIDAS_PARTE, _chaves
from tests.test_funil_fontes import (  # noqa: F401  (ambiente é autouse)
    ENVELOPE, _admin_client, _envelhece, _run, ambiente)
from tests.test_funil_fontes_r3 import _REQUEST_REAL, _linha
from tests.test_log_falha_traceback import _system_event_logs

_GET_REAL = requests.get  # capturado ANTES do bloqueio de rede do conftest
_KW: list[dict] = []  # os kwargs de cada requests.get da fonte


def _get_so_local(url, **kw):
    if not url.startswith("http://127.0.0.1:"):  # um host de verdade levanta aqui em vez de sair para a rede
        raise RuntimeError("rede real bloqueada no teste")
    _KW.append(kw)
    return _GET_REAL(url, **kw)


RAIZ = Path(__file__).resolve().parent.parent
PID = "123456789012345"
TOKEN = "EAABsbCS1iHgBO-TOKEN-FALSO_9f3a"
TOKEN_CAPI = "EAAB-TOKEN-DA-CAPI-NUNCA-LIDO"
VAZA = "Invalid OAuth: ana@exemplo.com cus_ABC123 fbtrace_ID_SECRETO"  # texto "da Meta" que nunca pode chegar ao painel
SEGREDOS = (TOKEN, TOKEN_CAPI, VAZA, "ana@exemplo.com", "cus_ABC123", "fbtrace_ID_SECRETO", "Bearer")


def sem_segredo(*textos):
    for t in textos:
        for s in SEGREDOS:
            assert s not in t, s


def _res(*pares, extra=None):
    """Um resultado da Graph: buckets {value, count}."""
    return {"aggregation": "event_total_counts", "start_time": "2026-10-01T00:00:00+0000",
            "data": [{"value": v, "count": c} for v, c in pares], **(extra or {})}


FORMA_A = {"data": [_res(("PageView", 1000), ("ViewContent", 300), ("Purchase", 2), ("SubscribedButtonClick", 99),
                         ("Microdata", 7))]}
ESPERADO = {"eventos": {"PageView": 1000, "ViewContent": 300, "InitiateCheckout": 0, "CompleteRegistration": 0,
                        "StartTrial": 0, "OnboardingComplete": 0, "TesteClick": 0, "Purchase": 2},
            "reconhecido": True}


# ── Graph falsa ────────────────────────────────────────────────────────────

class Graph:
    def __init__(self):
        self.hits, self.modo, self.resp = [], "ok", FORMA_A
        self.status, self.codigo, self.bruto, self.alvo = 400, 190, b"", None
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
        m = self.modo
        if m == "ok":
            return self._raw(h, 200, json.dumps(self.resp).encode())
        if m == "erro":  # corpo de erro no formato da Graph, com texto que nunca pode sair
            return self._raw(h, self.status, json.dumps({"error": {
                "message": f"{VAZA} {TOKEN}", "type": "OAuthException", "code": self.codigo, "error_subcode": 33,
                "fbtrace_id": "fbtrace_ID_SECRETO"}}).encode())
        if m == "bruto":  # status + corpo qualquer
            return self._raw(h, self.status, self.bruto)
        if m == "gigante":  # 200 de 12 MB: a fonte tem de parar no teto
            return self._grande(h, 200, b'{"data":[]}' + b" " * (12 * 1024 * 1024))
        if m == "erro_gigante":  # 400 de 20 MB: o corpo de erro tem teto
            return self._grande(h, 400, b'{"error":{"code":190}}' + b" " * (20 * 1024 * 1024))
        if m == "bomba_gzip":  # poucos KB comprimidos, dezenas de MB descomprimidos
            corpo = gzip.compress(b'{"data":[]}' + b" " * (64 * 1024 * 1024))
            h.send_response(200)
            h.send_header("Content-Encoding", "gzip")
            h.send_header("Content-Length", str(len(corpo)))
            h.end_headers()
            try:
                return h.wfile.write(corpo)
            except OSError:
                return
        if m in ("gzip_ok", "chunked_gzip"):  # respostas LEGÍTIMAS comprimidas: têm de parsear
            corpo = gzip.compress(json.dumps(self.resp).encode())
            h.send_response(200)
            h.send_header("Content-Encoding", "gzip")
            if m == "gzip_ok":
                h.send_header("Content-Length", str(len(corpo)))
                h.end_headers()
                return h.wfile.write(corpo)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            for i in range(0, len(corpo), 40):
                parte = corpo[i:i + 40]
                h.wfile.write(f"{len(parte):x}\r\n".encode() + parte + b"\r\n")
            return h.wfile.write(b"0\r\n\r\n")
        if m in ("para_no_meio", "cortado", "reset"):  # promete 100000 bytes, manda 10 e: trava / fecha / reseta
            h.send_response(200)
            h.send_header("Content-Length", "100000")
            h.end_headers()
            try:
                h.wfile.write(b'{"data": [')
                h.wfile.flush()
                if m == "para_no_meio":
                    time.sleep(3)
                elif m == "reset":
                    import socket
                    import struct
                    h.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    h.connection.close()
            except OSError:
                pass
            return
        if m in ("gotejo", "gotejo_erro"):  # 1 byte a cada `gota` s e Content-Length enorme
            h.send_response(200 if m == "gotejo" else 400)
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
        if m.startswith("redir"):  # "redir301": 3xx para o host B (`alvo`)
            h.send_response(int(m[5:]))
            h.send_header("Location", self.alvo + h.path)
            h.send_header("Content-Length", "0")
            return h.end_headers()
        raise AssertionError(m)

    def _grande(self, h, code, corpo):
        h.send_response(code)
        h.send_header("Content-Length", str(len(corpo)))
        h.end_headers()
        try:
            for i in range(0, len(corpo), 65536):
                h.wfile.write(corpo[i:i + 65536])
        except OSError:  # o cliente fechou: era o esperado
            pass

    def _raw(self, h, code, b):
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(b)))
        h.end_headers()
        h.wfile.write(b)


@pytest.fixture()
def api(monkeypatch):
    g = Graph()
    monkeypatch.setattr(requests.sessions.Session, "request", _REQUEST_REAL)  # só localhost (o conftest bloqueia)
    monkeypatch.setattr(requests, "get", _get_so_local)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(k.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(meta, "_URL", g.base + "/v25.0/{}/stats")
    monkeypatch.setenv("META_PIXEL_ID", PID)
    monkeypatch.setenv("META_INSIGHTS_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("META_PIXEL_ACCESS_TOKEN", TOKEN_CAPI)  # a Conversions API: NUNCA lido por esta fonte
    _KW.clear()
    yield g
    g.srv.shutdown()


_CLI: dict = {}


@pytest.fixture(autouse=True)
def _um_login_por_teste():  # o login do admin tem limite por minuto: um cliente por teste
    _CLI.clear()
    yield


def _get(esperado=200):
    cli = _CLI.get("c") or _CLI.setdefault("c", _admin_client())
    r = cli.get("/admin/api/funil/fonte/meta")
    assert r.status_code == esperado and r.headers["cache-control"] == "no-store"
    return r


def _erro_fixo(j, codigo):
    assert j["estado"] == "erro" and j["dados"] is None
    assert j["mensagem"] == funil_fontes.MENSAGENS[codigo]


def _log_formato(caplog):
    return [x.getMessage() for x in caplog.records
            if x.name == meta.__name__ and x.levelno == logging.WARNING and "formato:" in x.getMessage()]


# ── Constantes, envs, rota ─────────────────────────────────────────────────

def test_host_versao_e_parametros_da_fonte():
    assert meta._URL == "https://graph.facebook.com/v25.0/{}/stats"
    assert meta._URL.format("1") == "https://graph.facebook.com/v25.0/1/stats"
    f = meta.FONTE
    assert (f.nome, f.ttl_s, f.backoff_s, f.limite_dia) == ("meta", 1800, 600, None)
    assert f.janela == {"rotulo": "Disparos do pixel por evento, últimos 7 dias (limite da API)", "fuso": "UTC"}
    assert 0 < meta._PRAZO_S <= 8 and meta._PRAZO_TOTAL_S < funil_fontes.TIMEOUT_S
    assert (meta._MAX_BYTES, meta._MAX_BYTES_ERRO) == (1024 * 1024, 64 * 1024)


def test_modulo_registrado_e_a_rota_e_a_da_meta(api):
    assert funil_fontes.MODULOS["meta"] == "core.funil_fonte_meta"
    assert funil_fontes.fonte("meta") is meta.FONTE
    j = _get().json()
    assert j["fonte"] == "meta" and j["estado"] == "ok"


@pytest.mark.parametrize("definidas,faltam", [
    ((), ["META_PIXEL_ID", "META_INSIGHTS_ACCESS_TOKEN"]),
    (("META_PIXEL_ID",), ["META_INSIGHTS_ACCESS_TOKEN"]),
    (("META_INSIGHTS_ACCESS_TOKEN",), ["META_PIXEL_ID"]),
    (("META_PIXEL_ID", "META_INSIGHTS_ACCESS_TOKEN"), []),
])
def test_faltam_lista_so_os_nomes_lidos_na_hora(monkeypatch, definidas, faltam):
    for e in ("META_PIXEL_ID", "META_INSIGHTS_ACCESS_TOKEN", "META_PIXEL_ACCESS_TOKEN"):
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setenv("META_PIXEL_ACCESS_TOKEN", TOKEN_CAPI)  # o token da CAPI NÃO substitui o de leitura
    for e in definidas:
        monkeypatch.setenv(e, TOKEN)
    assert meta.FONTE.faltam() == faltam
    monkeypatch.setenv("META_INSIGHTS_ACCESS_TOKEN", "   ")  # só espaço = ausente
    assert "META_INSIGHTS_ACCESS_TOKEN" in meta.FONTE.faltam()
    sem_segredo(json.dumps(meta.FONTE.faltam()))


def test_rota_sem_sessao_401_e_sem_credencial_nao_configurado_sem_tocar_na_api(api, monkeypatch):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    assert TestClient(dashboard.app).get("/admin/api/funil/fonte/meta").status_code == 401
    monkeypatch.delenv("META_INSIGHTS_ACCESS_TOKEN", raising=False)
    j = _get().json()
    assert j["estado"] == "nao_configurado" and j["dados"] is None and j["fonte"] == "meta"
    assert j["falta"] == ["META_INSIGHTS_ACCESS_TOKEN"] and _linha("meta") is None and api.hits == []


# ── Caminho feliz: UMA chamada, cabeçalho, parser, cache ───────────────────

def test_ok_uma_chamada_bearer_so_no_cabecalho_dados_certos_e_cache(api):
    antes = int(time.time())
    j = _get().json()
    assert set(j) == ENVELOPE and j["estado"] == "ok" and j["mensagem"] is None
    assert j["janela"] == {"rotulo": "Disparos do pixel por evento, últimos 7 dias (limite da API)", "fuso": "UTC"}
    assert j["dados"] == ESPERADO
    [h] = api.hits  # EXATAMENTE uma requisição HTTP por busca
    assert h["path"] == f"/v25.0/{PID}/stats"
    assert set(h["query"]) == {"aggregation", "start_time", "end_time"} and h["query"]["aggregation"] == "event_total_counts"
    fim, ini = int(h["query"]["end_time"]), int(h["query"]["start_time"])
    assert antes <= fim <= int(time.time()) + 1 and fim - ini == 7 * 86400 - 3600  # 7 dias menos 1 h de folga (limite da API)
    assert h["auth"] == "Bearer " + TOKEN
    assert TOKEN not in h["bruto"] and TOKEN_CAPI not in json.dumps(h["cabecalhos"])  # nunca na URL; a CAPI nunca é lida
    [kw] = _KW
    assert kw["allow_redirects"] is False and kw["stream"] is True and 0 < kw["timeout"] <= 8
    assert TOKEN not in json.dumps(kw["params"]) and set(kw["params"]) == {"aggregation", "start_time", "end_time"}
    assert kw["headers"]["Authorization"] == "Bearer " + TOKEN and "data" not in kw and "json" not in kw
    # dentro do TTL (30 min): nada de chamada nova
    assert _get().json()["estado"] == "ok" and len(api.hits) == 1
    _envelhece("meta", buscado_s=1801)
    assert _get().json()["estado"] == "ok" and len(api.hits) == 2


def test_dados_sao_lista_fechada_sem_pessoa_nem_texto_da_meta(api):
    j = _get().json()
    assert set(j["dados"]) == {"eventos", "reconhecido"} and set(j["dados"]["eventos"]) == set(meta.EVENTOS)
    assert _chaves(j["dados"]) <= set(meta.EVENTOS) | {"eventos", "reconhecido"}
    for ch in _chaves(j):
        assert ch not in _PROIBIDAS_EXATAS and not any(p in ch for p in _PROIBIDAS_PARTE), ch
    texto = json.dumps(j)
    for fora in ("SubscribedButtonClick", "Microdata", "aggregation", "start_time"):  # evento fora da lista é descartado
        assert fora not in texto, fora
    sem_segredo(texto)


def test_stale_e_backoff_vem_da_infra(api):
    assert _get().json()["estado"] == "ok"
    _envelhece("meta", buscado_s=1801)
    api.modo, api.status, api.codigo = "erro", 500, 1
    s = _get().json()
    assert s["estado"] == "stale" and s["dados"] == ESPERADO and s["mensagem"] == funil_fontes.MENSAGENS["indisponivel"]
    n = len(api.hits)
    assert _get().json()["estado"] == "stale" and len(api.hits) == n  # backoff de 600 s: não martela a Graph


# ── pixel_id e token validados ANTES de qualquer chamada ───────────────────

@pytest.mark.parametrize("pid", ["12a", "../x", "1/2", "１２３", "1" * 21, "12 34", "-1", "1.5", "1?x=1", "%31", "٣٣"])
def test_pixel_id_fora_do_formato_e_recusado_sem_nenhuma_chamada(api, monkeypatch, pid):
    monkeypatch.setenv("META_PIXEL_ID", pid)
    _erro_fixo(_get().json(), "nao_encontrado")
    assert api.hits == [] and _KW == []


def test_pixel_id_com_espacos_nas_pontas_e_aparado_e_de_20_digitos_passa(api, monkeypatch):
    monkeypatch.setenv("META_PIXEL_ID", "  " + "9" * 20 + "\n")
    assert _get().json()["estado"] == "ok"
    assert api.hits[0]["path"] == "/v25.0/" + "9" * 20 + "/stats"


@pytest.mark.parametrize("ruim", ["ab cd", "ab\ncd", "ab\r\nX: y", "EAAB.é", "a;b", 'a"b', "a,b", "a%20b", "a" * 4097,
                                  '"' + TOKEN + '"', "Bearer " + TOKEN, "EAAB EAAB"])
def test_token_fora_do_formato_nunca_vai_ao_cabecalho_nem_ao_log(api, caplog, monkeypatch, ruim):
    monkeypatch.setenv("META_INSIGHTS_ACCESS_TOKEN", ruim)
    with caplog.at_level(logging.DEBUG):
        _erro_fixo(_get().json(), "auth")
    assert api.hits == [] and _KW == []
    assert ruim not in caplog.text
    assert any("token: formato recusado" in x.getMessage() for x in caplog.records)


@pytest.mark.parametrize("bom", ["a" * 4096, "EAAB.a-b_c~d+e/f==", TOKEN, "  " + TOKEN + "\n"])
def test_token_no_formato_de_bearer_passa_e_espacos_nas_pontas_sao_aparados(api, monkeypatch, bom):
    monkeypatch.setenv("META_INSIGHTS_ACCESS_TOKEN", bom)
    assert _get().json()["estado"] == "ok"
    assert api.hits[0]["auth"] == "Bearer " + bom.strip()


# ── Erros da Graph: HTTP + error.code ──────────────────────────────────────

@pytest.mark.parametrize("status,codigo,esperado", [
    (400, 190, "auth"), (401, 190, "auth"), (403, 190, "auth"),
    (400, 200, "permissao_meta"), (403, 200, "permissao_meta"), (400, 10, "permissao_meta"), (403, 10, "permissao_meta"),
    (400, 80004, "limite"), (400, 4, "limite"), (400, 17, "limite"), (400, 32, "limite"), (400, 613, "limite"),
    (403, 4, "limite"), (400, 100, "nao_encontrado"), (404, 100, "nao_encontrado"),
    (400, 2500, "indisponivel"), (400, 1, "indisponivel"), (400, 0, "indisponivel"), (403, 999999, "indisponivel"),
    (429, 190, "limite"), (429, 1, "limite"),  # 429: sempre `limite`, o corpo nem é lido
    (500, 190, "indisponivel"), (502, 4, "indisponivel"), (503, 100, "indisponivel"),  # 5xx: o código do corpo não conta
])
def test_codigo_de_erro_da_graph_vira_mensagem_fixa_sem_vazar(api, caplog, status, codigo, esperado):
    api.modo, api.status, api.codigo = "erro", status, codigo
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), esperado)
    sem_segredo(r.text, caplog.text)
    assert len(api.hits) == 1


@pytest.mark.parametrize("corpo", [b"", b"<html>x</html>", b"{}", b'{"error":{"message":"x"}}'])
@pytest.mark.parametrize("status,esperado", [(401, "auth"), (403, "permissao_meta"), (429, "limite"), (400, "indisponivel"),
                                             (404, "indisponivel"), (418, "indisponivel"), (500, "indisponivel"), (503, "indisponivel")])
def test_sem_codigo_legivel_o_status_decide(api, status, esperado, corpo):
    api.modo, api.status, api.bruto = "bruto", status, corpo
    _erro_fixo(_get().json(), esperado)


@pytest.mark.parametrize("status", [401, 403])
def test_codigo_legivel_mas_desconhecido_nao_cai_no_fallback_do_status(api, status):
    api.modo, api.status, api.codigo = "erro", status, 2500
    _erro_fixo(_get().json(), "indisponivel")


def test_mensagem_de_permissao_meta_orienta_com_hedge_sobre_qual_permissao():
    m = funil_fontes.MENSAGENS["permissao_meta"]
    assert "usuário do sistema" in m and "pixel" in m and "Business Manager" in m
    assert "provavelmente" in m and "leitura de anúncios" in m and "a permissão exata não foi confirmada" in m
    env = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "provavelmente a de leitura de anúncios; a permissão exata" in env.replace("\n# ", " ")
    assert not re.search(r"ads_read|ads_management|business_management|pixel_read", m)


@pytest.mark.parametrize("corpo", [
    b"", b"<html>502</html>", b"nao e json", b"[]", b'"190"', b"null", b"{}", b'{"error":null}', b'{"error":[]}',
    b'{"error":{}}', b'{"error":{"code":"190"}}', b'{"error":{"code":190.0}}', b'{"error":{"code":true}}',
    b'{"error":{"code":null}}', b'{"error":{"code":-190}}', b'{"error":{"code":' + b"9" * 400 + b'}}',
    b'{"error":{"code":1000000}}', b'{"error":{"message":"190"}}', b'{"code":190}', b"\xff\xfe\x00",
])
def test_erro_sem_codigo_inteiro_legivel_vira_indisponivel(api, corpo):
    api.modo, api.status, api.bruto = "bruto", 400, corpo
    _erro_fixo(_get().json(), "indisponivel")


def test_corpo_de_erro_so_entrega_o_inteiro_e_o_resto_nunca_sai(api, caplog, monkeypatch, capfd):
    api.modo, api.status, api.codigo = "erro", 400, 190
    with caplog.at_level(logging.DEBUG):
        r = _get()
    _erro_fixo(r.json(), "auth")
    out, err = capfd.readouterr()
    warns = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert all(not x.exc_info for x in warns)
    sem_segredo(r.text, caplog.text, out, err, repr(_system_event_logs(monkeypatch, warns)), json.dumps(_linha("meta") or "", default=str))
    linha = [x.getMessage() for x in warns if x.name == meta.__name__]
    assert linha == ["[funil_meta] HTTP 400 código 190"]  # status e o inteiro: nada mais do corpo
    assert meta._codigo(json.dumps({"error": {"message": VAZA, "code": 4, "fbtrace_id": "x"}}).encode()) == 4


@pytest.mark.parametrize("digitos", [7, 50, 4000, 4300])
def test_error_code_gigante_nao_e_logado_nem_vira_mensagem(api, caplog, digitos):
    api.modo, api.status = "bruto", 400
    api.bruto = b'{"error":{"code":' + b"9" * digitos + b"}}"
    with caplog.at_level(logging.DEBUG):
        _erro_fixo(_get().json(), "indisponivel")
    assert "9" * 7 not in caplog.text
    assert [x.getMessage() for x in caplog.records if x.name == meta.__name__ and x.levelno == logging.WARNING] == [
        "[funil_meta] HTTP 400 código None"]


def test_corpo_de_erro_tem_teto_de_64kb_em_stream(api):
    pad = "x" * 70000
    api.modo, api.status = "bruto", 400
    api.bruto = json.dumps({"error": {"code": 190, "pad": pad}}).encode()  # > 64 KB: o código nem é considerado
    _erro_fixo(_get().json(), "indisponivel")
    api.bruto = json.dumps({"error": {"code": 190, "pad": "x" * 60000}}).encode()  # < 64 KB: lido
    _envelhece("meta", falha_s=601)
    _erro_fixo(_get().json(), "auth")


def test_corpo_de_erro_gigante_para_no_teto_e_nao_baixa_tudo(api):
    api.modo = "erro_gigante"
    _erro_fixo(_get().json(), "indisponivel")
    assert len(api.hits) == 1


def test_redirecionamento_nao_e_seguido_e_o_bearer_nao_vaza_para_o_outro_host(api):
    mau = Graph()
    try:
        for codigo in (301, 302, 303, 307, 308):
            api.alvo, api.modo = mau.base, f"redir{codigo}"
            _erro_fixo(_get().json(), "indisponivel")
            assert mau.hits == []  # nem Bearer nem nada chegou ao host B
            _envelhece("meta", falha_s=601)  # fora do backoff: a próxima volta chama de novo
        assert _KW and all(kw["allow_redirects"] is False for kw in _KW)
    finally:
        mau.srv.shutdown()


# ── Teto de bytes e prazo total ────────────────────────────────────────────

def test_corpo_200_acima_do_teto_e_recusado_sem_baixar_tudo(api, monkeypatch):
    api.modo = "gigante"
    _erro_fixo(_get().json(), "resposta_invalida")
    assert len(api.hits) == 1


def test_corpo_exatamente_no_teto_passa_e_um_byte_a_mais_e_recusado(api, monkeypatch):
    tam = len(json.dumps(api.resp))
    monkeypatch.setattr(meta, "_MAX_BYTES", tam)
    assert _get().json()["estado"] == "ok"
    monkeypatch.setattr(meta, "_MAX_BYTES", tam - 1)
    _envelhece("meta", buscado_s=1801)
    s = _get().json()
    assert s["estado"] == "stale" and s["mensagem"] == funil_fontes.MENSAGENS["resposta_invalida"]


def test_bomba_gzip_e_medida_descomprimida(api, caplog):
    api.modo = "bomba_gzip"
    with caplog.at_level(logging.WARNING):
        _erro_fixo(_get().json(), "resposta_invalida")
    # o motivo é o TETO (corpo descomprimido), não o JSON cru do gzip ser ilegível
    assert any("corpo acima do teto" in x.getMessage() for x in caplog.records)


@pytest.mark.parametrize("modo", ["gzip_ok", "chunked_gzip"])
def test_resposta_legitima_com_gzip_e_com_chunked_gzip_parseia_e_da_ok(api, modo):
    api.modo = modo
    j = _get().json()
    assert j["estado"] == "ok" and j["dados"] == ESPERADO


@pytest.mark.parametrize("modo", ["gzip_ok", "chunked_gzip"])
def test_teto_de_bytes_vale_para_o_descomprimido_de_resposta_legitima(api, monkeypatch, modo):
    monkeypatch.setattr(meta, "_MAX_BYTES", len(json.dumps(api.resp)) - 1)  # o gzip em si é bem menor que o teto
    api.modo = modo
    _erro_fixo(_get().json(), "resposta_invalida")


# corpo que para / é cortado / é resetado no meio: chama `_buscar` direto (a infra também converteria uma
# exceção solta em `indisponivel`, o que esconderia o ramo urllib3 do módulo)
def _codigo_de():
    with pytest.raises(funil_fontes.FonteErro) as e:
        meta._buscar()
    assert e.value.__cause__ is None
    return e.value.codigo


def test_servidor_real_que_para_no_meio_do_corpo_vira_timeout_pelo_ramo_do_urllib3(api, monkeypatch):
    monkeypatch.setattr(meta, "_PRAZO_S", 0.5)
    api.modo = "para_no_meio"
    t0 = time.monotonic()
    assert _codigo_de() == "timeout" and time.monotonic() - t0 < 2.5


@pytest.mark.parametrize("modo", ["cortado", "reset"])
def test_corpo_cortado_ou_resetado_no_meio_vira_indisponivel(api, modo):
    api.modo = modo
    assert _codigo_de() == "indisponivel"


@pytest.mark.parametrize("modo", ["gotejo", "gotejo_erro"])
def test_goteio_termina_no_prazo_total_com_timeout(api, monkeypatch, modo):
    monkeypatch.setattr(meta, "_PRAZO_TOTAL_S", 1.0)
    api.modo, api.gota, api.n_gotas = modo, 0.3, 20  # sem prazo total o corpo só acabaria em ~6 s
    t0 = time.monotonic()
    _erro_fixo(_get().json(), "timeout")
    assert time.monotonic() - t0 < 4
    assert "meta" not in funil_fontes._EM_VOO


def test_timeout_real_vira_timeout_e_recusa_de_conexao_indisponivel(api, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout(TOKEN)))
    _erro_fixo(_get().json(), "timeout")
    monkeypatch.setattr(requests, "get", _get_so_local)
    monkeypatch.setattr(meta, "_URL", "http://127.0.0.1:1/{}")
    _envelhece("meta", falha_s=601)
    _erro_fixo(_get().json(), "indisponivel")


@pytest.mark.parametrize("corpo", [b"nao e json", b"", b"[]", b'"x"', b"null", b"1", b"[" * 200000 + b"]" * 200000, b"\xff\xfe"])
def test_200_com_corpo_ilegivel_ou_que_nao_e_objeto_e_resposta_invalida(api, corpo):
    api.modo, api.status, api.bruto = "bruto", 200, corpo
    _erro_fixo(_get().json(), "resposta_invalida")


@pytest.mark.parametrize("status", [201, 202, 204, 304])
def test_status_2xx_ou_3xx_diferente_de_200_e_indisponivel(api, status):
    api.modo, api.status, api.bruto = "bruto", status, b""
    _erro_fixo(_get().json(), "indisponivel")


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
    linha = _linha("meta")
    sem_segredo(json.dumps(linha, default=str) if linha else "")
    mem = repr({k: v for k, v in vars(meta).items() if k != "__doc__"})  # o docstring cita "Authorization"
    assert TOKEN not in mem and TOKEN not in repr(meta.FONTE) and TOKEN not in repr(vars(meta._buscar))


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


@pytest.mark.parametrize("modo,status", [("ok", 200), ("erro", 400), ("erro", 401), ("erro", 403), ("erro", 429), ("erro", 500),
                                         ("bruto", 502), ("bruto", 200)])
def test_respostas_com_o_token_no_corpo_nao_vazam(api, caplog, monkeypatch, capfd, modo, status):
    api.modo, api.status = modo, status
    api.bruto = f"<html>{VAZA} {TOKEN}</html>".encode()
    with caplog.at_level(logging.DEBUG):  # inclui urllib3/requests em DEBUG
        r = _get()
    if modo == "ok":
        assert any(x.name.startswith("urllib3") for x in caplog.records)  # o DEBUG de verdade foi capturado
    _saidas(r, caplog, monkeypatch, capfd)


@pytest.mark.parametrize("cenario", ["token_ruim", "pixel_ruim", "http_500", "http_429", "erro_190", "html", "lista", "rede", "timeout"])
def test_erro_da_fonte_nao_carrega_cadeia_de_excecoes(api, monkeypatch, cenario):
    if cenario == "token_ruim":
        monkeypatch.setenv("META_INSIGHTS_ACCESS_TOKEN", "a b")
    elif cenario == "pixel_ruim":
        monkeypatch.setenv("META_PIXEL_ID", "x")
    elif cenario in ("http_500", "http_429"):
        api.modo, api.status, api.codigo = "erro", int(cenario[5:]), 1
    elif cenario == "erro_190":
        api.modo, api.status, api.codigo = "erro", 400, 190
    elif cenario == "html":
        api.modo, api.status, api.bruto = "bruto", 200, f"<html>{TOKEN}</html>".encode()
    elif cenario == "lista":
        api.modo, api.status, api.bruto = "bruto", 200, b"[]"
    elif cenario == "rede":
        monkeypatch.setattr(meta, "_URL", "http://127.0.0.1:1/{}")
    else:
        monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout(TOKEN)))
    with pytest.raises(funil_fontes.FonteErro) as e:
        meta._buscar()
    x = e.value
    assert x.__cause__ is None and (x.__suppress_context__ is True or x.__context__ is None)
    assert TOKEN not in repr(x) and TOKEN not in str(x)


def test_token_nao_fica_em_atributo_de_modulo_nem_em_cache_depois_de_uma_busca(api):
    assert _get().json()["estado"] == "ok"
    for k, v in vars(meta).items():
        if k != "__doc__":
            assert TOKEN not in repr(v) and TOKEN_CAPI not in repr(v), k
    sem_segredo(json.dumps(_linha("meta"), default=str))


def test_o_modulo_nunca_le_o_token_da_conversions_api():
    codigo = (RAIZ / "core" / "funil_fonte_meta.py").read_text(encoding="utf-8")
    sem_docstring = codigo.split('"""', 2)[2]
    assert "META_PIXEL_ACCESS_TOKEN" not in sem_docstring and "meta_capi" not in sem_docstring
    assert "access_token" not in sem_docstring.lower().replace("insights_access_token", "")


def test_faltam_que_levanta_com_o_token_vira_erro_generico_sem_vazar(monkeypatch, caplog, capfd):
    from dataclasses import replace
    quebrada = replace(meta.FONTE, faltam=lambda: (_ for _ in ()).throw(RuntimeError(TOKEN)))
    monkeypatch.setattr(funil_fontes, "fonte", lambda n: quebrada)
    with caplog.at_level(logging.DEBUG):
        r = _get()
    out, err = capfd.readouterr()
    j = r.json()
    assert j["estado"] == "erro" and j["mensagem"] == funil_fontes.MENSAGENS["indisponivel"] and set(j) == ENVELOPE
    for texto in (r.text, caplog.text, out, err):
        assert TOKEN not in texto


# ── Parser tolerante (sem HTTP) ────────────────────────────────────────────

def _zeros(**ev):
    return {e: ev.get(e, 0) for e in meta.EVENTOS}


def test_forma_a_resultados_com_data_aninhada():
    assert meta._dados(FORMA_A) == ESPERADO


def test_forma_b_entradas_direto_em_data():
    d = meta._dados({"data": [{"value": "PageView", "count": 10}, {"value": "Purchase", "count": 1}]})
    assert d == {"eventos": _zeros(PageView=10, Purchase=1), "reconhecido": True}


def test_as_duas_formas_juntas_e_o_paging_extra_sao_tolerados():
    d = meta._dados({"data": [_res(("PageView", 5)), {"value": "PageView", "count": 7}], "paging": {"cursors": {}}})
    assert d["eventos"]["PageView"] == 12 and d["reconhecido"] is True


@pytest.mark.parametrize("nome,cont", [("value", "count"), ("Value", "Count"), ("VALUE", "COUNT"), ("event", "eventCount"),
                                       ("event_name", "event_count"), ("eventName", "total"), ("Event Name", "Total"),
                                       ("event-name", "event-count")])
def test_chaves_em_variantes_normalizadas(nome, cont):
    d = meta._dados({"data": [{nome: "StartTrial", cont: 4}]})
    assert d == {"eventos": _zeros(StartTrial=4), "reconhecido": True}


def test_soma_de_buckets_do_mesmo_evento():
    d = meta._dados({"data": [_res(("PageView", 100), ("Purchase", 1)), _res(("PageView", 50)), _res(("PageView", "25"))]})
    assert d["eventos"]["PageView"] == 175 and d["eventos"]["Purchase"] == 1


def test_evento_fora_da_lista_e_descartado_e_nome_e_exato():
    d = meta._dados({"data": [_res(("PageView", 1), ("pageview", 50), ("Page View", 50), ("PageView ", 50), ("Lead", 9),
                                   ("<script>alert(1)</script>", 3), ("__proto__", 3), ("constructor", 3))]})
    assert d["eventos"] == _zeros(PageView=1) and set(d["eventos"]) == set(meta.EVENTOS)
    assert "Lead" not in json.dumps(d) and "script" not in json.dumps(d)


@pytest.mark.parametrize("ruim", ["abc", "", "  ", -1, "-5", True, False, 1e15, 10 ** 15, "1e15", float("nan"), float("inf"),
                                  "nan", "inf", None, [], {}, [1], {"n": 1}, 10 ** 400, "9" * 400, float("-inf")])
def test_contagem_invalida_ignora_a_entrada(ruim):
    d = meta._dados({"data": [_res(("PageView", ruim), ("Purchase", 3))]})
    assert d["eventos"] == _zeros(Purchase=3) and d["reconhecido"] is True


@pytest.mark.parametrize("bom,esperado", [(0, 0), (5, 5), ("12", 12), (12.0, 12), ("7.9", 7), (999999999999999, 999999999999999), (" 8 ", 8)])
def test_contagem_valida_vira_int(bom, esperado):
    d = meta._dados({"data": [_res(("PageView", bom), ("Purchase", 1))]})
    assert d["eventos"]["PageView"] == esperado and isinstance(d["eventos"]["PageView"], int)


@pytest.mark.parametrize("nome", [None, 5, ["PageView"], {"a": 1}, b"PageView", True, 1.5])
def test_nome_que_nao_e_texto_ignora_a_entrada(nome):
    d = meta._dados({"data": [{"value": nome, "count": 5}, {"value": "Purchase", "count": 1}]})
    assert d["eventos"] == _zeros(Purchase=1)


@pytest.mark.parametrize("resp", [{"data": []}, {"data": [_res()]}, {"data": [{"data": []}, {"data": []}]}, {"data": [], "paging": {"next": "x"}}])
def test_data_lista_vazia_e_sem_disparos_e_formato_reconhecido(caplog, resp):
    with caplog.at_level(logging.WARNING):
        d = meta._dados(resp)
    assert d == {"eventos": _zeros(), "reconhecido": True} and _log_formato(caplog) == []


@pytest.mark.parametrize("resp", [
    {"data": [{"foo": "PageView", "bar": 5}]},                       # entradas sem as chaves conhecidas
    {"data": [{"value": "Lead", "count": 5}]},                       # forma ok, mas NENHUM evento da lista
    {"data": [{"value": "PageView", "count": "muito"}]},             # contagem ilegível
    {"data": [{"value": "PageView"}]},                               # sem contagem
    {"data": {"PageView": 5}},                                       # `data` que não é lista
    {"data": "PageView"}, {"data": 5}, {"data": [1, "x", None]},     # `data` com lixo no lugar dos resultados
    {"data": [{"aggregation": "event_total_counts", "start_time": "x"}]},  # resultado sem `data`
    # 200 que não é a resposta esperada: `data` ausente/null nunca vale como "sem disparos"
    {}, {"success": False}, {"error": {"code": 190}}, {"events": [{"value": "PageView", "count": 5}]}, {"data": None}, {"paging": {}},
    # lixo DENTRO da `data` interna (nenhum evento lido): não é "vazio"
    {"data": [{"data": [1, "x"]}]}, {"data": [{"data": ["PageView"]}]}, {"data": [{"data": [["PageView", 5]]}]},
    {"data": [{"data": [None, 5, "x"]}]}, {"data": [{"data": []}, {"data": [None]}]}, {"data": [[]]},
])
def test_resposta_sem_nenhum_evento_da_lista_e_sem_forma_de_vazio_nao_reconhece_e_loga_so_nomes(caplog, resp):
    with caplog.at_level(logging.WARNING):
        d = meta._dados(resp)
    assert d["eventos"] == _zeros() and d["reconhecido"] is False and len(_log_formato(caplog)) == 1


def test_um_evento_da_lista_lido_basta_mesmo_com_lixo_ao_lado():
    d = meta._dados({"data": [{"data": [1, {"value": "Purchase", "count": 2}, "x"]}, 7]})
    assert d == {"eventos": _zeros(Purchase=2), "reconhecido": True}


@pytest.mark.parametrize("corpo", [b"{}", b'{"success":false}', b'{"error":{"code":190}}', b'{"events":[{"value":"PageView","count":5}]}',
                                   b'{"data":null}'])
def test_200_que_nao_e_a_resposta_esperada_pela_rota_vira_ok_nao_reconhecido(api, caplog, corpo):
    api.modo, api.status, api.bruto = "bruto", 200, corpo
    with caplog.at_level(logging.WARNING):
        j = _get().json()
    assert j["estado"] == "ok" and j["dados"] == {"eventos": _zeros(), "reconhecido": False}
    assert len(_log_formato(caplog)) == 1


def test_soma_de_buckets_para_em_1e15():
    d = meta._dados({"data": [_res(*[("PageView", 999999999999999)] * 10000)]})
    assert d["eventos"]["PageView"] == 10 ** 15 and d["reconhecido"] is True
    d = meta._dados({"data": [_res(("PageView", 999999999999999), ("PageView", 5))]})
    assert d["eventos"]["PageView"] == 10 ** 15
    assert funil_fontes._validar(d)["eventos"]["PageView"] == 10 ** 15


def test_linha_de_formato_tem_so_nomes_nunca_valor_id_ou_contagem(api, caplog, monkeypatch, capfd):
    api.resp = {"data": [{"valor_estranho": "ana@exemplo.com", "contagem": 7654321,
                          "https://x.test/?t=SEGREDO": 1, "cus_ABC1234567": "x", "Pixel": PID}], "fbtrace_id": "fbtrace_ID_SECRETO"}
    with caplog.at_level(logging.DEBUG):
        r = _get()
    assert r.json()["estado"] == "ok" and r.json()["dados"]["reconhecido"] is False
    [linha] = _log_formato(caplog)
    assert linha.startswith("[funil_meta] formato: chaves=[")
    for fora in ("ana@exemplo.com", "7654321", "SEGREDO", "cus_ABC1234567", PID, "fbtrace_ID_SECRETO", "https://", TOKEN):
        assert fora not in linha and fora not in r.text, fora
    assert "valor_estranho" in linha and "contagem" in linha and "(nome fora do padrão)" in linha
    out, err = capfd.readouterr()
    sem_segredo(out, err, repr(_system_event_logs(monkeypatch, [x for x in caplog.records if x.levelno >= logging.WARNING])))


def test_formato_reconhecido_nao_gera_linha_de_diagnostico(api, caplog):
    with caplog.at_level(logging.DEBUG):
        _get()
    assert _log_formato(caplog) == []


def test_nomes_no_log_sao_allowlist_limitados_e_sem_dado():
    for v in ("ana@exemplo.com", "cus_ABC123", "x" * 41, "1234", "a/b", "", "é", "7abc", "Bearer EAAB.xyz", None, 5, b"a"):
        assert meta._nome(v) == "(nome fora do padrão)", v
    for v in ("value", "count", "event_name", "Event Name", "total", "A1"):
        assert meta._nome(v) == v


def test_log_de_formato_limita_a_quantidade_de_nomes(caplog):
    resp = {"data": [{f"chave{chr(97 + i % 26)}{chr(97 + i // 26)}": 1 for i in range(200)}]}
    with caplog.at_level(logging.WARNING):
        meta._dados(resp)
    [linha] = _log_formato(caplog)
    assert linha.count("'") // 2 <= 30


def test_dados_passam_pelo_validar_da_infra():
    assert funil_fontes._validar(meta._dados(FORMA_A)) == meta._dados(FORMA_A)
    assert set(meta._dados({"data": []})) == {"eventos", "reconhecido"}


# ── SQL do doc para liberar um payload cacheado ────────────────────────────

def test_sql_do_doc_para_liberar_payload_cacheado_forca_nova_busca_e_guarda_o_dado(api):
    import re as _re
    from db import get_conn
    doc = (RAIZ / "docs" / "CLAUDE.md").read_text(encoding="utf-8")
    [sql] = _re.findall(r"`(update funil_fontes_cache set buscado_em[^`]*where fonte = 'meta')`", doc)
    api.resp = {"data": []}
    assert _get().json()["dados"]["reconhecido"] is True and len(api.hits) == 1
    assert _get().json()["estado"] == "ok" and len(api.hits) == 1  # dentro do TTL: servido do cache
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            assert cur.rowcount == 1
        conn.commit()
    api.resp = FORMA_A
    assert _get().json()["dados"] == ESPERADO and len(api.hits) == 2  # o `update` de fato liberou a nova busca
    assert "delete" not in sql.lower()


# ── Lista de eventos == o que o app dispara ────────────────────────────────

def _disparados():
    achados = set()
    pix = re.compile(r"""fbq\(\s*['"]track(?:Custom)?['"]\s*,\s*['"]([A-Za-z]+)['"]""")
    arquivos = [p for ext in ("*.html", "*.js", "*.py") for p in (RAIZ / "frontend").rglob(ext)
                if "node_modules" not in p.parts and "webapp" not in p.parts]
    for p in arquivos:
        achados |= set(pix.findall(p.read_text(encoding="utf-8", errors="ignore")))
    capi = re.compile(r"""event_name\s*=\s*['"]([A-Z][A-Za-z]+)['"]|_ev_name,\s*_ev_id\s*=\s*['"]([A-Z][A-Za-z]+)['"]""")
    for p in [*(RAIZ / "core").rglob("*.py"), *(RAIZ / "frontend").rglob("*.py")]:
        for a, b in capi.findall(p.read_text(encoding="utf-8", errors="ignore")):
            achados.add(a or b)
    return achados


def test_lista_de_eventos_e_exatamente_a_que_o_codigo_dispara():
    achados = _disparados()
    assert len(achados) >= 7
    assert set(meta.EVENTOS) == achados, (sorted(set(meta.EVENTOS) - achados), sorted(achados - set(meta.EVENTOS)))
    assert len(set(meta.EVENTOS)) == len(meta.EVENTOS)
