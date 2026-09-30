"""Conferência pós-deploy do PR #675 (foto diária por posição do Open Finance).

READ-ONLY e ANONIMIZADO: só contagens por dia e por coluna. Nunca imprime
user_id, id de conexão, id/nome de posição, saldo ou valor individual.

Rodar (o dono, na raiz do repositório):
    PROD_DATABASE_URL='postgres://...' .venv/bin/python scripts/conferir_fotos_of.py
Opcional: DIAS=45 (janela das seções por dia; padrão 14).

O que cada seção responde:
  1. a tabela nasceu no deploy?
  2. quantas fotos por dia, e quantas vieram de coleta confirmada
  3. o sync que atualizou o espelho hoje gravou a foto? (divergência = a gravação
     caiu no SAVEPOINT; procure "foto das posições OF não gravada" no log do Railway)
  4. o dia da foto é o dia de São Paulo? (TZ do Railway)
  5. colunas vazias: taxa do banco (esperado: vazia), saldo/aplicado (esperado: cheios)
  6. status das posições no último dia
  7. evolução dia a dia: o aplicado mexe junto com o saldo? (hipótese do aporte;
     só diz algo depois de umas semanas de histórico)

Teste: tests/test_conferir_fotos_of.py.
"""
import os
import sys

import psycopg
from psycopg.rows import dict_row

SP = "America/Sao_Paulo"
T = "open_finance_investment_snapshots"


def tabela(cur, titulo, sql, args=()):
    cur.execute(sql, args)
    linhas = cur.fetchall()
    print(f"\n## {titulo}")
    if not linhas:
        print("  (nenhuma linha)")
    for r in linhas:
        print("  " + " | ".join(f"{k}={v}" for k, v in r.items()))


