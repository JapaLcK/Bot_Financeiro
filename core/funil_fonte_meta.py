"""Fonte Meta do painel de funil: disparos do pixel por evento nos últimos 7 dias, pela Graph API
(`GET /{pixel_id}/stats?aggregation=event_total_counts`). Só agregados: o que não está na lista
fechada `EVENTOS` é descartado e nunca chega ao JSON.

Graph v25.0 (`_URL`); o `core/services/meta_capi.py` (Conversions API) usa v21.0 e NÃO foi
alinhado de propósito: são chamadas independentes.

Credencial: `META_INSIGHTS_ACCESS_TOKEN` (usuário do sistema do Business Manager, com o pixel
atribuído; SEPARADO do `META_PIXEL_ACCESS_TOKEN` da Conversions API, que nunca é lido aqui) e
`META_PIXEL_ID` (só `[0-9]{1,20}`, validado ANTES de montar a URL). Host FIXO; o token vai SÓ no
cabeçalho `Authorization: Bearer` (nunca em query/params: o `str()` de uma exceção do requests
carrega a URL inteira) e só depois de passar por `_TOKEN` (formato de bearer); redirecionamentos
NÃO são seguidos.

SEGREDO: o token não sai deste módulo: nem em log (só `type(exc).__name__`, status HTTP e códigos
fechados), nem em exceção repassada (`from None`), nem em cache/payload. Do corpo de erro da Graph
(`{"error":{"code":N,...}}`) lê-se SÓ o inteiro `error.code`; mensagem, `fbtrace_id` e o resto nunca
são guardados nem logados.

Erros: 190 `auth`; 200 e 10 `permissao_meta`; 80004, 4, 17, 32 e 613 `limite`; 100
`nao_encontrado` (a Graph também usa 100 para pixel que o token não enxerga); demais códigos,
`indisponivel`. Sem `error.code` legível: HTTP 401 `auth`, 403 `permissao_meta`, 429 `limite`; o
resto (400, 404, 5xx, 3xx) `indisponivel`. Token fora do formato de bearer: `auth`, sem chamada.
Sucesso (200) com corpo ilegível ou que não é objeto: `resposta_invalida`.

Limites declarados:
- Formato REAL da resposta não foi visto (a doc diz: `data` = lista de resultados com `start_time` e
  `data` = lista de `{value, count}`; nomes não confirmados). Parser tolerante: aceita também
  `{value, count}` direto em `data`, chaves normalizadas (`value`/`event`/`eventname` e
  `count`/`eventcount`/`total`) e soma os buckets do mesmo evento (a soma para em 10**15).
  `reconhecido` só é true com `data` lista vazia ou ao menos um evento da lista lido; 200 sem `data`
  lista (`{}`, `{"error":...}`) ou com item que não é objeto vale false + linha de log só com nomes.
- A Graph pode paginar (`paging.next`): só a 1ª página é lida.
- A API só guarda os últimos 7 dias. Os números são DISPAROS recebidos pela Meta (navegador +
  servidor), não conversões deduplicadas: divergem do nosso banco e do GA4.
- Resposta lida em stream (`read1`), teto de 1 MB descomprimido e PRAZO TOTAL de 10 s (menor que o
  `TIMEOUT_S` da infra); corpo de erro com teto de 64 KB. Uma chamada HTTP por `buscar`; sem cota
  diária da nossa parte (a Graph tem limite próprio, tratado como `limite` + backoff).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests
import urllib3

from core.funil_fontes import Fonte, FonteErro

log = logging.getLogger(__name__)

# Eventos que o app dispara no Meta Pixel (navegador) e na Conversions API (servidor)
# (`tests/test_funil_fonte_meta.py` compara esta lista com o código). `PageView` vem do `fbq('track')`
# do snippet em `frontend/routes/shared.py`; `TesteClick` e `OnboardingComplete` são `trackCustom`.
EVENTOS = ("PageView", "ViewContent", "InitiateCheckout", "CompleteRegistration", "StartTrial",
           "OnboardingComplete", "TesteClick", "Purchase")

_URL = "https://graph.facebook.com/v25.0/{}/stats"
_PRAZO_S = 8
_PRAZO_TOTAL_S = 10  # da chamada ao último byte; MENOR que o `TIMEOUT_S` (12 s) da infra
_MAX_BYTES = 1024 * 1024
_MAX_BYTES_ERRO = 64 * 1024
_JANELA_S = 7 * 86400 - 3600  # a API só guarda 7 dias; 1 h de folga: relógio/latência não viram código 100
_ID = re.compile(r"[0-9]{1,20}")  # [0-9], não \d (que casa dígito Unicode)
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")  # formato de um bearer; fora disso nem vai ao cabeçalho
_NOME = re.compile(r"[A-Za-z][A-Za-z0-9_ ]{0,39}")  # allowlist de NOME para o log: só letras, dígitos, _ e espaço
_FORA = "(nome fora do padrão)"
_STATUS = {401: "auth", 403: "permissao_meta", 429: "limite"}  # só quando o corpo não traz `error.code` legível
_COD = {190: "auth", 200: "permissao_meta", 10: "permissao_meta", 80004: "limite", 4: "limite", 17: "limite",
        32: "limite", 613: "limite", 100: "nao_encontrado"}
_NOMES = ("value", "event", "eventname")
_CONTAGENS = ("count", "eventcount", "total")


def _faltam() -> list[str]:
    return [e for e in ("META_PIXEL_ID", "META_INSIGHTS_ACCESS_TOKEN") if not (os.getenv(e) or "").strip()]  # só NOMES de env


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""


def _cont(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):  # int de 309+ dígitos: só esta entrada é ignorada
        return None
    return int(f) if 0 <= f < 1e15 else None  # recusa negativo, NaN e inf


def _nome(v) -> str:
    # ALLOWLIST, não limpeza: a resposta pode trazer DADO como nome de campo; fora do padrão (ou com
    # mais de 2 dígitos) o nome some inteiro, sem nenhum caractere do original.
    ok = isinstance(v, str) and _NOME.fullmatch(v) and sum(c.isdigit() for c in v) <= 2
    return v if ok else _FORA


def _entradas(data: list):
    """(entradas `{value, count}`, forma_ok). Entradas: dentro de cada resultado (`data` aninhada) ou direto
    em `data`. `forma_ok` = nenhum item (em nenhum nível) que não seja objeto."""
    out, ok = [], True
    for x in data:
        if not isinstance(x, dict):
            ok = False
            continue
        sub = x.get("data")
        if not isinstance(sub, list):
            out.append(x)
            continue
        for y in sub:
            if isinstance(y, dict):
                out.append(y)
            else:
                ok = False
    return out, ok


def _dados(resp: dict) -> dict:
    data = resp.get("data")
    entradas, forma_ok = _entradas(data) if isinstance(data, list) else ([], False)  # sem `data` lista: 200 que não é a resposta esperada
    eventos, lido = {e: 0 for e in EVENTOS}, False
    for en in entradas:
        n = {_norm(k): v for k, v in en.items()}
        nome = next((n[c] for c in _NOMES if c in n), None)
        cont = _cont(next((n[c] for c in _CONTAGENS if c in n), None))
        if isinstance(nome, str) and cont is not None and nome in eventos:  # fora da lista: descartado
            eventos[nome] = min(eventos[nome] + cont, 10**15)  # a soma de buckets também respeita o teto de cada contagem
            lido = True
    # Reconhecido: leu ao menos um evento da lista, ou a Graph disse "sem disparos" (`data` lista vazia, ou só
    # resultados com `data` vazia). `data` ausente/null/não-lista ({}, {"error":...}) NÃO é "sem disparos".
    rec = lido or (forma_ok and not entradas)
    if not rec:
        chaves = list(dict.fromkeys([*resp, *(k for en in entradas[:20] for k in en)]))[:30]
        log.warning("[funil_meta] formato: chaves=%s", [_nome(k) for k in chaves])  # SÓ nomes: nunca valor, ID ou contagem
    return {"eventos": eventos, "reconhecido": rec}


def _ler(r, teto: int, t0: float):
    """Corpo em stream (`read1`: `iter_content` só entrega com 64 KB e deixaria o goteio passar do
    prazo). None se passar de `teto` bytes DESCOMPRIMIDOS (bomba gzip)."""
    corpo, total = [], 0
    while True:
        pedaco = r.raw.read1(65536, decode_content=True)
        if not pedaco:
            return b"".join(corpo)
        total += len(pedaco)
        if total > teto:
            return None
        corpo.append(pedaco)
        if time.monotonic() - t0 > _PRAZO_TOTAL_S:  # o `timeout` do requests é por operação, não total
            log.warning("[funil_meta] prazo total de leitura estourado")
            raise FonteErro("timeout")


def _codigo(corpo):
    """SÓ o inteiro `error.code` do corpo de erro da Graph; todo o resto é jogado fora."""
    try:
        c = json.loads(corpo)["error"]["code"] if corpo else None
    except (ValueError, RecursionError, KeyError, TypeError):
        return None
    return c if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < 10**6 else None


def _buscar() -> dict:
    pid = (os.getenv("META_PIXEL_ID") or "").strip()
    if not _ID.fullmatch(pid):  # antes de montar a URL: nada além de dígitos entra no caminho
        raise FonteErro("nao_encontrado")
    token = (os.getenv("META_INSIGHTS_ACCESS_TOKEN") or "").strip()
    if not _TOKEN.fullmatch(token):  # antes do cabeçalho; nunca vai ao log nem ao cache
        log.warning("[funil_meta] token: formato recusado")  # só a etapa: nem valor, nem tamanho
        raise FonteErro("auth")  # token colado com aspas, "Bearer " ou espaço no meio: a Meta o recusaria
    agora = int(time.time())
    t0 = time.monotonic()
    try:  # UMA chamada por `buscar`
        r = requests.get(_URL.format(pid), timeout=_PRAZO_S, stream=True, allow_redirects=False,
                         params={"aggregation": "event_total_counts", "start_time": agora - _JANELA_S, "end_time": agora},
                         headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            st = r.status_code
            if st != 200:
                cod = _codigo(_ler(r, _MAX_BYTES_ERRO, t0)) if 400 <= st < 500 and st != 429 else None
                log.warning("[funil_meta] HTTP %s código %s", st, cod)
                raise FonteErro(_COD.get(cod, "indisponivel") if cod is not None else _STATUS.get(st, "indisponivel"))
            corpo = _ler(r, _MAX_BYTES, t0)
        finally:
            r.close()
    except (requests.exceptions.Timeout, urllib3.exceptions.TimeoutError):
        log.warning("[funil_meta] rede (Timeout)")
        raise FonteErro("timeout") from None
    except (requests.exceptions.RequestException, urllib3.exceptions.HTTPError) as exc:  # conexão ou meio do corpo
        log.warning("[funil_meta] rede (%s)", type(exc).__name__)
        raise FonteErro("indisponivel") from None
    if corpo is None:
        log.warning("[funil_meta] corpo acima do teto")
        raise FonteErro("resposta_invalida")
    try:
        resp = json.loads(corpo)
    except (ValueError, RecursionError):
        raise FonteErro("resposta_invalida") from None
    if not isinstance(resp, dict):
        raise FonteErro("resposta_invalida")
    return _dados(resp)


FONTE = Fonte(
    nome="meta",
    ttl_s=1800,
    backoff_s=600,
    janela={"rotulo": "Disparos do pixel por evento, últimos 7 dias (limite da API)", "fuso": "UTC"},
    faltam=_faltam,
    buscar=_buscar,
)
