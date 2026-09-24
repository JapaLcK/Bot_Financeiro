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
import logging
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
# `_CursorComTeto` mora em `db/open_finance.py` porque a escrita da reconexão foi
# quem precisou dele primeiro; a regra que ele carrega (teto por STATEMENT,
# reajustado antes de cada `execute`) é a mesma aqui, e ter uma segunda cópia
# dela seria a mesma regra em dois lugares (CLAUDE.md §0.7). O import é de mão
# única: `open_finance.py` só usa este módulo em import LOCAL de função
# (`:1099`), então não há ciclo — se algum dia ele subir para o topo de lá, este
# é o import que quebra.
from .open_finance import _CursorComTeto

# `logging` da stdlib e não o `_log_falha` de `core/observability.py`: aquele
# helper exige um `user_id` (2º posicional, cobrado por `ast` em
# `tests/test_log_falha_user_id.py`) e esta camada não tem um. É o mesmo padrão
# dos vizinhos `db/accounts.py`, `db/mfa.py` e `db/plans.py`.
logger = logging.getLogger(__name__)

# Estados locais terminais: nenhum resultado de sync pode sobrescrevê-los.
# PAUSED = trial venceu (o item nem existe mais na Pluggy); DELETED = removido.
# CUIDADO: interpolado como tupla Python nos `f"""..."""` abaixo — reduzir a lista
# a UM elemento emitiria `('PAUSED',)` e quebraria o SQL. Ao mexer, vire lista SQL.
_TERMINAL = ("PAUSED", "DELETED")

# ── O `executionStatus` derivado do `raw` ────────────────────────────────────
# FONTE ÚNICA (§0.7) da regra "o `raw` ainda descreve a autorização ATUAL". Dois
# consumidores, formatos diferentes, MESMA condição e MESMO parâmetro: o select
# do snapshot / `get_connections_by_item_id` (abaixo, via `SQL_EXECUTION_STATUS`)
# e o predicado do aviso proativo (`list_connections_needing_reconnect`, em
# `db/open_finance.py`, que precisa da condição CRUA dentro do `coalesce` dele).
#
# Por que em SQL e não em Python: `connection_ui_state` se declara "sem banco,
# sem rede" (`core/services/pluggy_health.py`) e não tem relógio. E aqui o
# `raw->>'executionStatus'` sai como TEXTO — nenhum `::timestamptz` sobre string
# vinda do provedor, cujo `InvalidDatetimeFormat` viraria 500 na tela de todos.
#
# A ÂNCORA é `coalesce(reconnected_at, created_at)`, e não `updated_at` nem
# `last_attempt_at`: `mark_sync_attempt` empurra esses dois para frente SEM que o
# `raw` mude, o que renovaria a validade de um valor congelado. `reconnected_at`
# tem um escritor só em PRODUÇÃO (o `on conflict` de
# `save_pluggy_open_finance_item`) e é gravado JUNTO com o `raw` que ele data —
# "em toda a árvore" seria falso, e por um: `scripts/of_corrida_dois_processos.py`
# também escreve a coluna (`reconnected_at=null`), e é script de laboratório;
# `created_at` é `default now()` e o `on conflict` nunca o toca.
#
# O `health is null` é obrigatório, não enfeite (mesmo precedente medido do
# predicado irmão): sem ele, um `health` observado DEPOIS e sem
# `execution_status` cairia no `raw` VELHO — `mark_sync_result` não toca em
# `raw`.
#
# O intervalo é FECHADO DOS DOIS LADOS, e o teto não é zelo: as duas pontas vêm
# de RELÓGIOS DIFERENTES — o carimbo é `datetime.now(_tz())` do PYTHON
# (`save_pluggy_open_finance_item`) e o `now()` aqui é do POSTGRES. Sem teto,
# qualquer adiantamento do app estende a janela um-para-um e um relógio
# grosseiramente errado a torna PERMANENTE. Medido com `reconnected_at = now() +
# interval '10 days'`: a tela devolvia "Autorize o acesso no app do banco" e o
# aviso proativo ficava calado — os dois para sempre, que é exatamente a falha
# que o PRAZO existe para fechar.
#
# A folga de 5 min é o desvio NORMAL entre app e banco (segundos): com `<= now()`
# puro, um app 2 s adiantado matava o conserto na RECONEXÃO recém-gravada. E é só
# na reconexão: no primeiro INSERT o `reconnected_at` nasce NULL e a âncora é o
# `created_at`, que é `default now()` do POSTGRES e nunca está no futuro — o
# carimbo do relógio do PYTHON só entra pelo ramo do CONFLITO
# (`reconnected_at = excluded.updated_at`, `db/open_finance.py`), que é o do
# widget reconectando. Ela custa 5 min a mais no pior caso legítimo (65 em vez de
# 60) e continua descartando o relógio errado de verdade.
SQL_RAW_AINDA_VALE = (
    "health is null "
    "and coalesce(reconnected_at, created_at) > now() - make_interval(mins => %s) "
    "and coalesce(reconnected_at, created_at) <= now() + interval '5 minutes'"
)

