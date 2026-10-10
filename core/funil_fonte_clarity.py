"""Fonte Clarity do painel de funil: sessões, rolagem, cliques mortos e cliques de raiva da
página /precos nos últimos 3 dias (cliques: só o % das sessões), pela Microsoft Clarity Data Export API
(`project-live-insights`). Só agregados: a lista de URLs da resposta nunca sai deste módulo.

Cota REAL do Clarity: 10 requisições por projeto por dia, 1 a 3 dias, resposta de até 1.000
linhas SEM paginação, horário UTC. Por isso: `limite_dia=8` (2 de folga), `ttl_s` de 3 h
(8 por dia = a cota inteira) e `backoff_s` de HORAS. O `funil_fontes` reserva a cota por
`buscar`, ANTES de chamar: falha, timeout ou token ilegível depois da reserva queimam 1 sem
devolução (por isso o backoff longo). `buscar` faz EXATAMENTE 1 chamada HTTP; um 429 vira
`FonteErro("cota")` e a infra esgota o dia no banco (`funil_fontes_cache.esgotar`).

Credencial: só `CLARITY_API_TOKEN` (JWT do projeto, Settings > Data Export). O host é FIXO
(`_URL`), o token vai SÓ no cabeçalho `Authorization` e só depois de passar por `_TOKEN`
(formato de bearer); redirecionamentos NÃO são seguidos.

SEGREDO: o token não sai deste módulo: nem em log (só `type(exc).__name__`, status HTTP,
código fechado e a linha `formato:` de NOMES), nem em exceção repassada (`from None`), nem em
cache/payload. Corpo de erro nunca é lido.

PARSER TOLERANTE (a doc não dá os nomes exatos; a 1ª carga em produção é o teste). Nomes
comparados normalizados (minúsculas, só letras e dígitos). Nada é inventado: o que não for
reconhecido vira `null` ("n/d") e UMA linha de log com SÓ NOMES (nunca valores, URLs ou
números): `[funil_clarity] formato: metricas=[...] campos={metricName: [chaves da 1ª linha]}`.
- métrica: nome normalizado IGUAL a `traffic` / `scrolldepth` / `deadclickcount` (ou `deadclicks`) /
  `rageclickcount` (ou `rageclicks`); sem igual, vale a ÚNICA que contém `traffic` / `scroll`+`depth` /
  `deadclick` / `rageclick`. Duas ou mais candidatas (ou o mesmo nome repetido) = métrica NÃO
  reconhecida: nunca soma métricas diferentes (`TrafficSources` não dobra `Traffic`).
- URL da linha: a chave `url` ou, na falta, a 1ª que contém `url`. A página vale se
  `path.rstrip("/") == "/precos"` (host, query e fragmento ignorados; caixa EXATA).
- Traffic: `session`+`count` sem `bot` (total), `bot`+`session` (bots; sem ele vale o total),
  `user`+`count`. Sessões = total − bots.
- Rolagem: chave com `scroll`+`depth`, `scroll`+`percentage` ou só `depth`; valor em 0..100.
- Dead/Rage: SÓ o % das sessões = `session`+`percentage` (sem `without`/`bot`), 0..100. A contagem
  `session`+`count` dessas linhas NÃO é exposta: pode ser o total de sessões da URL e não as com o
  evento (ambíguo até haver resposta real); ela serve só de PESO da média.
Médias ponderadas pelas sessões da própria linha SÓ quando ela traz a chave `session`+`count` (a doc
pública do Clarity só mostra a contagem no Traffic, não na rolagem); sem ela em NENHUMA linha, média
SIMPLES das variantes (peso 1; com `fbclid` cada anúncio é uma variante e a média pode divergir do
valor real; ponderar pelo `totalSessionCount` do Traffic da MESMA URL é melhoria futura); em só
algumas linhas, `null`.
NUNCA soma parcial: se qualquer linha de /precos não tiver o número legível, o campo agregado vira
`null`. Sem linha de /precos: sessões e usuários 0, médias `null`; com `truncado` e sem linha de
/precos, sessões e usuários `null` (a linha pode estar na parte cortada).
Resposta lida em stream (`read1`), com teto de 8 MB descomprimido e PRAZO TOTAL de 10 s (menor que o
`TIMEOUT_S` da infra: goteio não prende a thread do executor); corpo de erro não é lido (`r.close()`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

import requests
import urllib3

from core.funil_fontes import Fonte, FonteErro

log = logging.getLogger(__name__)

_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
_PRAZO_S = 8
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")  # formato de um JWT/bearer; fora disso nem vai ao cabeçalho
_NOME = re.compile(r"[A-Za-z][A-Za-z0-9_ ]{0,39}")  # allowlist de NOME para o log: só letras, dígitos, _ e espaço
_FORA = "(nome fora do padrão)"
_MAX_BYTES = 8 * 1024 * 1024  # 16 métricas x 1.000 linhas x ~300 bytes de URL com `fbclid` ~ 4,8 MB; acima de 8 MB é recusada
_PRAZO_TOTAL_S = 10  # da chamada ao último byte; MENOR que o `TIMEOUT_S` (12 s) da infra: a thread nunca fica presa
_ERRO = {400: "resposta_invalida", 401: "auth", 403: "permissao", 429: "cota"}
_LIMITE_LINHAS = 1000  # a API corta aqui, sem paginação
# chave -> (nomes normalizados EXATOS, fragmentos que o nome tem de conter na falta de um exato)
_METRICAS = {"trafego": ({"traffic"}, ("traffic",)), "rolagem": ({"scrolldepth"}, ("scroll", "depth")),
             "mortos": ({"deadclickcount", "deadclicks"}, ("deadclick",)),
             "raiva": ({"rageclickcount", "rageclicks"}, ("rageclick",))}


def _faltam() -> list[str]:
    return [e for e in ("CLARITY_API_TOKEN",) if not (os.getenv(e) or "").strip()]  # só NOMES de env


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):  # int de 309+ dígitos: `null` só deste campo, não o fetch inteiro
        return None
    return f if 0 <= f < 1e15 else None  # recusa negativo, NaN e inf


def _pct(v):
    return v if v is not None and v <= 100 else None


def _campo(row: dict, tem: tuple, sem: tuple = ()):
    """1º valor numérico (na ordem do JSON) de uma chave que contém todos os `tem` e nenhum `sem`."""
    for k, v in row.items():
        kn = _norm(k)
        if all(t in kn for t in tem) and not any(t in kn for t in sem):
            if (n := _num(v)) is not None:
                return n
    return None


def _pagina(row: dict):
    """True/False se a linha é a página /precos; None se não tem URL legível."""
    ks = [k for k in row if "url" in _norm(k)]
    k = next((k for k in ks if _norm(k) == "url"), ks[0] if ks else None)
    u = row.get(k) if k is not None else None
    if not isinstance(u, str):
        return None
    if "://" not in u and not u.startswith("/"):
        u = "//" + u  # "host/precos" sem esquema
    try:
        return urlsplit(u).path.rstrip("/") == "/precos"
    except ValueError:
        return None


def _traf(r):
    t, b = _campo(r, ("session", "count"), ("bot",)), _campo(r, ("bot", "session"))
    return (None if t is None else max(0.0, t - (b or 0.0)), _campo(r, ("user", "count")), None)


def _roll(r):
    v = next((x for x in (_campo(r, ("scroll", "depth")), _campo(r, ("scroll", "percentage")),
                          _campo(r, ("depth",))) if x is not None), None)
    return (_pct(v), _peso(r))


def _clique(r):
    return (_pct(_campo(r, ("session", "percentage"), ("without", "bot"))), _peso(r))


def _peso(r):
    return _campo(r, ("session", "count"), ("bot", "without", "percentage"))


def _soma(pares):
    if any(v is None for v, _ in pares):  # nunca soma parcial: uma linha ilegível = campo `null`
        return None
    return int(round(sum(v for v, _ in pares)))


def _media(pares):
    if not pares or any(v is None for v, _ in pares):  # sem linha de /precos (ou linha ilegível): n/d
        return None
    ws = [w for _, w in pares]
    if any(w is None for w in ws):
        if any(w is not None for w in ws):  # peso em só algumas linhas: média ambígua
            return None
        ws = [1.0] * len(pares)  # ponytail: sem peso em nenhuma linha, média simples; ajustar após o log de formato
    tot = sum(ws)
    vs = [v for v, _ in pares]
    return round(sum(v * w for v, w in zip(vs, ws)) / tot if tot > 0 else sum(vs) / len(vs), 2)


# chave -> (extrator da linha, como juntar cada campo extraído)
_ESPEC = {"trafego": (_traf, (_soma, _soma)), "rolagem": (_roll, (_media,)),
          "mortos": (_clique, (_media,)), "raiva": (_clique, (_media,))}


def _agrega(linhas: list, ext, aggs: tuple):
    """(campos, reconhecida) da métrica para a página /precos. Reconhecida = a URL e CADA campo
    apareceram em alguma linha (de qualquer página); sem nenhuma linha, vale (sem visita)."""
    pares = [(ext(r), _pagina(r)) for r in linhas]
    achou = [any(t[i] is not None for t, _ in pares) for i in range(len(aggs))]
    url_ok = any(p is not None for _, p in pares)
    sel = [t for t, p in pares if p]
    campos = [None if pares and not (url_ok and achou[i]) else ag([(t[i], t[-1]) for t in sel])
              for i, ag in enumerate(aggs)]
    return campos, (not pares) or (url_ok and all(achou))


def _nome(v) -> str:
    # ALLOWLIST, não limpeza: o Clarity pode mandar DADO como nome de campo (URL, e-mail, token...).
    # Fora do padrão (ou com mais de 2 dígitos) o nome some inteiro, sem nenhum caractere do original.
    ok = isinstance(v, str) and _NOME.fullmatch(v) and sum(c.isdigit() for c in v) <= 2
    return v if ok else _FORA


def _loga_formato(metricas: list, campos: dict) -> None:
    # SÓ nomes (sanitizados): nenhum valor de linha, URL ou número entra aqui.
    log.warning("[funil_clarity] formato: metricas=%s campos=%s", [_nome(m) for m in metricas[:20]],
                {_nome(m): [_nome(k) for k in ks[:30]] for m, ks in list(campos.items())[:20]})


def _escolhe(entradas: list, exatos: set, toks: tuple):
    """Linhas da métrica: a de nome EXATO; sem exata, a ÚNICA que contém os fragmentos. Ausente ou
    ambígua (2 ou mais, inclusive o mesmo nome repetido) = None: nunca soma métricas diferentes."""
    cand = [ls for n, ls in entradas if n in exatos] or [ls for n, ls in entradas if all(t in n for t in toks)]
    return cand[0] if len(cand) == 1 else None


def _dados(resp: list) -> dict:
    entradas, nomes, campos, trunc = [], [], {}, False
    for m in resp:
        info = m.get("information") if isinstance(m, dict) else None
        if not isinstance(info, list) or not isinstance(m.get("metricName"), str):
            continue
        nome, linhas = m["metricName"], [r for r in info if isinstance(r, dict)]
        nomes.append(nome)
        campos[nome] = list(linhas[0]) if linhas else []
        trunc = trunc or len(info) >= _LIMITE_LINHAS
        entradas.append((_norm(nome), linhas))
    rec, out = True, {}
    for chave, (ext, aggs) in _ESPEC.items():
        linhas = _escolhe(entradas, *_METRICAS[chave])
        out[chave], ok = _agrega(linhas, ext, aggs) if linhas is not None else ([None] * len(aggs), False)
        rec = rec and ok
    if trunc and not any(_pagina(r) for r in _escolhe(entradas, *_METRICAS["trafego"]) or []):
        out["trafego"] = [None, None]  # a linha de /precos pode estar na parte cortada: não afirma 0
    if not rec:
        _loga_formato(nomes, campos)
    return {"pagina": "/precos", "janela_dias": 3,
            "trafego": {"sessoes": out["trafego"][0], "usuarios": out["trafego"][1]},
            "rolagem_media_pct": out["rolagem"][0],
            "cliques_mortos": {"pct_sessoes": out["mortos"][0]},
            "cliques_raiva": {"pct_sessoes": out["raiva"][0]},
            "truncado": trunc, "reconhecido": rec}


def _buscar() -> dict:
    token = (os.getenv("CLARITY_API_TOKEN") or "").strip()
    if not _TOKEN.fullmatch(token):  # antes do cabeçalho; nunca vai ao log nem ao cache
        log.warning("[funil_clarity] token: formato recusado")  # só a etapa: nem valor, nem tamanho
        raise FonteErro("resposta_invalida")
    t0 = time.monotonic()
    try:  # UMA chamada por `buscar`: a reserva de cota é por `buscar`
        r = requests.get(_URL, params={"numOfDays": 3, "dimension1": "URL"}, timeout=_PRAZO_S, stream=True,
                         allow_redirects=False, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            if r.status_code != 200:  # corpo de erro NUNCA é lido: só o status, e a conexão é fechada
                log.warning("[funil_clarity] HTTP %s", r.status_code)
                raise FonteErro(_ERRO.get(r.status_code, "indisponivel"))
            corpo, total = [], 0
            while True:  # read1 (e não iter_content, que só entrega com 64 KB e deixa o goteio passar do prazo)
                pedaco = r.raw.read1(65536, decode_content=True)
                if not pedaco:
                    break
                total += len(pedaco)  # já descomprimido: o teto vale contra bomba gzip/brotli
                if total > _MAX_BYTES:
                    log.warning("[funil_clarity] corpo acima do teto")
                    raise FonteErro("resposta_invalida")
                corpo.append(pedaco)
                if time.monotonic() - t0 > _PRAZO_TOTAL_S:  # o `timeout` do requests é por operação, não total
                    log.warning("[funil_clarity] prazo total de leitura estourado")
                    raise FonteErro("timeout")
        finally:
            r.close()
    except (requests.exceptions.Timeout, urllib3.exceptions.TimeoutError):
        log.warning("[funil_clarity] rede (Timeout)")
        raise FonteErro("timeout") from None
    except (requests.exceptions.RequestException, urllib3.exceptions.HTTPError) as exc:  # conexão ou meio do corpo
        log.warning("[funil_clarity] rede (%s)", type(exc).__name__)
        raise FonteErro("indisponivel") from None
    try:
        resp = json.loads(b"".join(corpo))
    except (ValueError, RecursionError):
        raise FonteErro("resposta_invalida") from None
    if not isinstance(resp, list):
        raise FonteErro("resposta_invalida")
    return _dados(resp)


FONTE = Fonte(
    nome="clarity",
    ttl_s=10800,
    backoff_s=10800,  # falha depois da reserva queima cota sem devolução: horas, não segundos
    janela={"rotulo": "últimos 3 dias (UTC)", "fuso": "UTC"},
    faltam=_faltam,
    buscar=_buscar,
    limite_dia=8,  # a API aceita 10 por dia; 2 de folga
)