def conferir(cur, dias):
    """`cur` com linhas em dict. Só lê."""
    cur.execute("select to_regclass(%s) is not null as existe", (T,))
    if not cur.fetchone()["existe"]:
        sys.exit(f"1. a tabela {T} NÃO existe: o deploy do #675 não rodou o init_db.")
    print(f"1. a tabela {T} existe.")

    tabela(cur, f"2. fotos por dia (últimos {dias} dias)", f"""
        select s.observed_on as dia, count(*) as fotos,
               count(distinct s.connection_id) as conexoes,
               count(*) filter (where s.collection_confirmed) as confirmadas,
               count(*) filter (where not s.collection_confirmed) as nao_confirmadas
          from {T} s
         where s.observed_on >= (now() at time zone %s)::date - %s
         group by 1 order by 1""", (SP, dias))

    # Espelho atualizado hoje × foto de hoje, POR POSIÇÃO, agregado por conexão sem
    # expor a conexão. `save_open_finance_investments` grava `updated_at` do espelho
    # e `observed_at` da foto com o MESMO `now`: foto do mesmo sync ⇔ os dois são
    # iguais. Casar só pelo dia deixava a foto de um sync da manhã cobrir a de um
    # sync da tarde que caiu no SAVEPOINT. Foto de hoje de sync anterior tem uma
    # explicação legítima: coleta não confirmada não sobrescreve a confirmada
    # (db/of_snapshots.py). Então: foto anterior CONFIRMADA = ambígua (o espelho
    # não guarda se o sync era confirmado); NÃO confirmada = falha, porque qualquer
    # coleta posterior a sobrescreveria. Comparar totais não serve: foto de posição
    # que já saiu do espelho (resgatada) cobriria a que ficou sem foto.
    tabela(cur, "3. espelho atualizado hoje x foto de hoje (dia de SP)", f"""
        with hoje as (select (now() at time zone %s)::date as d),
             esp as (
               select i.connection_id, count(s.connection_id) as com_foto,
                      count(*) filter (where s.connection_id is null
                                          or (s.observed_at <> i.updated_at
                                              and not s.collection_confirmed)) as falhas,
                      count(*) filter (where s.observed_at <> i.updated_at
                                         and s.collection_confirmed) as anteriores
                 from open_finance_investments i
                 cross join hoje
                 left join {T} s on s.connection_id = i.connection_id
                                and s.provider_investment_id = i.provider_investment_id
                                and s.observed_on = hoje.d
                where (i.updated_at at time zone %s)::date = hoje.d
                group by 1)
        select case when com_foto = 0 then 'sem foto nenhuma'
                    when falhas > 0 then 'foto parcial'
                    when anteriores > 0 then 'ambigua: foto confirmada de sync anterior'
                    else 'ok' end as situacao,
               count(*) as conexoes
          from esp
         group by 1 order by 1""", (SP, SP))

    # observed_on vem de `now()` do app no fuso `_tz()`. Se o Railway rodar com
    # outro fuso efetivo, as fotos gravadas entre 21h e 24h de SP caem no dia errado.
    tabela(cur, "4. dia da foto x dia de SP do instante gravado", f"""
        select case when s.observed_on = (s.observed_at at time zone %s)::date then 'bate'
                    else 'NAO BATE' end as dia, count(*) as fotos
          from {T} s group by 1 order by 1""", (SP,))

    colunas = ["position_at", "status", "balance", "amount", "amount_original", "quantity",
               "contract_rate", "contract_rate_type", "last_month_rate",
               "last_twelve_months_rate", "annual_rate"]
    tabela(cur, "5. colunas vazias no último dia com foto", f"""
        select count(*) as fotos,
               {', '.join(f"count(*) filter (where s.{c} is null) as {c}_vazio" for c in colunas)}
          from {T} s
         where s.observed_on = (select max(observed_on) from {T})""")

    tabela(cur, "6. status no último dia com foto", f"""
        select coalesce(s.status, '(vazio)') as status, count(*) as fotos
          from {T} s
         where s.observed_on = (select max(observed_on) from {T})
         group by 1 order by 2 desc""")

    # Transições entre dias CONSECUTIVOS da mesma posição (lacuna não conta).
    # Se o aplicado subir exatamente no valor do aporte, "saldo mudou e aplicado
    # parado" é rendimento puro, e "aplicado mudou" é aporte/resgate.
    # Janela: lê a partir de hoje - dias - 1 (o dia anterior do primeiro par). Isso
    # já filtra os PARES: o primeiro dia lido de cada posição fica sem `lag` e sai
    # no `dia_ant = observed_on - 1`, então todo par contado termina em hoje - dias ou depois.
    tabela(cur, f"7. evolução dia a dia (pares de dias consecutivos por posição, últimos {dias} dias)", f"""
        with p as (
          select s.observed_on, s.balance, s.amount, s.last_month_rate,
                 lag(s.observed_on) over w as dia_ant, lag(s.balance) over w as saldo_ant,
                 lag(s.amount) over w as aplic_ant, lag(s.last_month_rate) over w as taxa_ant
            from {T} s
           where s.observed_on >= (now() at time zone %s)::date - %s - 1
          window w as (partition by s.connection_id, s.provider_investment_id order by s.observed_on))
        select count(*) as pares,
               count(*) filter (where balance is not distinct from saldo_ant
                                  and amount is not distinct from aplic_ant) as nada_mudou,
               count(*) filter (where balance is distinct from saldo_ant
                                  and amount is not distinct from aplic_ant) as so_saldo_mudou,
               count(*) filter (where amount is distinct from aplic_ant
                                  and balance - saldo_ant = amount - aplic_ant) as aplicado_e_saldo_mesmo_delta,
               count(*) filter (where amount is distinct from aplic_ant
                                  and balance - saldo_ant is distinct from amount - aplic_ant) as aplicado_mudou_delta_diferente,
               count(*) filter (where last_month_rate is distinct from taxa_ant) as taxa_banco_mudou
          from p
         where dia_ant = observed_on - 1""", (SP, dias))


def main():
    url = os.environ.get("PROD_DATABASE_URL")
    if not url:
        sys.exit("defina PROD_DATABASE_URL (de propósito não lê o .env)")
    try:
        dias = int(os.environ.get("DIAS", "14"))
    except ValueError:
        sys.exit("DIAS tem de ser um número inteiro (padrão 14)")
    with psycopg.connect(url, row_factory=dict_row,
                         options="-c default_transaction_read_only=on") as conn, conn.cursor() as cur:
        conferir(cur, dias)
    print("\nfim — nada foi escrito (sessão read-only).")


if __name__ == "__main__":
    main()
