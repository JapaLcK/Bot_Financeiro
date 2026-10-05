"""tests/test_client_ip_segredo.py — âncora por cabeçalho secreto (#766, PR A).

`linha N` = tabela de estados do plano v2 da #766. E1 = `CLOUDFLARE_ORIGIN_SECRET`
válida; E0 = ausente ou curta. "XFF prod" = `C, R` (medido em produção: 1ª entrada
da Cloudflare, última um IP público de infra). PAR: todo caso que dá o visitante
com E1 roda também com E0 — se E0 der o mesmo, o caso não mede a env.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocket

import core.client_ip as cip
from core.client_ip import SEGREDO_CABECALHO as SEG, client_ip, rate_limit_key
from tests.test_client_ip import (  # noqa: F401 — `gravadas` é a fixture autouse (E0)
    A, C, CF, P, V, V6, XFF, XRI, _details, _req, _scope, gravadas,
)

R = "203.0.113.9"               # última entrada do XFF em produção (infra)
PROD = (XFF, f"{C}, {R}")
S = "0123456789abcdef" * 4      # o que `openssl rand -hex 32` produz (64 chars)
S2 = "fedcba9876543210" * 4     # segredo errado, mesmo comprimento
CURTA = "a" * 31
V6C, V6_64 = "2804:14c:1a2:3b4::8", "2804:14c:1a2:3b4::/64"
WORKER = "104.16.0.1"           # CF-Connecting-IP de Worker de terceiro (linha 15)


@pytest.fixture(autouse=True)
def _aviso_zerado(monkeypatch):
    monkeypatch.setattr(cip, "_AVISOU_SEGREDO_CURTO", False)


def _com_env(monkeypatch, env, peer, *headers):
    if env is None:
        monkeypatch.delenv(cip.SEGREDO_ENV, raising=False)
    else:
        monkeypatch.setenv(cip.SEGREDO_ENV, env)
    return client_ip(_req(peer, *headers))


# ---- E. tabela do segredo ----------------------------------------------------
# (nome, peer, headers, com E1, com E0)
CASOS = [
    ("1 S, CF V, XFF prod", P, [PROD, (SEG, S), (CF, V)], V, R),
    ("2 S, CF V6", P, [PROD, (SEG, S), (CF, V6)], V6C, R),
    ("3 S, CF ausente", P, [PROD, (SEG, S)], R, R),
    ("5 S, CF V, sem XFF", P, [(SEG, S), (CF, V)], V, P),
    ("6/24 S errado", P, [PROD, (SEG, S2), (CF, V)], R, R),
    ("7 segredo ausente", P, [PROD, (CF, V)], R, R),
    ("8 duplicado S,S", P, [PROD, (SEG, S), (SEG, S), (CF, V)], R, R),
    ("8 duplicado S,S'", P, [PROD, (SEG, S), (SEG, S2), (CF, V)], R, R),
    ("8 duplicado S',S", P, [PROD, (SEG, S2), (SEG, S), (CF, V)], R, R),
    ("9 segredo vazio", P, [PROD, (SEG, ""), (CF, V)], R, R),
    ("10 não-ASCII", P, [PROD, (SEG, S[:-1] + "é"), (CF, V)], R, R),
    ("11 maiúsculas", P, [PROD, (SEG, S.upper()), (CF, V)], R, R),
    ("11 +1 char", P, [PROD, (SEG, S + "0"), (CF, V)], R, R),
    ("11 -1 char", P, [PROD, (SEG, S[:-1]), (CF, V)], R, R),
    ("11 espaço antes (sem strip)", P, [PROD, (SEG, " " + S), (CF, V)], R, R),
    ("11 espaço depois (sem strip)", P, [PROD, (SEG, S + " "), (CF, V)], R, R),
    ("12 sem segredo, conexão da CF", P, [(XFF, C), (CF, V)], C, V),
    ("13 S' forjado, direto", P, [(XFF, A), (SEG, S2), (CF, V)], A, A),
    ("14 S vazado, direto", P, [(XFF, A), (SEG, S), (CF, V)], V, A),
    ("15 Worker de terceiro", P, [PROD, (SEG, S), (CF, WORKER)], WORKER, R),
    ("16 regra em Adicionar", P, [PROD, (SEG, "do-visitante"), (SEG, S), (CF, V)], R, R),
    ("19 última do XFF inválida", P, [(XFF, f"{C}, lixo"), (SEG, S), (CF, V)], V, P),
    ("20 peer fora do Railway", "203.0.113.50", [PROD, (SEG, S), (CF, V)], "203.0.113.50", "203.0.113.50"),
    ("22 TestClient", "testclient", [PROD, (SEG, S), (CF, V)], "testclient", "testclient"),
]
IDS = [c[0] for c in CASOS]


@pytest.mark.parametrize("nome,peer,headers,e1,e0", CASOS, ids=IDS)
def test_com_env_valida(nome, peer, headers, e1, e0, monkeypatch):
    assert _com_env(monkeypatch, S, peer, *headers) == e1


@pytest.mark.parametrize("env", [None, CURTA, "   "], ids=["ausente", "curta", "brancos"])
@pytest.mark.parametrize("nome,peer,headers,e1,e0", CASOS, ids=IDS)
def test_sem_env_ou_curta_e_o_de_hoje(nome, peer, headers, e1, e0, env, monkeypatch):
    assert _com_env(monkeypatch, env, peer, *headers) == e0


def test_par_todo_visitante_com_e1_muda_com_e0():
    """Se E0 desse o mesmo visitante, o caso não mediria a env."""
    medem = [c for c in CASOS if c[3] in (V, V6C, WORKER)]
    assert len(medem) >= 5
    for nome, _peer, _h, e1, e0 in medem:
        assert e0 != e1, nome


def test_linha_2_ipv6_vira_rede_64(monkeypatch):
    monkeypatch.setenv(cip.SEGREDO_ENV, S)
    assert rate_limit_key(_req(P, PROD, (SEG, S), (CF, V6))) == V6_64
    monkeypatch.delenv(cip.SEGREDO_ENV)
    assert rate_limit_key(_req(P, PROD, (SEG, S), (CF, V6))) == R


NAO_CLIENTE = ["10.0.0.1", "127.0.0.1", "::1", "100.64.0.1", "240.0.0.1", "192.0.2.5",
               "64:ff9b::7f00:1", "::8.8.8.8", "224.0.0.1", "ff02::1", "fe80::1%eth0",
               "lixo", "", "1.2.3.4:80", f"{V}, {A}"]


@pytest.mark.parametrize("cf", NAO_CLIENTE)
def test_linha_4_cf_nao_publico_ou_invalido_vale_a_conexao(cf, monkeypatch):
    assert _com_env(monkeypatch, S, P, PROD, (SEG, S), (CF, cf)) == R


def test_linha_4_cf_duplicado(monkeypatch):
    assert _com_env(monkeypatch, S, P, PROD, (SEG, S), (CF, V), (CF, A)) == R


def test_limite_de_32_caracteres(monkeypatch):
    """31 chars é E0 mesmo com o cabeçalho igual a ela; 32 já vale. A env passa
    pelo `strip` do padrão do repo (o cabeçalho, não)."""
    assert _com_env(monkeypatch, CURTA, P, PROD, (SEG, CURTA), (CF, V)) == R
    assert _com_env(monkeypatch, CURTA + "a", P, PROD, (SEG, CURTA + "a"), (CF, V)) == V
    assert _com_env(monkeypatch, f"  {S}\n", P, PROD, (SEG, S), (CF, V)) == V


def test_env_lida_a_cada_chamada(monkeypatch):
    h = (PROD, (SEG, S), (CF, V))
    assert _com_env(monkeypatch, S, P, *h) == V
    assert _com_env(monkeypatch, S2, P, *h) == R  # rotação: a env nova vale já
    assert _com_env(monkeypatch, None, P, *h) == R


def test_linha_10_cabecalho_estranho_nao_levanta(monkeypatch):
    """dict sem `getlist` (como o `_FakeRequest` de test_audit) com surrogate,
    bytes ou None no lugar do segredo: R, sem exceção."""
    monkeypatch.setenv(cip.SEGREDO_ENV, S)
    for valor in ("\udcff" * 64, S[:-1] + "\ud800", S.encode(), 123):
        conn = SimpleNamespace(client=SimpleNamespace(host=P),
                               headers={XFF: f"{C}, {R}", SEG: valor, CF: V})
        assert client_ip(conn) == R, repr(valor)
    conn = SimpleNamespace(client=SimpleNamespace(host=P), headers={XFF: f"{C}, {R}", SEG: S, CF: V})
    assert client_ip(conn) == V  # positivo: o dict com o segredo certo confia


def test_linha_21_sem_client(monkeypatch):
    monkeypatch.setenv(cip.SEGREDO_ENV, S)
    assert client_ip(_req(None, PROD, (SEG, S), (CF, V))) is None
    assert rate_limit_key(_req(None, PROD, (SEG, S), (CF, V))) == "127.0.0.1"


def test_linha_23_websocket_igual_ao_http(monkeypatch):
    async def _nada():
        return {}

    def ws():
        return WebSocket(_scope(P, [PROD, (SEG, S), (CF, V)], tipo="websocket"), _nada, _nada)
    monkeypatch.setenv(cip.SEGREDO_ENV, S)
    assert client_ip(ws()) == V
    monkeypatch.delenv(cip.SEGREDO_ENV)
    assert client_ip(ws()) == R


# ---- F. o valor do segredo não vaza ------------------------------------------

SENT = "SENTINELA-" + "f" * 40
CURTA_SENT = "curta-SENTINELA"
_AVISO = f"[client_ip] {cip.SEGREDO_ENV} curto demais: ignorado"


def _varrer(gravadas, capsys, caplog, *valores):
    saida = capsys.readouterr()
    textos = [repr(gravadas), json.dumps(_details(gravadas)), saida.out, saida.err, caplog.text]
    for texto in textos:
        for valor in valores:
            for trecho in (valor, valor[:12]):
                assert trecho not in texto, (trecho, texto[:200])
    return saida


def test_segredo_nao_vaza_na_sonda_nem_no_log(gravadas, capsys, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(cip.SEGREDO_ENV, SENT)
    pedidos = [
        [PROD, (SEG, SENT), (CF, V)], [PROD, (SEG, SENT[::-1]), (CF, V)],
        [PROD, (SEG, SENT), (SEG, SENT), (CF, V)], [PROD, (CF, V)],
        [(XFF, A), (SEG, SENT), (CF, V)], [PROD, (SEG, SENT), (CF, "lixo")],
    ]
    for headers in pedidos:
        client_ip(_req(P, *headers))
        rate_limit_key(_req(P, *headers))
    assert len(gravadas) >= 5
    assert _details(gravadas)[0]["segredo_ok"] is True  # positivo: a sonda grava o caso 1
    _varrer(gravadas, capsys, caplog, SENT)


def test_env_curta_avisa_uma_vez_sem_valor_nem_comprimento(gravadas, capsys, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(cip.SEGREDO_ENV, CURTA_SENT)
    assert client_ip(_req(P, PROD, (SEG, CURTA_SENT), (CF, V))) == R
    assert client_ip(_req(P, PROD, (SEG, CURTA_SENT), (CF, V), (XRI, C))) == R
    saida = _varrer(gravadas, capsys, caplog, CURTA_SENT)
    assert saida.err.count(_AVISO) == 1, saida.err
    linhas = [l for l in saida.err.splitlines() if cip.SEGREDO_ENV in l]
    assert linhas == [_AVISO] and not any(ch.isdigit() for ch in linhas[0])


# ---- G. sonda ----------------------------------------------------------------

ANTIGAS = ["xff_entradas", "conexao_cf", "cf_presente", "cf_valido", "cf_global",
           "cf_igual_conexao", "xri_igual_conexao", "fonte", "xff_cabecalhos",
           "xff_primeira_tipo", "xff_ultima_tipo", "xri_tipo", "xri_igual_primeira",
           "cf_igual_primeira"]
NOVAS = ["segredo_configurado", "segredo_presente", "segredo_ok"]


def _sinais(gravadas, monkeypatch, env, *headers):
    cip._SONDA_VISTAS.clear()
    _com_env(monkeypatch, env, P, *headers)
    d = _details(gravadas)[-1]
    assert list(d) == ANTIGAS + NOVAS  # antigas na ordem, novas no fim
    assert all(type(d[k]) is bool for k in NOVAS)
    return tuple(d[k] for k in NOVAS), d["fonte"]


def test_sonda_segredo(gravadas, monkeypatch):
    sinais = lambda env, *h: _sinais(gravadas, monkeypatch, env, *h)  # noqa: E731
    assert sinais(S, PROD, (SEG, S), (CF, V)) == ((True, True, True), "cf")
    assert sinais(S, PROD, (SEG, S2), (CF, V)) == ((True, True, False), "conexao")
    assert sinais(S, PROD, (CF, V)) == ((True, False, False), "conexao")
    assert sinais(S, PROD, (SEG, S), (SEG, S), (CF, V)) == ((True, True, False), "conexao")
    # alarme: regra criada e env ausente/curta
    assert sinais(None, PROD, (SEG, S), (CF, V)) == ((False, True, False), "conexao")
    assert sinais(CURTA, PROD, (SEG, CURTA), (CF, V)) == ((False, True, False), "conexao")
    assert sinais(None, (XFF, C), (CF, V)) == ((False, False, False), "cf")


def test_sonda_dedupe_e_teto_com_segredo(gravadas, monkeypatch):
    monkeypatch.setenv(cip.SEGREDO_ENV, S)
    for _ in range(3):
        client_ip(_req(P, PROD, (SEG, S), (CF, V)))
    assert len(gravadas) == 1
    monkeypatch.setattr(cip, "_SONDA_TETO", 2)
    client_ip(_req(P, PROD, (SEG, S2), (CF, V)))
    assert client_ip(_req(P, PROD, (CF, V))) == R  # acima do teto: IP certo, não grava
    assert client_ip(_req(P, PROD, (SEG, S), (CF, V), (XRI, C))) == V
    assert len(gravadas) == 2
