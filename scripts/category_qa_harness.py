#!/usr/bin/env python3
"""
Harness de QA de CATEGORIZAÇÃO (blocos B1..B8).

O QUE FAZ
  - Cria um database Postgres isolado e descartável (`qa_cat_<hex>`), aponta
    DATABASE_URL pra ele, chama `db.init_db()` e SEMPRE derruba o banco no
    final (try/finally).
  - Dispara mensagens pelo pipeline real do WhatsApp
    (`core.handle_incoming.handle_incoming`), semeia categorias custom
    chamando `db.create_user_category` direto (sem passar pelo gate de plano
    do endpoint do dashboard) e exercita o botão "categoria errada?" chamando
    `adapters.whatsapp.wa_runtime._apply_recategorize` direto.
  - Registra TRÊS oráculos por caso: a linha "🏷️ Categoria:" da resposta, o
    `launches.categoria` lido do banco e o `reason` do `infer_category`
    (capturado por wrapper nos namespaces `parsers` e `core.handlers.launches`),
    mais o DIFF de `user_category_rules` antes/depois de cada caso.
  - O bloco B8 mede o INPUT REAL DO USUÁRIO (valor-primeiro, duas transações
    na mesma mensagem, wake-word `pig`, gíria, escrita errada, CAIXA ALTA,
    transcrição de áudio). Nele a categoria esperada vem do comportamento
    PRETENDIDO, não do que o código devolve: vermelho ali é achado de QA, e
    nenhum arquivo de produção foi tocado por causa dele.
  - Gera `docs/qa_categorizacao_<label>_<data>_<sha7>.md` + o JSON de mesmo nome
    (label sozinho no nome deixava `--tree <pr> --label main` sobrescrever a
    baseline). Com `--compare <json>` a tabela ganha a coluna de delta; caminho
    inexistente é ERRO, não silêncio.
  - SIGTERM/SIGHUP viram saída limpa (o teardown roda). `--gc` recolhe o que só
    o SIGKILL deixa para trás: databases `qa_cat_*` E tempdirs `qa_cat_noenv_*`.

DETERMINÍSTICO POR PADRÃO, MESMO AMBIENTE NAS DUAS ÁRVORES, SEM REDE
  - O `.env` da árvore importada é NEUTRALIZADO (`config.env.ROOT_DIR` aponta
    pra um diretório vazio). Sem isso, `load_app_env()` — chamado no import de
    `core/observability.py` — reinjeta por `os.environ.setdefault` TODO o `.env`
    da raiz daquela árvore (o checkout do usuário tem 37 variáveis, incluindo
    OPENAI_API_KEY, WA_TOKEN, RESEND_API_KEY), desfazendo o `pop` do topo e
    fazendo as duas árvores rodarem sob ambientes DIFERENTES.
  - Sem `--ai`, o kill switch de rede da suíte (`tests/conftest.py::
    _block_outbound_network`) é instalado no processo — o MESMO, não uma cópia.
  - As duas coisas são VERIFICADAS em runtime e o resultado vai pro relatório
    (variáveis injetadas pelo import + OPENAI_API_KEY presente/ausente).
  Os casos do bloco B7 saem com veredito 🔍 (não medido), nunca ❌: eles não
  rodam, `obtido_db` é nulo. Os três casos do B8 que só o passo 6 (GPT)
  acertaria (`rango`, `cerva`, `netflis`) NÃO são 🔍 — rodam inteiros, têm
  resultado medido (`outros`/`default`), e o que bloqueia o passo 6 para eles é
  o PLANO, não a flag: o gate exige `OPENAI_API_KEY` **e** `is_pro`, e os
  usuários do B8 são todos grátis. Em produção, com a chave presente, o
  usuário grátis recebe esse mesmo `outros`. Logo: ❌.

COMO RODAR
    cd <este worktree>
    /Users/lucaskuramoti/Desktop/bot/bot_wa/.venv/bin/python \
        scripts/category_qa_harness.py --label main

    # contra a árvore do PR #123 (SÓ LEITURA daquele checkout):
    /Users/lucaskuramoti/Desktop/bot/bot_wa/.venv/bin/python \
        scripts/category_qa_harness.py \
        --tree /Users/lucaskuramoti/Desktop/bot/bot_wa --label pr123 \
        --compare docs/qa_categorizacao_main_<data>_<sha7>.json

NÃO é uma suíte pytest e não entra na baseline de testes.
"""
from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid as _uuid
from datetime import datetime
from pathlib import Path

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CHECKOUT_ENV = "/Users/lucaskuramoti/Desktop/bot/bot_wa/.env"
VAULT_DEFAULT = "/Users/lucaskuramoti/Desktop/bot/Obsidian/PigBank"

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=WORKTREE_ROOT,
                 help="raiz da árvore de código a importar (default: este worktree = main)")
_ap.add_argument("--ai", action="store_true",
                 help="carrega OPENAI_API_KEY do .env real e roda o bloco B7 (custa $)")
_ap.add_argument("--label", default="main", help="rótulo desta execução (vai no nome do JSON)")
_ap.add_argument("--compare", default=None, help="JSON de outra execução, para a coluna de delta")
_ap.add_argument("--vault", default=VAULT_DEFAULT,
                 help="raiz do vault Obsidian (as discrepâncias são reconferidas nele em runtime)")
_ap.add_argument("--gc", action="store_true",
                 help="derruba os databases qa_cat_* deixados por execuções mortas e sai")
ARGS = _ap.parse_args()

# Um --compare pedido e não aplicado saía sem coluna de delta e SEM AVISO, com
# cara de sucesso. Conferido aqui, antes de criar database e rodar 70s.
if ARGS.compare and not os.path.exists(ARGS.compare):
    _ap.error(f"--compare {ARGS.compare!r} não existe (delta pedido não pode falhar calado)")

TREE = os.path.abspath(ARGS.tree)
# A árvore importada pode ser o checkout do usuário, que é SÓ LEITURA: nada de
# __pycache__ lá dentro. PYTHONDONTWRITEBYTECODE só é lida na partida do
# interpretador; de dentro do processo quem manda é este flag.
sys.dont_write_bytecode = True
sys.path.insert(0, TREE)


def git_state(path: str) -> str:
    """`<sha7> (<branch>, clean|dirty)` da árvore — LIDO, nunca presumido."""
    def _g(*a: str) -> str:
        return subprocess.run(("git", "-C", path, *a),
                              capture_output=True, text=True).stdout.strip()
    return (f"{_g('rev-parse', '--short', 'HEAD') or '?'} "
            f"({_g('rev-parse', '--abbrev-ref', 'HEAD') or '?'}, "
            f"{'dirty' if _g('status', '--porcelain') else 'clean'})")


TREE_GIT = git_state(TREE)
print(f"[harness] árvore importada: {TREE} — {TREE_GIT}")

# ─────────────────────────────────────────────────────────────────────────
# 1) DB isolado e descartável
# ─────────────────────────────────────────────────────────────────────────
import psycopg  # noqa: E402

_ADMIN_DSN = "postgresql://localhost:5432/postgres"


def _drop_db(name: str) -> None:
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as c:
        c.execute(f'drop database if exists "{name}" with (force)')


_TMP_GLOB = os.path.join(tempfile.gettempdir(), "qa_cat_noenv_*")

if ARGS.gc:
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as _c:
        _orfaos = [r[0] for r in _c.execute(
            r"select datname from pg_database where datname like 'qa\_cat\_%'").fetchall()]
    for _n in _orfaos:
        _drop_db(_n)
        print(f"[harness] --gc: derrubado {_n}")
    # A execução morta deixa DUAS coisas para trás, não uma: o database e o
    # tempdir do `.env` neutralizado. Varrer só o Postgres deixava o segundo
    # vazamento sem NENHUMA ferramenta de recuperação (medido: depois de um
    # SIGTERM, `--gc` zerava os databases e o tempdir continuava lá).
    _tmps = glob.glob(_TMP_GLOB)
    for _d in _tmps:
        shutil.rmtree(_d, ignore_errors=True)
        print(f"[harness] --gc: removido {_d}")
    print(f"[harness] --gc: {len(_orfaos)} database(s), {len(_tmps)} tempdir(s).")
    sys.exit(0)

DB_NAME = f"qa_cat_{_uuid.uuid4().hex[:12]}"
print(f"[harness] criando database isolado {DB_NAME} ...")
with psycopg.connect(_ADMIN_DSN, autocommit=True) as _conn:
    _conn.execute(f'create database "{DB_NAME}"')


# Preenchido na secao de paridade de ambiente, ~40 linhas abaixo. Declarado aqui
# porque `_teardown` (logo em seguida) precisa do nome, e o atexit pode disparar
# antes de a atribuicao acontecer se o import da arvore quebrar no meio.
_NOENV_DIR: str | None = None


def _teardown() -> None:
    print(f"[harness] derrubando database {DB_NAME} ...")
    try:
        _drop_db(DB_NAME)
        print("[harness] database derrubado.")
    except Exception as e:
        print(f"[harness] AVISO: falha ao derrubar {DB_NAME}: {e} — rode com --gc.")
    # O `.env` neutralizado (abaixo) vive num mkdtemp; sem isto cada execucao
    # largava um diretorio vazio em $TMPDIR (7 achados em 2026-08-22).
    if _NOENV_DIR:
        shutil.rmtree(_NOENV_DIR, ignore_errors=True)
        print(f"[harness] tempdir removido: {_NOENV_DIR}")


# atexit, e NÃO try/finally dentro do `__main__`: o database nasce aqui, no nível
# de módulo, então QUALQUER falha antes de `main()` — import quebrado na árvore
# sob teste, que é justamente o caso de uso deste harness — deixava um database
# órfão. atexit roda também na saída por exceção.
atexit.register(_teardown)

# atexit NÃO roda em sinal: o processo morre sem passar por ele. SIGINT já vinha
# de graça (vira KeyboardInterrupt), mas SIGTERM é o sinal padrão do `kill`, do
# `timeout` e de qualquer supervisor — e deixava database E tempdir órfãos
# (medido: `kill -TERM` depois do "schema inicializado" → rc=143, 1 database e 1
# tempdir vivos). `sys.exit` dentro do handler levanta SystemExit na thread
# principal, e aí o atexit roda.
# ponytail: SIGKILL continua deixando órfão (inevitável, o processo não executa
# nada); é pra isso que existe o `--gc`, que varre database E tempdir.
for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, lambda s, _f: sys.exit(128 + s))

os.environ["DATABASE_URL"] = f"postgresql://localhost:5432/{DB_NAME}"

# ─────────────────────────────────────────────────────────────────────────
# 2) Envs obrigatórias (mesmos valores/padrão de tests/conftest.py)
# ─────────────────────────────────────────────────────────────────────────
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-32-bytes")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("PII_HASH_PEPPER", "test-pepper-for-pytest-only-must-be-32-chars-long")
os.environ.setdefault("PII_AUDIT_DISABLED", "1")
os.environ.setdefault("RUN_BACKGROUND_TASKS", "0")
os.environ.setdefault("PLANS_V2_ENABLED", "0")
os.environ.pop("PAYWALL_ENABLED", None)

# ── Paridade de ambiente entre as duas árvores ───────────────────────────────
# `os.environ.pop("OPENAI_API_KEY")` sozinho NÃO segura: `core/observability.py`
# chama `load_app_env()` no import, que faz `os.environ.setdefault(k, v)` pra TODO
# o `.env` da RAIZ DA ÁRVORE IMPORTADA — a chave volta ao processo antes do
# primeiro caso, e a árvore que tem `.env` roda com 37 variáveis a mais que a
# outra (medido: worktree sem `.env` × checkout do usuário com WA_TOKEN,
# RESEND_API_KEY, SMTP_PASSWORD, OPENAI_API_KEY).
# Corta-se a FONTE, não a instância: `ROOT_DIR` passa a apontar pra um diretório
# vazio, então `load_app_env()` não acha `.env` em NENHUMA das duas árvores e o
# ambiente das duas execuções é exatamente o que este arquivo setou.
import config.env as _cfg_env  # noqa: E402

_NOENV_DIR = tempfile.mkdtemp(prefix="qa_cat_noenv_")
_cfg_env.ROOT_DIR = Path(_NOENV_DIR)
os.environ.pop("OPENAI_API_KEY", None)
if ARGS.ai:
    from dotenv import dotenv_values  # noqa: E402

    _real_env = dotenv_values(REAL_CHECKOUT_ENV)
    for _k in ("OPENAI_API_KEY", "OPENAI_MODEL"):
        if _real_env.get(_k):
            os.environ[_k] = _real_env[_k]
    if not os.environ.get("OPENAI_API_KEY"):
        print("[harness] AVISO: --ai pedido mas OPENAI_API_KEY não encontrada no .env real.")
AI_ON = bool(ARGS.ai and os.environ.get("OPENAI_API_KEY"))

