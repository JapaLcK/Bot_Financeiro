"""Remove `affiliate_commissions.refund_alert` e `refund_alert_sent_at`, órfãs do #1418 (#1507).

ATENÇÃO: `--aplicar` APAGA AS DUAS COLUNAS, e isso não se desfaz. Sem ele é simulação.

O #1418 criou as colunas; o #1508 tirou o código e deixou as colunas em quem as tinha (produção e
staging). O `init_db` não as cria, então o CI nunca as vê. O `refund_alert_sent_at` (o admin recebeu
o aviso?) só existe ali, e a Q2 abaixo é a consulta que o drop destrói. Por isso, nesta ordem:

1. Este script sem flag em produção. Na thread `afiliado_reembolso.py:104` do #1508 (repositório público),
   cole só a última linha, `Resumo para a thread:` (contagens de Q1-Q4, conclusão da Q2, modo e resultado,
   sem id, valor nem data). Guarde a saída inteira em local restrito, fora do repositório. Faça este
   passo antes de ~08/02/2027: a Q3 lê `system_event_logs` na janela de 10 a 11/11/2026, e a poda (piso
   de 90 dias, `RETENCAO_MINIMA_DIAS` em `core/services/log_retention.py`) pode esvaziá-la depois disso.
   Q3 vazia nessa altura não prova nada.
2. Q1 com caso: `scripts/backfill_afiliado_pagamentos.py`, e este script de novo, até a Q1 zerar.
3. Este script com `--aplicar`. Staging antes, como ensaio.

O que ele faz, lendo numa transação `read only`:
- Imprime Q1, Q3 e Q4 (informativas, não dependem das colunas) e, se as colunas existem, a Q2. As
  quatro são as do plano pós-#1508, sem mudança. No terminal saem ids, estados, valores e datas (sem
  fatura, cliente nem indicado); o resumo final, só contagens. Sem a tabela `system_event_logs` (nasce
  no startup do app, não no `init_db`), a Q3 imprime "tabela system_event_logs ausente" e o script segue.
- Sem as colunas: "nada a remover", sai 0. Rodar de novo é seguro.
- Recusa (sai 3), com ou sem `--aplicar`, se uma comissão não estornada da Q2 tem alerta e nenhuma
  pendência (o buraco que o passo 2 repara; estornada não prende, porque o backfill ignora `reversed`),
  ou se uma função do banco, em qualquer schema, cita `refund_alert` no corpo (sem diferenciar
  maiúscula): trigger plpgsql não é objeto dependente, o `alter` passaria e todo `update` da tabela
  quebraria depois.
- Com `--aplicar`, numa transação nova: `lock_timeout` de 2 s e um `alter table` só (uma trava, sem
  `cascade`). Tabela ocupada: sai 2, nada removido, rode de novo.
- Qualquer outro erro é traceback e sai 1, sem resumo: só uma das duas colunas existe, view ou objeto
  dependente criado à mão, papel sem permissão. Pare e pergunte. Argumento inválido (`--aplica`, sem
  abreviação) sai 2 pelo argparse, com o uso, antes de tocar no banco.
- Dois `--aplicar` ao mesmo tempo: o segundo pode sair 0 ("nada a remover") ou 2 (tabela ocupada). Nos
  dois casos nada se perde.

Limites conhecidos:
- Comissão com alerta cuja fatura a Stripe não mostra como devolvida prende a guarda para sempre: com 0
  devolvido o backfill passa por ela em silêncio (`scripts/backfill_afiliado_pagamentos.py:122`), sem
  listar nem em `sem_confirmacao`; disputa aberta ou `payment_record` ficam em `sem_confirmacao`. Em
  nenhum dos casos nasce pendência. A saída é o dono conferir a fatura na Stripe e resolver a comissão à
  mão: a guarda libera quando ela tiver pendência ou estiver `reversed`.
- Um `statement_timeout` menor que 2 s no papel do banco derruba o `alter` com `QueryCanceled` (sai 1)
  antes do `lock_timeout`. Nada é removido; rode com um papel sem esse teto.
- Função que monta o nome da coluna em SQL dinâmico (`format('%I', 'refund' || '_alert')`) não é pega.

Rodar (o dono, na raiz do repositório):
    DATABASE_URL='postgres://...' .venv/bin/python scripts/remover_colunas_refund_alert.py [--aplicar]

Teste: tests/test_remover_colunas_refund_alert.py.
"""
import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_conn  # noqa: E402

RECUSA = 3

