"""
db/lancamentos.py — a lista de lançamentos do `/painel` (`GET /api/v2/lancamentos`). Só lê.

O mês é a regra de `db/resumo_mes.TOTAIS_SQL`, pelas MESMAS pernas (`MES_LANCAMENTOS_SQL`,
`MES_CARTAO_SQL`): lançamentos por `criado_em` (forma legada canonizada), compras no cartão
sem estorno pela fatura (`period_end`), mostrando o dia da COMPRA (P6). A diferença é de
propósito: o interno entra, com `interno=true`, e fica fora de qualquer soma (P8). Assim a
soma dos não internos de um mês é o Entrou/Saiu de `resumo-do-mes`
(`tests/test_api_v2_lancamentos.py`). Com busca (`q`), o mês dá lugar à janela inteira
do plano (P7).

Ordem e página: KEYSET em (dia desc, instante desc, tabela desc, id desc); o cursor leva
só essas chaves e a query refiltra pelo usuário, então cursor alheio não revela nada.

`pode` é uma regra só (`PODE_SQL`, sobre `_ESTADO_SQL`; `PODE_CARTAO_SQL` no cartão); a
escrita da v2 (`api/v2/lancamentos.py`) relê a linha por `pode_da_linha`, sob lock,
e decide pelo mesmo predicado. Linha sem a marca `launches.origem` é antiga e só leitura
(P2). Quem grava a marca: o escritor da carteira (`db.accounts.add_launch_and_update_balance`,
por padrão, em todo canal) e o saque/depósito automático da Q41 (`open_finance_cash._credita`).

Limites declarados: o mês corta `criado_em` pela data ingênua, como `TOTAIS_SQL`, e o dia
mostrado é o de `launch_day` — numa linha antiga do Open Finance (meia-noite UTC) o dia
pode cair fora do mês da lista. Tipos que não são despesa/receita (caixinha, aporte,
`criar_caixinha`) ficam fora, como na soma.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime

from utils_date import _tz

from .analytics import clausula_busca, termos_busca
from .connection import LAUNCH_HAS_TIME_SQL, TIPO_CANON_SQL, cat_key_sql, cat_norm_sql
from .open_finance import ACTIONABLE_PENDING_SQL, actionable_pending_params
from .open_finance_cash import VINCULADO_SQL
from .reconciliation import FUSED_STATUSES
from .resumo_mes import MES_CARTAO_SQL, MES_LANCAMENTOS_SQL

ORIGENS = ("carteira", "banco", "cartao", "registro_antigo")
PODE = ("categoria", "descricao", "data", "valor", "apagar")
MOTIVOS_ITEM = ("conciliacao_pendente", "transacao_pendente", "outra_moeda", "moeda_presumida")
_FUSED = "(" + ",".join(f"'{s}'" for s in FUSED_STATUSES) + ")"
_MAX_ID = 2**63 - 1

# Lançamentos em par pendente com o banco, dos dois lados (o manual e a sombra): o mesmo
# conjunto acionável que o bloco de contas conta (`ACTIONABLE_PENDING_SQL`). Params:
# `actionable_pending_params`.
_CTE_PENDENTES = f"""
    with pend as ({ACTIONABLE_PENDING_SQL}),
    pendentes as (select id from pend
                  union select t.imported_launch_id from open_finance_transactions t
                   where t.id in (select of_tx_id from pend) and t.imported_launch_id is not null)
