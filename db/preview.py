"""
db/preview.py — Modo preview/demo.

Mantém um usuário fixo (`PREVIEW_USER_ID`) com dados seed determinísticos.
Usado para:
- Renderizar o dashboard no Launch Preview do Claude (e em screenshots públicos)
  sem exigir login real.
- Demo pública do produto: link compartilhável "veja como funciona" sem cadastro.

Segurança: o user demo é isolado (id negativo, fora do espaço de IDs reais
do Discord/WhatsApp/email). Operações destrutivas são bloqueadas no FastAPI
quando o JWT carrega claim `is_preview=true`. Os dados são resetados pelo
scheduler diário (`core/services/preview_scheduler.py`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from psycopg.types.json import Jsonb

from .connection import get_conn
from .users import ensure_user_tx


# ID negativo: garante que nunca colide com IDs reais (Discord/WhatsApp são
# sempre positivos, e o serial de auth_accounts também).
PREVIEW_USER_ID = -1
PREVIEW_USER_EMAIL = "demo@pigbankai.com"
PREVIEW_USER_NAME = "Demo PigBank"


def is_preview_user(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) == PREVIEW_USER_ID


def ensure_preview_user() -> int:
    """Cria o user demo se ainda não existe. Idempotente."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, PREVIEW_USER_ID)
        conn.commit()
    return PREVIEW_USER_ID


def reset_preview_user_data() -> None:
    """Apaga todos os dados do user demo e reinsere o seed determinístico.

    Idempotente: pode rodar a qualquer momento, varias vezes ao dia.
    """
    ensure_preview_user()
    today = datetime.now(timezone.utc).date()
    criado_em = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Limpa tudo do user demo (na ordem certa pra respeitar FKs)
            for table in (
                "credit_transactions",
                "credit_bills",
                "credit_cards",
                "investment_lots",
                "investments",
                "pockets",
                "launches",
                "pending_actions",
                "user_category_rules",
            ):
                cur.execute(f"delete from {table} where user_id = %s", (PREVIEW_USER_ID,))

            # 2. Conta corrente: saldo R$ 5.000
            cur.execute(
                "update accounts set balance = 5000 where user_id = %s",
                (PREVIEW_USER_ID,),
            )

            # 3. Lançamentos do mês corrente
            samples = [
                ("receita", Decimal("5000.00"), "Salário", None, today - timedelta(days=12)),
                ("despesa", Decimal("348.50"), "Mercado", "Supermercado Pão de Açúcar", today - timedelta(days=10)),
                ("despesa", Decimal("89.90"), "Uber", "Corridas da semana", today - timedelta(days=8)),
                ("despesa", Decimal("145.00"), "Restaurante", "Almoço sábado", today - timedelta(days=6)),
                ("despesa", Decimal("220.00"), "Combustível", "Posto Shell", today - timedelta(days=5)),
                ("despesa", Decimal("59.90"), "Netflix", "Streaming", today - timedelta(days=4)),
                ("despesa", Decimal("78.40"), "Farmácia", "Remédios", today - timedelta(days=3)),
                ("despesa", Decimal("165.00"), "Mercado", "Reposição da semana", today - timedelta(days=1)),
            ]
            for tipo, valor, alvo, nota, data in samples:
                cur.execute(
                    """
                    insert into launches(user_id, tipo, valor, alvo, nota, criado_em, posted_at)
                    values (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (PREVIEW_USER_ID, tipo, valor, alvo, nota,
                     datetime.combine(data, datetime.min.time(), tzinfo=timezone.utc), data),
                )

            # 4. Caixinhas
            for name, balance, description in [
                ("Reserva de emergência", Decimal("8500.00"), "6 meses de despesas"),
                ("Viagem Europa 2027", Decimal("2300.00"), "Meta: R$ 15.000"),
            ]:
                cur.execute(
                    """
                    insert into pockets(user_id, name, balance, description)
                    values (%s, %s, %s, %s)
                    """,
                    (PREVIEW_USER_ID, name, balance, description),
                )

            # 5. Investimentos: cobre os principais cenários do dashboard
            investments = [
                {
                    "name": "Tesouro IPCA+ 2032",
                    "rate": Decimal("0.0680"),
                    "period": "ipca_spread",
                    "asset_type": "Tesouro IPCA+",
                    "issuer": "Tesouro Direto",
                    "indexer": "ipca_spread",
                    "tax_profile": "regressive_ir_iof",
                    "maturity": date(2032, 1, 1),
                    "lots": [
                        {"principal": Decimal("3000.00"), "balance": Decimal("3210.00"),
                         "rate": Decimal("0.0680"), "opened": today - timedelta(days=180)},
                        {"principal": Decimal("2000.00"), "balance": Decimal("2050.00"),
                         "rate": Decimal("0.0712"), "opened": today - timedelta(days=60)},
                    ],
                },
                {
                    "name": "CDB Nubank 110% CDI",
                    "rate": Decimal("1.10"),
                    "period": "cdi",
                    "asset_type": "CDB",
                    "issuer": "Nubank",
                    "indexer": "pct_cdi",
                    "tax_profile": "regressive_ir_iof",
                    "maturity": today + timedelta(days=730),
                    "lots": [
                        {"principal": Decimal("5000.00"), "balance": Decimal("5240.00"),
                         "rate": Decimal("1.10"), "opened": today - timedelta(days=200)},
                    ],
                },
                {
                    "name": "LCI Itaú IPCA+ 5,5%",
                    "rate": Decimal("0.055"),
                    "period": "ipca_spread",
                    "asset_type": "LCI",
                    "issuer": "Itaú",
                    "indexer": "ipca_spread",
                    "tax_profile": "exempt_ir_iof",
                    "maturity": today + timedelta(days=900),
                    "lots": [
                        {"principal": Decimal("4000.00"), "balance": Decimal("4180.00"),
                         "rate": Decimal("0.055"), "opened": today - timedelta(days=150)},
                    ],
                },
            ]

            for inv in investments:
                total_balance = sum(l["balance"] for l in inv["lots"])
                last_date = max(l["opened"] for l in inv["lots"])
                cur.execute(
                    """
                    insert into investments(
                        user_id, name, balance, rate, period, last_date,
                        asset_type, indexer, issuer, purchase_date, maturity_date,
                        tax_profile
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    returning id
                    """,
                    (
                        PREVIEW_USER_ID, inv["name"], total_balance,
                        inv["rate"], inv["period"], last_date,
                        inv["asset_type"], inv["indexer"], inv["issuer"],
                        last_date, inv["maturity"], inv["tax_profile"],
                    ),
                )
                inv_id = cur.fetchone()["id"]
                for lot in inv["lots"]:
                    cur.execute(
                        """
                        insert into investment_lots(
                            user_id, investment_id, principal_initial, principal_remaining,
                            balance, opened_at, last_date, status, rate, period
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s)
                        """,
                        (
                            PREVIEW_USER_ID, inv_id, lot["principal"], lot["principal"],
                            lot["balance"], lot["opened"], last_date, lot["rate"], inv["period"],
                        ),
                    )

        conn.commit()