# Só o ESCALAR viaja. O `raw` inteiro nunca sai do Postgres: ele carrega
# `clientUserId` (e `statusDetail`), e o snapshot vai para o navegador.
#
# O `upper` É mudança de comportamento, a MESMA que o predicado irmão do aviso
# documenta (`list_connections_needing_reconnect`, `db/open_finance.py`) — e
# agora ela vale também para a TELA: um `executionStatus` em minúscula no `raw`
# passa a virar a instrução de dispositivo, onde antes caía no detalhe fixo
# "Reautorize o banco". Hoje é INALCANÇÁVEL pelo caminho de produção — a Pluggy
# manda `USER_AUTHORIZATION_PENDING` em maiúscula, e `_DETALHE_POR_STATUS` só tem
# chaves maiúsculas. Fica assim, e não numa comparação sensível a caixa, porque a
# direção é a barata: se um dia entrar minúscula, a tela erra para o lado de
# mandar ler o QR em vez de para o de fazer a pessoa PERDER a janela. Sem teste
# próprio — não há entrada de produção que chegue lá; o que tem teste são as
# células medidas na varredura em duas colunas
# (`tests/test_of_connection_state.py`,
# `test_as_celulas_que_mudaram_na_varredura_ficam_na_instrucao_de_dispositivo`).
SQL_EXECUTION_STATUS = (
    f"case when {SQL_RAW_AINDA_VALE} then upper(raw->>'executionStatus') end as execution_status"
)


def janela_device_auth_min() -> int:
    """O único `%s` de `SQL_RAW_AINDA_VALE` / `SQL_EXECUTION_STATUS`.

    Import local porque `db` -> `core.services` é de mão única neste pacote (ver
    `connection_ui_state` em `db/open_finance.py`); e função em vez de constante
    para que os três chamadores não repitam o import.

    SEM VALIDAÇÃO de propósito: o valor é um literal do módulo, sem override por
    env, então nenhum valor hostil é alcançável e validar aqui seria código
    defensivo para caso impossível (§0.2). Se um dia ela virar configuração, dois
    valores medidos quebram e a validação passa a ser devida: `0` NÃO desliga mais
    o derivado (o teto sozinho ainda admite `(now, now + 5 min]`), e qualquer
    valor acima de 2³¹−1 estoura `make_interval(mins => bigint) does not exist` —
    500 na aba de Open Finance E no laço do aviso proativo.
    """
    from core.services.pluggy_health import JANELA_DEVICE_AUTH_MIN
    return JANELA_DEVICE_AUTH_MIN


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


