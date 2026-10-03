"""Diagnóstico de items Pluggy para o OPERADOR (`scripts/of_itens_operador.py`).

Leitura ENTRE usuários, e por isso nenhuma rota chama nada daqui: é ferramenta
de terminal, de quem já tem o banco inteiro na mão. Mesmo assim nada aqui devolve
`user_id` — o operador decide sobre o ITEM. Quem precisa do dono pega no `GET
/items/{id}` da Pluggy (`clientUserId`, só no detalhe de `--item`) e contata pelo
`admin.py inspect <uid> --reason`. NUNCA se lê `details.user_id` de log: ele
existe em alguns eventos por engano (issue #541) e não é fonte de nada.

Sem escrita: quem escreve é o script, com as guardas dele.

O RASTRO DE LOG É APAGÁVEL: `DELETE /admin/api/events` e `admin.py purge` limpam
`system_event_logs`, e log com `user_id` na COLUNA some na cascata da exclusão de
conta. Nesses casos a marca "remoção remota falhou" desaparece e, se o registry
também não tiver o item, ele sai da ferramenta (DESCONHECIDO) — sobra o painel
da Pluggy.
"""
from __future__ import annotations

from core.audit import AuditEvent
from db.connection import get_conn
from db.open_finance_state import ITEMS_SEM_CONEXAO, RESOLVIDO_PELO_OPERADOR

REMOVIDO = "REMOVIDO"
INTERROMPIDO = "INTERROMPIDO"
LEGADO_AMBIGUO = "LEGADO_AMBIGUO"
NUNCA_ATRIBUIDO = "NUNCA_ATRIBUIDO"
CONECTADO = "CONECTADO"
CONECTADO_SEM_RASTRO = "CONECTADO_SEM_RASTRO"
DESCONHECIDO = "DESCONHECIDO"
# Só estes aceitam `--apagar`: CONECTADO* tem conexão local e DESCONHECIDO não é
# item que vimos — os três são recusados.
ESTADOS_SEM_CONEXAO = (REMOVIDO, INTERROMPIDO, LEGADO_AMBIGUO, NUNCA_ATRIBUIDO)

# Os eventos em que o DELETE remoto de um item falhou. Formato de LOG virou
# contrato de LEITURA (um teste por formato em tests/test_of_diagnostico_estados.py):
#   • `pluggy_item_delete_failed`, `details.item_id` — 1º passe, por item
#     (`delete_pluggy_items_best_effort`, frontend/routes/open_finance.py);
#   • `pluggy_item_delete_failed`, `details.items` — 2º passe do disconnect;
#   • `pluggy_disconnect_auth_failed`, `details.items` — sem apiKey;
#   • `account_reset_pluggy_cleanup_failed`, `details.items` — 2º passe do reset
#     (frontend/routes/settings.py; o do 1º passe não traz os items);
#   • `account_deletion_pluggy_cleanup_failed`, `details.items`, `source='db.privacy'`
#     — exclusão de conta. Ali a cascata já levou o registry: o log é o ÚNICO rastro.
# Filtra por `event_type` e não por `source`: o nome do evento é o que identifica.
EVENTOS_FALHA_DELETE = (
    "pluggy_item_delete_failed",
    "pluggy_disconnect_auth_failed",
    "account_reset_pluggy_cleanup_failed",
    "account_deletion_pluggy_cleanup_failed",
)


