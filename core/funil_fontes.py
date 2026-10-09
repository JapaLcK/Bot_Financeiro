"""Infraestrutura das fontes externas do painel de funil (`GET /admin/api/funil/fonte/{nome}`).

Só a mecânica: registro, cache em TABELA (`funil_fontes_cache`), TTL, backoff, cota
diária e o envelope. Cada fonte vive em `core/funil_fonte_<nome>.py` e exporta `FONTE`;
um PR novo acrescenta UMA linha em `MODULOS`.

Regras que valem para toda fonte (testes em `tests/test_funil_fontes.py`):
- só AGREGADOS: nada de e-mail, nome, id de cliente/usuário/sessão, nem no JSON nem no log;
- o `mensagem` do envelope vem SEMPRE de `MENSAGENS` (tabela fixa), nunca do texto da
  fonte; o log leva só `type(exc).__name__` e um código curto fechado (nunca `exc_info`,
  nunca `str(exc)`: a mensagem de erro de um cliente HTTP pode trazer a chave);
- chaves/tokens ficam no servidor e são lidos NO MOMENTO da chamada (`faltam()`);
- o `buscar` roda num executor DEDICADO (`EXECUTOR`, 4 threads), nunca no padrão do loop,
  com uma marca de voo por fonte e por processo (`_EM_VOO`, dict simples; sem `asyncio.Lock`
  porque o pool async já foi pego entre loops). Fonte SÍNCRONA: no máximo UMA thread em voo;
  quem chega com a fonte ocupada recebe cache/`stale`/`erro` com `ocupada`, sem thread nova.
  A marca sai em `finally`; thread que sobrevive ao timeout segura a marca até terminar ou
  até `TIMEOUT_S + backoff_s`. Entre processos quem limita é a cota diária no banco.
- CUIDADO com `async def buscar`: roda NO LOOP (corpo bloqueante trava o servidor) e um
  `asyncio.to_thread` interno usa o executor PADRÃO, fora deste controle (caso do Stripe).
- `dados` só passa se for JSON puro e pequeno (`_validar`); senão `resposta_invalida`.
- cancelar a requisição propaga o `CancelledError`; a reserva já feita fica gasta e nenhum
  `falha_em` é gravado (limite declarado).
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core import funil_fontes_cache as cache

log = logging.getLogger(__name__)

# nome (validado na rota) -> módulo que exporta `FONTE`. Import lazy: o módulo da fonte
# só carrega (e só importa a lib dela) na primeira consulta. GA4, Clarity e Meta entram
# aqui nos PRs seguintes, uma linha cada; até lá a rota responde 404 para eles.
MODULOS = {
    "stripe": "core.funil_fonte_stripe",
}

TIMEOUT_S = 12  # teto por consulta; as fontes têm timeout próprio menor
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="funil-fonte")
_EM_VOO: dict[str, dict] = {}  # nome -> marca da busca em andamento

MENSAGENS = {
    "auth": "A fonte recusou as credenciais configuradas.",
    "permissao": "A credencial não tem permissão para ler estes dados.",
    "cota": "Limite diário de consultas à fonte atingido; volta amanhã.",
    "limite": "A fonte pediu para reduzir o ritmo das consultas.",
    "timeout": "A fonte demorou demais para responder.",
    "indisponivel": "A fonte está indisponível no momento.",
    "resposta_invalida": "A fonte respondeu algo que não deu para ler.",
    "nao_encontrado": "A fonte não encontrou o recurso configurado.",
    "ocupada": "Já há uma consulta em andamento; tente em instantes.",
}


class FonteErro(Exception):
    """Falha esperada de uma fonte. `codigo` é uma chave de `MENSAGENS`; o texto da
    exceção original NUNCA entra aqui (nem no log)."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo if codigo in MENSAGENS else "indisponivel"


@dataclass(frozen=True)
class Fonte:
    nome: str
    ttl_s: int
    backoff_s: int
    janela: dict  # {"rotulo": str, "fuso": str}, para o rodapé do cartão
    faltam: Callable[[], list[str]]  # NOMES das envs ausentes, lidas a cada chamada
    # Chamada em thread do `EXECUTOR`; se devolver um awaitable (`async def`, lambda que
    # devolve coroutine, instância com `async def __call__`), ele é aguardado no loop.
    buscar: Callable[[], Any]
    limite_dia: int | None = None  # chamadas por dia UTC; None = sem cota, senão >= 1

    def __post_init__(self):
        if self.limite_dia is not None and (not isinstance(self.limite_dia, int) or self.limite_dia < 1):
            raise ValueError(f"funil_fontes: limite_dia de {self.nome!r} deve ser None ou >= 1")