# Fotografia do ambiente ANTES de importar a árvore: o que aparecer depois foi o
# import que injetou. É essa diferença que o relatório publica — verificação em
# runtime, não promessa escrita à mão.
_ENV_ANTES = set(os.environ)

from db import init_db  # noqa: E402

init_db()

# `db.init_db()` NÃO cria `system_event_logs`: ela nasce em
# `core.admin_dashboard.ensure_admin_tables()`, que só o painel chama. Sem ela o
# pipeline rodava DEGRADADO em relação a produção — `recent_event_exists`
# estourava, a dedup do nudge de primeiro lançamento não valia nada e cada
# lançamento cuspia duas linhas de erro no stderr, escondendo erro de verdade.
# Reusa o DDL de produção em vez de copiar o CREATE TABLE pra cá.
import asyncio  # noqa: E402
from core.admin_dashboard import ensure_admin_tables  # noqa: E402

asyncio.run(ensure_admin_tables())
print(f"[harness] schema inicializado (AI_ON={AI_ON}).")

import db  # noqa: E402
import parsers  # noqa: E402
import core.handle_incoming as hi  # noqa: E402
import core.handlers.launches as h_launches  # noqa: E402
from core.types import IncomingMessage  # noqa: E402
from db.connection import get_conn  # noqa: E402
# Estas NÃO são reexportadas pelo pacote `db` (só o dashboard as usa, via
# import direto do submódulo) — o harness importa do submódulo igual a ele.
from db.categories import (  # noqa: E402
    create_user_category,
    update_user_category,
    set_user_category_archived,
    delete_user_category,
)

# ── Verificação em runtime + kill switch de rede ─────────────────────────────
ENV_INJETADAS = sorted(set(os.environ) - _ENV_ANTES)

if AI_ON:
    NET_GUARD = "DESLIGADO (--ai): chamadas reais à OpenAI liberadas"
else:
    # O MESMO kill switch da suíte (requests, httpx, Session, OpenAI, ai_router,
    # send_email), não uma cópia: `@pytest.fixture` embrulha a função e guarda a
    # original em `_fixture_function`.
    import pytest as _pytest  # noqa: E402
    import tests.conftest as _conftest  # noqa: E402

    _MP = _pytest.MonkeyPatch()  # referência viva: sair de escopo não desfaz nada
    _fx = _conftest._block_outbound_network
    getattr(_fx, "_fixture_function", getattr(_fx, "__wrapped__", _fx))(_MP)
    NET_GUARD = "LIGADO (tests/conftest.py::_block_outbound_network)"

# A checagem que falha se a neutralização do `.env` for desfeita algum dia: sem
# --ai, chave no processo = ABORTA antes de qualquer caso (o B7 já cria usuário
# Pro; basta um caso futuro pra virar dinheiro gasto sem ninguém ter pedido).
if not AI_ON and os.environ.get("OPENAI_API_KEY"):
    raise SystemExit("[harness] ABORTADO: OPENAI_API_KEY entrou no processo sem --ai "
                     "(load_app_env reinjetou o .env da árvore?). Nada foi executado.")

print(f"[harness] rede: {NET_GUARD}")
print(f"[harness] OPENAI_API_KEY no processo: {bool(os.environ.get('OPENAI_API_KEY'))} · "
      f"variáveis injetadas pelo import da árvore: {ENV_INJETADAS or '(nenhuma)'}")

# ── Contadores de cobertura ──────────────────────────────────────────────────
# Quantas vezes a bateria chega em cada função que o PR #123 toca. Derivado, não
# afirmado: a seção de cegueiras do relatório imprime estes números.
CALLS: dict[str, int] = {}


def _count(mod, name: str, rotulo: str) -> None:
    fn = getattr(mod, name, None)
    if not callable(fn):
        CALLS.setdefault(rotulo + " (inexistente nesta árvore)", 0)
        return

    def wrapped(*a, _o=fn, _r=rotulo, **kw):
        CALLS[_r] = CALLS.get(_r, 0) + 1
        return _o(*a, **kw)

    setattr(mod, name, wrapped)
    CALLS.setdefault(rotulo, 0)


def _install_call_probes() -> None:
    import core.services.category_service as _cs
    import core.handlers.categories as _hc
    # `from ... import x` COPIA o nome: contar só no módulo de origem perde as
    # chamadas feitas pelas cópias (foi o que deu "learn_from_inference: 1").
    _count(_cs, "learn_from_signals", "category_service.learn_from_signals")
    _count(_hc, "learn_from_signals", "categories.learn_from_signals")
    _count(_cs, "learn_from_inference", "category_service.learn_from_inference")
    _count(h_launches, "learn_from_inference", "launches.learn_from_inference")
    # os dois hunks de CONSULTA do #123 (`intent_router` chama por atributo do
    # módulo, então o wrapper aqui alcança os dois caminhos)
    _count(h_launches, "list_launches", "launches.list_launches")
    _count(h_launches, "spend_query", "launches.spend_query")


_install_call_probes()

# ─────────────────────────────────────────────────────────────────────────
# 3) Oráculo (iii): captura do `reason` REAL usado na decisão
#    `infer_category` é importado por VALOR nos módulos abaixo, então o
#    wrapper tem que ser instalado em cada namespace, não no módulo de origem.
# ─────────────────────────────────────────────────────────────────────────
_INFER_LOG: list[tuple[str, str, str]] = []  # (texto, categoria, reason)


def _install_infer_probe() -> None:
    for mod in (parsers, h_launches):
        original = mod.infer_category

        def wrapped(*a, _orig=original, **kw):
            res = _orig(*a, **kw)
            text = kw.get("text_base") if "text_base" in kw else (a[1] if len(a) > 1 else "")
            _INFER_LOG.append((str(text), res.category, res.reason))
            return res

        mod.infer_category = wrapped


_install_infer_probe()

# ─────────────────────────────────────────────────────────────────────────
# 4) Helpers
# ─────────────────────────────────────────────────────────────────────────
_CAT_LINE_RE = re.compile(r"🏷️ Categoria:\s*(.+)")


# Cada mensagem INDIVIDUAL que o bot devolveu na bateria. Uma cena pode
# devolver várias (a mensagem com duas transações devolve duas), e no WhatsApp
# elas chegam como bolhas separadas — o `resposta` do JSON é a concatenação.
# Sem guardar as partes, a conferência das notas do vault obrigaria a colar
# duas mensagens numa bolha só.
PARTES_ENVIADAS: set[str] = set()


def send(uid: int, text: str) -> str:
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=text,
        message_id=str(_uuid.uuid4()), attachments=[], external_id=str(uid), raw={},
    ))
    partes = [m.text for m in out] if out else []
    PARTES_ENVIADAS.update(p.strip() for p in partes if p.strip())
    return "\n".join(partes) if partes else "(sem resposta)"


def new_uid(pro: bool = False) -> int:
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    if pro:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into auth_accounts(user_id, email, password_hash, plan) "
                    "values (%s, %s, 'x', 'pro')",
                    (uid, f"pro-{uid}@qa.local"),
                )
            conn.commit()
    return uid


def last_launch(uid: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, categoria, nota, valor, tipo from launches "
                "where user_id=%s order by id desc limit 1", (uid,))
            row = cur.fetchone()
    return dict(row) if row else None


