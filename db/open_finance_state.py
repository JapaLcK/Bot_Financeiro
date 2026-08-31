"""Estado da conexão Open Finance: tentativa, resultado, cooldown e posse do item.

Existe porque `save_open_finance_sync` carimbava `status='ACTIVE', last_sync_at=now()`
INCONDICIONALMENTE, no fim de todo caminho — inclusive quando a Pluggy tinha devolvido
`{"results": []}` para um item já deletado (o `/accounts` responde 200 com lista vazia;
só o `GET /items/{id}` devolve 404). Resultado medido em produção: conexão morta
aparecendo como "Atualizado agora" e `DELETED`/`ERROR` ressuscitando para `ACTIVE`.

A separação é toda a ideia:
  • `last_attempt_at` = tentamos;
  • `last_sync_at`    = deu certo (não há `last_success_at`: seria a segunda versão
    da mesma verdade — §0.7);
  • `health`          = o que o `GET /items/{id}` disse, produto a produto;
  • `next_refresh_at` = cooldown E alvo do claim atômico entre instâncias.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from utils_date import _tz

from .connection import get_conn

# Estados locais terminais: nenhum resultado de sync pode sobrescrevê-los.
# PAUSED = trial venceu (o item nem existe mais na Pluggy); DELETED = removido.
# CUIDADO: interpolado como tupla Python nos `f"""..."""` abaixo — reduzir a lista
# a UM elemento emitiria `('PAUSED',)` e quebraria o SQL. Ao mexer, vire lista SQL.
_TERMINAL = ("PAUSED", "DELETED")


class AmbiguousItemError(RuntimeError):
    """O mesmo provider_item_id aparece em mais de uma conexão (usuários diferentes).

    Antes disto, `get_open_finance_connection_by_item_id` fazia `limit 1` SEM
    `order by`: o webhook do item escolhia um dono ao acaso e sincronizava a
    carteira do usuário errado. Levantar é o único desfecho seguro.
    """

    def __init__(self, item_id: str, connections: list[dict]):
        super().__init__(f"item {item_id} está ligado a {len(connections)} conexões")
        self.item_id = item_id
        self.connections = connections


def get_connections_by_item_id(item_id: str, provider: str = "pluggy") -> list[dict]:
    """TODAS as conexões daquele item — sem `limit`, para que a ambiguidade apareça."""
    item = (item_id or "").strip()
    if not item:
        return []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, user_id, provider, provider_item_id, status, institution_name,
                       last_sync_at, last_attempt_at, status_reason, health,
                       next_refresh_at, last_refresh_origin, reconnected_at
                from open_finance_connections
                where provider=%s and provider_item_id=%s
                order by id
                """,
                (provider, item),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def mark_sync_attempt(connection_id: int, *, origin: str = "sync") -> int:
    """Carimba a TENTATIVA. Nunca toca em last_sync_at."""
    now = datetime.now(_tz())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update open_finance_connections
                   set last_attempt_at=%s, last_refresh_origin=coalesce(%s, last_refresh_origin),
                       updated_at=%s
                 where id=%s
                   and upper(coalesce(status,'')) not in {_TERMINAL}
                """,
                (now, origin, now, connection_id),
            )
            updated = cur.rowcount
        conn.commit()
    return updated


# Sentinela: `None` é um valor VÁLIDO de `reconnected_at` (nunca reconectou),
# então não serve de "não checar".
_SEM_CHECAGEM: Any = object()


def mark_sync_result(
    connection_id: int,
    *,
    ok: bool | None,
    status: str | None = None,
    status_reason: str | None = None,
    health: dict | None = None,
    at: datetime | None = None,
    reconnected_at_visto: Any = _SEM_CHECAGEM,
) -> int:
    """Resultado de um sync (ou do job de saúde, com ok=None).

    `ok=True`  → carimba last_sync_at (sucesso) e o status pedido.
    `ok=False` → carimba só a tentativa e o motivo; last_sync_at fica onde estava.
    `ok=None`  → job de saúde: grava `health` sem afirmar sucesso nem falha.

    `status_reason`: None mantém o motivo atual, `""` APAGA. Apagar precisava
    existir — com `coalesce` puro, um `item_missing` gravado por um 404
    transitório só saía num sync completo, e o sync periódico é dormente por
    padrão (`OF_REFRESH_ENABLED`): a tela mandava refazer a conexão para sempre.

    `reconnected_at_visto`: o `reconnected_at` que o sync leu ao COMEÇAR. Só o
    sucesso o consulta, e só para decidir se ainda pode carimbá-lo. A fase de
    leitura roda FORA do lock e leva minutos (transações paginadas por conta),
    então um sync que começou antes de o usuário reconectar pode terminar depois
    — e carimbaria `last_sync_at > reconnected_at` com dado buscado sob a
    autorização ANTIGA, devolvendo o verde que esta onda existe para tirar.
    Mesmo idioma otimista de `pending_actions`: só grava se ainda for o que leu.
    NÃO é o guarda principal: quem mata o run de geração velha é a relectura de
    `reconnected_at` dentro do `pluggy_item_lock` (`_sync_pluggy_item_confirmado`),
    antes de qualquer escrita. Este aqui fecha a janela que sobra — a rota de
    reconexão não pega aquele lock, então ela ainda pode cair entre a relectura e
    este carimbo. Nessa janela o espelho fica (o dado é real, só velho); o que se
    recusa é chamá-la de sucesso — e a própria rota de reconexão agenda um sync
    novo, então o âmbar é transitório.
    """
    now = at or datetime.now(_tz())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update open_finance_connections
                   set status = coalesce(%s, status),
                       status_reason = case when %s = '' then null
                                            else coalesce(%s, status_reason) end,
                       health = coalesce(%s, health),
                       last_attempt_at = case when %s then last_attempt_at else %s end,
                       last_sync_at = case when %s and (%s or reconnected_at
                                                        is not distinct from %s)
                                           then %s else last_sync_at end,
                       updated_at = %s
                 where id=%s
                   and upper(coalesce(status,'')) not in {_TERMINAL}
                """,
                (
                    (str(status).upper() if status else None),
                    status_reason, status_reason,
                    (Jsonb(health) if health is not None else None),
                    ok is None, now,          # job de saúde não é tentativa de sync
                    # só sucesso avança last_sync_at, e só se ninguém reconectou
                    # no meio (`is not distinct from` para casar NULL com NULL)
                    bool(ok),
                    reconnected_at_visto is _SEM_CHECAGEM,
                    (None if reconnected_at_visto is _SEM_CHECAGEM else reconnected_at_visto),
                    now,
                    now,
                    connection_id,
                ),
            )
            updated = cur.rowcount
        conn.commit()
    return updated