def fonte(nome: str) -> Fonte:
    return importlib.import_module(MODULOS[nome]).FONTE


def _env(f: Fonte, estado: str, *, codigo=None, falta=None, linha=None, dados=None) -> dict:
    """Envelope comum. Lista fechada de chaves; `dados` só quando há payload."""
    buscado = (linha or {}).get("buscado_em")
    return {
        "fonte": f.nome,
        "estado": estado,
        "mensagem": MENSAGENS[codigo] if codigo else None,
        "falta": falta,
        "buscado_em": buscado.isoformat() if buscado else None,
        "janela": f.janela,
        "dados": dados,
    }


# ── Fluxo ──────────────────────────────────────────────────────────────────

def _sem_dado(f: Fonte, codigo: str, linha: dict | None) -> dict:
    """Falha: `stale` se há payload anterior, senão `erro`."""
    if linha and linha.get("payload") is not None:
        return _env(f, "stale", codigo=codigo, linha=linha, dados=linha["payload"])
    return _env(f, "erro", codigo=codigo)


def _soltar(nome: str, marca: dict) -> None:
    # ponytail: dict sem lock; um callback de thread velha só solta a PRÓPRIA marca.
    if _EM_VOO.get(nome) is marca:
        _EM_VOO.pop(nome, None)


def _ocupada(f: Fonte) -> bool:
    marca = _EM_VOO.get(f.nome)
    if marca is None:
        return False
    if time.monotonic() - marca["t"] > TIMEOUT_S + f.backoff_s:  # thread presa: libera
        _soltar(f.nome, marca)
        return False
    return True


async def _chamar(f: Fonte, marca: dict) -> dict:
    cf = EXECUTOR.submit(f.buscar)  # concurrent.futures: `done()` diz se a THREAD terminou

    async def esperar():
        r = await asyncio.wrap_future(cf)
        return await r if inspect.isawaitable(r) else r

    try:
        return await asyncio.wait_for(esperar(), TIMEOUT_S)
    finally:
        if not cf.cancel() and not cf.done():  # thread ainda rodando: a marca fica com ela
            marca["preso"] = True
            cf.add_done_callback(lambda _: _soltar(f.nome, marca))


_MAX_PROFUNDIDADE, _MAX_NOS, _MAX_BYTES = 20, 20_000, 256 * 1024


def _validar(payload: Any) -> dict:
    """O payload tem de ser um objeto JSON puro e pequeno; o que sai daqui (re-lido do JSON)
    é o que vai à tabela. Recusa: raiz que não é dict, chave que não é texto (int viraria
    str e colidiria), NaN/inf, tipos não JSON, NUL e surrogate solitário (o jsonb recusaria
    e a gravação falharia em silêncio), aninhamento acima de 20 níveis e mais de 256 KB."""
    try:
        if not isinstance(payload, dict):
            raise TypeError
        pilha, nos = [(payload, 1)], 0  # varredura iterativa: sem RecursionError
        while pilha:
            no, prof = pilha.pop()
            nos += 1
            if nos > _MAX_NOS or (prof > _MAX_PROFUNDIDADE and isinstance(no, (dict, list, tuple))):
                raise ValueError
            if isinstance(no, dict):
                for k, v in no.items():
                    if not isinstance(k, str):
                        raise TypeError
                    _texto_ok(k)
                    pilha.append((v, prof + 1))
            elif isinstance(no, (list, tuple)):
                pilha.extend((v, prof + 1) for v in no)
            elif isinstance(no, str):
                _texto_ok(no)
        txt = json.dumps(payload, allow_nan=False)
        if len(txt) > _MAX_BYTES:
            raise ValueError
        return json.loads(txt)
    except Exception:
        raise FonteErro("resposta_invalida") from None


def _texto_ok(t: str) -> None:
    if "\x00" in t:
        raise ValueError
    t.encode("utf-8")  # surrogate solitário levanta UnicodeEncodeError


