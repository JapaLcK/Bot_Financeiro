"""tests/test_client_ip.py — IP real atrás de Cloudflare → Railway (#766, PR 1).

Os números de linha (`linha N`) são os da tabela de estados do plano da #766.
Requisições montadas de scope real do Starlette (o `getlist` é o de verdade).
"""
from __future__ import annotations

import ipaddress
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.websockets import WebSocket

import core.client_ip as cip
from core.client_ip import CLOUDFLARE_FAIXAS, client_ip, rate_limit_key

P = "100.64.3.7"          # proxy do Railway (peer)
C = "172.64.1.2"          # borda da Cloudflare
V = "200.150.10.20"       # visitante
W = "187.1.2.3"
X = "203.0.113.9"         # fora da Cloudflare
A = "198.51.100.5"        # atacante batendo direto no Railway
V6 = "2804:14C:1A2:3B4:0:0:0:8"


def _scope(peer, headers, tipo="http"):
    return {
        "type": tipo, "path": "/", "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in headers],
        "client": (peer, 1234) if peer else None,
    }


def _req(peer, *headers):
    return Request(_scope(peer, headers))


class _ThreadFalsa:
    def __init__(self, gravadas, **kw):
        self._gravadas, self.kw = gravadas, kw

    def start(self):
        self._gravadas.append(self.kw)


@pytest.fixture(autouse=True)
def gravadas(monkeypatch):
    """Sonda zerada e thread substituída: cada `start()` vira um item da lista."""
    lista: list[dict] = []
    monkeypatch.setattr(cip, "_SONDA_VISTAS", set())
    monkeypatch.setattr(cip, "Thread", lambda **kw: _ThreadFalsa(lista, **kw))
    return lista


# ---- A. tabela de estados -------------------------------------------------

XFF, CF = "x-forwarded-for", "cf-connecting-ip"

CASOS = [
    ("1 CF v4 válido", P, [(XFF, C), (CF, V)], V, V),
    ("2 CF IPv6", P, [(XFF, C), (CF, V6)], "2804:14c:1a2:3b4::8", "2804:14c:1a2:3b4::/64"),
    ("3 CF IPv4 mapeado", P, [(XFF, C), (CF, "::ffff:" + V)], V, V),
    ("4 CF com espaços", P, [(XFF, C), (CF, f"  {V} ")], V, V),
    ("5 CF abc", P, [(XFF, C), (CF, "abc")], C, C),
    ("5 CF vazio", P, [(XFF, C), (CF, "")], C, C),
    ("5 CF com porta", P, [(XFF, C), (CF, "1.2.3.4:80")], C, C),
    ("6 CF duplicado", P, [(XFF, C), (CF, V), (CF, W)], C, C),
    ("7 CF lista", P, [(XFF, C), (CF, f"{V}, {W}")], C, C),
    ("8 XFF X, C", P, [(XFF, f"{X}, {C}"), (CF, V)], V, V),
    ("9 XFF C, X", P, [(XFF, f"{C}, {X}"), (CF, V)], X, X),
    ("9 XFF em dois cabeçalhos", P, [(XFF, C), (XFF, X), (CF, V)], X, X),
    ("10 última inválida", P, [(XFF, f"{C}, lixo"), (CF, V)], P, P),
    ("11 direto no Railway, CF forjado", P, [(XFF, A), (CF, V)], A, A),
    ("12 direto sem CF", P, [(XFF, A)], A, A),
    ("13 sem XFF", P, [(CF, V)], P, P),
    ("14 peer fora do Railway", "203.0.113.50", [(XFF, C), (CF, V)], "203.0.113.50", "203.0.113.50"),
    ("16 TestClient", "testclient", [(XFF, C), (CF, V)], "testclient", "testclient"),
]


@pytest.mark.parametrize("nome,peer,headers,ip,chave", CASOS, ids=[c[0] for c in CASOS])
def test_tabela_de_estados(nome, peer, headers, ip, chave):
    assert client_ip(_req(peer, *headers)) == ip
    assert rate_limit_key(_req(peer, *headers)) == chave


NAO_GLOBAIS = ["127.0.0.1", "10.0.0.1", "::1", "169.254.1.1", "100.64.0.1",
               "192.0.2.5", "240.0.0.1", "fe80::1%eth0", V6 + "%eth0"]


