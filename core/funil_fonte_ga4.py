"""Fonte GA4 do painel de funil: eventos do funil e origem das sessões (7d e 30d), pela
Google Analytics Data API (`runReport`). Só agregados; o que não está na lista fechada de
eventos é descartado e nunca chega ao JSON. `canal` é texto do GA (pode ser custom): sai
sanitizado e o front o passa por `esc()`.

Credencial: conta de serviço (`GA4_SERVICE_ACCOUNT_JSON`, o JSON inteiro da chave) e
`GA4_PROPERTY_ID` (só dígitos). Os dois hosts são FIXOS (`_URL_TOKEN`, `_URL_DADOS`): o
`token_uri` que vem dentro do JSON de credencial nunca é lido. Troca do JWT por token com
PyJWT + `requests` (e não `google-auth`): as exceções do google-auth podem carregar trechos
da resposta do Google ou do JSON, e aqui cada falha vira um código fechado de
`funil_fontes.MENSAGENS`. Sem `google-auth` neste caminho, o logger dele não entra; o
`urllib3` em DEBUG só imprime host, caminho (o ID da propriedade, que não é segredo) e
status, nunca cabeçalhos; por isso nenhum logger é rebaixado.

SEGREDO: a `private_key` e o token OAuth não saem deste módulo: nem em log (só
`type(exc).__name__`, status HTTP e código fechado; nunca `str(exc)`, `exc_info`, corpo de
resposta), nem em exceção repassada (`from None`), nem em cache/payload. Do JSON de
credencial só fica em memória o SHA-256 (para invalidar o token quando a env muda).

Limites declarados:
- 7d = `7daysAgo..today` e 30d = `30daysAgo..today`: incluem o dia de HOJE, parcial, e o fuso
  é o da propriedade GA4. Os números do GA4 não batem com os do nosso banco (bloqueador de
  anúncio, consentimento). `purchase` inclui os eventos enviados pelo servidor.
- `timeout=5` é por OPERAÇÃO de rede (token + 2 relatórios): servidor que goteja bytes pode
  segurar a thread além dos 12 s do `funil_fontes`; o backoff de 300 s limita isso.
- Formato de `runReport` conferido na documentação, não contra o Google real (parser
  tolerante: campo faltando = zeros, linha ilegível é pulada).
- Fica de fora de propósito: demografia, dimensões customizadas (ex.: `method` do `sign_up`)
  e o relatório de funil nativo (v1alpha).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time

import jwt
import requests

from core.funil_fontes import Fonte, FonteErro

log = logging.getLogger(__name__)

# Eventos que o app dispara no GA4 (`tests/test_funil_fonte_ga4.py` compara esta lista com o
# código): `page_view` vem do `gtag('config')`, o resto de `gtag('event')`/`pbTrack` nas
# páginas e de `core/services/ga4_mp.py` (purchase, no servidor).
EVENTOS = ("page_view", "view_item_list", "begin_checkout", "sign_up", "start_trial",
           "onboarding_complete", "vsl_play", "vsl_progress", "purchase")

_URL_TOKEN = "https://oauth2.googleapis.com/token"
_URL_DADOS = "https://analyticsdata.googleapis.com/v1beta/properties/{}:runReport"
_ESCOPO = "https://www.googleapis.com/auth/analytics.readonly"
_PRAZO_S = 5
_ID = re.compile(r"[0-9]{1,20}")  # [0-9], não \d (que casa dígito Unicode)
_ACCESS_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")  # formato de um bearer; fora disso nem vai ao cabeçalho
_JANELAS = {"7d": "7daysAgo", "30d": "30daysAgo"}
_ERRO_DADOS = {400: "resposta_invalida", 401: "auth", 403: "permissao_ga4", 404: "nao_encontrado", 429: "limite"}
_ERRO_TOKEN = {400: "auth", 401: "auth", 403: "auth", 429: "limite"}

_TOKEN: dict = {}  # {"chave": sha256 do JSON, "valor": access_token, "expira": monotonic}
_LOCK = threading.Lock()


def _faltam() -> list[str]:
    # No momento da chamada. Só NOMES de env.
    return [e for e in ("GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON") if not (os.getenv(e) or "").strip()]


def _int(v) -> int:
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _canal(v) -> str:
    # isprintable() recusa controle, NUL, surrogate e separadores; sobra texto visível.
    t = "".join(c for c in v if c.isprintable()).strip()[:40] if isinstance(v, str) else ""
    return t or "(sem nome)"


def _post(etapa: str, url: str, **kw) -> requests.Response:
    try:
        return requests.post(url, timeout=_PRAZO_S, allow_redirects=False, **kw)
    except requests.exceptions.Timeout:
        log.warning("[funil_ga4] %s: rede (Timeout)", etapa)
        raise FonteErro("timeout") from None
    except requests.exceptions.RequestException as exc:
        log.warning("[funil_ga4] %s: rede (%s)", etapa, type(exc).__name__)
        raise FonteErro("indisponivel") from None


def _json(r: requests.Response, mapa: dict, etapa: str):
    if r.status_code != 200:
        log.warning("[funil_ga4] %s: HTTP %s", etapa, r.status_code)
        raise FonteErro(mapa.get(r.status_code, "indisponivel"))
    try:
        j = r.json()
    except ValueError:
        raise FonteErro("resposta_invalida") from None
    if not isinstance(j, dict):
        raise FonteErro("resposta_invalida")
    return j


def _token(raw: str) -> str:
    """Access token da conta de serviço (~1 h), reaproveitado entre refreshes; muda o valor da
    env, muda a chave do cache. Uma thread por vez (`_LOCK`)."""
    chave = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    with _LOCK:
        if _TOKEN.get("chave") == chave and _TOKEN["expira"] > time.monotonic() + 60:
            return _TOKEN["valor"]
        _TOKEN.clear()
        try:
            info = json.loads(raw, strict=False)  # strict=False: colar a chave com quebra de linha real
            agora = int(time.time())
            assertion = jwt.encode(
                {"iss": info["client_email"], "scope": _ESCOPO, "aud": _URL_TOKEN, "iat": agora, "exp": agora + 3600},
                info["private_key"], algorithm="RS256")
        except Exception:  # JSON ruim, campo faltando, PEM ilegível: o motivo NUNCA é repassado
            log.warning("[funil_ga4] credencial: JSON inválido")  # só a etapa: nada do conteúdo
            raise FonteErro("credencial_invalida") from None
        j = _json(_post("token", _URL_TOKEN, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}), _ERRO_TOKEN, "token")
        tok = j.get("access_token")
        if not isinstance(tok, str) or not _ACCESS_TOKEN.fullmatch(tok):  # nem entra no cache
            log.warning("[funil_ga4] token: formato recusado")  # só a etapa: nem valor, nem tamanho, nem tipo
            raise FonteErro("resposta_invalida")
        _TOKEN.update(chave=chave, valor=tok, expira=time.monotonic() + max(60, min(_int(j.get("expires_in")), 3600)))
        return tok


def _relatorio(token: str, pid: str, corpo: dict) -> dict:
    corpo = {"dateRanges": [{"name": k, "startDate": d, "endDate": "today"} for k, d in _JANELAS.items()],
             "limit": 100, **corpo}
    r = _post("dados", _URL_DADOS.format(pid), json=corpo, headers={"Authorization": "Bearer " + token})
    if r.status_code == 401:  # token revogado/expirado antes da hora: o próximo refresh assina de novo
        with _LOCK:
            _TOKEN.clear()
    return _json(r, _ERRO_DADOS, "dados")


def _linhas(resp: dict, dims: tuple, mets: tuple):
    """(dimensões, métricas) de cada linha de `runReport`, por NOME de coluna (cabeçalhos);
    sem cabeçalho vale a ordem pedida. Linha ilegível é pulada; `rows` ausente = nenhuma."""
    def nomes(chave, padrao):
        h = resp.get(chave)
        n = [x.get("name") if isinstance(x, dict) else None for x in h] if isinstance(h, list) and h else []
        return n or list(padrao)
    dn, mn = nomes("dimensionHeaders", dims), nomes("metricHeaders", mets)
    rows = resp.get("rows")
    for row in rows if isinstance(rows, list) else []:
        try:
            d = [x.get("value") for x in row["dimensionValues"]]
            m = [x.get("value") for x in row["metricValues"]]
        except (TypeError, KeyError, AttributeError):
            continue
        yield dict(zip(dn, d)), dict(zip(mn, m))


def _eventos(resp: dict) -> dict:
    out = {j: {e: {"eventos": 0, "usuarios": 0} for e in EVENTOS} for j in _JANELAS}
    for d, m in _linhas(resp, ("eventName", "dateRange"), ("eventCount", "totalUsers")):
        j, e = d.get("dateRange"), d.get("eventName")
        if isinstance(j, str) and isinstance(e, str) and j in out and e in out[j]:  # fora da lista: descartado
            out[j][e] = {"eventos": _int(m.get("eventCount")), "usuarios": _int(m.get("totalUsers"))}
    return out


def _origens(resp: dict) -> dict:
    out = {j: [] for j in _JANELAS}
    for d, m in _linhas(resp, ("sessionDefaultChannelGroup", "dateRange"), ("sessions", "totalUsers")):
        j = d.get("dateRange")
        if isinstance(j, str) and j in out:
            out[j].append({"canal": _canal(d.get("sessionDefaultChannelGroup")),
                           "sessoes": _int(m.get("sessions")), "usuarios": _int(m.get("totalUsers"))})
    # O `limit` da API vale para as linhas dos DOIS ranges juntas: pede-se bastante e corta-se aqui.
    return {j: sorted(v, key=lambda x: (-x["sessoes"], x["canal"]))[:8] for j, v in out.items()}


def _buscar() -> dict:
    pid = (os.getenv("GA4_PROPERTY_ID") or "").strip()
    if not _ID.fullmatch(pid):  # antes de montar a URL: nada além de dígitos entra no caminho
        raise FonteErro("nao_encontrado")
    token = _token(os.getenv("GA4_SERVICE_ACCOUNT_JSON") or "")
    ev = _relatorio(token, pid, {
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}, {"name": "totalUsers"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName", "inListFilter": {"values": list(EVENTOS)}}}})
    og = _relatorio(token, pid, {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}]})
    return {"eventos": _eventos(ev), "origens": _origens(og)}


FONTE = Fonte(
    nome="ga4",
    ttl_s=1800,
    backoff_s=300,
    janela={"rotulo": "Eventos do funil e origem das sessões, 7 e 30 dias mais hoje (parcial)",
            "fuso": "o da propriedade GA4"},
    faltam=_faltam,
    buscar=_buscar,
)