def rows_since(uid: int, min_id: int) -> list[tuple[str, str, float]]:
    """(categoria, tipo, valor) de TODOS os lançamentos criados depois de `min_id`.

    `last_launch` só devolve o último — cego para a mensagem com duas
    transações, que é justamente o que o B8 mede. E só a CATEGORIA também era
    cego: dois lançamentos com as categorias certas e os valores trocados,
    duplicados ou zerados passavam ✅."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, tipo, valor from launches where user_id=%s and id>%s "
                "order by id", (uid, min_id))
            return [(r["categoria"], r["tipo"], round(float(r["valor"]), 2))
                    for r in (cur.fetchall() or [])]


def max_launch_id(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select coalesce(max(id), 0) as m from launches where user_id=%s",
                        (uid,))
            return int(cur.fetchone()["m"])


def n_launches(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from launches where user_id=%s", (uid,))
            return int(cur.fetchone()["n"])


def snapshot_rules(uid: int) -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select keyword, category from user_category_rules "
                "where user_id=%s order by keyword", (uid,))
            rows = cur.fetchall() or []
    return [f"{r['keyword']}→{r['category']}" for r in rows]


def cat_from_reply(resp: str) -> str | None:
    m = _CAT_LINE_RE.search(resp)
    return m.group(1).strip() if m else None


# ─────────────────────────────────────────────────────────────────────────
# 5) Registro de resultados
# ─────────────────────────────────────────────────────────────────────────
RESULTS: list[dict] = []
# Quantos casos CADA bloco tem que registrar. Sem isto o relatório imprimia
# `len(RESULTS)` sem nada com que comparar: um `raise` no topo do `bloco_b8`
# levava junto os 20 casos que ele ainda não tinha registrado e o relatório
# saía com 92 casos, 7 ❌ (em vez de 112 e 14 ❌) e exit code 0 — resultado
# INCOMPLETO com cara de resultado. Mexeu no número de casos? Atualize aqui:
# divergência para MAIS também é erro, senão a guarda envelhece sozinha.
CASOS_ESPERADOS = {"B1": 50, "B2": 14, "B3": 12, "B4": 5,
                   "B5": 5, "B6": 3, "B7": 2, "B8": 32}
TOTAL_ESPERADO = sum(CASOS_ESPERADOS.values())


def conferir_total() -> list[str]:
    """[] quando a bateria rodou inteira; uma linha por bloco fora do esperado."""
    got: dict[str, int] = {}
    for r in RESULTS:
        got[r["block"]] = got.get(r["block"], 0) + 1
    return [f"`{b}`: {got.get(b, 0)} caso(s) registrado(s), {n} esperado(s)"
            for b, n in CASOS_ESPERADOS.items() if got.get(b, 0) != n]

# ✅ significa "igual ao comportamento atual", não "correto". Os casos cuja nota
# começa com este marcador são verdes que documentam comportamento que a própria
# nota chama de indesejável — o relatório os conta e lista à parte.
VERDE_RESSALVA = "⚠️ ✅ = comportamento atual, NÃO desejável — "
# Discrepâncias vault × código encontradas na bateria de 2026-08-22. Cada item diz
# em que ARQUIVO do vault ela estava e qual MARCADOR prova que já foi corrigida —
# o estado é RECONFERIDO no vault em runtime (`--vault`) na hora de escrever o
# relatório. Sem isso o relatório afirmava que o vault "promete X" contra um vault
# que já tinha sido reescrito, e ia envelhecendo sozinho.
DISCREPANCIAS: list[dict] = [
    {
        "arquivo": "Interacoes/Corrigir categoria pelo botão.md",
        "marcador": "Duas coisas que esta nota afirmava e o código NÃO faz",
        "texto":
            "A nota prometia “ao escolher, atualiza (e pode virar regra)” e tinha "
            "“Oferece virar regra permanente” no checklist. **Medido (B5.02/B5.03):** a "
            "resposta é só `✅ Categoria do lançamento #N atualizada para *X*.` — "
            "`_apply_recategorize` não chama nenhum `learn_*` e não oferece nada.",
    },
    {
        "arquivo": "Interacoes/Corrigir categoria pelo botão.md",
        "marcador": "A lista não mostra as categorias personalizadas",
        "texto":
            "A mesma nota mostrava o usuário digitando a categoria. **Medido (B5.05):** a "
            "lista interativa tem 10 rows FIXAS (8 comuns + `outros` + `✏️ Outra (digitar)`) "
            "e nenhuma categoria custom do usuário aparece.",
    },
    {
        "arquivo": "Interacoes/Listar categorias e regras.md",
        "marcador": "Correção de 2026-08-22",
        "texto":
            "A nota mostrava `🏷️ Suas regras: • ifood → alimentação`. **Medido (B2.04):** o "
            "cabeçalho é `🧠 *Regras de categoria*` e o agrupamento é por CATEGORIA "
            "(`• *lazer* (1 regras)` + `└ rifa`), não por keyword.",
    },
    {
        "arquivo": "Interacoes/Ensinar regra de categoria.md",
        "marcador": "✅ Aprendido: sempre que aparecer *rifa*, vou usar *lazer*",
        "texto":
            "A nota mostrava `✅ Aprendido! Agora *ifood* vai pra *alimentação*.`. **Medido "
            "(B2.01):** `✅ Aprendido: sempre que aparecer *rifa*, vou usar *lazer*`.",
    },
    {
        "arquivo": "Interacoes/Ensinar regra de categoria.md",
        "marcador": "`criar categoria X` NÃO cria categoria",
        "texto":
            "A mesma nota listava `criar categoria X` como gatilho de criação. **Medido "
            "(B2.12/B2.13):** nem `criar categoria viagens` nem `criar categoria viagens "
            "linkar decolar` criam linha em `user_categories` — só regra. Criar categoria "
            "de verdade é só pelo dashboard.",
    },
    {
        "arquivo": "Interacoes/Remover regra de categoria.md",
        "marcador": "Correções de 2026-08-22",
        "texto":
            "A nota mostrava `🗑️ Regra *ifood → alimentação* removida.`. **Medido (B2.05):** "
            "`✅ Regra removida: *rifa*` (sem 🗑️ e sem a categoria). E há um caminho não "
            "documentado: remover por CATEGORIA apaga N regras de uma vez (B2.07: "
            "`✅ 2 regras da categoria *lazer* foram removidas.`).",
    },
    {
        "arquivo": "Dominios/Categorias e Regras.md",
        "marcador": "Só pelo dashboard",
        "texto":
            "A nota de domínio listava 4 interações; o domínio tem ≥10 (criar/renomear/"
            "arquivar/excluir categoria custom, categoria custom reconhecida no lançamento, "
            "categoria automática por palavra-chave, recategorizar pela IA).",
    },
]


def vault_estado(item: dict) -> str:
    """Reconfere a discrepância no vault AGORA (não na data em que foi escrita)."""
    caminho = os.path.join(ARGS.vault, item["arquivo"])
    if not os.path.exists(caminho):
        return f"⚠️ arquivo ausente no vault (`{caminho}`) — não reconferido"
    with open(caminho, encoding="utf-8") as f:
        conteudo = f.read()
    if item["marcador"] in conteudo:
        return f"✅ já corrigida no vault (conferido em runtime: `{item['arquivo']}` traz `{item['marcador'][:48]}`)"
    return f"❌ AINDA ABERTA no vault (`{item['arquivo']}` não traz `{item['marcador'][:48]}`)"


# ── Bolhas de fala do bot no vault × respostas realmente capturadas ──────────
# O vault escreve os diálogos como `> **PigBank:** <texto>`. Nada conferia esse
# texto contra a medição, e uma nota chegou a trazer QUATRO bolhas de um caso
# cuja `resposta` no JSON era string vazia (o harness mandava a mensagem e
# jogava a saída fora). Critério aqui: uma bolha é UMA mensagem do bot INTEIRA
# e VERBATIM — se não bate com nenhuma `resposta` desta execução, é ficção.
_BOLHA_INI = re.compile(r"^>\s*\*\*PigBank:\*\*\s?(.*)$")
_FALA_USUARIO = re.compile(r"^>\s*\*\*Você:\*\*")
_ANOTACAO_FIM = re.compile(r"\s*(\*\(.*\)\*|←.*)\s*$")   # `*(B5.02)*` / `← esperado: x`
_SO_ANOTACAO = re.compile(r"^\s*[*_(].*$")               # linha inteira de comentário
_CID_NA_ANOTACAO = re.compile(r"B\d\.\d\d")                # `*(B5.02)*` → B5.02


def _desmarca(linha: str) -> str:
    """Tira só o `> ` do blockquote — `lstrip()` comeria a indentação do `└`."""
    corpo = linha[1:] if linha.startswith(">") else linha
    return corpo[1:] if corpo.startswith(" ") else corpo


def _bolhas(texto: str) -> list[tuple[str, str | None]]:
    """[(fala do bot, caso citado na anotação ou None)]."""
    linhas = texto.splitlines()
    saida: list[tuple[str, str | None]] = []
    i = 0
    while i < len(linhas):
        m = _BOLHA_INI.match(linhas[i])
        if not m:
            i += 1
            continue
        anotacao = ""
        primeira = _ANOTACAO_FIM.sub("", m.group(1))
        if primeira != m.group(1):
            anotacao = m.group(1)[len(primeira):]
        corpo = [primeira] if primeira.strip() else []
        i += 1
        while i < len(linhas) and linhas[i].startswith(">"):
            if _BOLHA_INI.match(linhas[i]) or _FALA_USUARIO.match(linhas[i]):
                break
            crua = _desmarca(linhas[i])
            if crua.strip() and _SO_ANOTACAO.match(crua):
                anotacao = crua           # a anotação FECHA a bolha e diz de que caso ela é
                break
            limpa = _ANOTACAO_FIM.sub("", crua)
            if limpa != crua:
                anotacao = crua[len(limpa):]
            corpo.append(limpa)
            i += 1
        citado = _CID_NA_ANOTACAO.search(anotacao)
        saida.append(("\n".join(corpo).strip(), citado.group(0) if citado else None))
    return saida


def bolhas_vault() -> dict:
    """Confere as bolhas contra a medição e diz DE QUANTAS — `0 sem respaldo` sozinho
    não distingue "tudo conferido" de "nada conferido".

    {notas, bolhas, orfas, avisos, notas_vault, bolhas_vault, vault_ok}
    """
    # `resposta` (cena inteira) OU uma mensagem individual: as duas contam como
    # verbatim; o que não pode é a bolha não existir em lugar nenhum.
    respostas = {r["resposta"].strip() for r in RESULTS if r["resposta"]} | PARTES_ENVIADAS
    por_cid = {r["cid"]: r for r in RESULTS}
    notas = bolhas = notas_vault = bolhas_vault_total = 0
    orfas: list[tuple[str, str, str]] = []
    avisos: list[tuple[str, str, str]] = []
    for caminho in sorted(glob.glob(os.path.join(ARGS.vault, "**", "*.md"),
                                    recursive=True)):
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        do_arquivo = _bolhas(conteudo)
        if do_arquivo:
            notas_vault += 1
            bolhas_vault_total += len(do_arquivo)
        # Só as notas que dizem ter sido medidas POR ESTE harness — as outras
        # citam outra bateria e não é este relatório que responde por elas.
        # A busca vale para o arquivo INTEIRO: com o `[:600]` de antes, uma nota
        # que citasse o harness depois do char 600 saía da auditoria sem uma
        # linha de aviso (medido: `Referencia/Checklist QA.md`, citação no 2685).
        if "category_qa_harness.py" not in conteudo:
            continue
        notas += 1
        rel = os.path.relpath(caminho, ARGS.vault)
        for b, citado in do_arquivo:
            bolhas += 1
            if b not in respostas:
                orfas.append((rel, b, "não existe em NENHUMA resposta desta execução"))
            elif citado is None:
                # Verbatim, mas sem `*(B5.02)*`: dá pra provar que o bot já disse
                # isso alguma vez, NÃO que disse na cena que a nota descreve.
                avisos.append((rel, b, "sem anotação de caso — verbatim conferido, "
                                       "ATRIBUIÇÃO não conferível"))
            elif citado not in por_cid:
                avisos.append((rel, b, f"anotação cita `{citado}`, que não existe nesta "
                                       f"bateria — atribuição não conferível"))
            elif b != por_cid[citado]["resposta"].strip():
                # Verbatim, mas de OUTRA cena: o texto existe, a atribuição é
                # que está errada. Mesma classe da nota colada em cima de um
                # resultado sem conferir se o resultado é do tipo que ela
                # descreve — só que aqui do lado do vault.
                orfas.append((rel, b, f"verbatim, mas NÃO é a resposta de `{citado}` "
                                      f"(é de outra cena)"))
    return {"notas": notas, "bolhas": bolhas, "orfas": orfas, "avisos": avisos,
            "notas_vault": notas_vault, "bolhas_vault": bolhas_vault_total,
            "vault_ok": os.path.isdir(ARGS.vault)}


def record(block: str, cid: str, desc: str, esperado, obtido_resp, obtido_db,
           reason, veredito: str, *, notas: str = "", rules_diff: str = "",
           resposta: str = "", excecao: bool = False) -> dict:
    r = {
        "block": block, "cid": cid, "desc": desc,
        "esperado": esperado, "obtido_resp": obtido_resp, "obtido_db": obtido_db,
        "reason": reason, "veredito": veredito, "notas": notas,
        # `excecao` separa "o caso rodou e não gerou lançamento" de "o pipeline
        # estourou". Sem esse campo o chamador só via `obtido_db is None` nos
        # dois e colava a nota errada em cima do crash.
        "excecao": excecao,
        # 400 caracteres cortavam a resposta mais longa da bateria (B8.15, o
        # menu de cartões) exatamente onde a evidência ficava; e as notas do
        # vault citam a resposta VERBATIM, então cortar é falsificar.
        "rules_diff": rules_diff, "resposta": (resposta or "")[:1200],
    }
    RESULTS.append(r)
    return r


def launch_case(block: str, cid: str, uid: int, text: str, esperado: str,
                *, notas: str = "", aceitaveis: set[str] | None = None,
                exige_lancamento: bool = False) -> dict:
    """Manda um lançamento e confere os 3 oráculos + diff de regras."""
    before = snapshot_rules(uid)
    _INFER_LOG.clear()
    n_before = n_launches(uid)
    try:
        resp = send(uid, text)
    except Exception as e:
        # ❌, não ⚠️: o pipeline estourando é o PIOR resultado possível, e o
        # veredito mais brando fazia a contagem de vermelhos CAIR quando uma
        # regressão aparecia (medido: 15 ❌ → 14 ❌ + 1 ⚠️ com um RuntimeError
        # injetado no B8.04). O `notas` do chamador vai junto — descartá-lo
        # apagava o prefixo `[classe]` e jogava o caso num balde "(sem classe)".
        return record(block, cid, text, esperado, f"EXCEÇÃO {e.__class__.__name__}: {e}",
                      None, None, "❌", excecao=True,
                      notas=f"{notas} — EXCEÇÃO no pipeline: "
                            f"{traceback.format_exc().splitlines()[-1]}")
    cat_resp = cat_from_reply(resp)
    row = last_launch(uid) if n_launches(uid) > n_before else None
    cat_db = row["categoria"] if row else None
    reason = _INFER_LOG[-1][2] if _INFER_LOG else None
    after = snapshot_rules(uid)
    novos = [x for x in after if x not in before]
    rules_diff = ("+ " + ", ".join(novos)) if novos else "(nenhuma regra nova)"

    if row is None:
        # ⚠️ é o default honesto: sem lançamento não dá pra julgar categoria.
        # Mas quando "virar lançamento" faz PARTE do comportamento pretendido
        # (bloco B8), não virar é FALHA, não indefinição — deixar ⚠️ ali
        # esconderia num balde de "inconclusivo" a mensagem que o bot recusa.
        vered = "❌" if exige_lancamento else "⚠️"
    elif aceitaveis is not None:
        vered = "🔍" if cat_db in aceitaveis else "❌"
    else:
        vered = "✅" if (cat_db == esperado and cat_resp == esperado) else "❌"
    return record(block, cid, text, esperado, cat_resp, cat_db, reason, vered,
                  notas=notas, rules_diff=rules_diff, resposta=resp)


def plain_case(block: str, cid: str, desc: str, esperado, obtido, *,
               reason=None, notas: str = "", resposta: str = "",
               rules_diff: str = "") -> dict:
    """Caso cujo oráculo não é a linha de categoria de um lançamento."""
    vered = "✅" if obtido == esperado else "❌"
    return record(block, cid, desc, esperado, obtido, obtido, reason, vered,
                  notas=notas, resposta=resposta, rules_diff=rules_diff)


# ═════════════════════════════════════════════════════════════════════════
# B1 — as 16 categorias das LOCAL_RULES + fallback `outros`
# Cada caso roda num usuário NOVO: `learn_from_inference` grava regra quando
# o reason é local_rule/ticker_match, e uma regra aprendida no caso anterior
# contaminaria o seguinte.
# ═════════════════════════════════════════════════════════════════════════
B1_CASOS: list[tuple[str, str, str]] = [
    # (mensagem, categoria esperada, nota)
    ("gastei 45 no ifood",                 "alimentação", ""),
    ("paguei 32 no almoço",                "alimentação", ""),
    ("gastei 28 na PADARIA",               "alimentação", "variação de CAIXA ALTA"),
    ("gastei 60 no açaí",                  "alimentação", "acento no keyword"),

    ("gastei 250 no supermercado",         "mercado", ""),
    ("comprei material de limpeza 80",     "mercado", "armadilha: parece moradia; literal está em mercado"),
    ("gastei 120 num jogo de cama",        "mercado", "armadilha: KEYWORD_BLOCKERS tira 'jogo' de lazer"),
    ("gastei 200 no atacadão",             "mercado", ""),

    ("gastei 25 no uber",                  "transporte", ""),
    ("paguei 180 de gasolina",             "transporte", ""),
    ("paguei 90 de passagem de ônibus",    "transporte", "armadilha: 'passagem' é de lazer, bloqueada aqui"),
    ("paguei 900 de IPVA",                 "transporte", ""),

    ("gastei 120 na farmácia",             "saúde", ""),
    ("paguei 150 na academia",             "saúde", ""),
    ("paguei 300 no dentista",             "saúde", ""),

    ("paguei 1500 de aluguel",             "moradia", ""),
    ("paguei 180 de conta de luz",         "moradia", ""),
    ("paguei 120 de internet",             "moradia", ""),
    ("gastei 400 com aluguel de carro",    "lazer", "armadilha: blocker tira de moradia; literal 'aluguel de carro' é de lazer"),

    ("gastei 60 no cinema",                "lazer", ""),
    ("gastei 250 no show",                 "lazer", ""),
    ("paguei 400 de hotel",                "lazer", ""),

    ("paguei 800 de faculdade",            "educação", ""),
    ("comprei um livro 45",                "educação", ""),
    ("gastei 300 no curso de inglês",      "educação", ""),

    ("paguei 39,90 de netflix",            "assinaturas", ""),
    ("paguei 55 do amazon prime",          "assinaturas", "armadilha: 'amazon' é de compras online, bloqueada por 'amazon prime'"),
    ("paguei 34,90 de spotify",            "assinaturas", ""),

    ("gastei 180 no petshop",              "pets", ""),
    ("comprei ração 120",                  "pets", ""),
    ("gastei 90 com vacina do cachorro",   "pets", "armadilha: blocker tira 'vacina' de saúde quando é do pet"),

    ("comprei 150 na shopee",              "compras online", ""),
    ("gastei 89 na amazon",                "compras online", ""),
    ("comprei 200 no mercado livre",       "compras online", "armadilha: 'mercado livre' vem antes de 'mercado'"),

    ("gastei 90 no cabeleireiro",          "beleza", ""),
    ("paguei 60 na manicure",              "beleza", ""),
    ("gastei 45 no barbeiro",              "beleza", ""),

    ("comprei 500 de bitcoin",             "criptomoedas", ""),
    ("comprei 200 em ethereum",            "criptomoedas", ""),
    ("gastei 300 no mercado bitcoin",      "investimento_aporte",
     "armadilha nomeada no código: 'mercado bitcoin' é literal do bloco de APORTE, que vem antes de criptomoedas"),

    ("gastei 1000 com tesouro direto",     "investimento_aporte",
     "'aportei ...' é sequestrado pelo handler de investimentos e nem vira lançamento"),
    ("comprei 5000 de PETR4",              "investimento_aporte", "ticker MAIÚSCULO → reason esperado ticker_match"),
    ("comprei 5000 de petr4",              "outros",
     "ticker minúsculo NÃO é aceito sem IA (comentário do código: 'o GPT decide'); sem --ai cai em outros"),

    ("gastei 1000 em investimentos",       "investimentos",
     VERDE_RESSALVA + "rótulo ÓRFÃO: existe nas LOCAL_RULES mas não em "
     "SYSTEM_CATEGORIES_SEED nem em ALLOWED_CATEGORIES — o lançamento é gravado "
     "com um rótulo que o dashboard não conhece"),
    ("recebi 2000 de resgate",             "investimento_resgate",
     "rótulo interno; receita"),

    ("recebi 120 de dividendos",           "rendimentos", ""),
    ("recebi 80 de juros",                 "rendimentos", ""),
    ("paguei 90 de juros do cheque especial", "outros",
     "armadilha: blocker tira 'juros' de rendimentos; nada mais casa. "
     "('juros do cartão' não serve como caso: é sequestrado pelo handler de cartão)"),

    ("gastei 70 com zzqwx",                "outros", "fallback puro"),
    ("gastei 50 numa sexta-feira",         "outros", "armadilha: blocker tira 'feira' de alimentação"),
]


def bloco_b1() -> None:
    for i, (msg, esperado, nota) in enumerate(B1_CASOS, 1):
        uid = new_uid()
        launch_case("B1", f"B1.{i:02d}", uid, msg, esperado, notas=nota)


# ═════════════════════════════════════════════════════════════════════════
# B2 — regras do usuário (user_category_rules) pelo WhatsApp
# ═════════════════════════════════════════════════════════════════════════
def bloco_b2() -> None:
    # B2.1 ensinar e provar no lançamento seguinte
    uid = new_uid()
    r = send(uid, "aprender rifa como lazer")
    plain_case("B2", "B2.01", "aprender rifa como lazer → confirmação + regra no banco",
               True, ("Aprendido" in r and "rifa" in r and "lazer" in r
                      and snapshot_rules(uid) == ["rifa→lazer"]), resposta=r,
               notas="oráculo casado com o BANCO: só a mensagem passaria com 0 regras gravadas")
    launch_case("B2", "B2.02", uid, "gastei 20 na rifa", "lazer",
                notas="regra ensinada no B2.01 deve vencer o fallback")

    # B2.2 sobrescrever a mesma keyword
    uid = new_uid()
    send(uid, "aprender uber como transporte")
    r = send(uid, "aprender uber como lazer")
    launch_case("B2", "B2.03", uid, "gastei 30 no uber", "lazer",
                notas=f"regra reescrita uber→lazer (2ª resposta: {r[:80]!r}); "
                      f"LOCAL_RULES diriam transporte")

    # B2.3 listar
    uid = new_uid()
    send(uid, "aprender rifa como lazer")
    r = send(uid, "categorias")
    plain_case("B2", "B2.04", "`categorias` lista a regra criada",
               True, ("Regras de categoria" in r and "rifa" in r and "lazer" in r),
               resposta=r)

    # B2.4 remover por keyword
    uid = new_uid()
    send(uid, "aprender rifa como lazer")
    r = send(uid, "remover regra rifa")
    plain_case("B2", "B2.05", "remover regra <keyword>",
               True, ("Regra removida" in r and "rifa" in r and snapshot_rules(uid) == []),
               resposta=r, rules_diff=str(snapshot_rules(uid)),
               notas="mensagem + tabela vazia; a mensagem sozinha passaria sem apagar nada")
    launch_case("B2", "B2.06", uid, "gastei 20 na rifa", "outros",
                notas="após remover a regra, volta ao fallback")

    # B2.5 remover por CATEGORIA
    uid = new_uid()
    send(uid, "aprender rifa como lazer")
    send(uid, "aprender bingo como lazer")
    r = send(uid, "remover regra lazer")
    plain_case("B2", "B2.07", "remover regra <categoria> apaga as N regras dela",
               True, ("2 regras" in r and "lazer" in r and snapshot_rules(uid) == []),
               resposta=r, rules_diff=str(snapshot_rules(uid)),
               notas="era `\"2 regras\" in r or \"removidas\" in r` — um "
                     "\"0 regras removidas\" passaria. Agora exige o número certo E a "
                     "tabela vazia")

    # B2.6 remover inexistente
    uid = new_uid()
    r = send(uid, "remover regra zzqwx")
    plain_case("B2", "B2.08", "remover regra inexistente → aviso, sem estourar",
               True, ("Não encontrei" in r and "zzqwx" in r), resposta=r)

    # B2.7 regra do usuário CONTRADIZ LOCAL_RULES
    uid = new_uid()
    send(uid, "aprender netflix como lazer")
    launch_case("B2", "B2.09", uid, "paguei 39,90 de netflix", "lazer",
                notas="regra do usuário (passo 3) vence LOCAL_RULES (passo 5), "
                      "que diriam assinaturas")

    # B2.8 regra CURTA batendo por SUBSTRING (frouxidão de get_memorized_category)
    uid = new_uid()
    send(uid, "aprender cafe como beleza")
    launch_case("B2", "B2.10", uid, "gastei 18 na cafeteria", "beleza",
                notas=VERDE_RESSALVA +
                      "get_memorized_category aceita `kw_norm in memo_norm`: a regra "
                      "'cafe' casa dentro de 'cafeteria'. Registrado como comportamento "
                      "medido, não como acerto desejável")

    # B2.9 ticker vence a regra do usuário (passo 2 antes do passo 3) — sem teste no repo
    uid = new_uid()
    send(uid, "aprender PETR4 como mercado")
    launch_case("B2", "B2.11", uid, "comprei 1000 de PETR4", "mercado",
                notas="ordem REAL do infer_category: ticker (passo 2) roda ANTES da "
                      "regra do usuário (passo 3) — regra para PETR4 nunca vence")

    # B2.12 `criar categoria X` SEM " linkar " não cria categoria nenhuma
    uid = new_uid()
    r = send(uid, "criar categoria viagens")
    ncat = len(db.list_custom_category_names(uid) or [])
    plain_case("B2", "B2.12",
               "`criar categoria viagens` (sem ' linkar ') cria linha em user_categories?",
               0, ncat, resposta=r,
               notas=VERDE_RESSALVA +
                     "o handler só grava REGRA; sem ' linkar ' devolve o texto de formato. "
                     "Criar categoria de verdade só pelo dashboard")

    # B2.13 `criar categoria X linkar Y` grava REGRA, não categoria
    uid = new_uid()
    r = send(uid, "criar categoria viagens linkar decolar")
    ncat = len(db.list_custom_category_names(uid) or [])
    plain_case("B2", "B2.13",
               "`criar categoria viagens linkar decolar` cria linha em user_categories?",
               0, ncat, resposta=r,
               notas=VERDE_RESSALVA + f"grava só a regra: {snapshot_rules(uid)}",
               rules_diff="+ " + ", ".join(snapshot_rules(uid)))
    launch_case("B2", "B2.14", uid, "gastei 300 na decolar", "viagens",
                notas="a REGRA criada funciona; a categoria em si não existe em "
                      "user_categories (não aparece no dashboard)")


# ═════════════════════════════════════════════════════════════════════════
# B3 — categorias custom (semeadas por db.create_user_category)
# ═════════════════════════════════════════════════════════════════════════
def bloco_b3() -> None:
    # B3.1 token distintivo
    uid = new_uid()
    create_user_category(uid, "gastos com namorada")
    launch_case("B3", "B3.01", uid, "gastei 400 com a namorada", "gastos com namorada",
                notas="token distintivo 'namorada'; 'gastos'/'com' são genéricos")

    # B3.2 nome com ACENTO — tem que voltar exatamente como cadastrado
    uid = new_uid()
    create_user_category(uid, "saúde da família")
    launch_case("B3", "B3.02", uid, "gastei 200 com a família", "saúde da família",
                notas="infer_category NÃO canonicaliza custom (canonicalize tiraria o acento)")

    # B3.3 nome só de tokens genéricos não pode casar nada
    uid = new_uid()
    create_user_category(uid, "gastos gerais")
    launch_case("B3", "B3.03", uid, "gastei 70 com zzqwx", "outros",
                notas="'gastos'/'gerais' estão em _CUSTOM_CATEGORY_GENERIC_TOKENS")

    # B3.4 duas customs sobrepostas — a mais específica vence
    uid = new_uid()
    create_user_category(uid, "cachorro")
    create_user_category(uid, "cachorro do vizinho")
    launch_case("B3", "B3.04", uid, "gastei 50 com o cachorro do vizinho",
                "cachorro do vizinho",
                notas="empate desfeito por nº de tokens casados e comprimento")
    uid2 = new_uid()
    create_user_category(uid2, "cachorro")
    create_user_category(uid2, "cachorro do vizinho")
    launch_case("B3", "B3.05", uid2, "gastei 50 com o cachorro", "cachorro",
                notas="mesma dupla, texto sem 'vizinho'; "
                      "list_custom_category_names ordena por length desc")

    # B3.6 colisão com categoria de sistema
    uid = new_uid()
    try:
        create_user_category(uid, "Saúde")
        obtido = "criou (sem erro)"
    except ValueError as e:
        obtido = str(e)
    plain_case("B3", "B3.06", 'create_user_category(uid, "Saúde") com a system "saúde" semeada',
               "CATEGORIA_DUPLICADA", obtido,
               notas="_normalize_category_name faz lowercase; unique (user_id, name)")

    # B3.7 arquivar some da inferência
    uid = new_uid()
    cat = create_user_category(uid, "gastos com namorada")
    launch_case("B3", "B3.07", uid, "gastei 100 com a namorada", "gastos com namorada",
                notas="antes de arquivar")
    set_user_category_archived(uid, cat["id"], True)
    launch_case("B3", "B3.08", uid, "gastei 100 com a namorada", "outros",
                notas="depois de arquivar: list_custom_category_names filtra is_archived")

    # B3.9 renomear com cascata (5 tabelas) — sem teste nenhum no repo
    uid = new_uid()
    cat = create_user_category(uid, "gastos com namorada")
    send(uid, "gastei 400 com a namorada")
    lid = last_launch(uid)["id"]
    update_user_category(uid, cat["id"], new_name="gastos com a esposa")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select categoria from launches where id=%s", (lid,))
            cascata = cur.fetchone()["categoria"]
    plain_case("B3", "B3.09", "renomear categoria custom faz cascata em launches.categoria",
               "gastos com a esposa", cascata,
               notas="update_user_category cascateia em launches, credit_transactions, "
                     "category_budgets, budget_alert_sent, user_category_rules")
    launch_case("B3", "B3.10", uid, "gastei 90 com a esposa", "gastos com a esposa",
                notas="inferência passa a usar o nome novo")
    launch_case("B3", "B3.11", uid, "gastei 90 com a namorada", "outros",
                notas="nome antigo não casa mais")

    # B3.12 apagar categoria COM lançamentos deve recusar
    uid = new_uid()
    cat = create_user_category(uid, "gastos com namorada")
    send(uid, "gastei 400 com a namorada")
    try:
        delete_user_category(uid, cat["id"])
        obtido = "apagou (sem erro)"
    except ValueError as e:
        obtido = str(e)
    plain_case("B3", "B3.12", "apagar categoria custom que tem lançamentos",
               "CATEGORIA_COM_LANCAMENTOS", obtido)


# ═════════════════════════════════════════════════════════════════════════
# B4 — o bug do PR #123: regra envenenada rouba a categoria custom
# ═════════════════════════════════════════════════════════════════════════
def bloco_b4() -> None:
    # ── B4.01/.02: custom JÁ criada antes do lançamento envenenador ────────
    uid = new_uid()
    create_user_category(uid, "gastos com namorada")
    antes = snapshot_rules(uid)
    r_env = send(uid, "gastei 80 com a namorada no cinema")
    depois = snapshot_rules(uid)
    novas = [x for x in depois if x not in antes]
    envenenou = any("namorada" in x and not x.endswith("→gastos com namorada") for x in novas)
    plain_case("B4", "B4.01",
               "custom JÁ existe: 'gastei 80 com a namorada no cinema' grava alguma "
               "regra com o token 'namorada'?",
               False, envenenou,
               notas="com a custom já criada, infer_category devolve reason=user_category "
                     "e learn_from_inference retorna cedo (o gate de reason ignora "
                     "default/user_rule/user_category). Regras gravadas nesta árvore: "
                     f"{novas or '(nenhuma)'}",
               resposta=r_env,
               rules_diff=("+ " + ", ".join(novas)) if novas else "(nenhuma)")
    launch_case("B4", "B4.02", uid, "gastei 400 com a namorada", "gastos com namorada",
                notas="lançamento seguinte: é aqui que o estrago apareceria")

    # ── B4.03: ORDEM TEMPORAL (regra aprendida ANTES da custom existir) ────
    # É a sequência real do cliente: o usuário lança primeiro e só depois cria
    # a categoria. O guard do #123 roda no momento do APRENDIZADO, quando a
    # custom ainda não existe — então não protege esta ordem.
    uid = new_uid()
    a0 = snapshot_rules(uid)
    send(uid, "80 namorada cinema")
    a1 = snapshot_rules(uid)
    novas = [x for x in a1 if x not in a0]
    create_user_category(uid, "gastos com namorada")
    launch_case("B4", "B4.03", uid, "200 namorada cinema", "gastos com namorada",
                notas="regra aprendida ANTES da custom existir: "
                      f"{novas or '(nenhuma)'}. O passo 3 (user_rule) roda antes do "
                      "passo 4 (user_category) e sequestra o lançamento")

    # ── B4.04: o caso que o #123 de fato corrige ──────────────────────────
    # Entrada ALCANÇÁVEL EM PRODUÇÃO: confirmação de foto de cupom. O
    # `core/handlers/pending.py:57` chama `add_from_entities(...,
    # category_reason="image_confirmed")` com a categoria que o usuário
    # confirmou; `image_confirmed` não está no gate de reason do
    # `learn_from_inference` ({default, user_rule, user_category}), então o
    # aprendizado roda com `guard_local_conflict=True` e é exatamente aí que o
    # guard novo do #123 entra. (A versão anterior deste caso chamava
    # `learn_from_inference(..., reason="local_rule")` direto — entrada que
    # nenhum caller de produção produz.)
    uid = new_uid()
    create_user_category(uid, "gastos com namorada")
    a0 = snapshot_rules(uid)
    h_launches.add_from_entities(
        uid, tipo="despesa", valor=80.0, alvo="namorada cinema",
        nota="namorada cinema", categoria="lazer", category_reason="image_confirmed",
    )
    a1 = snapshot_rules(uid)
    novas = [x for x in a1 if x not in a0]
    roubou = any("namorada" in x for x in novas)
    plain_case("B4", "B4.04",
               "confirmação de foto de cupom (add_from_entities com "
               "category_reason='image_confirmed', categoria 'lazer', alvo "
               "'namorada cinema') com a custom já criada — grava regra com 'namorada'?",
               False, roubou,
               notas="ESTE é o caso que separa as duas árvores: o guard novo do #123 "
                     "(category_service.learn_from_signals, dentro do "
                     "guard_local_conflict) recusa candidatos cujo token pertence a uma "
                     f"categoria custom do usuário. Regras gravadas nesta árvore: "
                     f"{novas or '(nenhuma)'}",
               rules_diff=("+ " + ", ".join(novas)) if novas else "(nenhuma)")

    # e o efeito no lançamento seguinte — a nota é DERIVADA do que esta árvore
    # gravou, não uma frase fixa (a fixa dizia "com a regra envenenada gravada"
    # e ficava factualmente errada na árvore que não a grava).
    regras_agora = snapshot_rules(uid)
    r405 = launch_case("B4", "B4.05", uid, "300 namorada cinema", "gastos com namorada",
                       notas="")
    # A nota SÓ pode ser colada se o caso for do tipo que ela descreve: se o
    # pipeline estourou, a atribuição abaixo apagaria o traceback e afirmaria
    # "o lançamento caiu em `None`" sobre um lançamento que nunca aconteceu.
    r405["notas"] = r405["notas"] if r405["excecao"] else (
        f"regras vivas antes deste lançamento: {regras_agora or '(nenhuma)'} — "
        f"o lançamento caiu em `{r405['obtido_db']}` por `{r405['reason']}`. "
        + ("A regra com 'namorada' sequestrou o lançamento."
           if any("namorada" in x for x in regras_agora)
           else "Mesmo SEM a regra com 'namorada', a cena termina fora da categoria "
                "custom: a regra `cinema→lazer` (aprendida no mesmo evento, e que o "
                "guard do #123 não recusa) vence no passo 3, antes do passo 4."
           if any("cinema" in x for x in regras_agora)
           else "Sem regra nenhuma gravada.")
    )


# ═════════════════════════════════════════════════════════════════════════
# B5 — correção pelo botão "categoria errada?"
# ═════════════════════════════════════════════════════════════════════════
def bloco_b5() -> None:
    from adapters.whatsapp import wa_runtime

    # B5.1 pending gravado pelo lançamento no WhatsApp
    # A resposta do lançamento é CAPTURADA, não descartada: é ela que a nota do
    # vault transcreve como a fala do bot. Enquanto a saída ia pro lixo, a nota
    # tinha quatro bolhas de fala contra um `resposta` que era string vazia.
    uid = new_uid()
    r = send(uid, "gastei 70 com zzqwx")
    pend = db.get_pending_action(uid)
    plain_case("B5", "B5.01", "lançamento no WhatsApp grava pending recategorize_launch_offer",
               "recategorize_launch_offer",
               (pend or {}).get("action_type") if isinstance(pend, dict) else str(pend),
               resposta=r)

    # B5.2 aplicar categoria de sistema pelo botão
    lid = last_launch(uid)["id"]
    r = wa_runtime._apply_recategorize(uid, lid, "lazer")
    plain_case("B5", "B5.02", "_apply_recategorize(..., 'lazer') grava lazer",
               "lazer", last_launch(uid)["categoria"], resposta=r)

    # B5.3 categoria de sistema ACENTUADA
    uid = new_uid()
    send(uid, "gastei 70 com zzqwx")
    lid = last_launch(uid)["id"]
    r = wa_runtime._apply_recategorize(uid, lid, "saúde")
    plain_case("B5", "B5.03", "_apply_recategorize com rótulo acentuado 'saúde'",
               "saúde", last_launch(uid)["categoria"], resposta=r,
               notas="canonicalize_category_label mapeia 'saude'→'saúde' (está em CATEGORY_LABELS)")

    # B5.4 categoria CUSTOM digitada em "Outra (digitar)" — perde o acento
    uid = new_uid()
    create_user_category(uid, "saúde da família")
    send(uid, "gastei 70 com zzqwx")
    lid = last_launch(uid)["id"]
    r = wa_runtime._apply_recategorize(uid, lid, "saúde da família")
    gravado = last_launch(uid)["categoria"]
    plain_case("B5", "B5.04",
               "_apply_recategorize com nome de categoria CUSTOM acentuada",
               "saúde da família", gravado, resposta=r,
               notas="canonicalize_category_label normaliza (tira acento) qualquer rótulo "
                     "fora de CATEGORY_LABELS; user_categories guarda 'saúde da família' e "
                     "o usage_count casa por lower(categoria)=uc.name")

    # B5.5 a lista interativa não oferece categoria custom
    uid = new_uid()
    create_user_category(uid, "gastos com namorada")
    titles: list[str] = []
    try:
        import adapters.whatsapp.wa_client as wc
        orig = wc.send_interactive_list
        captured: dict = {}

        def fake(**kw):
            captured.update(kw)

        wa_runtime.send_interactive_list = fake
        try:
            wa_runtime._send_recategorize_list("5511999999999", "corpo", 1)
            for sec in captured.get("sections", []):
                titles += [row["title"] for row in sec["rows"]]
        finally:
            # sem o finally, uma exceção aqui deixava o fake instalado no
            # wa_runtime pelo resto do processo (todos os blocos seguintes)
            wa_runtime.send_interactive_list = orig
    except Exception as e:
        titles = [f"EXCEÇÃO: {e}"]
    plain_case("B5", "B5.05", "a lista interativa de recategorização inclui a categoria custom?",
               True, ("gastos com namorada" in titles),
               notas=f"rows oferecidas ({len(titles)}): {titles}")


# ═════════════════════════════════════════════════════════════════════════
# B6 — isolamento entre usuários
# ═════════════════════════════════════════════════════════════════════════
def bloco_b6() -> None:
    a, b = new_uid(), new_uid()
    send(a, "aprender zzqwx como lazer")
    create_user_category(a, "gastos com namorada")
    launch_case("B6", "B6.01", a, "gastei 70 com zzqwx", "lazer", notas="usuário A: regra própria")
    launch_case("B6", "B6.02", b, "gastei 70 com zzqwx", "outros",
                notas="usuário B NÃO pode herdar a regra do A")
    launch_case("B6", "B6.03", b, "gastei 400 com a namorada", "outros",
                notas="usuário B NÃO pode herdar a categoria custom do A")


# ═════════════════════════════════════════════════════════════════════════
# B7 — IA (só com --ai; sem a flag os casos saem 🔍, nunca ❌)
# ═════════════════════════════════════════════════════════════════════════
def bloco_b7() -> None:
    if not AI_ON:
        record("B7", "B7.01", "frase sem keyword nenhuma, usuário Pro → passo 6 (GPT)",
               "categoria plausível", None, None, None, "🔍",
               notas="não medido: rodado sem --ai (OPENAI_API_KEY ausente do processo)")
        record("B7", "B7.02", "cross-check IA × determinístico (launches.py:418)",
               "regra determinística vence a IA quando contradiz", None, None, None, "🔍",
               notas="não medido: rodado sem --ai")
        return

    uid = new_uid(pro=True)
    launch_case("B7", "B7.01", uid, "gastei 90 no lugar de sempre daquele rolê",
                "(conjunto aceitável)",
                aceitaveis={"lazer", "alimentação", "outros"},
                notas="sem keyword determinística; veredito 🔍 com o valor registrado")

    uid = new_uid(pro=True)
    launch_case("B7", "B7.02", uid, "gastei 500 no mercado", "mercado",
                aceitaveis={"mercado"},
                notas="cross-check: se o LLM disser 'alimentação', a LOCAL_RULE 'mercado' "
                      "tem que vencer (launches.py:418)")


# ═════════════════════════════════════════════════════════════════════════
# B8 — o input REAL do usuário
#
# POR QUE ESTE BLOCO EXISTE: as 50 frases do B1 começam TODAS com
# `gastei|paguei|comprei|recebi` e os 50 verdes saem todos com
# `reason=local_rule`. Isso mede o passo 5 dos 7 com a frase mais fácil
# possível — e não diz nada sobre a classe que mais quebra no WhatsApp, que é
# a mensagem torta: sem verbo, com duas transações, com wake-word, com gíria,
# escrita errada, em caixa alta, ou transcrita de áudio.
#
# REGRA DESTE BLOCO: a categoria esperada sai do COMPORTAMENTO PRETENDIDO —
# o que o usuário razoavelmente espera —, NUNCA do que o código devolve. Caso
# vermelho aqui é ACHADO, não bug pra consertar: nada em `core/`, `db/`,
# `frontend/`, `adapters/`, `utils_text.py` ou `ai_router.py` foi tocado.
#
# A classe de cada caso vai entre colchetes no início da nota; o relatório
# agrupa por ela.
# ═════════════════════════════════════════════════════════════════════════

# (mensagem, categoria esperada, classe, nota)
B8_CASOS: list[tuple[str, str, str, str]] = [
    # ── valor-primeiro, sem verbo ────────────────────────────────────────
    ("77,90 mercado", "mercado", "valor-primeiro",
     "forma mais comum de quem já usa o bot todo dia: valor e estabelecimento, "
     "sem verbo nenhum"),
    ("25 uber", "transporte", "valor-primeiro", "duas palavras, sem verbo e sem preposição"),
    ("1500 aluguel", "moradia", "valor-primeiro", "valor alto sem verbo"),

    # ── wake-word `pig` no começo ────────────────────────────────────────
    ("pig gastei 45 no ifood", "alimentação", "wake-word",
     "`pig` É a wake-word do bot — não é requisito inventado aqui: "
     "`utils_text.py:572` (`MEMORY_STOP_TOKENS`) a põe na stoplist de ruído do "
     "auto-aprendizado e `tests/test_category_learn_noise.py:30` cita "
     "`pig eu mercado mais` como frase-ruído REAL colhida em produção. O "
     "aprendizado sabe limpar o `pig`; a pergunta é se a mensagem chega até lá"),
    ("pig 60 cinema", "lazer", "wake-word", "wake-word + valor-primeiro na mesma mensagem"),

    # ── gíria e abreviação ───────────────────────────────────────────────
    ("40 no rango", "alimentação", "gíria/abreviação",
     "`rango` é gíria corrente pra comida; não está nas LOCAL_RULES"),
    ("paguei 25 de cerva", "alimentação", "gíria/abreviação",
     "`cerva` = cerveja; abreviação de bar/boteco"),
    # NÃO está na classe "gíria/abreviação": `mercadinho` é literal das
    # LOCAL_RULES (`utils_text.py:115`), então o verde aqui mede o DICIONÁRIO,
    # não tolerância a gíria — daria o mesmo ✅ num código com zero capacidade
    # de inferência. Contá-lo como 1/3 da classe inflava o placar dela.
    ("gastei 30 no mercadinho", "mercado", "gíria já no dicionário",
     "diminutivo que ALGUÉM JÁ CADASTROU: `mercadinho` é entrada literal das "
     "LOCAL_RULES (`utils_text.py:115`), ao lado de `mercearia` e `sacolão`. "
     "Verde aqui = a palavra está na lista; a classe `gíria/abreviação` "
     "(rango/cerva) é que mede inferência, e é 0 medida sem IA"),

    # ── escrita errada / sem acento ──────────────────────────────────────
    ("gastei 60 no acai", "alimentação", "escrita errada/sem acento",
     "sem acento: `normalize_text` tira o acento dos DOIS lados, então isto "
     "deve passar — é o caso de controle desta classe"),
    ("paguei 39,90 da netflis", "assinaturas", "escrita errada/sem acento",
     "erro de digitação de polegar; LOCAL_RULES casa por substring exata"),
    ("gastei 120 na farmacia de manha", "saúde", "escrita errada/sem acento",
     "dois sem-acento na mesma frase (`farmácia`, `manhã`)"),

    # ── tudo em MAIÚSCULO ────────────────────────────────────────────────
    ("PAGUEI 180 DE GASOLINA", "transporte", "CAIXA ALTA",
     "caps lock na mensagem inteira; `_BR_TICKER_UPPER_RE` roda no texto CRU, "
     "antes da normalização, então caixa alta é o único input que pode "
     "disparar o passo do ticker por engano"),
    ("COMPREI 150 NA SHOPEE", "compras online", "CAIXA ALTA", "idem, outra categoria"),

    # ── transcrição de áudio (sem pontuação, com hesitação) ──────────────
    ("entao eu gastei 45 reais la no ifood hoje de manha", "alimentação",
     "transcrição de áudio", "sem pontuação, com marcador de fala e ruído em volta"),
    ("ahn paguei 180 de gasolina acho que foi ontem", "transporte",
     "transcrição de áudio", "hesitação no começo e incerteza no fim"),
]

# Casos IMPOSSÍVEIS por construção no caminho medido: acertá-los exige
# casamento aproximado, que não existe fora do passo 6 (GPT) — e o passo 6
# precisa de `OPENAI_API_KEY` **e** `is_pro` (`core/services/category_service.py:218`),
# enquanto o harness tira a chave e o B8 cria todos os usuários no plano grátis.
# Sem `--ai` eles saem 🔍 (não medido), igual ao B7, pelo mesmo motivo: manter
# ❌ inflaria o placar com "esperado impossível". Com `--ai` voltam a valer ❌.
# Os três casos que SÓ o passo 6 (GPT) acertaria. Eles NÃO saem 🔍: rodaram
# inteiros e têm resultado medido (`outros`, reason `default`). O que bloqueia o
# passo 6 aqui não é a flag `--ai` — é o PLANO. O gate em
# `core/services/category_service.py:219-222` exige `OPENAI_API_KEY` **E**
# `is_pro(user_id)`, e todo usuário do B8 nasce de `new_uid()` sem `pro=True`.
# Em produção, onde a chave EXISTE, um usuário grátis recebe exatamente este
# `outros` — logo é medição de comportamento real, não buraco de cobertura, e
# vale ❌. (Diferente do B7, que não roda caso nenhum e sai 🔍 com `obtido_db`
# nulo.)
B8_SO_COM_PASSO6 = {"40 no rango", "paguei 25 de cerva", "paguei 39,90 da netflis"}
assert B8_SO_COM_PASSO6 <= {c[0] for c in B8_CASOS}, \
    f"B8_SO_COM_PASSO6 fora de sincronia: {B8_SO_COM_PASSO6 - {c[0] for c in B8_CASOS}}"

# Duas ou mais transações na MESMA mensagem. Esperado = a lista completa de
# `(categoria, tipo, valor)`, na ordem — não só as categorias.
# (mensagem, lançamentos esperados, nota)
B8_MULTI: list[tuple[str, list[tuple[str, str, float]], str]] = [
    ("gastei 30 no uber e 45 no ifood",
     [("transporte", "despesa", 30.0), ("alimentação", "despesa", 45.0)],
     "dois valores separados por ` e `, com verbo: é a forma que funciona"),
    ("paguei 120 de internet e 180 de luz",
     [("moradia", "despesa", 120.0), ("moradia", "despesa", 180.0)],
     "mesma categoria nos dois: o segundo não pode sumir por dedup"),
    ("35 padaria e 80 farmacia",
     [("alimentação", "despesa", 35.0), ("saúde", "despesa", 80.0)],
     "CONTROLE da classe abaixo: sem verbo, mas COM ` e ` entre os valores — "
     "reproduzido fora do harness, divide certo em 2"),
    # ── a classe do separador ausente (o que era só o B8.18) ─────────────
    ("35 padaria 80 farmacia",
     [("alimentação", "despesa", 35.0), ("saúde", "despesa", 80.0)],
     "sem ` e ` entre os valores: o segundo par some e a resposta ainda diz "
     "sucesso — perda SILENCIOSA de lançamento"),
    ("gastei 35 na padaria 80 na farmacia",
     [("alimentação", "despesa", 35.0), ("saúde", "despesa", 80.0)],
     "mesma perda com verbo e preposições: não é a ausência do verbo que "
     "quebra, é a ausência do separador"),
    ("gastei 30 no uber e no ifood",
     [],
     "UM valor e DOIS destinos: o ` e ` está lá, o segundo valor não. A "
     "decisão do dono MUDOU e esta linha mudou com ela. Era "
     "[(transporte,30), (alimentação,30)] — repetir o valor —, e isso "
     "gravava R$60 de um `gastei 30`. Agora o esperado é ZERO lançamento "
     "automático: o R$30 pode ser o total ou o de cada um, então o bot "
     "PERGUNTA os dois (`Quanto foi gasto em *uber*?` → `... em *ifood*?`) e "
     "só grava o que o usuário responder. Ver o campo `resposta` deste caso"),

    # ── REDE DE PROTEÇÃO: nenhuma destas pode virar 2 lançamentos ────────
    # Todas corretas HOJE. Entraram ANTES da correção do separador ausente
    # justamente para que a correção não possa inflar o gasto do usuário e a
    # bateria ainda sair verde: um " e " entre duas partes da MESMA coisa
    # ("banho e tosa"), um número que é unidade/data/parcela e não dinheiro
    # ("20 litros", "dia 15", "em 3x").
    ("gastei cento e vinte no mercado", [("mercado", "despesa", 120.0)],
     "[rede] valor por extenso com ` e ` DENTRO do número"),
    ("gastei 80 no banho e tosa", [("pets", "despesa", 80.0)],
     "[rede] ` e ` liga duas metades do MESMO serviço"),
    ("paguei 50 de agua e luz", [("moradia", "despesa", 50.0)],
     "[rede] dois destinos, MESMA categoria: dividir dobraria a conta"),
    ("comprei 100 de arroz e feijão", [("outros", "despesa", 100.0)],
     "[rede] lista de itens de UMA compra"),
    ("gastei 50 no posto 20 litros", [("outros", "despesa", 50.0)],
     "[rede] segundo número é UNIDADE, não dinheiro"),
    ("comprei 3 cervejas 15 reais", [("alimentação", "despesa", 3.0)],
     "[rede] primeiro número é QUANTIDADE — hoje o bot grava R$3 (lê a quantidade como dinheiro). Bug PRÉ-EXISTENTE, fora do escopo desta correção; o que a rede vigia é não virarem DOIS lançamentos"),
    ("paguei 100 do cartão 1234", [],
     "[rede] segundo número é IDENTIFICADOR do cartão — hoje não nasce lançamento nenhum (outro caminho responde); o que a rede vigia é continuar assim, não virar 1 ou 2"),
    ("gastei 30 no uber dia 15", [("transporte", "despesa", 30.0)],
     "[rede] segundo número é DATA"),
    ("gastei 100 no mercado às 15h", [("mercado", "despesa", 100.0)],
     "[rede] segundo número é HORA"),
    ("gastei 100 no cartão em 3x", [],
     "[rede] segundo número é NÚMERO DE PARCELAS — hoje sem lançamento (cai no caminho de cartão)"),
    ("parcelei 300 em 6 vezes", [],
     "[rede] idem, com a palavra `vezes` — `parcelei` não é verbo de lançamento; hoje sem lançamento"),
]


# Casos cujo "esperado" foi COPIADO da saída de hoje: o comportamento medido é
# reconhecidamente errado, mas está fora do escopo desta correção e a rede de
# proteção existe pra ele não PIORAR. Saem 🟡, nunca ✅ — verde aqui fazia o
# placar afirmar "certo" sobre seis linhas que a própria nota chama de erradas.
B8_MULTI_CONGELADO = {
    "comprei 100 de arroz e feijão":  "categoria errada: lista de itens de uma compra vira `outros`",
    "gastei 50 no posto 20 litros":   "categoria errada: `guess_category('posto')` é `transporte`, congelado como `outros`",
    "comprei 3 cervejas 15 reais":    "valor errado: grava R$3 (a quantidade) em vez dos R$15",
    "paguei 100 do cartão 1234":      "não nasce lançamento nenhum — o gasto some",
    "gastei 100 no cartão em 3x":     "não nasce lançamento nenhum — o gasto some",
    "parcelei 300 em 6 vezes":        "não nasce lançamento nenhum — o gasto some",
}
assert set(B8_MULTI_CONGELADO) <= {c[0] for c in B8_MULTI}, \
    f"B8_MULTI_CONGELADO fora de sincronia: {set(B8_MULTI_CONGELADO) - {c[0] for c in B8_MULTI}}"


def bloco_b8() -> None:
    for i, (msg, esperado, classe, nota) in enumerate(B8_CASOS, 1):
        uid = new_uid()  # usuário NOVO por caso: learn_from_inference contamina o seguinte
        if msg in B8_SO_COM_PASSO6:
            nota += (" — só o passo 6 (GPT) acertaria isto, e ele é inalcançável para "
                     "este usuário por PLANO, não pela flag `--ai`: o gate em "
                     "`core/services/category_service.py:219-222` exige "
                     "`OPENAI_API_KEY` **E** `is_pro`, e o usuário aqui é grátis. "
                     "❌ = o que um usuário do plano grátis recebe hoje em produção")
        r = launch_case("B8", f"B8.{i:02d}", uid, msg, esperado,
                        notas=f"[{classe}] {nota}", exige_lancamento=True)
        # A nota só entra se o caso for DESTE tipo: com o pipeline estourando,
        # `obtido_db` também é None e a frase afirmava, falsamente, que o bot
        # tinha recebido a mensagem e respondido "(sem resposta)".
        if r["obtido_db"] is None and not r["excecao"]:
            r["notas"] += (" — ⚠️→❌: a mensagem NÃO virou lançamento nenhum. "
                           f"O bot respondeu: {(r['resposta'] or '(sem resposta)')[:120]!r}")

    # ── duas ou mais transações na mesma mensagem ────────────────────────
    # Oráculo diferente: `launch_case` só olha o ÚLTIMO lançamento e é cego
    # justamente para o que interessa aqui (quantos lançamentos nasceram, e
    # categoria, TIPO e VALOR de cada um).
    for j, (msg, esperado, nota) in enumerate(B8_MULTI, 1):
        cid = f"B8.{len(B8_CASOS) + j:02d}"
        uid = new_uid()
        base = max_launch_id(uid)
        _INFER_LOG.clear()
        before = snapshot_rules(uid)
        try:
            resp = send(uid, msg)
        except Exception as e:
            record("B8", cid, msg, esperado, f"EXCEÇÃO {e.__class__.__name__}: {e}",
                   None, None, "❌", excecao=True,   # crash é ❌, nunca o veredito mais brando
                   notas=f"[multi-transação] {nota} — EXCEÇÃO no pipeline: "
                         f"{traceback.format_exc().splitlines()[-1]}")
            continue
        obtidas = rows_since(uid, base)
        novas = [x for x in snapshot_rules(uid) if x not in before]
        vered = "✅" if obtidas == esperado else "❌"
        congelado = B8_MULTI_CONGELADO.get(msg)
        if vered == "✅" and congelado:
            vered = "🟡"
            nota += f" — 🟡 (não ✅): {congelado}"
        record("B8", cid, msg, esperado, cat_from_reply(resp), obtidas,
               " · ".join(r[2] for r in _INFER_LOG) or None, vered,
               notas=f"[multi-transação] {nota} — esperado {len(esperado)} lançamento(s) "
                     f"{esperado}; nasceram {len(obtidas)}: {obtidas or '(nenhum)'}",
               resposta=resp,
               rules_diff=("+ " + ", ".join(novas)) if novas else "(nenhuma regra nova)")


# ═════════════════════════════════════════════════════════════════════════
# Relatório
# ═════════════════════════════════════════════════════════════════════════
def cegueiras() -> list[str]:
    """As classes que a bateria não pega. As de cobertura são DERIVADAS dos
    contadores de chamada — antes era uma lista fixa que não citava dois dos três
    hunks de produção do #123 e, por omissão, se lia como cobertura completa."""
    q = lambda k: CALLS.get(k, 0)  # noqa: E731
    consulta = q("launches.spend_query") + q("launches.list_launches")
    return [
        f"**Dois dos três hunks de produção do #123 não são exercitados.** O PR tem (a) o "
        f"guard em `core/services/category_service.py` — coberto pelo B4.04 —, (b) a "
        f"delegação `list_launches → spend_query` e (c) o `date_filter` do `spend_query` "
        f"(`core/handlers/launches.py`, +61 linhas). Medido nesta execução: "
        f"`list_launches` {q('launches.list_launches')} chamada(s), `spend_query` "
        f"{q('launches.spend_query')} — "
        + ("nenhuma mensagem de CONSULTA passa por esta bateria, que só faz lançamentos "
           "e comandos de regra. (b) e (c) estão **sem nenhum caso**."
           if consulta == 0 else
           "há casos de consulta, mas confira se eles SEPARAM as duas árvores.")
        + " O `scripts/cleanup_poisoned_category_rules.py` do mesmo PR também não é "
          "executado em lugar nenhum daqui.",
        f"**Contadores desta execução** (o que a bateria de fato chamou): {CALLS}.",
        "**Renderização no dashboard e agrupamento por `lower(categoria)`** — a bateria lê "
        "`launches.categoria` cru; não exercita a agregação nem a tela.",
        "**A lista interativa do WhatsApp** só é inspecionada estruturalmente (B5.05); o "
        "envio real pela API do WhatsApp não é exercitado.",
        "**O que a conferência de bolhas do vault (seção acima) pega e o que não pega.** "
        "PEGA, automaticamente: bolha que não existe em resposta nenhuma; e bolha VERBATIM "
        "porém atribuída ao caso ERRADO (a anotação `*(B5.02)*` é casada com a resposta "
        "daquele caso). AVISA, sem conseguir verificar: bolha sem anotação de caso, e "
        "anotação apontando para um caso que não existe nesta bateria — nos dois a "
        "atribuição fica sem oráculo (antes passavam caladas). NÃO PEGA, e só leitura "
        "linha a linha alcança: (a) as linhas `**Você:**`, que ninguém confere contra a "
        "mensagem realmente enviada; (b) tudo que a nota afirma FORA de um bloco de "
        "citação — tabelas, prosa, checklist; (c) nota que descreve um comportamento "
        "sem citar bolha nenhuma (é o caso de `Interacoes/Recategorizar pela IA.md`).",
        "**Qualidade do GPT** — com `--ai` desligado o passo 6 é inalcançável; B7 sai 🔍 "
        "(não roda caso nenhum). Para o B8 há uma SEGUNDA tranca, independente da flag: "
        "todo usuário do B8 nasce de `new_uid()` sem `pro=True`, e o gate "
        "(`core/services/category_service.py:219-222`) exige `OPENAI_API_KEY` **e** "
        "`is_pro`. Por isso `B8.06` (`rango`), `B8.07` (`cerva`) e `B8.10` (`netflis`) "
        "contam como ❌ e não como 🔍: ligar `--ai` não mudaria nada para eles, e o "
        "`outros` medido é o que o plano grátis entrega hoje em produção. O que continua "
        "NÃO medido é o teto do produto: o mesmo input num usuário Pro com chave real. "
        "Para medir isso faltam `--ai` + `new_uid(pro=True)` + rede — nada disso existe "
        "nesta execução.",
        "**Isolamento entre usuários só é medido no caminho da INFERÊNCIA.** B6.01–B6.03 "
        "provam que a regra (`zzqwx`) e a categoria custom (`gastos com namorada`) do "
        "usuário A não alcançam o B — e o par B6.01×B6.02 é o controle: a MESMA frase dá "
        "`lazer` para A e `outros` para B. O que NÃO tem caso nenhum: listar/remover regra "
        "de outro usuário, o botão “categoria errada?” num lançamento alheio, consulta de "
        "gastos cruzada, e o sentido B→A.",
        "**Divergência entre as 3 listas** (`SYSTEM_CATEGORIES_SEED` 15 · "
        "`ALLOWED_CATEGORIES` 15 · rótulos de `LOCAL_RULES` 16) — isso é enumeração "
        "escrita, nenhum teste alcança.",
        "**Cartão de crédito, endpoints do dashboard e import de OFX/PDF** — fora do escopo "
        "combinado; nenhuma medição aqui.",
    ]