def claim_items_for_refresh(
    *,
    cooldown_sec: int,
    jitter_pct: float,
    origin: str,
    limit: int,
    user_id: int | None = None,
) -> list[dict]:
    """Reivindica os items elegíveis e JÁ agenda o próximo — num UPDATE ... RETURNING.

    Atômico entre instâncias: duas réplicas do Railway (o deploy sobe a nova antes
    de derrubar a velha) rodando o mesmo tick não pegam o mesmo item, porque quem
    perde a linha já a vê com `next_refresh_at` no futuro. O jitter espalha os
    items para não baterem todos na Pluggy no mesmo segundo.

    NÃO escreve `last_refresh_requested_at`: aquela coluna é o relógio do
    cooldown MANUAL (`claim_manual_refresh`), e só ele a lê. Uma coluna com dois
    relógios (§0.7) fazia o tick periódico queimar o botão do usuário — medido:
    logo depois de um tick, o refresh manual voltava vazio por 120s. O relógio
    deste caminho é `next_refresh_at`, que já está sendo escrito acima.
    """
    # Jitter serve para ESPALHAR, não para viajar no tempo: com 400% (medido),
    # 14 de 40 agendamentos caíam no PASSADO e o cooldown persistido — a razão de
    # existir da coluna — deixava de valer. Teto de 100% → fator em [0.5, 1.5].
    jitter = min(max(float(jitter_pct or 0), 0.0), 100.0) / 100.0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update open_finance_connections
                   set next_refresh_at = now()
                        + make_interval(secs => %s) * (1 + (random()-0.5)*%s),
                       last_refresh_origin = %s
                 where id in (
                       select id from open_finance_connections
                        where provider='pluggy'
                          and upper(coalesce(status,'')) not in {_TERMINAL}
                          and (next_refresh_at is null or next_refresh_at <= now())
                          and (%s::bigint is null or user_id = %s)
                        order by next_refresh_at nulls first, id
                        limit %s
                        for update skip locked
                 )
                returning id, user_id, provider_item_id
                """,
                (cooldown_sec, jitter, origin, user_id, user_id, limit),
            )
            rows = [dict(r) for r in (cur.fetchall() or [])]
        conn.commit()
    return rows


def claim_manual_refresh(user_id: int, item_ids: list[str], *, cooldown_sec: int) -> list[str]:
    """Quais items deste usuário PODEM levar um PATCH agora. Reivindica e devolve.

    Duas coisas que o caminho manual não tinha e viraram incidente quando o
    pull-to-refresh passou a chamá-lo (antes ele só relia o snapshot):

      • **rajada**: 5 puxões seguidos viravam 5 PATCH por banco — medido. O
        `where` condicional é o que serializa: quem chega dentro do cooldown não
        atualiza linha nenhuma e volta de mãos vazias, mesmo em paralelo.
      • **status terminal**: PAUSED (trial vencido) e DELETED levavam PATCH
        também, gastando cota num item que nem existe mais na Pluggy.

    O cooldown é CURTO de propósito (`OF_MANUAL_REFRESH_COOLDOWN_SEC`, 120s): o
    manual tem que poder furar o tick de 6h — ele só não pode martelar. Por isso
    ele mora em `last_refresh_requested_at`, e não em `next_refresh_at`, que é o
    relógio do tick periódico.
    """
    ids = [str(i) for i in (item_ids or []) if i]
    if not ids:
        return []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update open_finance_connections
                   set last_refresh_requested_at = now(), last_refresh_origin = 'manual'
                 where user_id = %s and provider = 'pluggy'
                   and provider_item_id = any(%s)
                   and upper(coalesce(status,'')) not in {_TERMINAL}
                   and (last_refresh_requested_at is null
                        or last_refresh_requested_at <= now() - make_interval(secs => %s))
                returning provider_item_id
                """,
                (user_id, ids, cooldown_sec),
            )
            claimed = [r["provider_item_id"] for r in (cur.fetchall() or [])]
        conn.commit()
    return claimed


