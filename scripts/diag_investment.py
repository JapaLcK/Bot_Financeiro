"""
Diagnóstico de um investimento específico — confere lots, last_date,
histórico recente de aportes/resgates e validação de yield esperado.

Uso:
    python -m scripts.diag_investment <user_id> <nome_investimento>

Ex:
    python -m scripts.diag_investment 123456789 fabiana
"""
import sys
from datetime import date, timedelta
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

from db.connection import get_conn
from db.investments import _business_days_between, _get_cdi_daily_map


def fmt_brl(v) -> str:
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run(user_id: int, inv_name: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, purchase_date, maturity_date, tax_profile
                from investments
                where user_id = %s and lower(name) = lower(%s)
                """,
                (user_id, inv_name),
            )
            inv = cur.fetchone()
            if not inv:
                print(f"❌ Investimento '{inv_name}' não encontrado para user_id={user_id}")
                return

            print("=" * 70)
            print(f"INVESTIMENTO: {inv['name']}")
            print("=" * 70)
            print(f"  id            : {inv['id']}")
            print(f"  saldo         : {fmt_brl(inv['balance'])}")
            print(f"  rate          : {inv['rate']}  (período: {inv['period']})")
            print(f"  asset_type    : {inv['asset_type']}")
            print(f"  indexer       : {inv['indexer']}")
            print(f"  tax_profile   : {inv['tax_profile']}")
            print(f"  purchase_date : {inv['purchase_date']}")
            print(f"  maturity_date : {inv['maturity_date']}")
            print(f"  last_date     : {inv['last_date']}")

            # Lots
            cur.execute(
                """
                select id, principal_initial, principal_remaining, balance,
                       opened_at, last_date, status, closed_at
                from investment_lots
                where user_id = %s and investment_id = %s
                order by opened_at, id
                """,
                (user_id, inv["id"]),
            )
            lots = cur.fetchall()
            print(f"\nLOTES ({len(lots)} encontrados):")
            for lot in lots:
                print(
                    f"  lot#{lot['id']:>4} | "
                    f"opened={lot['opened_at']} | last={lot['last_date']} | "
                    f"principal_ini={fmt_brl(lot['principal_initial'])} | "
                    f"remaining={fmt_brl(lot['principal_remaining'])} | "
                    f"balance={fmt_brl(lot['balance'])} | "
                    f"status={lot['status']}"
                )

            # Launches recentes
            cur.execute(
                """
                select id, tipo, valor, criado_em, nota
                from launches
                where user_id = %s and alvo = %s
                order by criado_em desc
                limit 15
                """,
                (user_id, inv["name"]),
            )
            launches = cur.fetchall()
            print(f"\nÚLTIMOS LANÇAMENTOS ({len(launches)}):")
            for l in launches:
                print(
                    f"  #{l['id']:>6} | {str(l['criado_em'])[:19]} | "
                    f"{l['tipo']:<30} | {fmt_brl(l['valor'])} | {l.get('nota') or ''}"
                )

            # CDI esperado vs realizado
            if inv["period"] == "cdi" and inv["last_date"]:
                today = date.today()
                start = inv["last_date"] + timedelta(days=1)
                cdi_map = _get_cdi_daily_map(cur, start, today)
                cdi_days = sorted(d for d in cdi_map if inv["last_date"] < d <= today)
                print("\nCDI APÓS last_date:")
                if not cdi_days:
                    print("  (nenhum CDI publicado pelo BCB depois de last_date)")
                else:
                    factor = 1.0
                    rate = float(inv["rate"])
                    for d in cdi_days:
                        v = cdi_map[d]
                        factor *= 1.0 + (v / 100.0) * rate
                        print(
                            f"  {d}: CDI={v:.6f}%  | "
                            f"contrib={(v/100.0)*rate*100:.6f}%  | "
                            f"factor_acum={factor:.8f}"
                        )
                    expected = Decimal(str(float(inv["balance"]) * factor))
                    print(f"\n  Saldo se aplicasse esses CDIs: {fmt_brl(expected)}")

                # Última taxa conhecida (para projeção)
                cur.execute(
                    "select ref_date, value from market_rates where code='CDI' "
                    "order by ref_date desc limit 1"
                )
                latest = cur.fetchone()
                if latest:
                    print(
                        f"\n  Última CDI conhecida: {latest['ref_date']} = {float(latest['value']):.6f}% a.d."
                    )
                    bd = _business_days_between(inv["last_date"], today)
                    if bd > 0:
                        rate = float(inv["rate"])
                        proj_factor = (1.0 + (float(latest["value"]) / 100.0) * rate) ** bd
                        proj_balance = float(inv["balance"]) * proj_factor
                        print(
                            f"  Dias úteis até hoje ({today}): {bd} | "
                            f"saldo projetado: {fmt_brl(proj_balance)}"
                        )

            print()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python -m scripts.diag_investment <user_id> <nome>")
        sys.exit(2)
    run(int(sys.argv[1]), sys.argv[2])