def write_report() -> str:
    hoje = datetime.now().strftime("%Y-%m-%d")
    # Nome com DATA e SHA da árvore, não só o label: `--tree <pr> --label main`
    # sobrescrevia a baseline e o `--compare` seguinte comparava o PR com ele
    # mesmo, imprimindo "(igual)" nas 91 linhas.
    sha = TREE_GIT.split()[0]
    stamp = f"{ARGS.label}_{hoje}_{sha}"
    base = os.path.join(WORKTREE_ROOT, "docs", f"qa_categorizacao_{stamp}")
    json_path = base + ".json"
    with open(json_path, "w") as f:
        json.dump({"label": ARGS.label, "tree": TREE, "tree_git": TREE_GIT,
                   "total_esperado": TOTAL_ESPERADO,
                   "blocos_fora_do_esperado": conferir_total(),
                   "ai": AI_ON, "net_guard": NET_GUARD,
                   "openai_key_no_processo": bool(os.environ.get("OPENAI_API_KEY")),
                   "env_injetadas_pelo_import": ENV_INJETADAS,
                   "calls": CALLS, "results": RESULTS},
                  f, ensure_ascii=False, indent=1)

    other = None
    other_label = None
    sem_baseline: list[str] = []
    sumiram: list[str] = []
    if ARGS.compare:
        with open(ARGS.compare) as f:
            other_raw = json.load(f)
        other = {r["cid"]: r for r in other_raw["results"]}
        other_label = other_raw.get("label", "outra")
        other_git = other_raw.get("tree_git", "(execução antiga, sem git)")
        if other_raw.get("tree") == TREE:
            print(f"[harness] AVISO: --compare aponta pra MESMA árvore ({TREE}) — "
                  f"o delta vai sair todo '(igual)'.")
        # Baseline DESALINHADA falhava calada: os casos novos saíam com `—` na
        # coluna de delta, ao lado de linhas `✅ (igual)`, e nada dizia quantos
        # eram. Mesmo silêncio que o guard de `--compare` inexistente evita.
        # Mesma classe do guard de total: comparar contra uma baseline que ela
        # mesma saiu incompleta produz deltas inventados ("sumiu", "melhorou").
        if other_raw.get("blocos_fora_do_esperado"):
            print(f"[harness] AVISO: a baseline '{other_label}' saiu INCOMPLETA "
                  f"({'; '.join(other_raw['blocos_fora_do_esperado'])}) — o delta "
                  f"desta tabela compara contra um resultado parcial.")
        sem_baseline = [r["cid"] for r in RESULTS if r["cid"] not in other]
        sumiram = [c for c in other if c not in {r["cid"] for r in RESULTS}]
        if sem_baseline or sumiram:
            print(f"[harness] AVISO: baseline desalinhada — {len(sem_baseline)} caso(s) "
                  f"sem linha em '{other_label}', {len(sumiram)} caso(s) que existiam lá "
                  f"e não existem aqui.")

    counts: dict[str, int] = {}
    for r in RESULTS:
        counts[r["veredito"]] = counts.get(r["veredito"], 0) + 1

    L: list[str] = []
    L.append(f"# QA de categorização — {hoje}")
    L.append("")
    incompleto = conferir_total()
    if incompleto:
        L.append(f"> # ⛔ RELATÓRIO INCOMPLETO — NÃO LEIA OS NÚMEROS ABAIXO COMO RESULTADO")
        L.append(f">")
        L.append(f"> {len(RESULTS)} casos registrados, {TOTAL_ESPERADO} esperados. "
                 f"Bloco(s) fora do esperado: " + "; ".join(incompleto) + ". "
                 f"Um bloco que estoura fora dos casos leva junto tudo que ainda não "
                 f"registrou — o placar de ❌ CAI, e a queda parece melhora. "
                 f"O processo saiu com código 2.")
        L.append("")
    L.append(f"Gerado por `scripts/category_qa_harness.py --label {ARGS.label}"
             f"{' --ai' if AI_ON else ''}` contra a árvore `{TREE}`, "
             f"num Postgres isolado e descartável.")
    L.append("")
    L.append("## O que exatamente foi medido (lido em runtime)")
    L.append("")
    L.append(f"- **árvore desta execução:** `{TREE}` — **{TREE_GIT}**")
    if other:
        L.append(f"- **árvore comparada (`{other_label}`):** "
                 f"`{other_raw.get('tree', '?')}` — **{other_git}**")
        L.append("- ⚠️ **o que está sendo comparado não é “main × PR #123” em estado de "
                 "laboratório**: é uma árvore contra a outra, no estado `clean`/`dirty` "
                 "impresso acima. Arquivo modificado e não commitado entra na medição.")
    L.append(f"- **kill switch de rede:** {NET_GUARD}")
    key_viva = bool(os.environ.get("OPENAI_API_KEY"))
    L.append(f"- **`OPENAI_API_KEY` no processo, VERIFICADO agora:** "
             f"`{key_viva}`{' ← passo 6 do infer_category inalcançável' if not key_viva else ''}")
    L.append(f"- **variáveis que o import da árvore injetou no processo:** "
             f"{ENV_INJETADAS or '(nenhuma — `config.env.ROOT_DIR` neutralizado, as duas '
             'árvores rodam sob o mesmo ambiente)'}")
    L.append(f"- **chamadas contadas:** {CALLS}")
    L.append("")
    L.append(f"**Sumário:** {len(RESULTS)} casos — "
             f"✅ {counts.get('✅', 0)} · ❌ {counts.get('❌', 0)} · "
             f"🟡 {counts.get('🟡', 0)} · "
             f"⚠️ {counts.get('⚠️', 0)} · 🔍 {counts.get('🔍', 0)}")
    L.append("")
    congelados = [r for r in RESULTS if r["veredito"] == "🟡"]
    if congelados:
        L.append("> **🟡 = bateu com o comportamento de hoje, e o comportamento de hoje "
                 "está errado.** Esperado copiado da saída para a rede de proteção não "
                 "PIORAR o caso; corrigi-lo é outro escopo. Não conte como acerto: "
                 + ", ".join(f"`{r['cid']}`" for r in congelados) + ".")
        L.append("")
    ressalvados = [r for r in RESULTS
                   if r["veredito"] == "✅" and str(r["notas"]).startswith(VERDE_RESSALVA)]
    if ressalvados:
        L.append(f"> **✅ quer dizer “igual ao comportamento atual”, não “correto”.** "
                 f"{len(ressalvados)} dos {counts.get('✅', 0)} verdes documentam "
                 f"comportamento que a própria nota chama de indesejável: "
                 + ", ".join(f"`{r['cid']}`" for r in ressalvados) + ".")
        L.append("")
    L.append(f"**IA:** {'LIGADA (chamadas reais à OpenAI)' if AI_ON else 'DESLIGADA — `OPENAI_API_KEY` ausente do processo (verificado em runtime, ver acima); passo 6 do `infer_category` inalcançável; casos de IA saem 🔍'}")
    L.append("")

    # matriz categoria × acertos (só B1, que é o que tem categoria esperada canônica)
    L.append("## Matriz categoria × acertos (B1)")
    L.append("")
    mat: dict[str, list[int]] = {}
    for r in RESULTS:
        if r["block"] != "B1":
            continue
        cell = mat.setdefault(str(r["esperado"]), [0, 0])
        cell[1] += 1
        if r["veredito"] == "✅":
            cell[0] += 1
    L.append("| categoria esperada | acertos / casos |")
    L.append("|---|---|")
    for cat in sorted(mat):
        ok, tot = mat[cat]
        L.append(f"| `{cat}` | {ok}/{tot} |")
    L.append("")

    # B8 — o bloco que responde "funciona pra quem digita torto?"
    b8 = [r for r in RESULTS if r["block"] == "B8"]
    if b8:
        L.append("## B8 — o input REAL do usuário (por classe)")
        L.append("")
        L.append("A categoria esperada destes casos vem do **comportamento pretendido**, "
                 "não do que o código devolve. ❌ aqui é **achado de QA** — nenhum arquivo "
                 "de produção foi alterado por causa deles.")
        L.append("")
        classes: dict[str, list[dict]] = {}
        for r in b8:
            m = re.match(r"\[([^\]]+)\]", str(r["notas"]))
            classes.setdefault(m.group(1) if m else "(sem classe)", []).append(r)
        # A coluna 🔍 é obrigatória: sem ela, um caso IMPOSSÍVEL por construção
        # (só o passo 6 acertaria, e ele está desligado) aparecia somado aos
        # vermelhos e o placar da classe lia "0/3" como se fossem 3 falhas
        # medidas. A ressalva tem que estar ONDE O NÚMERO ESTÁ.
        # O rótulo NÃO diz "só com IA": os três casos que só o passo 6 acertaria são
        # ❌ medidos (bloqueados por PLANO, não pela flag — ver B8_SO_COM_PASSO6).
        L.append("| classe | ✅ medidos | ❌ medidos | 🔍 sem esperado único | casos |")
        L.append("|---|---|---|---|---|")
        def _cids(rs: list[dict], v: str) -> str:
            ids = [r["cid"] for r in rs if r["veredito"] == v]
            return f"{len(ids)} — " + ", ".join("`" + c + "`" for c in ids) if ids else "0"

        for c, rs in classes.items():
            ok = sum(1 for r in rs if r["veredito"] == "✅")
            L.append(f"| {c} | {ok} | {_cids(rs, '❌')} | {_cids(rs, '🔍')} | {len(rs)} |")
        L.append("")
        L.append("| caso | mensagem | esperado | obtido | reason | veredito |")
        L.append("|---|---|---|---|---|---|")
        for r in b8:
            L.append(f"| `{r['cid']}` | `{r['desc']}` | `{r['esperado']}` | "
                     f"`{r['obtido_db']}` | `{r['reason']}` | {r['veredito']} |")
        L.append("")
        ok8 = sum(1 for r in b8 if r["veredito"] == "✅")
        err8 = sum(1 for r in b8 if r["veredito"] == "❌")
        lupa8 = [r for r in b8 if r["veredito"] == "🔍"]
        # O número NUNCA sai nu: quem lê o placar tem que ler junto quantos
        # casos não são medição nenhuma. A ressalva estava só lá embaixo, na
        # seção de cegueiras, e quem lê o topo não chegava nela.
        L.append(f"**Placar do B8: {ok8} ✅ · {err8} ❌ · {len(lupa8)} 🔍 "
                 f"em {len(b8)} casos** — ou seja, **{ok8}/{ok8 + err8} do que foi de fato "
                 f"medido**. Os {len(lupa8)} 🔍 ("
                 + ", ".join("`" + r["cid"] + "`" for r in lupa8)
                 + ") saíram com um conjunto de resultados ACEITÁVEIS em vez de um "
                   "esperado único, então não contam nem como acerto nem como falha. "
                   "Atenção: 🔍 aqui NUNCA quer dizer “o `--ai` consertaria”. O passo 6 "
                   "(GPT) é gated por `OPENAI_API_KEY` **E** `is_pro` "
                   "(`core/services/category_service.py:219-222`) e todo usuário do B8 "
                   "nasce grátis — os casos que só o passo 6 acertaria são ❌ medidos, "
                   "não 🔍."
                 if lupa8 else
                 f"**Placar do B8: {ok8} ✅ · {err8} ❌ em {len(b8)} casos.**")
        L.append("")
        L.append(f"Para comparar: o B1, que usa só frases canônicas começando em "
                 f"`gastei|paguei|comprei|recebi`, acerta "
                 f"{sum(1 for r in RESULTS if r['block'] == 'B1' and r['veredito'] == '✅')}"
                 f"/{sum(1 for r in RESULTS if r['block'] == 'B1')}.")
        L.append("")

    # tabela completa
    L.append("## Todos os casos")
    L.append("")
    if other and other_raw.get("blocos_fora_do_esperado"):
        L.append(f"> ⛔ **A baseline `{other_label}` saiu INCOMPLETA** "
                 f"({'; '.join(other_raw['blocos_fora_do_esperado'])}). Todo delta desta "
                 f"coluna compara contra um resultado parcial.")
        L.append("")
    if other and (sem_baseline or sumiram):
        L.append(f"> ⚠️ **A baseline `{other_label}` não cobre esta bateria inteira.** "
                 f"{len(sem_baseline)} caso(s) saem com `—` na coluna de delta por não "
                 f"existirem lá — `—` é AUSÊNCIA DE COMPARAÇÃO, não “igual”"
                 + (": " + ", ".join("`" + c + "`" for c in sem_baseline[:25])
                    + (" …" if len(sem_baseline) > 25 else "") if sem_baseline else "")
                 + (f". Além disso {len(sumiram)} caso(s) existiam na baseline e não "
                    f"existem aqui: " + ", ".join("`" + c + "`" for c in sumiram[:25])
                    + (" …" if len(sumiram) > 25 else "") if sumiram else "") + ".")
        L.append("")
    head = "| caso | mensagem / cena | esperado | obtido (banco) | reason | veredito |"
    sep = "|---|---|---|---|---|---|"
    if other:
        head += f" {other_label} |"
        sep += "---|"
    L.append(head)
    L.append(sep)
    for r in RESULTS:
        row = (f"| `{r['cid']}` | {str(r['desc'])[:70]} | `{r['esperado']}` | "
               f"`{r['obtido_db']}` | `{r['reason']}` | {r['veredito']} |")
        if other:
            o = other.get(r["cid"])
            if o is None:
                row += " — |"
            elif o["veredito"] == r["veredito"]:
                row += f" {o['veredito']} (igual) |"
            else:
                row += f" **{o['veredito']} → {r['veredito']}** |"
        L.append(row)
    L.append("")

    # detalhe dos falhos
    falhos = [r for r in RESULTS if r["veredito"] in ("❌", "⚠️")]
    L.append(f"## Casos falhos ({len(falhos)})")
    L.append("")
    if not falhos:
        L.append("(nenhum)")
    for r in falhos:
        L.append(f"### {r['cid']} — {r['desc']}")
        L.append("")
        L.append(f"- **esperado:** `{r['esperado']}`")
        L.append(f"- **obtido (linha 🏷️ da resposta):** `{r['obtido_resp']}`")
        L.append(f"- **obtido (`launches.categoria`):** `{r['obtido_db']}`")
        L.append(f"- **reason do `infer_category`:** `{r['reason']}`")
        L.append(f"- **diff de `user_category_rules`:** {r['rules_diff'] or '—'}")
        if r["notas"]:
            L.append(f"- **nota:** {r['notas']}")
        if r["resposta"]:
            L.append(f"- **resposta crua:** `{r['resposta'][:200]!r}`")
        L.append("")

    # diff de regras por cena (obrigatório: a regra envenenada não aparece na resposta)
    L.append("## Diff de `user_category_rules` por cena")
    L.append("")
    L.append("| caso | regras gravadas pela cena |")
    L.append("|---|---|")
    for r in RESULTS:
        if r["rules_diff"]:
            L.append(f"| `{r['cid']}` | {r['rules_diff']} |")
    L.append("")

    L.append("## Discrepâncias entre vault e código")
    L.append("")
    L.append(f"Estado de cada uma RECONFERIDO no vault em `{ARGS.vault}` nesta execução.")
    L.append("")
    for d in DISCREPANCIAS:
        L.append(f"- {d['texto']}")
        L.append(f"  - **estado agora:** {vault_estado(d)}")
    L.append("")

    bv = bolhas_vault()
    orfas, avisos = bv["orfas"], bv["avisos"]
    L.append("## Fala do bot no vault × resposta medida")
    L.append("")
    if not bv["vault_ok"]:
        L.append(f"⛔ **Nada foi conferido:** o vault `{ARGS.vault}` não existe neste "
                 f"sistema. Os números abaixo são zero por ausência de dado, não por "
                 f"ausência de defeito.")
        L.append("")
    L.append(f"Cada `> **PigBank:** …` das notas que citam este harness tem que ser uma "
             f"mensagem INTEIRA e VERBATIM capturada nesta execução. **Denominador, para "
             f"o número não sair nu:** conferidas **{bv['bolhas']} de {bv['bolhas_vault']}** "
             f"bolhas do vault, nas **{bv['notas']} de {bv['notas_vault']}** notas que têm "
             f"bolha (as outras citam outra bateria e não é este relatório que responde por "
             f"elas). As notas reescritas nesta bateria são **14** (as 13 com "
             f"`medido: 2026-08-22` no frontmatter + `Referencia/Checklist QA.md`); a única "
             f"fora desta conferência é `Interacoes/Recategorizar pela IA.md`, que não tem "
             f"bolha nenhuma — o que ela afirma é prosa, e prosa esta seção não alcança.")
    L.append("")
    L.append(f"**{len(orfas)}** bolha(s) sem respaldo · **{len(avisos)}** com atribuição "
             f"não conferível.")
    L.append("")
    if orfas:
        L.append("| nota | bolha | problema |")
        L.append("|---|---|---|")
        for arq, b, porque in orfas:
            L.append(f"| `{arq}` | `{b[:140].replace(chr(10), ' ⏎ ')}` | {porque} |")
    else:
        L.append("Nenhuma sem respaldo. (Isto reprova bolha inventada, cortada pela metade, "
                 "quebrada em uma bolha por linha, ou verbatim mas atribuída ao caso errado "
                 "— os defeitos reais encontrados em 2026-08-22, quando uma cena mandava a "
                 "mensagem e descartava a saída.)")
    L.append("")
    if avisos:
        L.append("Bolhas que passam no verbatim mas cuja ATRIBUIÇÃO não dá pra conferir "
                 "(antes passavam caladas):")
        L.append("")
        L.append("| nota | bolha | aviso |")
        L.append("|---|---|---|")
        for arq, b, porque in avisos:
            L.append(f"| `{arq}` | `{b[:140].replace(chr(10), ' ⏎ ')}` | {porque} |")
        L.append("")

    L.append("## Classes de bug que esta bateria NUNCA pega")
    L.append("")
    for c in cegueiras():
        L.append(f"- {c}")
    L.append("")

    path = base + ".md"
    with open(path, "w") as f:
        f.write("\n".join(L))
    print(f"[harness] relatório: {path}")
    print(f"[harness] json:      {json_path}")
    return path