@pytest.mark.parametrize("cf", NAO_GLOBAIS)
def test_linha_5_cf_nao_global_ou_com_zona_vale_a_conexao(cf):
    assert client_ip(_req(P, (XFF, C), (CF, cf))) == C
    assert rate_limit_key(_req(P, (XFF, C), (CF, cf))) == C


# `is_global` diz True para todos (Python 3.13.2), exceto 64:ff9b:1:: — na lista
# para não depender da versão. Multicast nunca é origem; os outros embutem um IPv4.
GLOBAIS_QUE_NAO_SAO_CLIENTE = [
    "224.0.0.1", "239.255.255.255", "ff02::1", "ff00::",       # multicast
    "64:ff9b::7f00:1", "64:ff9b::6440:1", "64:ff9b::808:808",  # NAT64, RFC 6052
    "64:ff9b:1::808:808",                                      # NAT64 local, RFC 8215
    "::127.0.0.1", "::8.8.8.8",                                # IPv4-compatível ::/96
    "64:ff9b::c896:a14", "::200.150.10.20",  # IPv4 ≥ 128.0.0.0: pega o /96 virar /97
]


@pytest.mark.parametrize("cf", GLOBAIS_QUE_NAO_SAO_CLIENTE)
def test_cf_multicast_ou_que_embute_ipv4_vale_a_conexao(cf, gravadas):
    assert client_ip(_req(P, (XFF, C), (CF, cf))) == C
    assert rate_limit_key(_req(P, (XFF, C), (CF, cf))) == C
    assert _details(gravadas)[0]["cf_global"] is False  # sonda: cf_global=false ⇔ fonte=conexao


@pytest.mark.parametrize("cf,ip,chave", [
    (V, V, V),
    ("2606:4700:4700::1111", "2606:4700:4700::1111", "2606:4700:4700::/64"),
    ("64:ff9a::1", "64:ff9a::1", "64:ff9a::/64"),              # vizinho do NAT64, fora do /96
    ("64:ff9b:0:0:1::1", "64:ff9b::1:0:0:1", "64:ff9b::/64"),  # 64:ff9b::/64 fora do /96
    ("::1:0:0:1", "::1:0:0:1", "::/64"),                       # ::/64 fora do ::/96
])
def test_linha_1_cf_global_continua_valendo(cf, ip, chave):
    assert client_ip(_req(P, (XFF, C), (CF, cf))) == ip
    assert rate_limit_key(_req(P, (XFF, C), (CF, cf))) == chave


@pytest.mark.parametrize("peer,dentro", [
    ("100.64.0.0", True), ("100.100.0.1", True), ("100.127.255.255", True),
    ("100.63.255.255", False), ("100.128.0.0", False),
])
def test_borda_do_proxy_railway(peer, dentro):
    """Mata os mutantes /11 (100.64–100.95: 100.100 e 100.127 caem fora) e
    /9 (100.0–100.127, ou import quebrado com strict: 100.63 entra)."""
    assert client_ip(_req(peer, (XFF, C), (CF, V))) == (V if dentro else peer)


def test_linha_15_sem_client():
    assert client_ip(_req(None, (XFF, C), (CF, V))) is None
    assert rate_limit_key(_req(None, (XFF, C), (CF, V))) == "127.0.0.1"
    assert client_ip(None) is None


def test_linha_17_websocket_igual_ao_http():
    async def _nada():
        return {}
    ws = WebSocket(_scope(P, [(XFF, C), (CF, V)], tipo="websocket"), _nada, _nada)
    assert client_ip(ws) == V
    ws6 = WebSocket(_scope(P, [(XFF, C), (CF, V6)], tipo="websocket"), _nada, _nada)
    assert rate_limit_key(ws6) == "2804:14c:1a2:3b4::/64"


def test_objeto_sem_headers_e_com_dict():
    """`tests/test_auth_cookie.py` passa SimpleNamespace sem `.headers`; o
    `_FakeRequest` de `tests/test_audit.py` passa um dict sem `getlist`."""
    sem = SimpleNamespace(client=SimpleNamespace(host=P))
    assert client_ip(sem) == P and rate_limit_key(sem) == P
    fora = SimpleNamespace(client=SimpleNamespace(host="203.0.113.50"))
    assert client_ip(fora) == "203.0.113.50"
    com_dict = SimpleNamespace(client=SimpleNamespace(host=P), headers={XFF: C, CF: V})
    assert client_ip(com_dict) == V