"""

# Linha que não é do banco e está junta com uma transação dele (P3: o banco é dono da data e do
# valor). Sobre `launches` SEM alias; vale para manual, ofx e recorrente, sem olhar a marca. A
# guarda de data de `db.accounts.update_launch_fields` (todo canal) lê daqui.
FUNDIDO_SQL = f"""coalesce(source, 'manual') <> 'open_finance' and exists (
        select 1 from open_finance_transactions o
          join open_finance_accounts oa on oa.id = o.account_id
          join open_finance_connections oc on oc.id = oa.connection_id and oc.user_id = launches.user_id
         where o.imported_launch_id = launches.id and o.reconciliation_status in {_FUSED})"""

# Estado de cada linha, sobre `launches` SEM alias (dentro do FROM dela; a CTE `pendentes`
# precisa estar no ar). "Carteira" = manual com `delta_conta` ≠ 0; manual com delta 0 não é.
_ESTADO_SQL = f"""
      coalesce(source, 'manual') = 'manual'
        and jsonb_typeof(efeitos -> 'delta_conta') = 'number'
        and (efeitos ->> 'delta_conta')::numeric <> 0 as carteira,
      origem is not null as marcada,
      {VINCULADO_SQL} as especie,
      (efeitos -> 'bill_id') is not null or exists (
        select 1 from bill_instances bi where bi.launch_id = launches.id
           and bi.user_id = launches.user_id) as paga_conta,
      {FUNDIDO_SQL} as fundido,
      coalesce(id in (select id from pendentes), false) as pendente"""

# Tabela do dono (P2, P3, P5). A ordem dos `when` é a precedência: o vínculo do dinheiro
# em espécie e o pagamento de conta vencem a fusão e o par pendente — menos na data, que a
# fusão tira também do pagamento de conta (P3: o banco é dono da data em todo canal).
PODE_SQL = """case
      when source = 'open_finance' then array['categoria','descricao']
      when not (carteira and marcada) then '{}'::text[]
      when especie then array['descricao','apagar']
      when paga_conta and fundido then array['categoria']
      when paga_conta then array['categoria','data']
      when fundido or pendente then array['categoria','descricao','apagar']
      else array['categoria','descricao','data','valor','apagar'] end"""
# `paga_conta` sem apagar (dono, 2026-10-03): apagar o pagamento de conta devolve o dinheiro e
# a conta segue paga. O de fatura do cartão manual (`bill_id`) cai no mesmo ramo e sai junto,
# por conservadorismo. O /app e o WhatsApp seguem apagando os dois.

# O `pode` da compra no cartão (alias `ct`): só a do Open Finance edita, e só P5.
PODE_CARTAO_SQL = """case when ct.source = 'open_finance' then array['categoria','descricao']
      else '{}'::text[] end"""


class NaoEditavel(ValueError):
    """O campo pedido está fora do `pode` da linha (a escrita da v2 responde 409)."""


def _motivos(*pares: tuple[str, str]) -> str:
    return "array_remove(array[" + ", ".join(
        f"case when {cond} then '{m}' end" for m, cond in pares) + "]::text[], null)"


def _moeda(a: str) -> list[tuple[str, str]]:
    """Os motivos de moeda pela conta do banco `a` (o import grava tudo como BRL)."""
    return [("outra_moeda", f"{a}.id is not null and upper(coalesce({a}.currency, 'BRL')) <> 'BRL'"),
            ("moeda_presumida", f"{a}.id is not null and nullif({a}.raw ->> 'currencyCode', '') is null")]


def _chave(col: str) -> str:
    return f"case when nullif({col}, '') is null then null else {cat_norm_sql(col)} end"


# Params: tz, tz, `MES_LANCAMENTOS_SQL` (uid, início, fim), uid (a transação do banco).
_PERNA_LANCAMENTOS = f"""
    select 'l' as tabela, l.id, l.dia, l.criado_em as instante,
           case when l.has_time then l.hhmm end as hora,
           case when l.tipo_canon = 'receita' then 'entrada' else 'saida' end as tipo,
           l.is_internal_movement as interno, l.valor,
           coalesce(upper(a.currency), upper(l.currency), 'BRL') as moeda,
           l.alvo as descricao, l.nota as mensagem, {_chave('l.categoria')} as categoria,
           case when l.source = 'open_finance' then 'banco'
                when l.carteira and l.marcada then 'carteira' else 'registro_antigo' end as origem,
           l.fundido, conn.institution_name as instituicao,
           (select a2.id from open_finance_accounts a2
              join open_finance_connections c2 on c2.id = a2.connection_id and c2.user_id = l.user_id
             where a2.provider_account_id = a.provider_account_id and upper(a2.type) = 'BANK'
             order by c2.id desc limit 1) as conta_id,
           null::bigint as cartao_id, null::int as parcela_n, null::int as parcela_total,
           null::text as fatura, l.pode,
           {_motivos(("conciliacao_pendente", "l.pendente"),
                     ("transacao_pendente", "t.status_banco = 'PENDING'"), *_moeda("a"))} as motivos
      from (select launches.*, {TIPO_CANON_SQL} as tipo_canon, {LAUNCH_HAS_TIME_SQL} as has_time,
                   case when not ({LAUNCH_HAS_TIME_SQL}) and posted_at is not null then posted_at
                        else (criado_em at time zone %s)::date end as dia,
                   to_char(criado_em at time zone %s, 'HH24:MI') as hhmm,{_ESTADO_SQL}
              from launches where {MES_LANCAMENTOS_SQL}) l0
      cross join lateral (select l0.*, {PODE_SQL} as pode) l
      left join lateral (
        select t.account_id, t.raw ->> 'status' as status_banco
          from open_finance_transactions t
          join open_finance_accounts ta on ta.id = t.account_id
          join open_finance_connections tc on tc.id = ta.connection_id and tc.user_id = %s
         where t.imported_launch_id = l.id order by t.id desc limit 1) t on true
      left join open_finance_accounts a on a.id = t.account_id
      left join open_finance_connections conn on conn.id = a.connection_id
     where true"""

# Params: `MES_CARTAO_SQL` (uid, uid, início, fim), uid (conta do cartão), uid (transação do
# cartão). `cartao_id` e `instituicao` só pelo join guardado `cc`.
_PERNA_CARTAO = f"""
    select 'c' as tabela, ct.id, ct.purchased_at as dia,
           coalesce(ct.created_at, ct.purchased_at::timestamptz) as instante,
           null::text as hora, 'saida' as tipo, false as interno, ct.valor,
           coalesce(upper(oa.currency), 'BRL') as moeda,
           coalesce(nullif(ct.nota, ''), o.description) as descricao, null::text as mensagem,
           {_chave('ct.categoria')} as categoria,
           case when ct.source = 'open_finance' then 'cartao' else 'registro_antigo' end as origem,
           false as fundido, oc.institution_name as instituicao, null::bigint as conta_id,
           cc.id as cartao_id, ct.installment_no as parcela_n,
           ct.installments_total as parcela_total, to_char(b.period_end, 'YYYY-MM') as fatura,
           {PODE_CARTAO_SQL} as pode,
           {_motivos(("transacao_pendente", "o.status_banco = 'PENDING'"), *_moeda("oa"))} as motivos
      from {MES_CARTAO_SQL}
      left join credit_cards cc on cc.id = ct.card_id and cc.user_id = ct.user_id
      left join (open_finance_accounts oa
                 join open_finance_connections oc on oc.id = oa.connection_id and oc.user_id = %s)
             on oa.id = cc.open_finance_account_id
      left join lateral (
        select o.description, o.raw ->> 'status' as status_banco
          from open_finance_transactions o
          join open_finance_accounts xa on xa.id = o.account_id
          join open_finance_connections xc on xc.id = xa.connection_id and xc.user_id = %s
         where o.imported_credit_tx_id = ct.id order by o.id desc limit 1) o on true
     where true"""

# `conta` casa pela identidade do provedor (`provider_account_id`) com o dono nas duas
# pontas, como `_RECORTE_TX_FROM_SQL`: pega a transação presa a uma conexão antiga da mesma
# conta. Sem o recorte do saldo (BRL, conexão viva): `/contas` lista a pausada e a de
# outra moeda, e o filtro tem de achar as dela. Params: uid, uid, conta.
_DA_CONTA_SQL = """ and l.id in (
        select t.imported_launch_id from open_finance_accounts ra
          join open_finance_connections rc on rc.id = ra.connection_id and rc.user_id = %s
          join open_finance_accounts ta on ta.provider_account_id = ra.provider_account_id
          join open_finance_connections tc on tc.id = ta.connection_id and tc.user_id = %s
          join open_finance_transactions t on t.account_id = ta.id
         where ra.id = %s and upper(ra.type) = 'BANK')"""


def pode_da_linha(cur, user_id: int, launch_id: int) -> list | None:
    """O `pode` de UMA linha de `launches` do usuário (None = não achou), pela regra da lista.
    Sem `for update` (a CTE não deixa): quem escreve trava antes (`_lock_user` e a linha) e
    chama na MESMA transação."""
    cur.execute(f"""{_CTE_PENDENTES}
        select {PODE_SQL} as pode from (select launches.*, {_ESTADO_SQL}
          from launches where user_id = %s and id = %s) l0""",
                (*actionable_pending_params(cur, user_id), user_id, launch_id))
    row = cur.fetchone()
    return row["pode"] if row else None


class CursorInvalido(ValueError):
    pass


def cursor_de(item: dict) -> str:
    chave = [item["dia"].isoformat(), item["instante"].isoformat(), item["tabela"], item["id"]]
    return base64.urlsafe_b64encode(json.dumps(chave).encode()).decode()


def ler_cursor(texto: str) -> tuple:
    """Fronteira de confiança: o texto volta do cliente. Qualquer coisa fora da forma
    exata (4 chaves, datas ISO, tabela conhecida, id no bigint) é `CursorInvalido`."""
    try:
        dia, instante, tabela, ident = json.loads(base64.urlsafe_b64decode(texto.encode()))
        chave = (date.fromisoformat(dia), datetime.fromisoformat(instante), tabela, ident)
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise CursorInvalido("Cursor inválido.") from None
    if (chave[1].tzinfo is None or tabela not in ("l", "c") or type(ident) is not int
            or not 0 < ident <= _MAX_ID):
        raise CursorInvalido("Cursor inválido.")
    return chave


def listar(cur, user_id: int, inicio: date, fim: date, *, origem=None, conta=None, cartao=None,
           categoria=None, tipo=None, q=None, apos=None, limite=50) -> tuple[list[dict], bool]:
    """Os itens em `[inicio, fim)` pela regra do mês, mais novos primeiro, no máximo
    `limite`, e se há mais. `apos` = `ler_cursor(...)`. `tipo` = entrada|saida."""
    tz = _tz().key
    params: list = [*actionable_pending_params(cur, user_id)]
    perna_l = _PERNA_LANCAMENTOS + (_DA_CONTA_SQL if conta is not None else "")
    perna_l += " and false" if cartao is not None else ""
    params += [tz, tz, user_id, inicio, fim, user_id]
    params += [user_id, user_id, conta] if conta is not None else []
    perna_c = _PERNA_CARTAO + (" and cc.id = %s" if cartao is not None else "")
    perna_c += " and false" if conta is not None else ""
    params += [user_id, user_id, inicio, fim, user_id, user_id]
    params += [cartao] if cartao is not None else []

    filtros = []
    if apos is not None:
        filtros.append("(x.dia, x.instante, x.tabela, x.id) < (%s, %s, %s, %s)")
        params += apos
    for sql, valor in (("x.origem = %s", origem), ("x.tipo = %s", tipo),
                       (f"{cat_key_sql('x.categoria')} = {cat_key_sql('%s')}", categoria)):
        if valor is not None:
            filtros.append(sql)
            params.append(valor)
    # ponytail: a busca filtra a união já montada, então o estado de toda linha da janela é
    # calculado antes (250 ms com 6000 lançamentos, plano §8); empurrar o filtro de texto para
    # dentro das pernas se pesar.
    busca, busca_params = clausula_busca(termos_busca(q), ("x.descricao", "x.mensagem", "x.categoria"))
    if busca:
        filtros.append(busca)
        params += busca_params
    cur.execute(f"""{_CTE_PENDENTES}
        select * from (({perna_l}) union all ({perna_c})) x
         {"where " + " and ".join(filtros) if filtros else ""}
         order by x.dia desc, x.instante desc, x.tabela desc, x.id desc
         limit %s""", (*params, limite + 1))
    linhas = [dict(r) for r in cur.fetchall()]
    return linhas[:limite], len(linhas) > limite


def _item(r: dict) -> dict:
    parcela = ({"n": r["parcela_n"], "total": r["parcela_total"]}
               if r["parcela_n"] is not None and r["parcela_total"] is not None else None)
    return {"id": f"{r['tabela']}{r['id']}", "data": r["dia"].isoformat(), "hora": r["hora"],
            "tipo": r["tipo"], "interno": r["interno"], "valor": r["valor"], "moeda": r["moeda"],
            "descricao": r["descricao"], "mensagem": r["mensagem"], "categoria": r["categoria"],
            "origem": r["origem"], "fundido": r["fundido"], "instituicao": r["instituicao"],
            "conta_id": r["conta_id"], "cartao_id": r["cartao_id"], "parcela": parcela,
            "fatura": r["fatura"], "pode": r["pode"], "motivos": r["motivos"]}


def pagina(user_id: int, ano: int, mes: int, agora: datetime, *, cursor: str | None = None,
           limite: int = 50, q: str | None = None, **filtros) -> dict:
    """O `GET /api/v2/lancamentos`. O mês pedido, cortado pela janela do plano como
    `resumo_do_mes`; com busca, a janela inteira do plano. `motivos` da lista: os do bloco
    de contas (situação de hoje) e `inicio_do_historico` quando a janela cortou o pedido.
    `cursor` vem cru do cliente: `CursorInvalido` sobe para quem chama."""
    from core.services.plan_service import history_earliest_date

    from .connection import get_conn
    from .contas_hoje import listar as contas
    from .resumo_mes import MOTIVOS, mes_de

    apos = ler_cursor(cursor) if cursor else None
    corte = history_earliest_date(user_id, agora)
    inicio, fim = (date.min, date.max) if termos_busca(q) else mes_de(date(ano, mes, 1))
    cortou = bool(corte and corte > inicio)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        linhas, mais = listar(cur, user_id, max(inicio, corte) if corte else inicio, fim,
                              q=q, apos=apos, limite=limite, **filtros)
        vistos = set(contas(cur, user_id)["motivos"])
        conn.rollback()
    if cortou:
        vistos.add("inicio_do_historico")
    return {"mes": f"{ano:04d}-{mes:02d}", "itens": [_item(r) for r in linhas],
            "proximo": cursor_de(linhas[-1]) if mais else None,
            "motivos": [m for m in MOTIVOS if m in vistos]}