def main() -> None:
    t0 = time.time()
    for fn in (bloco_b1, bloco_b2, bloco_b3, bloco_b4, bloco_b5, bloco_b6,
               bloco_b7, bloco_b8):
        print(f"[harness] {fn.__name__} ...")
        try:
            fn()
        except Exception:
            tb = traceback.format_exc()
            print(f"[harness] {fn.__name__} quebrou fora dos casos:\n{tb}")
            record(fn.__name__.upper(), fn.__name__, "bloco quebrou fora dos casos",
                   None, None, None, None, "⚠️", notas=tb.splitlines()[-1])
    write_report()
    counts: dict[str, int] = {}
    for r in RESULTS:
        counts[r["veredito"]] = counts.get(r["veredito"], 0) + 1
    print(f"[harness] {len(RESULTS)} casos em {time.time() - t0:.1f}s — "
          f"✅{counts.get('✅', 0)} ❌{counts.get('❌', 0)} "
          f"⚠️{counts.get('⚠️', 0)} 🔍{counts.get('🔍', 0)}")
    incompleto = conferir_total()
    if incompleto:
        print(f"[harness] ⛔ INCOMPLETO: {len(RESULTS)} de {TOTAL_ESPERADO} casos — "
              + "; ".join(incompleto), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()  # o teardown do database é o atexit registrado lá em cima