def test_ipv6_do_proxy_nao_e_railway_e_peer_ipv6_vira_rede():
    assert client_ip(_req("2001:db8::1", (XFF, C), (CF, V))) == "2001:db8::1"
    assert rate_limit_key(_req("2001:db8::1")) == "2001:db8::/64"


def test_faixas_da_cloudflare():
    assert len([f for f in CLOUDFLARE_FAIXAS if f.version == 4]) == 15
    assert len([f for f in CLOUDFLARE_FAIXAS if f.version == 6]) == 7
    for f in CLOUDFLARE_FAIXAS:  # strict: endereço de rede certo, sem bit de host
        assert ipaddress.ip_network(str(f), strict=True) == f
    for medida in ("172.64.0.0/13", "104.16.0.0/13", "162.158.0.0/15",
                   "198.41.128.0/17", "108.162.192.0/18"):
        assert ipaddress.ip_network(medida) in CLOUDFLARE_FAIXAS, medida
    for fora in ("8.8.8.8", "100.64.0.1", "203.0.113.9"):
        assert not any(ipaddress.ip_address(fora) in f for f in CLOUDFLARE_FAIXAS), fora


# ---- B. sonda --------------------------------------------------------------

CHAVES = {"xff_entradas", "conexao_cf", "cf_presente", "cf_valido", "cf_global",
          "cf_igual_conexao", "xri_igual_conexao", "fonte"}


def _details(gravadas):
    return [g["kwargs"]["details"] for g in gravadas]


def test_sonda_grava_uma_vez_por_combinacao(gravadas):
    req = lambda: _req(P, (XFF, C), (CF, V), ("x-real-ip", C))  # noqa: E731
    client_ip(req())
    client_ip(req())
    assert len(gravadas) == 1
    g = gravadas[0]
    assert g["target"] is cip.log_system_event_sync and g["daemon"] is True
    assert g["args"] == ("info", "client_ip_sonda", "sonda do IP real (#766)")
    assert g["kwargs"]["source"] == "core.client_ip" and g["kwargs"]["user_id"] is None
    assert _details(gravadas)[0] == {
        "xff_entradas": 1, "conexao_cf": True, "cf_presente": True, "cf_valido": True,
        "cf_global": True, "cf_igual_conexao": False, "xri_igual_conexao": True,
        "fonte": "cf"}

    client_ip(_req(P, (XFF, A), (CF, V)))  # alarme: CF forjado direto no Railway
    assert len(gravadas) == 2
    d = _details(gravadas)[1]
    assert (d["conexao_cf"], d["cf_presente"], d["fonte"]) == (False, True, "conexao")

    client_ip(_req(P, (XFF, C), (CF, "240.0.0.1")))  # Pseudo-IPv4: válido, não global
    assert len(gravadas) == 3
    d = _details(gravadas)[2]
    assert (d["conexao_cf"], d["cf_valido"], d["cf_global"], d["fonte"]) == (True, True, False, "conexao")


def test_sonda_nao_leva_ip_nem_cabecalho(gravadas):
    ua = "Mozilla/5.0 PigTeste"
    casos = [
        _req(P, (XFF, f"{X}, {C}"), (CF, V), ("x-real-ip", C), ("user-agent", ua)),
        _req(P, (XFF, f"{C}, {X}, {W}"), (CF, V6), ("user-agent", ua)),
        _req(P, (XFF, A), (CF, V)),
        _req(P),
    ]
    for r in casos:
        client_ip(r)
    assert len(gravadas) == 4
    proibidos = [P, C, V, W, X, A, V6, V6.lower(), "2804:14c", ua, "PigTeste"]
    for d in _details(gravadas):
        assert set(d) == CHAVES
        texto = json.dumps(d)
        for p in proibidos:
            assert p not in texto, (p, texto)
        assert d["xff_entradas"] in (0, 1, 2, "3+")
        assert d["fonte"] in ("cf", "conexao", "peer")


def test_sonda_so_dispara_com_peer_do_railway(gravadas):
    for peer in ("testclient", "127.0.0.1", "203.0.113.50", "2001:db8::1"):
        client_ip(_req(peer, (XFF, C), (CF, V)))
    assert gravadas == []


def test_falha_da_sonda_nao_quebra_client_ip(monkeypatch):
    def _explode(**kw):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(cip, "Thread", _explode)
    assert client_ip(_req(P, (XFF, C), (CF, V))) == V