def classifica_item(item_id: str, *, provider: str = "pluggy") -> str:
    """O estado de UM item. A regra de precedência mora aqui (CLAUDE.md §0.7):
    `mark_items_removed` e `db/schema.py` apontam para cá.

    Conexão local viva decide antes de tudo: `CONECTADO`, ou `CONECTADO_SEM_RASTRO`
    quando nenhuma linha do registry daquele item tem dono (o `register_item` do
    POST falhou, ou a conexão é anterior ao registry). Só informativo.

    Sem conexão, o registry é log de APPEND: depois de "conectou → removeu →
    reconectou" o item tem, em ordem de `id`, `pluggy_item`, `removed`,
    `pluggy_item`. Ordene as linhas com `user_id is not null` por `id` e olhe a
    ÚLTIMA:

      • `origin='removed'`                       → REMOVIDO pelo usuário;
      • outra origem COM `removal_tracked`       → INTERROMPIDO (adoção ou POST que
        não completou: a era já marca remoção, então a ausência de `removed` é
        informação);
      • outra origem SEM `removal_tracked`       → LEGADO_AMBIGUO (rastro anterior
        à marca: não dá para saber);
      • nenhuma linha com dono, e auditoria `OPEN_FINANCE_CONNECTED` com o mesmo
        `item_id`                                → LEGADO_AMBIGUO (D3): o item TEVE
        dono e o rastro se perdeu — com ou sem linha sem dono no registry (quem
        conectou antes do registry não deixou linha nenhuma);
      • nenhuma linha com dono, sem auditoria, e pelo menos uma linha sem dono OU
        citação em log de DELETE falho           → NUNCA_ATRIBUIDO (só log é a
        conta excluída: a cascata levou registry e auditoria);
      • nada disso                               → DESCONHECIDO (recusado).

    Por `id` e não por `created_at`: `now()` é o tempo de INÍCIO da transação,
    empata entre duas escritas da mesma transação e pode inverter entre sessões
    concorrentes; `id` é `bigserial`, alocado no INSERT. Linha sem dono
    (`webhook`, `operator_delete`) não entra: ela não diz de quem o item era.

    LIMITE CONHECIDO (declarado, não consertado): ordem de ALOCAÇÃO não é ordem
    de COMMIT. Disconnect × reconexão do mesmo item é serializado pelo
    `pluggy_items_lock`, mas o `register_item` do `POST /pluggy-item` roda FORA do
    lock (depois de `_grava_reconexao`), então um `DELETE /open-finance/{uid}`
    que caia entre o commit da conexão e esse insert deixa a última linha como
    `pluggy_item` com o banco REMOVIDO — sai INTERROMPIDO. Janela de ms e exige o
    clique do usuário; o operador age item a item, e apagar ali não causa dano.

    LIMITE CONHECIDO: existência é por match EXATO, e só a conexão viva compara
    com `lower()`. Linha do registry em outra caixa (o webhook grava o que vier)
    classifica aquele id à parte; teórico, porque a Pluggy emite minúsculo.
    """
    item = str(item_id or "")
    with get_conn() as conn, conn.cursor() as cur:
        # `lower()` só AQUI, e é o lado seguro: a guarda de conexão viva não pode
        # depender da caixa que o operador digitou (UUID maiúsculo passava).
        cur.execute(
            "select 1 from open_finance_connections"
            " where provider = %s and lower(provider_item_id) = lower(%s) limit 1",
            (provider, item),
        )
        conectado = cur.fetchone() is not None
        # Existência e estado por match EXATO: é essa string que vai no DELETE.
        cur.execute(
            "select user_id is not null as com_dono, origin, removal_tracked"
            " from open_finance_item_registry"
            " where provider = %s and provider_item_id = %s"
            " order by (user_id is not null) desc, id desc limit 1",
            (provider, item),
        )
        linha = cur.fetchone()
        ultima = linha if linha and linha["com_dono"] else None
        if conectado:
            return CONECTADO if ultima else CONECTADO_SEM_RASTRO
        teve_auditoria = False
        if ultima is None:
            # ponytail: varre `audit_events` sem índice em `details->>'item_id'`.
            # Ferramenta de operador, um item por vez; índice se ficar lento.
            cur.execute(
                "select 1 from audit_events where event = %s "
                "and details->>'item_id' = %s limit 1",
                (AuditEvent.OPEN_FINANCE_CONNECTED, item),
            )
            teve_auditoria = cur.fetchone() is not None
    if ultima is not None:
        if ultima["origin"] == "removed":
            return REMOVIDO
        return INTERROMPIDO if ultima["removal_tracked"] else LEGADO_AMBIGUO
    if teve_auditoria:
        return LEGADO_AMBIGUO
    # Nem registry, nem auditoria, nem log de DELETE falho: o id não é de nada
    # que vimos (digitação, outra caixa, item de outro ambiente).
    # ponytail: varre os logs para UM id; só roda quando nada mais o conhece.
    if linha is None and item not in itens_com_remocao_remota_falha():
        return DESCONHECIDO
    return NUNCA_ATRIBUIDO


def apagados_pelo_operador(*, provider: str = "pluggy") -> int:
    """Quantos items estão resolvidos pelo operador (`RESOLVIDO_PELO_OPERADOR`):
    saem da lista e do card, e o dry-run só os conta."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(distinct r.provider_item_id) as n"
            f" from open_finance_item_registry r where r.provider = %s"
            f" and {RESOLVIDO_PELO_OPERADOR}", (provider,))
        return int(cur.fetchone()["n"])


def resolvido_pelo_operador(item_id: str, *, provider: str = "pluggy") -> bool:
    """Este item está resolvido pelo operador (`RESOLVIDO_PELO_OPERADOR`)?"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select 1 from open_finance_item_registry r where r.provider = %s"
            f" and r.provider_item_id = %s and {RESOLVIDO_PELO_OPERADOR} limit 1",
            (provider, str(item_id or "")))
        return cur.fetchone() is not None