def list_connections_for_health_check(*, older_than_sec: int, limit: int) -> list[dict]:
    """Conexões ativas cuja saúde nunca foi medida ou está velha demais.

    É o que faz uma conexão morta sair de ACTIVE mesmo com o refresh desligado e
    sem webhook nenhum: um `GET /items/{id}` não consome cota de coleta.

    `has_data` vem junto porque o job de saúde precisa decidir `no_accounts` por
    OBSERVAÇÃO e não por memória (ver a tabela em `core/services/pluggy_health.py`):
    sem ele, um `no_accounts` de uma passada ruim ficava pegajoso para sempre.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select c.id, c.user_id, c.provider_item_id, c.status, c.status_reason,
                       c.health,
                       (exists (select 1 from open_finance_accounts a
                                 where a.connection_id = c.id)
                        or exists (select 1 from open_finance_investments i
                                    where i.connection_id = c.id)) as has_data
                  from open_finance_connections c
                 where provider='pluggy'
                   and upper(coalesce(status,'')) not in {_TERMINAL}
                   and provider_item_id is not null
                   and (
                       health is null
                       or coalesce((health->>'observed_at')::timestamptz, to_timestamp(0))
                          < now() - make_interval(secs => %s)
                   )
                 order by id
                 limit %s
                """,
                (older_than_sec, limit),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def register_item(
    user_id: int | None,
    *,
    provider_item_id: str | None = None,
    token_hash: str | None = None,
    origin: str,
    status: str | None = None,
    last_event: str | None = None,
    provider: str = "pluggy",
) -> int:
    """Registra que um item (ou um connect token) existiu.

    O `GET /items` da Pluggy devolve 401, então o universo remoto NÃO é
    enumerável: sem este rastro não há como descobrir um item órfão. Guarda
    HASH do token, nunca o token — ele autoriza abrir a conexão.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_item_registry
                    (user_id, provider, provider_item_id, connect_token_hash,
                     origin, status, last_event)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, provider, provider_item_id, token_hash, origin, status, last_event),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return new_id


def token_hash(access_token: str) -> str:
    """sha256 do connect token — o BRUTO nunca vai para o banco."""
    return hashlib.sha256((access_token or "").encode("utf-8")).hexdigest()


def of_health_counters() -> dict[str, Any]:
    """Contadores da aba Open Finance do painel admin (uma query, sem PII)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  count(*) filter (where upper(coalesce(status,'')) in ('ACTIVE','UPDATED')) as ativas,
                  count(*) filter (where upper(coalesce(status,'')) = 'ERROR') as erro,
                  count(*) filter (where coalesce(jsonb_array_length(health->'stale_products'),0) > 0)
                      as parciais,
                  count(*) filter (where status_reason = 'item_missing') as item_missing,
                  count(*) filter (where status_reason = 'no_accounts') as sem_contas,
                  count(*) as total,
                  max(extract(epoch from (now() - last_sync_at)))
                      filter (where upper(coalesce(status,'')) not in ('PAUSED','DELETED'))
                      as maior_atraso_sec
                from open_finance_connections
                where provider='pluggy'
                """
            )
            row = dict(cur.fetchone() or {})

            # Produto mais atrasado: lido do health, que é onde a Pluggy diz
            # quem ficou pra trás (o status da conexão não sabe disso).
            cur.execute(
                """
                select p as produto, count(*) as n
                  from open_finance_connections,
                       lateral jsonb_array_elements_text(coalesce(health->'stale_products','[]'::jsonb)) p
                 where provider='pluggy'
                   and upper(coalesce(status,'')) not in ('PAUSED','DELETED')
                 group by p order by n desc
                """
            )
            row["stale_por_produto"] = {r["produto"]: int(r["n"]) for r in (cur.fetchall() or [])}

            # Items vistos (registry) que não têm conexão local nenhuma.
            cur.execute(
                """
                select count(distinct r.provider_item_id) as n
                  from open_finance_item_registry r
                 where r.provider_item_id is not null
                   and not exists (
                       select 1 from open_finance_connections c
                        where c.provider = r.provider
                          and c.provider_item_id = r.provider_item_id
                   )
                """
            )
            row["items_sem_conexao"] = int((cur.fetchone() or {}).get("n") or 0)

            # Deadlocks/retries recentes (24h) — o log de sistema é a fonte.
            # `system_event_logs` é criada PREGUIÇOSAMENTE (core/admin_dashboard.py):
            # num banco onde nenhum evento foi gravado ainda ela não existe, e o
            # erro derrubaria a caixa inteira de OF do painel. `to_regclass`
            # pergunta sem estourar — dentro de um except a transação já estaria
            # abortada e as leituras acima iriam junto.
            cur.execute("select to_regclass('public.system_event_logs') as t")
            if not (cur.fetchone() or {}).get("t"):
                row["eventos_24h"] = {}
                return _finaliza(row)
            cur.execute(
                """
                select event_type, count(*) as n
                  from system_event_logs
                 where source='open_finance' and created_at > now() - interval '24 hours'
                   and event_type in ('pluggy_sync_retry','pluggy_sync_failed',
                                      'of_item_missing','of_item_owner_conflict')
                 group by event_type
                """
            )
            row["eventos_24h"] = {r["event_type"]: int(r["n"]) for r in (cur.fetchall() or [])}

    return _finaliza(row)


def _finaliza(row: dict) -> dict:
    row["maior_atraso_sec"] = int(row["maior_atraso_sec"] or 0)
    return row


def _lock_key(item_id: str) -> str:
    return f"of_sync:{item_id}"


def _lock_wait_ms() -> int:
    """Teto de espera pelo lock, em ms. NUNCA 0.

    `lock_timeout='0ms'` no Postgres significa *desligado* — espera infinita —,
    o oposto do que "0" sugere. Medido: `400ms` desistia em 0,48s; `0` seguia
    bloqueado depois de 3s, com a thread segurando a conexão dedicada. O piso é
    1ms, que é "não espere" de verdade.
    """
    try:
        return max(1, int(os.getenv("OF_SYNC_LOCK_WAIT_MS", "15000")))
    except (TypeError, ValueError):
        return 15000


@lru_cache(maxsize=1)
def _lock_slots() -> threading.Semaphore:
    """Teto de conexões DEDICADAS simultâneas (fora do pool).

    Medido: 30 syncs concorrentes abriam 30 backends extras. Quando o
    `psycopg.connect` estoura, o erro não é retentável e a cota da Pluggy já foi
    gasta na leitura — troca um `PoolTimeout` local por falha de cluster. Quem
    não pega vaga volta `False` e o chamador reporta `sync_in_progress`, que é o
    mesmo desfecho de perder o lock.

    ponytail: teto POR PROCESSO. Com N réplicas o teto real é N×. Se isso
    apertar, o próximo degrau é contar as conexões no banco, não aqui.
    """
    try:
        teto = int(os.getenv("OF_SYNC_LOCK_MAX_CONN", "8"))
    except (TypeError, ValueError):
        teto = 8
    return threading.Semaphore(max(1, teto))


@contextmanager
def pluggy_item_lock(item_id: str, *, budget_ms: int | None = None):
    """Serializa a FASE DE ESCRITA de um item da Pluggy. Devolve True se adquiriu.

    Duas decisões, e as duas custaram caro na versão anterior:

    1. **A janela é só a escrita.** O lock era pego antes das leituras remotas e
       segurado durante `list_pluggy_transactions` — até 60 requisições paginadas
       POR CONTA, minutos. A leitura remota é read-only contra a Pluggy e não
       precisa de lock nenhum; quem dava os 10 `deadlock detected` medidos em
       produção eram os upserts. O chamador (`pluggy_sync`) entra aqui só depois
       de ter tudo em memória.
    2. **Conexão DEDICADA, fora do pool.** Um advisory lock de SESSÃO retém a
       conexão enquanto dura, e reter conexão do pool aqui é auto-deadlock: o
       próprio sync precisa do pool para escrever. Medido: com
       `DB_POOL_MAX_SYNC=2` e 2 locks abertos, um `select 1` trivial estourava
       `PoolTimeout` em 30s — em produção `max_size=8`, ou seja, 8 syncs
       travariam o processo inteiro (dashboard e WhatsApp junto).

    Bloqueante com teto (`OF_SYNC_LOCK_WAIT_MS`, 15s): como a janela agora é
    curta, esperar a vez é melhor que desistir — quem chega segundo escreve
    depois, e todas as escritas são idempotentes. Estourou o teto, devolve False
    e o chamador reporta `sync_in_progress`.

    `budget_ms` é o TETO TOTAL desta aquisição, dividido entre as duas esperas.
    Sem ele, cada uma tem o seu próprio teto — o que significa que uma chamada
    pode custar 2× `OF_SYNC_LOCK_WAIT_MS`. Isso é aceitável no sync, que roda
    fora de request; **não** é aceitável numa rota HTTP, e foi o apontamento do
    Codex (#166, P2): com 2 tentativas e backoff, o POST `/pluggy/item` podia
    passar de um minuto e o proxy derrubava antes — inclusive quando a gravação
    dava certo logo depois, ou seja, erro na cara do usuário num fluxo que
    funcionou. Quem passa `budget_ms` recebe a garantia de que ESTA chamada não
    excede esse total.

    O default continua sendo o comportamento de antes, de propósito: mudar o
    teto do sync não estava no escopo e o sync não tem cliente esperando.

    ponytail: a chave é por ITEM. Se aparecer deadlock entre items DO MESMO
    usuário (import de cartão + launches disputam as mesmas linhas de
    credit_bills), a correção é subir a chave para `of_sync_user:<user_id>` —
    mesma estrutura, uma linha de mudança, ao custo de serializar os bancos de
    um usuário.
    """
    item = (item_id or "").strip()
    if not item:
        yield False
        return

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não está definido.")

    teto_ms = _lock_wait_ms() if budget_ms is None else max(1, int(budget_ms))
    t0 = time.monotonic()
    # Vaga ANTES de abrir o socket: o teto só vale se ninguém conectar sem passar
    # por aqui. Mesmo teto de tempo do lock — quem espera demais desiste igual.
    if not _lock_slots().acquire(timeout=teto_ms / 1000.0):
        yield False
        return

    # COM orçamento: o que a vaga consumiu sai do que sobra para o advisory lock,
    # senão "teto total" seria teto por etapa e a soma dobraria. SEM orçamento:
    # cada etapa tem o seu, que é o comportamento que o sync já tinha.
    if budget_ms is None:
        restante_ms = _lock_wait_ms()
    else:
        restante_ms = teto_ms - int((time.monotonic() - t0) * 1000)
        if restante_ms < 1:
            # A vaga comeu o orçamento inteiro: não há tempo para o lock, e
            # esperar "só mais um pouquinho" é o que o deadline existe para
            # impedir. Devolve False, que o chamador já sabe tratar.
            # NÃO é só zelo: sem esta guarda, `restante_ms` chega a 0 e o
            # `set_config('lock_timeout','0ms')` lá embaixo significa espera
            # INFINITA no Postgres (o mesmo fato documentado em
            # `_lock_wait_ms`), ou negativo e o Postgres recusa.
            _lock_slots().release()
            yield False
            return

    try:
        # O `connect` TAMBÉM sai do orçamento. Sem `connect_timeout` o libpq
        # espera para sempre, e "prazo único" viraria mentira exatamente no
        # cenário que ele existe para cobrir — banco ou rede sob pressão.
        # Medido: com um host inalcançável e prazo de 1s, a chamada seguia
        # bloqueada aos 30s. Só no caminho COM orçamento; sem ele o
        # comportamento é o de antes, incluindo esta lacuna (o sync não tem
        # cliente esperando, e mudar o teto dele não estava no escopo).
        extra = {} if budget_ms is None else {
            # libpq conta em segundos inteiros e trata 0 como "sem limite";
            # o piso de 1s é dele, não nosso.
            "connect_timeout": max(1, restante_ms // 1000),
            # Backstop de SESSÃO para o PRIMEIRO statement desta conexão
            # dedicada — o `set_config` logo abaixo, que sem isto não teria teto
            # nenhum: o `connect_timeout` cobre só o handshake, então um servidor
            # que aceita o socket e não responde penduraria o `set_config` para
            # sempre, DENTRO da janela do prazo.
            # `options` é parâmetro de STARTUP: só existe ANTES do connect, então
            # carrega o `restante_ms` PRÉ-connect — o mesmo valor inflado que a
            # recontagem lá embaixo existe para corrigir. Por isso ele NÃO é o
            # teto do advisory: o `set_config` abaixo REESCREVE
            # `statement_timeout` com o valor recontado, e daí em diante fino
            # (`lock_timeout`) e grosso (`statement_timeout`) valem os dois o que
            # sobrou. O cancelamento chega como `QueryCanceled`, que o `except`
            # do `pg_advisory_lock` abaixo já trata como `got=False`.
            # Medido: `options` e `connect_timeout` convivem — `show
            # statement_timeout` devolveu `1500ms` e um `pg_sleep(5)` foi
            # cancelado em 1,57s.
            # ponytail: o kwarg SOBRESCREVE um `options` que venha na URL
            # (medido: `application_name` da URL virou vazio). Hoje a
            # `DATABASE_URL` não traz `options`; se um dia trouxer, o conserto é
            # concatenar em vez de substituir.
            "options": f"-c statement_timeout={restante_ms}ms",
        }
        conn = psycopg.connect(url, autocommit=True, **extra)
    except Exception:
        _lock_slots().release()
        raise
    try:
        if budget_ms is not None:
            # RECONTA depois do connect. Dar ao advisory o mesmo `restante_ms`
            # de antes de conectar fazia as duas etapas somarem quase o DOBRO da
            # cota — o mesmo erro de "cada etapa ganha o orçamento inteiro" que
            # o teto total veio consertar, um nível abaixo (Codex #166, P2).
            restante_ms = teto_ms - int((time.monotonic() - t0) * 1000)
            if restante_ms < 1:
                # Sem `close`/`release` aqui: o `finally` abaixo faz os dois. A
                # primeira versão fazia à mão E deixava o `finally` repetir —
                # fechar duas vezes é inócuo, mas `Semaphore.release()` duplo
                # AUMENTA o contador e afrouxa o teto de conexões dedicadas para
                # sempre. Pego por teste, não por leitura.
                yield False
                return
        # `statement_timeout` vai JUNTO com o `lock_timeout`, no valor RECONTADO.
        # Sem esta segunda metade o teto grosso ficava valendo o `restante_ms`
        # pré-connect que o `options` carregou — exatamente o erro que a
        # recontagem acima consertou para o `lock_timeout`: um connect de 9,9s
        # numa cota de 10s deixava a etapa seguinte com 10s de teto, e a etapa
        # somava ~20s numa cota de 10s.
        # Só no caminho COM orçamento, pelo mesmo motivo do `extra` lá em cima: o
        # sync não tem cliente esperando e o SQL dele fica byte a byte o de antes.
        # Medido no Postgres local: com `options=-c statement_timeout=9000ms` no
        # connect, este `set_config` levou o `show statement_timeout` de `9s` para
        # `1200ms` na mesma sessão, e um `pg_sleep(5)` seguinte foi cancelado em
        # 1,28s com `QueryCanceled` — que é o que o `except` abaixo já trata.
        # ponytail: sobra UM statement fora da recontagem — este próprio
        # `set_config`, que roda sob o `statement_timeout` de startup (o valor
        # inflado). É configuração, sem I/O de tabela; se um dia importar, o
        # conserto é mandar no `options` um piso pequeno e subir aqui.
        sql = "select set_config('lock_timeout', %s, false)"
        params = [f"{restante_ms}ms"]
        if budget_ms is not None:
            sql += ", set_config('statement_timeout', %s, false)"
            params.append(f"{restante_ms}ms")
        conn.execute(sql, params)
        try:
            conn.execute("select pg_advisory_lock(hashtext(%s))", (_lock_key(item),))
            got = True
        except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled):
            got = False
        yield got
    finally:
        # Fechar a conexão libera o advisory lock de sessão — não há unlock a
        # esquecer, e um processo morto no meio não deixa o item travado.
        conn.close()
        _lock_slots().release()