# Q1-Q4 do plano pós-#1508, sem mudar uma vírgula. A guarda e a evidência leem a mesma Q2.
Q1 = """
select c.id as comissao, c.status, c.payout_id as saque, po.status as status_saque, c.amount_cents,
       c.created_at, p.refund_status as rotulo, p.refunded_at, c.payment_check_pending
  from affiliate_commissions c
  join affiliate_referral_payments p on p.source = 'stripe' and p.payment_ref = c.stripe_invoice_id
  left join affiliate_payouts po on po.id = c.payout_id
 where p.refund_status <> 'none'
   and c.status <> 'reversed'
   and not exists (select 1 from affiliate_refund_reviews v where v.stripe_invoice_id = c.stripe_invoice_id)
 order by c.status, c.id
"""
Q3 = """
select l.created_at, l.level, l.details->>'commission_id' as comissao, l.details->>'tipo' as tipo,
       l.details ? 'alerta_entregue' as ja_e_o_1508,
       exists (select 1 from affiliate_commissions c
                 join affiliate_refund_reviews v on v.stripe_invoice_id = c.stripe_invoice_id
                where c.id::text = l.details->>'commission_id') as tem_pendencia
  from system_event_logs l
 where l.event_type = 'affiliate_refund_commission_kept'
   and l.created_at >= '2026-11-10 22:00+00' and l.created_at < '2026-11-11 03:00+00'
 order by l.created_at
"""
Q4 = """
select c.id as comissao, c.status, c.payout_id as saque, c.created_at, c.payment_check_pending
  from affiliate_commissions c
 where c.created_at >= '2026-11-10 22:00+00' and c.created_at < '2026-11-11 03:00+00'
   and c.status <> 'reversed'
   and not exists (select 1 from affiliate_refund_reviews v where v.stripe_invoice_id = c.stripe_invoice_id)
 order by c.id
"""
Q2 = """
select c.id as comissao, c.status, c.payout_id as saque, c.refund_alert, c.refund_alert_sent_at,
       v.status as pendencia, v.notified_at
  from affiliate_commissions c
  left join affiliate_refund_reviews v on v.stripe_invoice_id = c.stripe_invoice_id
 where c.refund_alert is not null
 order by c.id
"""


def _ler(aplicar: bool, resumo: dict) -> int | None:
    """Imprime Q1-Q4, preenche o `resumo` e decide. `None` = pode seguir para o drop."""
    with get_conn() as conn:
        conn.execute("set transaction read only")
        sem_logs = conn.execute("select to_regclass('system_event_logs') is null as x").fetchone()["x"]
        for nome, sql in (("Q1", Q1), ("Q3", Q3), ("Q4", Q4)):
            if nome == "Q3" and sem_logs:
                print("Q3: tabela system_event_logs ausente")
                resumo["Q3"] = "tabela ausente"
                continue
            linhas = conn.execute(sql).fetchall()
            print(f"{nome}: {len(linhas)} linhas")
            resumo[nome] = len(linhas)
            for r in linhas:
                print("  " + " · ".join(f"{k} {v}" for k, v in r.items()))
        if not conn.execute("select 1 from pg_attribute where attrelid = 'affiliate_commissions'::regclass"
                            " and attname in ('refund_alert', 'refund_alert_sent_at') and not attisdropped").fetchone():
            print("nada a remover")
            resumo["Q2"], resumo["resultado"] = "colunas ausentes", "nada a remover"
            return 0
        linhas = conn.execute(Q2).fetchall()
        funcoes = [r["f"] for r in conn.execute(
            "select oid::regprocedure::text as f from pg_proc"
            " where strpos(lower(prosrc), 'refund_alert') > 0 order by 1").fetchall()]
    print(f"Q2: {len(linhas)} comissões com refund_alert")
    for r in linhas:
        print(f"comissão {r['comissao']} · {r['status']} · saque {r['saque']} · alerta {r['refund_alert']}"
              f" avisado {r['refund_alert_sent_at']} · pendência {r['pendencia']} avisada {r['notified_at']}")
    soltas = [r["comissao"] for r in linhas if r["pendencia"] is None and r["status"] != "reversed"]
    n, com = len(linhas), sum(r["pendencia"] is not None for r in linhas)
    resumo["Q2"] = f"{n} registro{'' if n == 1 else 's'}" + (
        "" if not n else ", todos com pendência correspondente" if com == n else
        f": {com} com pendência correspondente, {len(soltas)} sem pendência,"
        f" {n - com - len(soltas)} estornados sem pendência")
    if soltas:
        print(f"alerta do #1418 sem pendência: comissões {', '.join(map(str, soltas))}. Rode"
              " scripts/backfill_afiliado_pagamentos.py e este script de novo até a Q1 zerar. Nada removido.")
        resumo["resultado"] = "recusa, alerta sem pendência, nada removido"
        return RECUSA
    if funcoes:
        print(f"funções que citam refund_alert: {', '.join(funcoes)}. Ajuste ou remova antes. Nada removido.")
        resumo["resultado"] = "recusa, função do banco cita refund_alert, nada removido"
        return RECUSA
    if not aplicar:
        print("simulação: nada removido")
        resumo["resultado"] = "nada removido"
        return 0
    return None


def _remover(resumo: dict) -> int:
    try:
        with get_conn() as conn:
            conn.execute("set local lock_timeout = '2s'")
            conn.execute("alter table affiliate_commissions"
                         " drop column if exists refund_alert, drop column if exists refund_alert_sent_at")
    except psycopg.errors.LockNotAvailable:
        print("tabela ocupada: nada removido, rode de novo")
        resumo["resultado"] = "tabela ocupada, nada removido"
        return 2
    print("removidas: refund_alert, refund_alert_sent_at")
    resumo["resultado"] = "colunas removidas"
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--aplicar", action="store_true", help="apaga as colunas; sem isto é simulação")
    args = ap.parse_args(argv)
    resumo: dict = {}
    codigo = _ler(args.aplicar, resumo)
    if codigo is None:
        codigo = _remover(resumo)
    # P4: a thread do #1508 é pública. Só contagens e conclusões: nenhum id, valor ou data.
    print(f"Resumo para a thread: Q1: {resumo['Q1']}; Q2: {resumo['Q2']}; Q3: {resumo['Q3']}; Q4: {resumo['Q4']}."
          f" Modo: {'aplicação' if args.aplicar else 'simulação'}. Resultado: {resumo['resultado']}.")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