def _pronta(f: Fonte, linha: dict | None, agora: datetime) -> dict | None:
    """Envelope vindo do cache (TTL válido ou backoff em curso), ou None se tem de buscar."""
    if not linha:
        return None
    buscado, falhou = linha.get("buscado_em"), linha.get("falha_em")
    # Timestamp no FUTURO (relógio adiantado de alguma instância) vale como VENCIDO: sem isso
    # `agora - buscado < ttl` e `agora < falhou + backoff` congelariam a fonte até o relógio real
    # alcançá-lo. Os dois timestamps vêm do relógio da instância que gravou.
    if buscado and buscado <= agora and agora - buscado < timedelta(seconds=f.ttl_s):
        return _env(f, "ok", linha=linha, dados=linha["payload"])
    if falhou and falhou <= agora and agora < falhou + timedelta(seconds=f.backoff_s):
        return _sem_dado(f, "indisponivel", linha)
    return None


async def _registrar_falha(nome: str, agora: datetime) -> None:
    try:
        await cache.gravar_falha(nome, agora)
    except Exception as exc:
        log.warning("[funil_fontes] %s: gravar falha falhou (%s)", nome, type(exc).__name__)


async def obter(nome: str) -> dict:
    """Devolve o envelope da fonte. Falha da fonte, do cache ou do payload vira `erro`/`stale`.
    Ainda podem levantar: `CancelledError` (propaga), módulo da fonte que não importa,
    `faltam()` quebrado e erro de programação na montagem do envelope; os testes de `MODULOS`
    pegam os dois primeiros antes de produção."""
    f = fonte(nome)
    falta = f.faltam()
    if falta:  # sem credencial: nem encosta no banco
        return _env(f, "nao_configurado", falta=list(falta))

    agora = datetime.now(timezone.utc)
    linha, banco_ok = None, True
    try:
        linha = await cache.ler(nome)
    except Exception as exc:
        banco_ok = False
        log.warning("[funil_fontes] %s: cache ilegivel (%s)", nome, type(exc).__name__)
    # Sem contador não dá para respeitar o limite diário: fonte com cota recusa.
    if not banco_ok and f.limite_dia is not None:
        return _env(f, "erro", codigo="indisponivel")

    pronta = _pronta(f, linha, agora)
    if pronta:
        return pronta

    if _ocupada(f):  # antes da reserva: quem não chama não gasta cota
        return _sem_dado(f, "ocupada", linha)
    marca = {"t": time.monotonic()}
    _EM_VOO[nome] = marca  # sem await entre a checagem e a marca
    try:
        if banco_ok:
            # Com a marca na mão, RELÊ: outra requisição pode ter gravado o resultado (ou a
            # falha) entre a leitura de cima e a marca; sem isso ela chamaria a fonte de novo
            # dentro do TTL/backoff.
            try:
                linha = await cache.ler(nome)
            except Exception as exc:
                log.warning("[funil_fontes] %s: cache ilegivel (%s)", nome, type(exc).__name__)
            agora = datetime.now(timezone.utc)
            pronta = _pronta(f, linha, agora)
            if pronta:
                return pronta
        return await _consultar(f, linha, banco_ok, agora, marca)
    finally:
        if not marca.get("preso"):
            _soltar(nome, marca)


async def _consultar(f: Fonte, linha: dict | None, banco_ok: bool, agora: datetime, marca: dict) -> dict:
    nome = f.nome
    if f.limite_dia is not None and banco_ok:
        try:
            if not await cache.reservar(nome, f.limite_dia):
                return _sem_dado(f, "cota", linha)
        except Exception as exc:  # fail-closed: sem reserva a fonte NÃO é chamada
            log.warning("[funil_fontes] %s: reserva falhou (%s)", nome, type(exc).__name__)
            return _env(f, "erro", codigo="indisponivel")

    try:
        payload = _validar(await _chamar(f, marca))
    except FonteErro as exc:
        codigo = exc.codigo
    except (asyncio.TimeoutError, TimeoutError):
        codigo = "timeout"
    except Exception as exc:  # inesperada: só o nome do tipo vai ao log
        log.warning("[funil_fontes] %s: erro inesperado (%s)", nome, type(exc).__name__)
        codigo = "indisponivel"
    else:
        novo = {"buscado_em": agora, "payload": payload}
        if banco_ok:
            try:
                await cache.gravar_ok(nome, payload, agora)
            except Exception as exc:
                # Devolve o dado vivo, mas marca a falha: sem cache, cada GET chamaria a fonte.
                log.warning("[funil_fontes] %s: gravar cache falhou (%s)", nome, type(exc).__name__)
                await _registrar_falha(nome, agora)
        return _env(f, "ok", linha=novo, dados=payload)

    log.warning("[funil_fontes] %s: falha (%s)", nome, codigo)
    if banco_ok:
        await _registrar_falha(nome, agora)
    return _sem_dado(f, codigo, linha)