def get_connections_by_item_id(item_id: str, provider: str = "pluggy", *,
                               budget_ms: int | None = None) -> list[dict]:
    """TODAS as conexões daquele item — sem `limit`, para que a ambiguidade apareça.

    `budget_ms` é o teto desta leitura de ponta a ponta, nas DUAS metades: a
    espera do POOL (`get_conn(timeout=...)`) e a QUERY parada numa linha ou numa
    tabela travada (`_CursorComTeto`, que reajusta o `statement_timeout` pelo que
    sobrou). `None` (o default, e o que todo chamador de fora do prazo passa)
    mantém o comportamento de sempre — `DB_CONNECT_TIMEOUT`, default 30s de
    espera e statement sem teto. Existe porque `_salva_item_sob_lock` chama esta
    leitura com o advisory lock do item NA MÃO e um cliente HTTP esperando: ali a
    leitura tem de caber no que sobrou do prazo. Só a metade do pool não bastava —
    a conexão vem do pool, que não tem `statement_timeout` (o do `pluggy_item_lock`
    é da conexão DEDICADA, outra sessão), então um `alter table` do `init_db`
    (`db/schema.py`, ACCESS EXCLUSIVE) segurava a query MEDIDO 6,02 s contra
    prazo de 1000 ms, com o advisory lock retido e a escrita commitando depois de
    o cliente ter desistido.
    """
    item = (item_id or "").strip()
    if not item:
        return []
    espera = None if budget_ms is None else max(0.001, budget_ms / 1000.0)
    t0 = time.monotonic()
    with get_conn(timeout=espera) as conn:
        with conn.cursor() as cur:
            if budget_ms is not None:
                cur = _CursorComTeto(cur, budget_ms, t0)
            cur.execute(
                f"""
                select id, user_id, provider, provider_item_id, status, institution_name,
                       last_sync_at, last_attempt_at, status_reason, health,
                       next_refresh_at, last_refresh_origin, reconnected_at,
                       -- Para o `connection_ui_state`: sem ele, o toast do
                       -- /refresh manda "Reautorize o banco" na janela do QR
                       -- (`_refresh_items_report` lê ESTA linha). `mark_sync_result`
                       -- recebe `health` como OPCIONAL, então um sync que falhe
                       -- antes do `GET /items` não grava saúde e cai aqui.
                       -- Sem `pop` do lado de fora: estas linhas são internas
                       -- (nenhum consumidor as devolve cruas ao navegador).
                       {SQL_EXECUTION_STATUS}
                from open_finance_connections
                where provider=%s and provider_item_id=%s
                order by id
                """,
                (janela_device_auth_min(), provider, item),
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
            from .bank_movements import _lock_user, reconcile_bank_movements
            owner = None
            if status:
                cur.execute("select user_id from open_finance_connections where id=%s", (connection_id,))
                owner = cur.fetchone()
                if owner:
                    _lock_user(cur, owner["user_id"])
            # #444: uma foto de health tirada com o item ainda em coleta
            # (`_UPDATING`) pode não trazer todo produto — mescla com a foto
            # anterior antes de gravar, pra um produto atrasado não sumir da tela
            # só porque o item voltou a "buscar". `for update` porque a leitura e
            # a gravação do health precisam ser a MESMA transação: sem o lock,
            # dois syncs concorrentes do mesmo item poderiam mesclar sobre uma
            # foto já superada.
            from core.services.pluggy_health import _UPDATING, mesclar_health_em_coleta
            if health is not None and str(health.get("item_status") or "").upper() in _UPDATING:
                cur.execute("select health from open_finance_connections where id=%s for update",
                            (connection_id,))
                linha = cur.fetchone()
                health = mesclar_health_em_coleta(linha["health"] if linha else None, health)
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
            if owner:
                reconcile_bank_movements(cur, owner["user_id"])
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


# Item visto no registry (o único rastro: o `GET /items` da Pluggy devolve 401)
# que não tem NENHUMA conexão local. Quem lê é o contador do painel de saúde abaixo.
ITEMS_SEM_CONEXAO = """
  from open_finance_item_registry r
 where r.provider_item_id is not null
   and not exists (
       select 1 from open_finance_connections c
        where c.provider = r.provider
          and c.provider_item_id = r.provider_item_id
   )
"""


def item_registry_origins(provider_item_id: str, *, provider: str = "pluggy",
                          exceto_registro_id: int | None = None,
                          exceto_user_id: int | None = None,
                          budget_ms: int | None = None) -> set[str]:
    """Por quais portas este item já foi visto COM DONO.

    Uma pergunta, uma fonte (CLAUDE.md §0.7). Vazio = o item nunca foi atribuído
    a ninguém, e é isso que separa ADOTAR de RESSUSCITAR: nem o disconnect nem o
    reset apagam o registry (`db/privacy.py` o preserva), então banco REMOVIDO
    fica para sempre "sem conexão local", e é o rastro com dono que conta que ele
    existiu. São TRÊS as origens com dono, e AQUI elas valem igual — qualquer uma
    não-vazia recusa a adoção:

      • `pluggy_item` — o NAVEGADOR registrou o item (`POST /pluggy-item`);
      • `webhook_adopt` — a adoção pelo `item/created` registrou;
      • `removed` — o usuário mandou TIRAR o banco: disconnect e reset gravam a
        marca na mesma transação do delete (`mark_items_removed`). É ela que faz
        esta pergunta responder "teve dono" para a conexão que nunca teve linha
        `pluggy_item` — anterior ao registry, ou cujo `register_item` falhou —,
        que era o buraco por onde a reentrega de `item/created` ressuscitava
        banco removido.

    Quem precisa SEPARAR as três (nenhum leitor automático precisa; é a
    recuperação por operador, fora desta PR) usa a regra de precedência escrita
    no docstring de `mark_items_removed`. Os leitores daqui:

      • `_adota_item_orfao` — só adota item sem NENHUM dono no rastro (duplicata
        de `item/created`, entrega at-least-once, ressuscitava o removido), e a
        MESMA pergunta de novo dentro do lock, em `_salva_item_sob_lock`
        (`exceto_registro_id`, abaixo);
      • `POST /pluggy-item` — `'pluggy_item' in ...` = o NAVEGADOR já registrou
        este item, logo a conexão que existe não é a que o webhook acabou de
        adotar (auditoria de reconexão).

    `exceto_registro_id` IGNORA uma linha do rastro pelo `id` — a que o próprio
    chamador acabou de gravar. É o que permite ao `_salva_item_sob_lock` refazer
    a pergunta do 1º leitor DENTRO do `pluggy_item_lock` ("alguém MAIS já tem
    este item?") sem que a adoção em curso responda a si mesma (Codex #313, P1).
    Um parâmetro na fonte única em vez de uma 2ª query com o mesmo `user_id is
    not null`: a regra "teve dono" continua escrita uma vez (CLAUDE.md §0.7).
    Ele ANDA COM `exceto_user_id`, e o `ValueError` abaixo OBRIGA: a query filtra
    por `provider + provider_item_id`, então um `id` de OUTRO item nunca mascara
    nada, mas o mesmo item pode ter linha de outro USUÁRIO — e esconder a origem
    dela seria vazar decisão entre usuários (CLAUDE.md §0, isolamento). O par só
    ignora a linha que é do próprio dono em curso. Sem a guarda, meio par era
    SILENCIOSAMENTE pior que nenhum: só `exceto_registro_id` faz o `user_id = %s`
    virar `= null`, o `not (...)` inteiro vira NULL, e o `where` descarta TODA
    linha — `set()` no lugar de `{'webhook_adopt'}`, ou seja "item nunca teve
    dono" para um item que tem. O chamador de produção passa os dois; isto é
    fronteira para o PRÓXIMO (CLAUDE.md §0.2, validação não se simplifica).

    Devolve ORIGENS, nunca `user_id`: o chamador decide sobre o item, e nenhum
    dado de outro usuário sai daqui.

    `budget_ms` é o teto desta leitura nas DUAS metades — espera do POOL e QUERY
    travada —, exatamente como em `get_connections_by_item_id`, que documenta a
    medição. `None` (o default, e o que todo chamador de fora do prazo passa)
    mantém o comportamento de sempre. Existe pela revalidação da adoção em
    `_salva_item_sob_lock`, que roda com o advisory lock do item NA MÃO e um
    cliente HTTP esperando: ali a leitura tem de caber no que sobrou do prazo.
    """
    if (exceto_registro_id is None) != (exceto_user_id is None):
        raise ValueError("exceto_registro_id e exceto_user_id andam juntos ou nenhum")
    espera = None if budget_ms is None else max(0.001, budget_ms / 1000.0)
    t0 = time.monotonic()
    with get_conn(timeout=espera) as conn:
        with conn.cursor() as cur:
            if budget_ms is not None:
                cur = _CursorComTeto(cur, budget_ms, t0)
            cur.execute(
                """
                select distinct origin from open_finance_item_registry
                 where provider = %s and provider_item_id = %s and user_id is not null
                   and (%s::bigint is null or not (id = %s and user_id = %s))
                """,
                (provider, str(provider_item_id or ""),
                 exceto_registro_id, exceto_registro_id, exceto_user_id),
            )
            return {r["origin"] for r in (cur.fetchall() or []) if r["origin"]}


def unregister_item(registro_id: int, user_id: int) -> int:
    """Apaga UMA linha do rastro: a reivindicação que a própria adoção ABANDONOU.

    O registry é log de append ("este item existiu, visto por esta porta") e
    continua sendo — o único caso que apaga é a linha que ESTA adoção gravou
    segundos atrás e não vai honrar, porque outra entrega ficou com o item. Sem
    isso o aborto era TERMINAL: rastro com dono e zero conexão recusa toda
    retentativa (1ª guarda de `_adota_item_orfao`), e o usuário fica com 0
    bancos sem saída pelo produto.

    `user_id` no `where` não é decoração: é o isolamento por usuário do
    CLAUDE.md §0 aplicado a um DELETE que recebe um `id` cru.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from open_finance_item_registry where id = %s and user_id = %s",
                (registro_id, user_id),
            )
            n = cur.rowcount
        conn.commit()
    return n


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

    `removal_tracked` é literal `true` e NÃO é parâmetro: toda linha escrita por
    esta versão é da era em que a remoção deliberada também deixa marca
    (`mark_items_removed`), inclusive a de `user_id` nulo. Um parâmetro obrigaria
    os quatro chamadores a decidir a mesma coisa (CLAUDE.md §0.2).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_item_registry
                    (user_id, provider, provider_item_id, connect_token_hash,
                     origin, status, last_event, removal_tracked)
                values (%s,%s,%s,%s,%s,%s,%s,true)
                returning id
                """,
                (user_id, provider, provider_item_id, token_hash, origin, status, last_event),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return new_id


def pluggy_items_a_deletar(linhas) -> list[str]:
    """Das linhas de `provider, provider_item_id, status`: quais items ainda
    precisam de DELETE na Pluggy.

    FONTE ÚNICA (CLAUDE.md §0.7) de um filtro que estava copiado palavra por
    palavra em três lugares e ia para o quarto: o disconnect
    (`db/open_finance.py`, `swept_out`), o reset e a exclusão de conta
    (`db/privacy.py` — lá são DOIS: o `RETURNING` do delete e a reconsulta que
    fecha a janela da cascata). Todos leem o MESMO formato de linha e alimentam o
    MESMO `delete_pluggy_items_best_effort`.

    `PAUSED` fica FORA: o item já foi deletado na Pluggy no vencimento do trial, e
    é a mesma regra do `list_pluggy_item_ids`. É exatamente aqui que esta função
    difere de `mark_items_removed`, que lê a mesma tupla e INCLUI `PAUSED` de
    propósito — a marca é sobre a intenção do usuário, não sobre o item existir
    lá (docstring dela, logo abaixo). Duas leituras da mesma tupla, uma regra
    cada, cada uma num lugar só.

    Ordenado e sem repetição: o chamador compara conjuntos (1º passe × 2º passe).
    """
    return sorted({
        r["provider_item_id"] for r in (linhas or [])
        if r["provider"] == "pluggy" and r["provider_item_id"]
        and str(r["status"] or "").upper() != "PAUSED"
    })


def mark_items_removed(cur, user_id: int, linhas, *, last_event: str) -> int:
    """Grava a marca da remoção DELIBERADA, no cursor de quem apagou a conexão.

    `linhas` são as do `returning provider, provider_item_id, status` do DELETE
    de `open_finance_connections` — disconnect (`db/open_finance.py`) e reset
    (`db/privacy.py`), os dois lugares onde o usuário diz "tire este banco".
    A marca é `origin='removed'`, e é o que faz a 1ª guarda de
    `_adota_item_orfao` recusar uma reentrega de `item/created` de um item
    REMOVIDO que não tem (ou perdeu) a linha `pluggy_item`: a guarda já recusa
    qualquer origem COM DONO, e a marca é a que passa a existir.

    ESCREVE NO CURSOR RECEBIDO, nunca em `get_conn()` próprio: a atomicidade com
    o delete é o ponto. Marca fora da transação = delete que commita sem marca
    (ou marca de um delete que deu rollback), e a ressurreição volta.

    `user_id` vem do PARÂMETRO (o mesmo do `where user_id = %s` do delete), nunca
    das linhas — isolamento por usuário, CLAUDE.md §0.

    `PAUSED` entra (ao contrário do `swept_out`, que alimenta o 2º passe de delete
    REMOTO): a marca é sobre a INTENÇÃO do usuário, não sobre o item ainda existir
    na Pluggy — e o delete remoto do trial expiry é best-effort, então item pausado
    pode estar vivo lá e mandando evento.

    PRECEDÊNCIA (a regra, escrita aqui porque é aqui que a linha nasce; nenhum
    leitor de HOJE a usa — `item_registry_origins` trata as três origens com dono
    igual, e é isso que recusa a adoção). O registry é log de APPEND: depois de
    "conectou → removeu → reconectou" o item tem, em ordem de `id`,
    `pluggy_item`, `removed`, `pluggy_item`. Para decidir o ESTADO de um item sem
    conexão local, ordene as linhas com `user_id is not null` por `id` crescente
    e olhe a ÚLTIMA:

      • `origin='removed'`                                  → REMOVIDO pelo usuário;
      • `pluggy_item`/`webhook_adopt` COM `removal_tracked`  → INTERROMPIDO (adoção
        ou POST que não completou: a era já marca remoção, então a ausência de
        `removed` é informação);
      • `pluggy_item`/`webhook_adopt` SEM `removal_tracked`  → LEGADO AMBÍGUO (rastro
        anterior à marca: não dá para saber);
      • nenhuma linha com dono                              → NUNCA ATRIBUÍDO (adotável).

    Por `id` e não por `created_at`: `now()` é o tempo de INÍCIO da transação,
    empata entre duas escritas da mesma transação e pode inverter entre sessões
    concorrentes; `id` é `bigserial`, alocado no INSERT.

    LIMITE CONHECIDO da regra (declarado, não consertado): ordem de ALOCAÇÃO não
    é ordem de COMMIT. O caso que importaria — disconnect × reconexão do mesmo
    item — é serializado pelo `pluggy_items_lock`, porque o item está em
    `list_pluggy_item_ids(user_id)` enquanto a conexão existe. SOBRA uma janela:
    o `register_item` do `POST /pluggy-item` roda FORA do lock (em
    `frontend/routes/open_finance.py`, logo DEPOIS de `_grava_reconexao`
    devolver), então um `DELETE /open-finance/{uid}` que caia entre o commit da
    conexão e esse insert deixa a última linha como `pluggy_item` com o banco
    REMOVIDO — o item sai classificado "interrompido". Janela de ms e exige o
    clique do usuário dentro dela; quem consumir a regra põe um humano item a
    item, então o desfecho é revisável.

    Devolve quantas marcas gravou.
    """
    # O `or "pluggy"` e o filtro de `provider_item_id` não podem disparar — as duas
    # colunas são `not null` em `open_finance_connections` (`db/schema.py:415-416`).
    # Ficam porque ESPELHAM, linha a linha, o filtro do `swept_out` que lê o MESMO
    # `returning` nos mesmos dois chamadores (`db/open_finance.py:2786-2789`,
    # `db/privacy.py:743-746`): duas leituras divergentes da mesma tupla é o que
    # custa caro depois. Só `status` e `PAUSED` divergem, de propósito (acima).
    valores = [
        (user_id, r["provider"] or "pluggy", r["provider_item_id"], r["status"], last_event)
        for r in (linhas or []) if r["provider_item_id"]
    ]
    if not valores:
        return 0
    cur.executemany(
        """
        insert into open_finance_item_registry
            (user_id, provider, provider_item_id, origin, status, last_event,
             removal_tracked)
        values (%s,%s,%s,'removed',%s,%s,true)
        """,
        valores,
    )
    return len(valores)


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
            cur.execute(f"select count(distinct r.provider_item_id) as n {ITEMS_SEM_CONEXAO}")
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
        # `try/finally` pelo mesmo motivo do irmão `pluggy_items_lock`: `close()`
        # que levanta pulava o `release()` e a vaga do `_lock_slots()` não voltava
        # nunca (issue #429). Mesma classe, mesmo conserto, nos dois.
        try:
            conn.close()
        finally:
            _lock_slots().release()


@contextmanager
def pluggy_items_lock(item_ids: list[str]):
    """Versão N-itens do `pluggy_item_lock`: todos os advisory locks numa
    ÚNICA sessão dedicada — 1 slot do semáforo e 1 conexão para N itens.

    Existe por causa do reset de conta (`db/privacy.reset_user_data`), que
    segura os locks de TODOS os itens do usuário ao mesmo tempo. N
    `pluggy_item_lock` aninhados retinham N slots — e com
    N > OF_SYNC_LOCK_MAX_CONN o (teto+1)-ésimo esperava um slot que o próprio
    chamador segurava: False sempre, reset impossível (Codex, PR #217).

    A semântica contra um sync concorrente é IDÊNTICA à do singular: mesma
    chave por item (`_lock_key`), mesmo prazo (`_lock_wait_ms`) — sync com a
    chave ocupada não entra, vira False e reporta sync_in_progress. O
    cancelamento chega como `QueryCanceled`, igual ao irmão singular, e NÃO como
    `LockNotAvailable`: o porquê está no comentário do `connect` abaixo (o
    `statement_timeout` corta antes do `lock_timeout` quando os dois valem o
    mesmo). Os dois caem no mesmo `except` e dão o mesmo `got=False`.

    Devolve True só se TODOS os locks entraram (lista vazia → True: nada a
    serializar). Fechar a conexão libera todos de uma vez — não há unlock
    parcial a esquecer.

    `sorted(set(...))`: a lista de entrada não tem ordem estável
    (`list_pluggy_item_ids` é sem ORDER BY) — dois chamadores adquirindo em
    ordens cruzadas podiam se deadlockar; ordem consistente elimina o ciclo
    por construção, e a dedup torna explícito que item repetido não conta duas
    vezes. DeadlockDetected no except é o cinto-e-suspensório: se um ciclo
    aparecer por outro caminho, o desfecho é o mesmo False→"tente de novo",
    não um 500.
    """
    itens = sorted({i.strip() for i in (item_ids or []) if i and i.strip()})
    if not itens:
        yield True
        return

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não está definido.")

    espera = _lock_wait_ms() / 1000.0
    if not _lock_slots().acquire(timeout=espera):
        yield False
        return
    try:
        # Mesmo idioma do irmão singular (`pluggy_item_lock`, ramo COM orçamento),
        # e NENHUM número novo: os dois saem de `_lock_wait_ms()`, que já é a fonte
        # de verdade (`OF_SYNC_LOCK_WAIT_MS`, default 15000, piso documentado lá).
        # Aqui não é zelo — tem CLIENTE ESPERANDO nos dois chamadores:
        # `db/privacy.py reset_user_data` e `frontend/routes/open_finance.py
        # _disconnect_sob_lock`, e este último roda por `asyncio.to_thread` DENTRO
        # da rota, sem try/except. Sem `connect_timeout`, banco que aceita o socket
        # e não responde penduraria a thread para SEMPRE, segurando uma vaga do
        # `_lock_slots()` — e a vaga não volta nunca (issue #429).
        # `statement_timeout` e `lock_timeout` ficam IGUAIS, e isso NÃO deixa
        # indeterminado quem corta primeiro — a versão anterior deste comentário
        # dizia que sim, e estava errada: o `statement_timeout` conta desde o
        # início do STATEMENT e o `lock_timeout` só desde o início da ESPERA pelo
        # lock, então com valores iguais o primeiro vence SEMPRE. MEDIDO: chave
        # ocupada por outra sessão sai `QueryCanceled` (57014) em 60/60, e
        # também nos valores de produção (15000/15000 → 15,00 s). É o mesmo fato
        # que o irmão singular já declara ("o cancelamento chega como
        # `QueryCanceled`") — duas versões dele no mesmo módulo era o §0.7.
        # COROLÁRIO, e por isso ele está escrito: o `set_config('lock_timeout',
        # …)` lá embaixo é INERTE — nunca é ele quem dispara. Continua onde está
        # de propósito: é o PRIMEIRO statement desta conexão e é a morte DELE que
        # o `except` de baixo passou a cobrir; trocar o valor dele (ou tirá-lo) é
        # outro PR, não este. De todo jeito os dois desfechos caem no MESMO
        # `except` abaixo e dão o mesmo `got=False`, que o chamador já traduz em
        # 503 "tente de novo".
        conn = psycopg.connect(
            url, autocommit=True,
            # libpq conta em segundos inteiros e trata 0 como "sem limite"; o piso
            # de 1s é dele, não nosso.
            connect_timeout=max(1, _lock_wait_ms() // 1000),
            options=f"-c statement_timeout={_lock_wait_ms()}ms",
        )
    except psycopg.OperationalError:
        # SIMÉTRICO com o `except` de baixo, de propósito. O `connect_timeout` que
        # esta função ganhou trocou "pendura para sempre" por exceção — melhora — mas
        # a exceção subia crua numa rota SEM try/except, virando 500 numa função cuja
        # docstring promete 503/"tente de novo". Fechar o 500 do `set_config` e abrir
        # o do `connect` no mesmo commit seria trocar um furo de lugar.
        #
        # A FRONTEIRA REAL, MEDIDA (psycopg 3.3.5, contra o Postgres local), e não
        # a que a versão anterior deste comentário imaginava:
        #   ProgrammingError (sobe → 500): URL malformada, esquema errado (`mysql://`)
        #   OperationalError (aqui → 503): host inalcançável, banco INEXISTENTE,
        #                                  usuário/credencial ERRADA
        # Ou seja, config quebrada NÃO fica toda do lado do 500 — "role does not
        # exist" e "database does not exist" caem aqui, são defeito PERMANENTE, e
        # o usuário só vê "sincronização em andamento, tente de novo"
        # (`frontend/routes/open_finance.py`, `db/privacy.py`). Daí o `warning`:
        # o 503 continua sendo a resposta certa para o transitório, e o permanente
        # deixa de ser MUDO — era o único valor que o 500 anterior comprava.
        #
        # `exc_info=True` porque o `sqlstate` NÃO discrimina: nas três falhas de
        # connect acima ele é `None` (medido), então tipo+sqlstate — o formato do
        # `_log_falha` — sairia idêntico para "servidor caiu" e para "usuário não
        # existe". Quem separa os dois é a mensagem do libpq, que traz host, porta,
        # base e papel — infraestrutura, não dado do cliente: não há `DETAIL: Key
        # (…)=(…)` num erro de connect (é a razão de privacidade que mantém o
        # traceback desligado por padrão no `_log_falha`), e a senha o libpq não
        # ecoa (medido).
        #
        # NÃO reentra no `_DashboardHandler` (medido, com o handler real no root):
        # profundidade máxima de `emit` = 1, 1 connect, 3–5 ms. O ciclo não fecha
        # porque `core/system_event_log.py` desfecha em `print`, não em `logging`;
        # e com a `DATABASE_URL` quebrada o INSERT do handler também falha e vira
        # `[observability] failed to record …` no stderr — a causa continua saindo
        # duas vezes lá, que é o que o operador perdeu em `935b2a7`.
        #
        # A VAGA VOLTA ANTES DO LOG, e a ordem é conserto, não estilo: este
        # WARNING passa pelo `_DashboardHandler` (`core/observability.py`), que
        # grava abrindo `psycopg.connect` + INSERT — o log é uma ida ao banco.
        # Com o banco bom ela custa milissegundos: a medição dessa grandeza é o
        # `ponytail:` do docstring de `_DashboardHandler`, e com `exc_info=True`
        # a 1ª chamada sai no mesmo intervalo (remedido 2026-09-16) — traceback
        # + `exc_info` + JSONB não mudam a ordem. Os 390,6 ms que uma versão
        # anterior desta frase dava para a 1ª chamada vieram de uma medição
        # feita com a suíte inteira rodando em paralelo na mesma máquina; sob
        # carga o número sobe (2026-09-16: até 88,3 ms com 12 workers de
        # connect+INSERT+CPU e CREATE/DROP DATABASE concorrentes), sem chegar a
        # 390 ms — carga é a causa provável, não confirmada. Remedir antes de
        # reusar. E com o host INALCANÇÁVEL ela custa ~2 s — o `connect_timeout=2`
        # de `core/system_event_log.py`; 2006,8 ms em 2026-09-16 com:
        #   python3 -c "import time,psycopg; t=time.perf_counter()
        #   try: psycopg.connect('postgresql://u:p@10.255.255.1/db', connect_timeout=2)
        #   except psycopg.OperationalError: print(f'{(time.perf_counter()-t)*1000:.1f} ms')"
        # É justamente aí que este `except`
        # dispara: quando o banco está indo embora. Logar com a vaga na mão
        # somava 2 s a uma das 8 do `_lock_slots()`, e o disconnect e o
        # `reset_user_data` enfileiravam atrás do log de um 503 — num caminho que
        # existe para não pendurar ninguém. Logar continua obrigatório (503 mudo
        # é o que `935b2a7` fechou); segurar a vaga para logar, não.
        # ponytail: os 2 s continuam na conta de QUEM PEDIU (o log roda antes do
        # `yield`, na mesma thread) — o que deixa de ser compartilhado é a vaga.
        # Tirar os 2 s do requisitante exigiria log fora da thread; se um dia
        # importar, é lá, não aqui.
        _lock_slots().release()
        logger.warning(
            "pluggy_items_lock: conexão dedicada falhou, devolvendo 503 (itens=%d)",
            len(itens), exc_info=True,
        )
        yield False
        return
    except Exception:
        _lock_slots().release()
        raise
    # Guardado aqui e logado lá embaixo, DEPOIS do `release()`: mesmo motivo
    # medido do `except` do `connect` (2006,3 ms de log com o host inalcançável).
    # `AdminShutdown`/`DiskFull`/`TooManyConnections` são exatamente o caso em que
    # o handler paga o `connect_timeout` inteiro, e o padrão dos dois `except`
    # tem de ser UM só.
    nao_rotineiro: Exception | None = None
    try:
        got = True
        # O `set_config` entra no MESMO `try` dos advisory locks de propósito: ele
        # é o PRIMEIRO statement desta conexão e agora tem teto (o `options` acima).
        # Fora do `except`, qualquer morte dele subia como exceção e virava 500 na
        # rota do disconnect (o `await asyncio.to_thread(_disconnect_sob_lock, ...)`
        # de `open_finance_disconnect_route`, em `frontend/routes/open_finance.py`,
        # não tem try/except — nome de construção e não número de linha, porque o
        # número desta citação já envelheceu uma vez), enquanto a docstring promete
        # 503/"tente de novo" e é o que o resto da função entrega.
        # O modo de morte PROVÁVEL deste statement não é o cancelamento: é a conexão
        # morrer (servidor fechou o socket, blip de rede, restart, pgbouncer). O
        # cancelamento por `statement_timeout` NÃO REPRODUZ — medido com
        # `OF_SYNC_LOCK_WAIT_MS=1` contra o Postgres local, 0 de 300 tentativas pela
        # réplica direta (o `set_config` cabe folgado em 1ms). Por isso o `except` é
        # `psycopg.OperationalError`, o PAI comum: `LockNotAvailable`,
        # `QueryCanceled` e `DeadlockDetected` são todos subclasses dele (medido:
        # `.__mro__[1]` é `OperationalError` nos três), então este nome só cobre
        # ESTRITAMENTE MAIS que a tupla anterior — e o que ele acrescenta é
        # justamente o modo provável. Os três nomes não se perdem: continuam sendo o
        # que os advisory locks levantam quando a chave está ocupada.
        # O teste do desfecho INJETA a exceção em vez de cronometrar: com 0/300, um
        # teste por cronômetro ali seria flaky e não prenderia nada.
        try:
            conn.execute("select set_config('lock_timeout', %s, false)", (f"{_lock_wait_ms()}ms",))
            for item in itens:
                conn.execute("select pg_advisory_lock(hashtext(%s))", (_lock_key(item),))
        except psycopg.OperationalError as exc:
            # ASSIMÉTRICO com o `except` do `connect` lá em cima, e de propósito:
            #   • lá, TODO `OperationalError` vira WARNING, porque o defeito
            #     PERMANENTE (base/usuário inexistente, credencial errada) cai
            #     justamente nele — mudo ali significa 503 eterno sem rastro;
            #   • aqui, o defeito permanente NÃO passa por este `except`: config
            #     quebrada destes dois statements é `ProgrammingError`
            #     (`UndefinedObject`/`UndefinedFunction` se `hashtext` sumir) ou
            #     `DataError` (`InvalidParameterValue` num `lock_timeout`
            #     inválido, sqlstate 22023); nenhuma das duas é
            #     `OperationalError` (medido pelo `__mro__` das três), então já
            #     sobem e viram 500 com traceback.
            # Por isso o filtro: as TRÊS rotineiras abaixo são o desfecho
            # PROJETADO desta função — outro sync segurando a mesma chave —, e um
            # WARNING por sync contendido seria laço quente no log. O resto
            # (`AdminShutdown`, `DiskFull`, `TooManyConnections`,
            # `ConnectionFailure`…) é infra morrendo e deixa de ser mudo.
            # O `exc_info` vai pela mesma razão do `connect`: o `sqlstate` sozinho
            # não discrimina, e a mensagem do libpq é infraestrutura — sem senha,
            # sem URL, sem `DETAIL: Key (…)=(…)` (medido). Aqui ele é a EXCEÇÃO
            # guardada, e não `True`, porque o registro saiu para o `finally`
            # (onde não há mais exceção "corrente"); o `logging` normaliza
            # instância em `(tipo, exc, exc.__traceback__)`, então o traceback é o
            # mesmo — é o que a 1ª metade de
            # `test_lock_nao_rotineiro_deixa_rastro_e_o_rotineiro_fica_mudo` afere
            # ao procurar `AdminShutdown` no traceback formatado.
            if not isinstance(exc, (psycopg.errors.LockNotAvailable,
                                    psycopg.errors.QueryCanceled,
                                    psycopg.errors.DeadlockDetected)):
                nao_rotineiro = exc
            got = False
        yield got
    finally:
        # `try/finally` e não `conn.close(); release()` em sequência: se o `close()`
        # levantar, o `release()` da versão anterior nunca rodava e a vaga do
        # `_lock_slots()` sumia PARA SEMPRE — medido, 8 slots livres viravam 7,
        # permanente. É a própria classe que a issue #429 nomeia ("a vaga não volta
        # nunca"), uma linha abaixo do conserto dela. O irmão `pluggy_item_lock`
        # tinha o padrão idêntico e foi consertado junto.
        # TETO CONHECIDO, e fica: com os DOIS levantando, a exceção do `close()`
        # vira `__context__` da do `release()`. `threading.Semaphore.release()` não
        # levanta (só a `BoundedSemaphore` levanta, por excesso, e esta não é uma),
        # então o caso é inalcançável — e o conserto exigiria engolir a do
        # `release()`, que é trocar um mascaramento impossível por um real.
        try:
            conn.close()
        finally:
            _lock_slots().release()
            # DEPOIS do `release()`, e DENTRO deste `finally` (não depois dele):
            # aqui o WARNING sai mesmo quando o `close()` levanta — e `close()`
            # que levanta é a mesma infra morrendo que este log existe para
            # contar. Ordem: `close()` → `release()` → log.
            # `logger.warning` PODE levantar — a versão anterior deste comentário
            # dizia que o `logging` segurava, e estava errada: `Handler.handle`
            # chama `self.emit(record)` SEM try/except, e o `handleError` só roda
            # dentro do `emit` de quem se dá ao trabalho de chamá-lo (o
            # `_DashboardHandler.emit` de `core/observability.py` não tem
            # try/except próprio). Quem segura de verdade é o `except Exception`
            # amplo de `log_system_event_sync` (`core/system_event_log.py`), que é
            # o que aquele `emit` chama. Se ele sumir, o WARNING sobe DAQUI e
            # mascara a exceção do `close()` (ela vira `__context__`) — e mesmo
            # nesse caso a vaga JÁ VOLTOU, porque o `release()` vem antes
            # (medido: 8 vagas livres → 8). É o que esta ordem garante, e é o que
            # `tests/test_of_items_lock_ordem.py` prende.
            if nao_rotineiro is not None:
                logger.warning(
                    "pluggy_items_lock: lock falhou por causa NÃO rotineira, "
                    "devolvendo 503 (itens=%d)", len(itens),
                    exc_info=nao_rotineiro,
                )