def listar_sem_conexao(*, provider: str = "pluggy") -> dict[str, str]:
    """Todo item sem conexão local → estado. Duas fontes:

      • o registry, pelo predicado do painel (`ITEMS_SEM_CONEXAO`, uma fonte só);
      • a auditoria `OPEN_FINANCE_CONNECTED` com `details.item_id` de item SEM
        nenhuma linha no registry (conectou antes do registry existir): sai
        LEGADO_AMBIGUO. O card do painel NÃO conta estes — ele lê só o registry.
        Da auditoria sai só o `item_id`, nunca o `user_id`.

    ponytail: uma `classifica_item` por item. Punhado de items; se virar milhares,
    é uma query com `distinct on`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select distinct r.provider_item_id as item {ITEMS_SEM_CONEXAO}"
            " and r.provider = %s order by 1", (provider,))
        itens = [r["item"] for r in (cur.fetchall() or [])]
        # Conexão com `lower()`, como em `classifica_item`; registry por match exato.
        cur.execute(
            """
            select distinct a.details->>'item_id' as item from audit_events a
             where a.event = %s and coalesce(a.details->>'item_id', '') <> ''
               and not exists (select 1 from open_finance_connections c
                                where c.provider = %s
                                  and lower(c.provider_item_id) = lower(a.details->>'item_id'))
               and not exists (select 1 from open_finance_item_registry r
                                where r.provider = %s
                                  and r.provider_item_id = a.details->>'item_id')
             order by 1
            """,
            (AuditEvent.OPEN_FINANCE_CONNECTED, provider, provider),
        )
        itens += [r["item"] for r in (cur.fetchall() or [])]
    return {i: classifica_item(i, provider=provider) for i in itens}


def conexoes_sem_rastro(*, provider: str = "pluggy") -> list[str]:
    """Items com conexão local e NENHUMA linha com dono no registry."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct c.provider_item_id as item
              from open_finance_connections c
             where c.provider = %s and c.provider_item_id is not null
               and not exists (select 1 from open_finance_item_registry r
                                where r.provider = c.provider
                                  and r.provider_item_id = c.provider_item_id
                                  and r.user_id is not null)
             order by 1
            """,
            (provider,),
        )
        return [r["item"] for r in (cur.fetchall() or [])]


def itens_com_remocao_remota_falha() -> list[str]:
    """Items citados num dos formatos de `EVENTOS_FALHA_DELETE`, sem conexão
    local e sem `operator_delete` gravado DEPOIS da última falha. O item pode nem
    estar no registry (exclusão de conta: a cascata o levou).

    `created_at` e não `id` aqui, e é o certo: são tabelas diferentes, e o
    `operator_delete` que resolve é gravado minutos ou dias depois do log.
    """
    with get_conn() as conn, conn.cursor() as cur:
        # `system_event_logs` nasce preguiçosa (core/admin_dashboard.py): sem ela,
        # não há falha registrada — e a query estouraria.
        cur.execute("select to_regclass('public.system_event_logs') as t")
        if not (cur.fetchone() or {}).get("t"):
            return []
        cur.execute(
            """
            with citados as (
                select l.details->>'item_id' as item, l.created_at as em
                  from system_event_logs l
                 where l.event_type = any(%(ev)s)
                   and jsonb_typeof(l.details->'item_id') = 'string'
                union all
                select i.item, l.created_at
                  from system_event_logs l,
                       jsonb_array_elements_text(l.details->'items') as i(item)
                 where l.event_type = any(%(ev)s)
                   and jsonb_typeof(l.details->'items') = 'array'
            ), ultima as (
                select item, max(em) as em from citados
                 where coalesce(item, '') <> '' group by item
            )
            select u.item from ultima u
             where not exists (select 1 from open_finance_connections c
                                where c.provider = 'pluggy'
                                  and c.provider_item_id = u.item)
               and not exists (select 1 from open_finance_item_registry r
                                where r.provider = 'pluggy'
                                  and r.provider_item_id = u.item
                                  and r.origin = 'operator_delete'
                                  and r.created_at > u.em)
             order by 1
            """,
            {"ev": list(EVENTOS_FALHA_DELETE)},
        )
        return [r["item"] for r in (cur.fetchall() or [])]
